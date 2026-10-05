# SPDX-License-Identifier: AGPL-3.0-or-later
"""The clip every encoding level works on, and the decoder's two rates measured on it."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from sift.kernel import media
from sift.kernel.config import Settings
from sift.kernel.log import get_logger
from sift.kernel.subprocess import Priority

log = get_logger(__name__)

#: How long each clip is, in seconds: long enough that a level is work rather than start-up.
CLIP_SECONDS = 20

#: 720p, the commonest size in a library, and quick enough to build on a slow machine.
CLIP_WIDTH = 1280
CLIP_HEIGHT = 720
CLIP_RATE = 30

#: Runs of each level: the middle one is kept, and the lowest and highest say how sure it is.
REPEATS = 3


def middle(values: Sequence[float]) -> float:
    """The median of a few readings: sorted, the one in the middle, the lower of two when even."""
    ordered = sorted(values)
    return ordered[(len(ordered) - 1) // 2]


@dataclass(frozen=True)
class Decode:
    """How fast this machine decodes and seeks the clip at 720p, which a Build reads by."""

    frames_per_second: float
    """Frames of 720p decoded per second by one task with a background job's thread share."""
    seek_seconds: float
    """What one more moment costs when taken by seeking on the local disk; a share adds its own."""

    def as_dict(self) -> dict[str, object]:
        return {
            "frames_per_second": round(self.frames_per_second, 1),
            "seek_seconds": round(self.seek_seconds, 4),
        }


# --- measuring the decoder ------------------------------------------------------------------------

#: Where the seek half takes its moments, as fractions of the clip: spread, as a strip's are.
SEEK_MOMENTS = tuple((index + 0.5) / 10 for index in range(10))


async def _decode_once(source: Path, settings: Settings) -> float:
    """Decode the whole clip with one background task's thread share. Seconds it took."""
    started = time.monotonic()
    await media.run(
        [
            settings.ffmpeg_path,
            *media.background_flags(settings),
            "-i",
            str(source),
            "-f",
            "null",
            "-",
        ],
        time_limit=300.0,
        priority=Priority.NORMAL,
    )
    return time.monotonic() - started


async def _seek_run(source: Path, ats: Sequence[float], settings: Settings) -> float:
    """Seconds to take a frame at each of `ats` by seeking, as `media.moments_to_files` does."""
    argv = [settings.ffmpeg_path, *media.background_flags(settings)]
    for at in ats:
        argv += ["-ss", f"{at:.3f}", "-i", str(source)]
    for index in range(len(ats)):
        argv += ["-map", f"{index}:v", "-frames:v", "1", "-f", "null", "-"]
    started = time.monotonic()
    await media.run(argv, time_limit=120.0, priority=Priority.NORMAL)
    return time.monotonic() - started


async def measure_decode(
    source: Path,
    settings: Settings,
    *,
    repeats: int = REPEATS,
    decode: Callable[[Path, Settings], Awaitable[float]] | None = None,
    seek: Callable[[Path, Sequence[float], Settings], Awaitable[float]] | None = None,
) -> Decode | None:
    """The Build's two rates, each the middle of `repeats` runs; None if the decoder can't run."""
    run_decode = decode or _decode_once
    run_seek = seek or _seek_run
    ats = [CLIP_SECONDS * at for at in SEEK_MOMENTS]
    try:
        decodes = [await run_decode(source, settings) for _ in range(repeats)]
        seeks = [await run_seek(source, ats, settings) / len(ats) for _ in range(repeats)]
    except (media.FFmpegError, OSError) as error:
        log.warning("performance.selftest.no_decoder", error=str(error))
        return None
    seconds = middle(decodes)
    found = Decode(
        frames_per_second=CLIP_SECONDS * CLIP_RATE / seconds if seconds > 0 else 0.0,
        seek_seconds=middle(seeks),
    )
    log.info("performance.selftest.decode", **found.as_dict())
    return found
