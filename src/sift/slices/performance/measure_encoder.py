# SPDX-License-Identifier: AGPL-3.0-or-later
"""The GPU's previews, timed with the preview's own command on a 1080p clip, one at a time and then
more. A width whose encodes fail isn't counted: that's how a card's limit on sessions shows."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
from typing import Protocol

from sift.kernel import media, sampling
from sift.kernel.config import Settings
from sift.kernel.hardware import HardwareReport
from sift.kernel.log import get_logger
from sift.kernel.media import Encoder
from sift.kernel.subprocess import Priority
from sift.slices.performance.budget import PREVIEWS, Deadline
from sift.slices.performance.measure_models import NO_SOURCE, MemoryWatch, Watch
from sift.slices.performance.selftest import (
    BUSY,
    LEVELS,
    NEAR_BEST,
    REPEATS,
    TOO_BUSY_SECONDS,
    Recommendation,
    middle_by,
    midpoint,
    near_best,
    others_busy,
    steady,
    sureness,
)

log = get_logger(__name__)

#: 1080p, and twice the longest preview, so the clip is cut into a preview's whole montage.
SOURCE_WIDTH = 1920
SOURCE_HEIGHT = 1080
SOURCE_SECONDS = 30
SOURCE_RATE = 30

NO_CARD = "this device has no GPU encoder that previews would use"


class PreviewCommand(Protocol):
    """The preview builder's own command, handed in by the composition root."""

    def __call__(
        self,
        source: Path,
        destination: Path,
        *,
        pieces: Sequence[sampling.Piece],
        encoder: Encoder,
        device: str | None = None,
        decode: Sequence[str] = (),
        settings: Settings,
    ) -> list[str]: ...


@dataclass(frozen=True)
class CardLevel:
    """One width: how many previews were built at the same time on the card, and what came of them."""

    at_once: int
    seconds: float
    finished: int
    worst_lag_seconds: float = 0.0
    worst_wait_seconds: float = 0.0
    low: float | None = None
    high: float | None = None
    busy: bool = False
    card_memory_bytes: int | None = None
    fell_behind: int = 0

    @property
    def throughput(self) -> float:
        return self.finished / self.seconds if self.seconds > 0 else 0.0

    @property
    def responsive(self) -> bool:
        worst = max(self.worst_lag_seconds, self.worst_wait_seconds)
        return worst < TOO_BUSY_SECONDS and not self.fell_behind

    @property
    def counted(self) -> bool:
        """Every encode finished and the server kept up: a width that may be the answer."""
        return self.finished == self.at_once and self.responsive


@dataclass(frozen=True)
class CardCurve:
    """How the card did as more previews were built on it together."""

    encoder: str
    decodes_on_card: bool
    levels: tuple[CardLevel, ...] = ()
    failed: str | None = None

    @property
    def best(self) -> CardLevel | None:
        return near_best([one for one in self.levels if one.counted], key=_per_second)

    def said(self) -> list[str]:
        """What the card was not asked, and what it was asked while other programs were busy."""
        if self.failed is not None:
            return [f"Previews on the GPU weren't measured: {self.failed}."]
        said = [
            f"On the GPU, {one.at_once - one.finished} of {one.at_once} previews built at the same "
            f"time failed, so {one.at_once} wasn't counted and nothing wider was tried."
            for one in self.levels
            if one.finished < one.at_once
        ]
        busy = [str(one.at_once) for one in self.levels if one.busy]
        if busy:
            said.append(f"The GPU at {', '.join(busy)} at the same time was {BUSY}.")
        return said


def _per_second(level: CardLevel) -> float:
    return level.throughput


def _marked(level: CardLevel) -> CardLevel:
    return replace(level, busy=True)


def _kept(runs: Sequence[CardLevel], chosen: Encoder) -> CardLevel:
    figures = [_per_second(each) for each in runs]
    kept = replace(middle_by(runs, key=_per_second), low=min(figures), high=max(figures))
    log.info(
        "performance.card.level",
        encoder=chosen.value,
        at_once=kept.at_once,
        finished=kept.finished,
        per_second=round(kept.throughput, 3),
        low=kept.low,
        high=kept.high,
    )
    return kept


async def build_source(into: Path, settings: Settings) -> Path:
    """Write the 1080p clip the card's previews and the models read."""
    target = into / "self-test-1080p.mp4"
    size = f"{SOURCE_WIDTH}x{SOURCE_HEIGHT}"
    await media.run(
        [
            settings.ffmpeg_path,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size={size}:rate={SOURCE_RATE}:duration={SOURCE_SECONDS}",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            str(target),
        ],
        time_limit=180.0,
    )
    return target


