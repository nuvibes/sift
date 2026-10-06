# SPDX-License-Identifier: AGPL-3.0-or-later
"""Working out what this device can do: encodes, the decoder and each storage's readers, every
curve judged by `near_best`. Nothing here writes a setting: an admin applies the advice."""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field, replace
from functools import partial
from pathlib import Path
from typing import Protocol

from sift.kernel import device_load, media
from sift.kernel.config import Settings
from sift.kernel.lanes import MAX_READS_AT_ONCE
from sift.kernel.log import get_logger
from sift.kernel.subprocess import Priority
from sift.kernel.wiring import Part
from sift.slices.performance import uncached
from sift.slices.performance.budget import (
    DECODER,
    ENCODING,
    GRACE_SECONDS,
    STORAGE,
    Budget,
    Deadline,
)

# Re-exported: the ladders below and their callers read these here.
from sift.slices.performance.clip import (
    CLIP_HEIGHT as CLIP_HEIGHT,
)
from sift.slices.performance.clip import (
    CLIP_RATE as CLIP_RATE,
)
from sift.slices.performance.clip import (
    CLIP_SECONDS as CLIP_SECONDS,
)
from sift.slices.performance.clip import (
    CLIP_WIDTH as CLIP_WIDTH,
)
from sift.slices.performance.clip import (
    REPEATS as REPEATS,
)
from sift.slices.performance.clip import (
    SEEK_MOMENTS as SEEK_MOMENTS,
)
from sift.slices.performance.clip import (
    Decode as Decode,
)
from sift.slices.performance.clip import (
    measure_decode as measure_decode,
)
from sift.slices.performance.clip import (
    middle as middle,
)

log = get_logger(__name__)

#: The encodes-at-once levels, in order. A small machine stops before the wide ones.
LEVELS = (1, 2, 4, 8, 16)

#: The answer is the smallest level within this share of the best: wider buys only noise.
NEAR_BEST = 0.95

#: Above this the application was not keeping up: the quarter second the server warns at.
TOO_BUSY_SECONDS = 0.25

BUSY = "measured while other programs were busy"

#: Why a stage measured less than it would have: its part of the run's time was used.
OUT_OF_TIME = "its time ran out"
OUT_OF_TIME_UNTRIED = f"nothing wider was tried: {OUT_OF_TIME}."


def others_busy() -> bool:
    """Whether the last reading of the device found other programs busy."""
    latest = device_load.READER.latest
    return latest is not None and bool(latest.busy())


async def steady[T](
    take: Callable[[], Awaitable[T]], mark: Callable[[T], T], busy: Callable[[], bool]
) -> T:
    """A level, marked where other programs were busy: once, as the run's time is kept to."""
    level = await take()
    return mark(level) if busy() else level


def _marked[T: (Level, StorageLevel)](level: T) -> T:
    return replace(level, busy=True)