async def _encode(
    preview: PreviewCommand,
    source: Path,
    into: Path,
    index: int,
    *,
    encoder: Encoder,
    decode: Sequence[str],
    settings: Settings,
) -> bool:
    """One preview, built the way the preview job builds it. True when it finished."""
    shape = sampling.preview_shape(sampling.DEFAULT_PREVIEW_SHAPE)
    try:
        argv = preview(
            source,
            into / f"card-{index}.mp4",
            pieces=sampling.preview_segments(SOURCE_SECONDS * 1000, shape),
            encoder=encoder,
            device=media.render_node() if encoder is Encoder.VAAPI else None,
            decode=decode,
            settings=settings,
        )
        await media.run(argv, time_limit=300.0, priority=Priority.BACKGROUND)
    except (media.FFmpegError, ValueError) as error:
        log.info("performance.card.encode_failed", error=str(error))
        return False
    return True


def _stops(done: Sequence[CardLevel]) -> bool:
    """Whether the card's ladder goes no wider: a width failed, or gained under the margin."""
    last = done[-1]
    if not last.counted:
        return True
    return len(done) > 1 and max(map(_per_second, done[:-1])) >= _per_second(last) * NEAR_BEST


async def measure_card(
    *,
    source: Path | None,
    workspace: Path,
    settings: Settings,
    hardware: HardwareReport,
    preview: PreviewCommand,
    worst_lag: Callable[[], float],
    worst_wait: Callable[[], float],
    encoder: Encoder | None = None,
    levels: Sequence[int] = LEVELS,
    repeats: int = REPEATS,
    busy: Callable[[], bool] = others_busy,
    watch: Callable[[], Watch] = MemoryWatch,
    encode: Callable[..., Awaitable[bool]] | None = None,
    clock: Callable[[], float] = time.monotonic,
    fell_behind: Callable[[], int] = lambda: 0,
    deadline: Deadline | None = None,
) -> CardCurve:
    """The card's previews at each width, with `encoder` or what previews use. Never raises."""
    chosen = encoder or media.choose_encoder(hardware)
    # The processor's fallback drops the card's decoder too, as `Accelerator.run` does.
    decode = () if chosen is Encoder.CPU else media.decode_flags(hardware)
    curve = CardCurve(encoder=chosen.value, decodes_on_card=bool(decode))
    if chosen is Encoder.CPU and encoder is None:
        return replace(curve, failed=NO_CARD)
    if source is None:
        return replace(curve, failed=NO_SOURCE)
    one = partial(
        encode or _encode,
        preview,
        source,
        workspace,
        encoder=chosen,
        decode=decode,
        settings=settings,
    )

    async def run(at_once: int) -> CardLevel:
        lag, wait, behind = worst_lag(), worst_wait(), fell_behind()
        memory = watch()
        memory.start()
        started = clock()
        try:
            done = await asyncio.gather(*(one(index) for index in range(at_once)))
        finally:
            _, card = memory.stop()
        took = clock() - started
        return CardLevel(
            at_once=at_once,
            seconds=took,
            finished=sum(1 for ok in done if ok),
            worst_lag_seconds=max(0.0, worst_lag() - lag),
            worst_wait_seconds=max(0.0, worst_wait() - wait),
            card_memory_bytes=card,
            fell_behind=fell_behind() - behind,
        )

    async def take(at_once: int) -> CardLevel:
        return _kept([await run(at_once) for _ in range(repeats)], chosen)

    deadline = deadline or Deadline(PREVIEWS)
    done: list[CardLevel] = []
    for at_once in levels:
        level = await deadline.within(steady(partial(take, at_once), _marked, busy))
        if level is None:
            break
        done.append(level)
        if _stops(done):
            break
    between = midpoint([level for level in done if level.counted], key=_per_second)
    if between is not None:
        level = await deadline.within(steady(partial(take, between), _marked, busy))
        done.extend([level] if level is not None else [])
        done.sort(key=lambda level: level.at_once)
    return replace(curve, levels=tuple(done))


def recommend_previews(
    curve: CardCurve | None, *, tasks: int, current: dict[str, int], key: str, label: str
) -> Recommendation | None:
    """Previews at the same time from what the card did with the preview's own command; never above the
    tasks, which a preview is one of. None where the card was not measured."""
    best = None if curve is None else curve.best
    if curve is None or best is None:
        return None
    suggested = max(1, min(best.at_once, tasks))
    widest = max(one.at_once for one in curve.levels)
    if best.at_once == widest and all(one.counted for one in curve.levels):
        why = "nothing wider was tried"
    elif any(not one.counted and one.at_once > best.at_once for one in curve.levels):
        why = "wider failed, or Sift stopped keeping up"
    else:
        why = f"building more at the same time added under {round((1 - NEAR_BEST) * 100)}%"
    capped = f" The task count, {tasks}, is the most it can be." if suggested < best.at_once else ""
    sure = sureness(best.low, best.high, best.throughput, "previews a second")
    return Recommendation(
        key=key,
        label=label,
        current=current.get(key, 0),
        suggested=suggested,
        reason=(
            f"Measured on the GPU with the command previews use ({curve.encoder}"
            f"{', decoding on the GPU' if curve.decodes_on_card else ''}): {best.at_once} at the same time "
            f"built {best.throughput:.2g} previews a second, and {why}.{capped}{sure}"
        ),
    )