def middle_by[T](runs: Sequence[T], *, key: Callable[[T], float]) -> T:
    """The whole run in the middle by `key`, so its other readings stay with the number."""
    ordered = sorted(runs, key=key)
    return ordered[(len(ordered) - 1) // 2]


class _Rung(Protocol):
    @property
    def at_once(self) -> int: ...


def near_best[T: _Rung](levels: Sequence[T], *, key: Callable[[T], float]) -> T | None:
    """The smallest level within `NEAR_BEST` of the best one; `levels` are in order of width."""
    if not levels:
        return None
    top = max(key(one) for one in levels)
    return next(one for one in levels if key(one) >= top * NEAR_BEST)


def midpoint[T: _Rung](levels: Sequence[T], *, key: Callable[[T], float]) -> int | None:
    """The whole width halfway between the best level and its better neighbour, if not yet tried."""
    if len(levels) < 2:
        return None
    best = max(range(len(levels)), key=lambda at: key(levels[at]))
    other = max((levels[at] for at in (best - 1, best + 1) if 0 <= at < len(levels)), key=key)
    total = levels[best].at_once + other.at_once
    if total % 2 or total // 2 in {one.at_once for one in levels}:
        return None
    return total // 2


def sureness(low: float | None, high: float | None, figure: float, unit: str) -> str:
    """How far apart a level's runs were, as how sure its number is; empty for one take."""
    if low is None or high is None or figure <= 0 or low == high:
        return ""
    apart = round((high - low) / figure * 100)
    margin = round((1 - NEAR_BEST) * 100)
    said = f" Its runs ranged from {low:.3g} to {high:.3g} {unit}, {apart}% apart"
    if apart > margin:
        return f"{said}: wider than the {margin}% the choice rests on, so another run may differ."
    return f"{said}, inside the {margin}% the choice rests on."


@dataclass(frozen=True)
class Level:
    """One run: how many encodes at once, and what came of it."""

    at_once: int
    seconds: float
    """Wall clock for the whole run."""
    finished: int
    """Encodes that actually completed. A failure here is not counted as work done."""
    worst_lag_seconds: float
    """The worst the event loop was held while this level ran."""
    worst_wait_seconds: float
    """The longest anything waited for a free thread while this level ran."""
    low: float | None = None
    high: float | None = None
    """The slowest and quickest of the level's runs, in encodes a second; None on an older reading."""
    busy: bool = False
    fell_behind: int = 0

    @property
    def throughput(self) -> float:
        """Encodes per second: what levels are compared on."""
        if self.seconds <= 0:
            return 0.0
        return self.finished / self.seconds

    @property
    def responsive(self) -> bool:
        """Whether the application stayed usable at this level."""
        worst = max(self.worst_lag_seconds, self.worst_wait_seconds)
        return worst < TOO_BUSY_SECONDS and not self.fell_behind

    def as_dict(self) -> dict[str, object]:
        return {
            "at_once": self.at_once,
            "seconds": round(self.seconds, 2),
            "finished": self.finished,
            "per_second": round(self.throughput, 3),
            "low": self.low,
            "high": self.high,
            "responsive": self.responsive,
            "fell_behind": self.fell_behind,
            "busy": self.busy,
        }


#: A level where one reader waits this long a seek is not advised: every other reader waits as long.
SEEK_BOUND_SECONDS = TOO_BUSY_SECONDS


@dataclass(frozen=True)
class StorageLevel:
    """One run against one storage: how many files were read at once, and what came back."""

    at_once: int
    seconds: float
    bytes_read: int
    seeks: int = 0
    """How many seeks the readers made between them, so the cost of one can be read off."""
    low: float | None = None
    high: float | None = None
    """The slowest and quickest of the level's runs, in MB/s; None on an older reading."""
    busy: bool = False

    @property
    def megabytes_per_second(self) -> float:
        if self.seconds <= 0:
            return 0.0
        return self.bytes_read / self.seconds / 1_000_000

    @property
    def seconds_per_seek(self) -> float:
        """What one reader paid per seek: the readers ran side by side, each with a share."""
        if self.seeks <= 0 or self.at_once <= 0:
            return 0.0
        return self.seconds * self.at_once / self.seeks

    @property
    def quick_enough(self) -> bool:
        return self.seconds_per_seek < SEEK_BOUND_SECONDS

    def as_dict(self) -> dict[str, object]:
        return {
            "at_once": self.at_once,
            "seconds": round(self.seconds, 2),
            "megabytes": round(self.bytes_read / 1_000_000, 1),
            "megabytes_per_second": round(self.megabytes_per_second, 1),
            "low": self.low,
            "high": self.high,
            "seconds_per_seek": round(self.seconds_per_seek, 4),
            "busy": self.busy,
        }


def _mbps(level: StorageLevel) -> float:
    return level.megabytes_per_second


def storage_name(storage: str) -> str:
    """A storage named once and shortly: a share by its path, a drive by its letter."""
    bare = storage.rstrip("\\/")
    if len(bare) == 2 and bare[1] == ":":
        return f"drive {bare}"
    return bare or "the system disk"


@dataclass(frozen=True)
class StorageCurve:
    """How one storage behaved as more files were read from it at once."""

    storage: str
    """The storage as the operating system names it: `\\\\server\\share\\` or `C:\\`."""
    label: str
    """The library folders on it, listed where a person asks which they are."""
    remote: bool
    levels: tuple[StorageLevel, ...] = ()
    failed: str | None = None
    """Why nothing was measured here, so a storage that could not be measured is not a slow one."""
    unmeasured: str | None = None
    """What the ladder did not try and why, where it stopped short of the widest level."""

    @property
    def name(self) -> str:
        return storage_name(self.storage)

    @property
    def eligible(self) -> tuple[StorageLevel, ...]:
        """The levels that may be the answer: the first, and each whose seeks stayed quick."""
        return tuple(one for at, one in enumerate(self.levels) if at == 0 or one.quick_enough)

    @property
    def best(self) -> StorageLevel | None:
        return near_best(self.eligible, key=_mbps)

    @property
    def too_slow(self) -> StorageLevel | None:
        """The first level wider than the answer whose seeks passed the bound, if one did."""
        best = self.best
        return next(
            (
                one
                for one in self.levels
                if best and one.at_once > best.at_once and not one.quick_enough
            ),
            None,
        )

    @property
    def collapsed(self) -> bool:
        """Whether the level after the answer delivered less, rather than merely not enough more."""
        best = self.best
        if best is None:
            return False
        after = [one for one in self.levels if one.at_once > best.at_once]
        return bool(after) and after[0].megabytes_per_second < best.megabytes_per_second


@dataclass(frozen=True)
class Measurement:
    """Everything the test found out. Passed to `recommend`, which decides what it means."""

    cores: int
    levels: tuple[Level, ...] = ()
    failed: str | None = None
    """Why nothing was measured, so a test that could not run does not read as a slow machine."""
    storages: tuple[StorageCurve, ...] = ()
    """Every storage the library sits on, local disks too, each read for how many it serves."""
    decode: Decode | None = None
    """How fast this machine decodes and seeks, or None where the decoder could not be run."""

    @property
    def best(self) -> Level | None:
        """The smallest responsive level within `NEAR_BEST` of the most work done."""
        usable = [level for level in self.levels if level.responsive and level.finished]
        return near_best(usable, key=lambda level: level.throughput)

    @property
    def at_ceiling(self) -> bool:
        """Whether the level chosen is the widest one tried, so nothing above it was measured."""
        best = self.best
        return best is not None and bool(self.levels) and best.at_once == self.levels[-1].at_once

    def not_measured(self) -> list[str]:
        """What each curve did not try and why, in sentences for under the result."""
        said = []
        last = self.levels[-1] if self.levels else None
        if last is not None and not last.responsive:
            said.append(
                f"Encoding more than {last.at_once} at once was not tried: at {last.at_once} "
                f"Sift stopped keeping up."
            )
        said += [f"On {one.name}, {one.unmeasured}" for one in self.storages if one.unmeasured]
        said += _busy("Encoding", self.levels)
        for one in self.storages:
            said += _busy(f"On {one.name}, reading", one.levels)
        return said


def _busy(what: str, levels: Sequence[Level] | Sequence[StorageLevel]) -> list[str]:
    widths = [str(one.at_once) for one in levels if one.busy]
    if not widths:
        return []
    return [f"{what} {', '.join(widths)} at the same time was {BUSY}."]


@dataclass(frozen=True)
class Recommendation:
    """One setting, what it is now, and what the measurement says it should be."""

    key: str
    label: str
    current: int
    suggested: int
    reason: str

    @property
    def changes_anything(self) -> bool:
        return self.current != self.suggested


#: What the recommendations may touch, one list to check against the screen.
WORKER_COUNT_KEY = "performance.worker_count"
GENERATION_LIMIT_KEY = "performance.generation_limit"
SHARE_READS_KEY = "performance.share_reads_at_once"

#: The share setting's "as measured for each storage".
AS_MEASURED = 0


def recommend(measurement: Measurement, *, current: dict[str, int]) -> list[Recommendation]:
    """What to change: tasks a margin above the measured level, previews at it, or nothing."""
    best = measurement.best
    if measurement.failed is not None or best is None:
        return []

    measured = best.at_once
    threads = measurement.cores

    # A margin rather than a multiple, so nothing is recommended that the test never ran.
    jobs = min(max(1, threads - 1), measured + max(2, measured // 2))

    # A cap above the task count could never take effect while still reading as a measurement.
    generation = min(measured, jobs)

    if measurement.at_ceiling:
        found = (
            f"Measured: this device was still finishing more work at {measured} encodes at once, "
            f"which is as wide as the test goes \u2014 nothing wider was tried, so {measured} is "
            f"the most this run can show rather than the point where it stopped helping."
        )
    else:
        found = (
            f"Measured: this device encoded {measured} clips at the same time without falling "
            f"behind{_why_not_wider(measurement.levels, best)}."
        )
    found += sureness(best.low, best.high, best.throughput, "encodes a second")

    margin = _tasks_said(jobs, measured, threads)

    if generation < measured:
        preview_reason = (
            f"{found} The cap is the task count above rather than {measured}, because building a "
            f"preview is one of those tasks \u2014 a cap above the task count could never come into "
            f"effect."
        )
    else:
        preview_reason = (
            f"{found} Building a preview is the encoding that was measured, so this is that number "
            f"with nothing derived in between."
        )

    recommendations = [
        Recommendation(
            key=WORKER_COUNT_KEY,
            label="How many tasks run at the same time",
            current=current.get(WORKER_COUNT_KEY, 0),
            suggested=jobs,
            reason=f"{found} {margin}",
        ),
        Recommendation(
            key=GENERATION_LIMIT_KEY,
            label="How many previews are built at the same time",
            current=current.get(GENERATION_LIMIT_KEY, 0),
            suggested=generation,
            reason=preview_reason,
        ),
    ]
    share = recommend_share_reads(measurement.storages, current=current)
    if share is not None:
        recommendations.append(share)
    return recommendations


def _why_not_wider(levels: Sequence[Level], best: Level) -> str:
    """What stopped the ladder past the answer, said of clips: the task count is another number."""
    margin = round((1 - NEAR_BEST) * 100)
    wider = [one for one in levels if one.at_once > best.at_once]
    said = ""
    if any(one.responsive and one.finished for one in wider):
        said = f", and running more than that finished no more work, within {margin}%"
    stalled = next((one for one in wider if not one.responsive), None)
    if stalled is not None:
        more = round((stalled.throughput / best.throughput - 1) * 100) if best.throughput else 0
        did = f" it finished {more}% more work, but" if more > margin else ""
        said += f". At {stalled.at_once} clips at the same time{did} Sift fell behind"
    return said


def _tasks_said(jobs: int, measured: int, threads: int) -> str:
    """Why the task count is what it is, beside the clips measured."""
    kept = (
        f"one of the {threads} threads this device reports is kept for everything that isn't a task"
    )
    if jobs < measured:
        return f"{jobs} is lower than that on purpose: {kept}."
    if jobs == measured:
        return f"{jobs} adds nothing for the tasks that aren't encoding, because {kept}."
    said = (
        f"Tasks aren't all encoding, so {jobs} is the {measured} clips measured plus "
        f"{jobs - measured} for the tasks that aren't \u2014 a judgment rather than something the "
        f"test measured"
    )
    if jobs == threads - 1:
        return f"{said}, and no more, because {kept}."
    return f"{said}."


def _share_said(curve: StorageCurve, best: StorageLevel) -> str:
    """One share's number, the readings it came from, and why wider was not chosen."""
    readings = ", ".join(
        f"{level.at_once} at once {level.megabytes_per_second:.0f} MB/s" for level in curve.levels
    )
    slow = curve.too_slow
    if slow is not None:
        why = (
            f"at {slow.at_once} one reader waited {slow.seconds_per_seek:.2f} s for each seek, "
            f"past the quarter second a person notices"
        )
    elif best.at_once == curve.levels[-1].at_once:
        why = "nothing wider was tried"
    elif curve.collapsed:
        why = "reading more at once delivered less, not more"
    else:
        why = f"reading more at once delivered under {round((1 - NEAR_BEST) * 100)}% more"
    files = "file" if best.at_once == 1 else "files"
    sure = sureness(best.low, best.high, best.megabytes_per_second, "MB/s")
    return f"Measured on {curve.name}: {best.at_once} {files} at once ({readings}); {why}.{sure}"


def recommend_share_reads(
    storages: Sequence[StorageCurve], *, current: dict[str, int]
) -> Recommendation | None:
    """Automatic, or the number set where every share measured it; None where none was measured."""
    measured = [(one, one.best) for one in storages if one.remote and one.best is not None]
    if not measured:
        return None
    found = " ".join(_share_said(curve, best) for curve, best in measured if best is not None)
    now = current.get(SHARE_READS_KEY, AS_MEASURED)
    same = all(best is not None and best.at_once == now for _curve, best in measured)
    return Recommendation(
        key=SHARE_READS_KEY,
        label="How many files are read at the same time from a network share",
        current=now,
        suggested=now if same else AS_MEASURED,
        reason=f"{found} {SAME_AS_MEASURED if same else ONE_FOR_EVERY_SHARE}",
    )


ONE_FOR_EVERY_SHARE = (
    "Automatic reads each share at its own measured number; a number here is used for every share "
    "instead."
)
SAME_AS_MEASURED = (
    "That's the number set here, so nothing changes. Automatic would follow each share's own "
    "measured number."
)


# --- taking the measurement ---------------------------------------------------------------------


@dataclass
class _Readings:
    """The two live readings, as deltas around a level, so each level is judged on what it did."""

    worst_lag: Callable[[], float]
    worst_wait: Callable[[], float]
    fell_behind: Callable[[], int]
    lag_before: float = 0.0
    wait_before: float = 0.0
    behind_before: int = 0

    def start(self) -> None:
        self.lag_before = self.worst_lag()
        self.wait_before = self.worst_wait()
        self.behind_before = self.fell_behind()

    def since(self) -> tuple[float, float, int]:
        return (
            max(0.0, self.worst_lag() - self.lag_before),
            max(0.0, self.worst_wait() - self.wait_before),
            self.fell_behind() - self.behind_before,
        )


CLIP_NAME = "self-test-source.mp4"


async def build_clip(into: Path, settings: Settings) -> Path:
    """Write the one clip every level encodes, and hand back where it is."""
    target = into / CLIP_NAME
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
            f"testsrc2=size={CLIP_WIDTH}x{CLIP_HEIGHT}:rate={CLIP_RATE}:duration={CLIP_SECONDS}",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            str(target),
        ],
        time_limit=120.0,
    )
    return target


#: Threads one encode is allowed, fixed so the encodes at once are the only thing that varies:
#: uncapped, one encode fills a large machine and the curve is flat from the start.
THREADS_PER_ENCODE = 2


def cores_in_use(at_once: int) -> int:
    """Roughly how much of the machine a level occupies."""
    return at_once * THREADS_PER_ENCODE


def planned_levels(cores: int, levels: Sequence[int] = LEVELS) -> tuple[int, ...]:
    """The doubling rungs this machine can reach, an upper bound for the screen; never none."""
    reachable = tuple(one for one in levels if cores_in_use(one) <= cores * 2)
    return reachable or (levels[0],)


async def _encode_once(
    source: Path, into: Path, index: int, settings: Settings, threads: int = 0
) -> bool:
    """Re-encode the clip once; True when it finished. `threads` caps the decoder and encoder."""
    cap = [] if threads <= 0 else ["-threads", str(threads), "-filter_threads", str(threads)]
    output_cap = [] if threads <= 0 else ["-threads", str(threads)]
    try:
        await media.run(
            [
                settings.ffmpeg_path,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                *cap,
                "-i",
                str(source),
                "-c:v",
                "libx264",
                # Slower than a preview's preset, so the encodes of a level overlap.
                "-preset",
                "medium",
                "-pix_fmt",
                "yuv420p",
                *output_cap,
                str(into / f"encoded-{index}.mp4"),
            ],
            time_limit=300.0,
            priority=Priority.NORMAL,
        )
    except media.FFmpegError:
        return False
    return True


# --- measuring the storage: read as a probe reads, the readers it serves read off the curve ------

#: How many places in a file one reader seeks to, and how much it reads at each: about what a
#: decoder pulls to rebuild one frame from the keyframe before it.
SEEKS_PER_FILE = 6
BYTES_PER_SEEK = 1 << 20

#: The smallest file worth seeking into. Below this the six reads overlap and measure the cache.
SAMPLE_FLOOR_BYTES = 16 << 20

#: How many entries a walk of a library folder looks at, and how many files it keeps: two for each
#: reader of the widest level, so no two readers of one run share a file.
SAMPLE_WALK_LIMIT = 4000
SAMPLE_FILES = 2 * MAX_READS_AT_ONCE

#: The readers-at-once levels, doubling up to the most the lanes allow one storage.
STORAGE_LEVELS = tuple(1 << step for step in range(MAX_READS_AT_ONCE.bit_length()))

#: One salt per level per repeat, the midpoint and a busy level's second take included, so no run
#: re-reads a place another left in the system's cache.
_SALTS = 2 * (len(STORAGE_LEVELS) + 1) * REPEATS

#: Why a local disk is not measured where the system cannot read past its cache: a read from
#: memory is not a read from the disk.
NO_UNCACHED = "this system offers no way to read a disk past its own cache"


@dataclass(frozen=True)
class StorageToMeasure:
    """One storage and the library folders on it, as the composition root hands them in."""

    storage: str
    label: str
    remote: bool
    roots: tuple[Path, ...]


def sample_files(roots: Sequence[Path], *, wanted: int = SAMPLE_FILES) -> list[Path]:
    """The largest files under these folders, within a walk bounded on entries. Blocking."""
    seen: list[tuple[int, Path]] = []
    looked = 0
    for root in roots:
        pending = [root]
        while pending and looked < SAMPLE_WALK_LIMIT:
            directory = pending.pop()
            try:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        looked += 1
                        if looked >= SAMPLE_WALK_LIMIT:
                            break
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                pending.append(Path(entry.path))
                            elif entry.is_file(follow_symlinks=False):
                                size = entry.stat(follow_symlinks=False).st_size
                                if size >= SAMPLE_FLOOR_BYTES:
                                    seen.append((size, Path(entry.path)))
                        except OSError:
                            continue
            except OSError:
                continue
    seen.sort(reverse=True)
    return [path for _, path in seen[:wanted]]


def _places(size: int, salt: int) -> list[int]:
    """Where in a file of `size` bytes one reader seeks, moved by `salt` so runs never overlap."""
    span = max(1, size - BYTES_PER_SEEK)
    within = (salt % _SALTS + 0.5) / _SALTS
    places = (
        int(((at + within) % SEEKS_PER_FILE) / SEEKS_PER_FILE * span)
        for at in range(SEEKS_PER_FILE)
    )
    return [place - place % uncached.ALIGN for place in places]


def _seek_and_read(path: Path, *, salt: int, uncached: bool = False) -> int:
    """Read `BYTES_PER_SEEK` at each of a file's places. How many bytes came back."""
    if uncached:
        return _read_uncached(path, salt)
    read = 0
    with open(path, "rb", buffering=0) as handle:
        for place in _places(os.fstat(handle.fileno()).st_size, salt):
            handle.seek(place)
            read += len(handle.read(BYTES_PER_SEEK))
    return read


def _read_uncached(path: Path, salt: int) -> int:
    return uncached.read(path, _places(path.stat().st_size, salt), BYTES_PER_SEEK)


async def _read_level(
    files: Sequence[Path], *, at_once: int, salt: int, uncached: bool = False
) -> StorageLevel:
    """Read `files` with `at_once` readers going at the same time, the way a probe does."""
    pending = list(files)
    total = 0
    seeks = 0

    async def reader() -> None:
        nonlocal total, seeks
        while pending:
            path = pending.pop()
            try:
                # Into a local first: `total += await ...` would lose a reader finishing alongside.
                came_back = await asyncio.to_thread(
                    _seek_and_read, path, salt=salt, uncached=uncached
                )
                total += came_back
                seeks += SEEKS_PER_FILE
            except OSError as error:
                log.info("performance.selftest.storage_read_failed", detail=str(error))

    started = time.monotonic()
    await asyncio.gather(*(reader() for _ in range(at_once)))
    return StorageLevel(
        at_once=at_once, seconds=time.monotonic() - started, bytes_read=total, seeks=seeks
    )


def _kept[T: (Level, StorageLevel)](runs: Sequence[T], *, key: Callable[[T], float]) -> T:
    """The middle run, carrying the slowest and quickest of all of them."""
    figures = [key(run) for run in runs]
    return replace(middle_by(runs, key=key), low=min(figures), high=max(figures))


def _stops(done: Sequence[StorageLevel], sample: int, wider: int | None) -> tuple[bool, str | None]:
    """Whether the storage ladder goes no wider, and what that leaves untried, if anything."""
    last = done[-1]
    if not last.quick_enough:
        return True, (
            f"nothing wider than {last.at_once} at once was tried: one reader waited "
            f"{last.seconds_per_seek:.2f} s for each seek, past the quarter second a person notices."
        )
    if len(done) > 1 and max(map(_mbps, done[:-1])) >= _mbps(last) * NEAR_BEST:
        return True, None
    if wider is None:
        return True, (
            f"nothing wider than {last.at_once} at once was tried, the most Sift reads from one "
            f"storage, and it was still getting quicker."
        )
    if wider * 2 > sample:
        return True, (
            f"nothing wider than {last.at_once} at once was tried: only {sample} files large "
            f"enough to seek into were found."
        )
    return False, None


async def measure_storage(
    one: StorageToMeasure,
    *,
    levels: Sequence[int] = STORAGE_LEVELS,
    read_level: Callable[..., Awaitable[StorageLevel]] | None = None,
    files: Sequence[Path] | None = None,
    repeats: int = REPEATS,
    busy: Callable[[], bool] = others_busy,
    deadline: Deadline | None = None,
) -> StorageCurve:
    """How many files this storage serves at once, never raising: doubling until a level gains
    nothing, a seek passes `SEEK_BOUND_SECONDS`, the sample or `deadline` runs out, then the midpoint."""
    deadline = deadline or Deadline(STORAGE)
    curve = StorageCurve(storage=one.storage, label=one.label, remote=one.remote)
    if not one.remote and not uncached.AVAILABLE:
        return replace(curve, failed=NO_UNCACHED)
    sample = files if files is not None else await asyncio.to_thread(sample_files, list(one.roots))
    if len(sample) < 2:
        return replace(curve, failed="too few large files to seek into")
    run_level = read_level or partial(_read_level, uncached=not one.remote)
    offset = 0
    turn = 0

    async def take(at_once: int) -> StorageLevel:
        nonlocal offset, turn
        runs = []
        for attempt in range(repeats):
            chosen = [sample[(offset + i) % len(sample)] for i in range(at_once * 2)]
            offset += at_once * 2
            runs.append(await run_level(chosen, at_once=at_once, salt=turn * repeats + attempt))
        turn += 1
        level = _kept(runs, key=_mbps)
        log.info("performance.selftest.storage_level", storage=one.storage, **level.as_dict())
        return level

    done: list[StorageLevel] = []
    untried: str | None = None
    stop = False
    while not stop:
        level = await deadline.within(steady(partial(take, levels[len(done)]), _marked, busy))
        if level is None:
            untried = OUT_OF_TIME_UNTRIED
            break
        done.append(level)
        wider = levels[len(done)] if len(done) < len(levels) else None
        stop, untried = _stops(done, len(sample), wider)
    curve = replace(curve, levels=tuple(done), unmeasured=untried)
    between = midpoint(curve.eligible, key=_mbps)
    if between is not None and between * 2 <= len(sample):
        level = await deadline.within(steady(partial(take, between), _marked, busy))
        done.extend([level] if level is not None else [])
        curve = replace(curve, levels=tuple(sorted(done, key=lambda level: level.at_once)))
    return curve


async def _encode_level(
    at_once: int,
    *,
    encode: Callable[..., Awaitable[bool]],
    source: Path,
    workspace: Path,
    settings: Settings,
    readings: _Readings,
    repeats: int,
) -> Level:
    """Run `at_once` encodes together `repeats` times; the middle run, with the spread."""
    runs: list[Level] = []
    for _ in range(repeats):
        readings.start()
        started = time.monotonic()
        finished = await asyncio.gather(
            *(
                encode(source, workspace, index, settings, THREADS_PER_ENCODE)
                for index in range(at_once)
            )
        )
        elapsed = time.monotonic() - started
        lag, wait, behind = readings.since()
        runs.append(
            Level(
                at_once=at_once,
                seconds=elapsed,
                finished=sum(1 for ok in finished if ok),
                worst_lag_seconds=lag,
                worst_wait_seconds=wait,
                fell_behind=behind,
            )
        )
    level = _kept(runs, key=lambda run: run.throughput)
    log.info("performance.selftest.level", runs=len(runs), **level.as_dict())
    return level


def _unheard(_measurement: Measurement) -> None:
    return None


async def _read_storages(
    storages: Sequence[StorageToMeasure],
    measure_one: Callable[..., Awaitable[StorageCurve]],
    repeats: int,
    budget: Budget,
    say: Callable[[tuple[StorageCurve, ...]], None],
) -> tuple[StorageCurve, ...]:
    """Each storage in an equal part of the stage's time left; one it had no time for says so."""
    curves: list[StorageCurve] = []
    reading = budget.stage(STORAGE)
    for index, one in enumerate(storages):
        part = reading.part(len(storages) - index)
        curve = await part.within(
            measure_one(one, repeats=repeats, deadline=part), grace=GRACE_SECONDS
        )
        reading.cut = reading.cut or part.cut
        curves.append(curve or StorageCurve(one.storage, one.label, one.remote, failed=OUT_OF_TIME))
        say(tuple(curves))
    return tuple(curves)


async def measure(
    *,
    workspace: Path,
    settings: Settings,
    cores: int,
    worst_lag: Callable[[], float],
    worst_wait: Callable[[], float],
    levels: Sequence[int] = LEVELS,
    encode: Callable[..., Awaitable[bool]] | None = None,
    report: Callable[[Measurement], None] | None = None,
    storages: Sequence[StorageToMeasure] = (),
    measure_one_storage: Callable[..., Awaitable[StorageCurve]] | None = None,
    repeats: int = REPEATS,
    measure_decoder: Callable[..., Awaitable[Decode | None]] | None = None,
    busy: Callable[[], bool] = others_busy,
    fell_behind: Callable[[], int] = lambda: 0,
    with_midpoint: bool = True,
    budget: Budget | None = None,
) -> Measurement:
    """Run the test and hand back what happened; never raises. `report` is handed each level as it
    lands. The doublings, the midpoint, the decoder, each storage: each stage within `budget`."""
    budget = budget or Budget()
    say = report or _unheard
    encoding = budget.stage(ENCODING)
    try:
        source = await encoding.within(build_clip(workspace, settings))
    except (media.FFmpegError, OSError) as error:
        # No working encoder is not a slow machine: the difference between leaving and lowering.
        log.warning("performance.selftest.no_encoder", error=str(error))
        return Measurement(cores=cores, failed="the video encoder couldn't be run")
    if source is None:
        return Measurement(cores=cores, failed=OUT_OF_TIME)

    take = partial(
        _encode_level,
        encode=encode or _encode_once,
        source=source,
        workspace=workspace,
        settings=settings,
        readings=_Readings(worst_lag, worst_wait, fell_behind),
        repeats=repeats,
    )
    done: list[Level] = []
    for at_once in levels:
        if cores_in_use(at_once) > cores * 2 and done:
            break
        level = await encoding.within(steady(partial(take, at_once), _marked, busy))
        if level is None:
            break
        done.append(level)
        say(Measurement(cores=cores, levels=tuple(done)))
        if not done[-1].responsive:
            break
    between = midpoint([one for one in done if one.responsive and one.finished], key=_per_second)
    if with_midpoint and between is not None and cores_in_use(between) <= cores * 2:
        level = await encoding.within(steady(partial(take, between), _marked, busy))
        done = sorted([*done, *([level] if level else [])], key=lambda one: one.at_once)
    decode = await budget.stage(DECODER).within(
        (measure_decoder or measure_decode)(source, settings, repeats=repeats)
    )
    say(Measurement(cores=cores, levels=tuple(done), decode=decode))
    curves = await _read_storages(
        storages,
        measure_one_storage or measure_storage,
        repeats,
        budget,
        lambda read: say(
            Measurement(cores=cores, levels=tuple(done), storages=read, decode=decode)
        ),
    )
    return Measurement(cores=cores, levels=tuple(done), storages=curves, decode=decode)


def _per_second(level: Level) -> float:
    return level.throughput


@dataclass
class SelfTest:
    """This process's run in flight, or its last one; `SelfTestRunner.recall` puts a kept one back."""

    running: bool = False
    measurement: Measurement | None = None
    recommendations: list[Recommendation] = field(default_factory=list)
    finished_at: float | None = None


#: The one self-test run there may be at a time, on the application so it goes with it.
SELF_TEST: Part[SelfTest] = Part("self_test")
