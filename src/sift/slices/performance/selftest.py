# SPDX-License-Identifier: AGPL-3.0-or-later
"""Working out what this machine can actually do, instead of guessing from its core count.

The core count is only a guess: cores are not equal, a machine may be busy with something else,
and how fast a library is indexed depends on how fast the machine encodes video, which can differ
by a factor of five between machines with the same core count.

What this measures: it encodes the same short clip one at a time and then several at once, and
watches how much total work got done and whether the application stayed responsive. Past the point
where the machine is saturated, another encode makes every encode slower and finishes no more
work; that turning point has to be watched, not derived.

It also reads each network share the library sits on the way probing does (several files at
once, several seeks into each) and takes the width at which the share stops delivering more.
The encoding curve says nothing about a share. See `StorageCurve`.

Not measured: a local disk, the graphics card, a library's own mix of resolutions and codecs, and
the recognition or description models, whose cost per file the ledger records from real runs.

Nothing here writes a setting. It produces recommendations (the setting, its value now, what it
should be and why) that an admin applies with the ordinary controls, and can undo.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from sift.kernel import media
from sift.kernel.config import Settings
from sift.kernel.log import get_logger
from sift.kernel.subprocess import Priority
from sift.kernel.wiring import Part

log = get_logger(__name__)

#: How long each clip the test builds is, in seconds.
#:
#: Shorter clips make each level mostly process start-up, and the differences between levels are
#: noise. Twenty seconds gives each encode a few seconds of real work and keeps the whole run near
#: half a minute on a workstation; a small machine takes longer, and its answer matters most.
CLIP_SECONDS = 20

#: The size the clips are built at. 720p because it is the commonest thing in a library and
#: because measuring at 4K on a slow machine would make the test itself the slow part.
CLIP_WIDTH = 1280
CLIP_HEIGHT = 720
CLIP_RATE = 30

#: A run at each of these many-at-once levels, in order. Stops early once adding more stops helping,
#: so a small machine does not sit through the wide levels it was never going to reach.
LEVELS = (1, 2, 4, 8, 16)

#: How much better a level has to be than the one before it to count as worth having. Below this the
#: extra parallelism is buying noise, and the honest answer is the smaller number, which also
#: leaves the machine with something left for whoever is using it.
WORTH_HAVING = 1.15

#: Above this, the application was not keeping up while the test ran, and the level that produced it
#: is not one to recommend however much work it finished. The same quarter second the running server
#: warns at and the same one the Performance screen calls noticeable.
TOO_BUSY_SECONDS = 0.25

#: How many times each measurement is taken, keeping the middle one.
#:
#: One run of a level is a minute of one machine, and a minute is exactly long enough for an update
#: check, a browser tab or the share's other user to land in it, and set every number on the
#: Performance screen from that noise. Three runs, the middle kept, rather than an average: an
#: average lets one bad run pull the answer a third of the way towards it, and the middle run does
#: not care how bad the bad one was. The test takes three times as long, which is the price of a
#: number that can be relied on; the machines it matters most on are the ones with the least to
#: spare, and they are also the ones where one noisy run would have misled furthest.
REPEATS = 3


def middle(values: Sequence[float]) -> float:
    """The median of a few readings: sorted, the one in the middle, the lower of two when even."""
    ordered = sorted(values)
    return ordered[(len(ordered) - 1) // 2]


def middle_by[T](runs: Sequence[T], *, key: Callable[[T], float]) -> T:
    """The run in the middle when the runs are put in order by `key`. The whole run, not a
    middle made from pieces of several: its other readings belong with the number that chose it."""
    ordered = sorted(runs, key=key)
    return ordered[(len(ordered) - 1) // 2]


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

    @property
    def throughput(self) -> float:
        """Encodes per second. The whole point of the level, and what levels are compared on."""
        if self.seconds <= 0:
            return 0.0
        return self.finished / self.seconds

    @property
    def responsive(self) -> bool:
        """Whether the application stayed usable at this level."""
        return (
            self.worst_lag_seconds < TOO_BUSY_SECONDS and self.worst_wait_seconds < TOO_BUSY_SECONDS
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "at_once": self.at_once,
            "seconds": round(self.seconds, 2),
            "finished": self.finished,
            "per_second": round(self.throughput, 3),
            "responsive": self.responsive,
        }


@dataclass(frozen=True)
class StorageLevel:
    """One run against one storage: how many files were read at once, and what came back."""

    at_once: int
    seconds: float
    bytes_read: int
    seeks: int = 0
    """How many seeks the readers made between them, so the cost of one can be read off."""

    @property
    def megabytes_per_second(self) -> float:
        if self.seconds <= 0:
            return 0.0
        return self.bytes_read / self.seconds / 1_000_000

    @property
    def seconds_per_seek(self) -> float:
        """What one reader paid per seek. The readers ran side by side, so the run's seconds are
        one reader's, and one reader made a `1/at_once` share of the seeks."""
        if self.seeks <= 0 or self.at_once <= 0:
            return 0.0
        return self.seconds * self.at_once / self.seeks

    def as_dict(self) -> dict[str, object]:
        return {
            "at_once": self.at_once,
            "seconds": round(self.seconds, 2),
            "megabytes": round(self.bytes_read / 1_000_000, 1),
            "megabytes_per_second": round(self.megabytes_per_second, 1),
            "seconds_per_seek": round(self.seconds_per_seek, 4),
        }


@dataclass(frozen=True)
class StorageCurve:
    """How one storage behaved as more files were read from it at once.

    The encoding curve cannot answer this: many concurrent seeking readers turn a network share
    into random I/O, and a share that serves two readers well can deliver half as much to twelve.
    So each network storage is read the way probing reads it, seeking into several files at once,
    and the point where more at once stops delivering more is how many readers it can serve.
    """

    storage: str
    """The storage as the operating system names it: `\\\\server\\share\\`."""
    label: str
    """The library folders on it, for a person."""
    remote: bool
    levels: tuple[StorageLevel, ...] = ()
    failed: str | None = None
    """Why nothing was measured here, when nothing was (too few large files to seek into, most
    often), so a storage that could not be measured does not read as a slow one."""

    @property
    def best(self) -> StorageLevel | None:
        """The widest level still worth having, walked the way `Measurement.best` walks.

        Stopped at the first level that is not an improvement on the one before, rather than at
        the maximum: a share that has begun to collapse can still show a high reading on a level
        that happened to catch its cache, and the level after the knee is the one that made every
        job slow.
        """
        if not self.levels:
            return None
        best = self.levels[0]
        for level in self.levels[1:]:
            if level.megabytes_per_second < best.megabytes_per_second * WORTH_HAVING:
                break
            best = level
        return best

    @property
    def collapsed(self) -> bool:
        """Whether reading WIDER actually delivered less, rather than merely not enough more.

        Two shapes stop the walk in `best` and only one is a share falling over. A curve such as
        22, 22, 23, 26 MB/s at one, two, four and eight readers did not collapse: no step earned the
        margin a step has to earn (`WORTH_HAVING`), so one reader is the answer for another reason.

        The level after the chosen one decides it, because that is the level that stopped the walk.
        """
        best = self.best
        if best is None:
            return False
        after = [one for one in self.levels if one.at_once > best.at_once]
        return bool(after) and after[0].megabytes_per_second < best.megabytes_per_second


@dataclass(frozen=True)
class Decode:
    """How fast this machine turns video into frames, measured with the test clip.

    The numbers the Build's reader is shaped by. A file's pictures can be taken by seeking to each
    wanted moment (a process, a seek and a keyframe decode each) or by decoding the file once
    from end to end and keeping the frames as they pass. Which is cheaper depends on how many
    moments are wanted, how long the file is, and two rates of THIS machine that nothing else
    measures: how many frames a second it decodes, and what one seek costs it. Both at the clip's
    720p; a file at another size is scaled by its pixels.
    """

    frames_per_second: float
    """Frames of 720p decoded per second by one task with a background job's thread share."""
    seek_seconds: float
    """What one more moment costs when taken by seeking, within a process that seeks many: the
    seek and the decode from the keyframe before it, on the local disk. A share's seek adds the
    share's own cost, which the storage curve measures."""

    def as_dict(self) -> dict[str, object]:
        return {
            "frames_per_second": round(self.frames_per_second, 1),
            "seek_seconds": round(self.seek_seconds, 4),
        }


@dataclass(frozen=True)
class Measurement:
    """Everything the test found out. Passed to `recommend`, which decides what it means."""

    cores: int
    levels: tuple[Level, ...] = ()
    failed: str | None = None
    """Why nothing was measured, when nothing was, so a test that could not run does not read as
    a slow machine."""
    storages: tuple[StorageCurve, ...] = ()
    """Every storage the library sits on, measured for how many readers it can serve at once.
    Empty when the library is local: a local disk does not collapse under several readers."""
    decode: Decode | None = None
    """How fast this machine decodes and seeks, or None where the decoder could not be run."""

    @property
    def best(self) -> Level | None:
        """The widest level still worth having: more work than the one before it, and responsive.

        Walked in order and stopped at the first level that is not an improvement, rather than
        taking whichever scored highest. Throughput wobbles, and picking the maximum of a wobbly
        curve reliably picks the peak of the noise, which on a saturated machine is the level
        that made everything else unusable.
        """
        usable = [level for level in self.levels if level.responsive and level.finished]
        if not usable:
            return None
        best = usable[0]
        for level in usable[1:]:
            if level.throughput < best.throughput * WORTH_HAVING:
                break
            best = level
        return best

    @property
    def at_ceiling(self) -> bool:
        """Whether the level chosen is the widest one that was TRIED.

        A peak or a floor. The ladder stops at one encode per thread, so a machine that keeps
        improving all the way up ends on the widest rung offered, and nothing above it was run.
        "More than that finished no more work" would then be a claim about levels never measured,
        so the words on the screen follow this.
        """
        best = self.best
        return best is not None and bool(self.levels) and best.at_once == self.levels[-1].at_once


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


#: What the recommendations may touch. Named here rather than assembled inline so it is one list a
#: reader can check against the screen, and so nothing can recommend a key that is not a setting.
WORKER_COUNT_KEY = "performance.worker_count"
GENERATION_LIMIT_KEY = "performance.generation_limit"
SHARE_READS_KEY = "performance.share_reads_at_once"

# Recognition's share of the machine is not recommended. This test measures video encoding, and a
# face pass runs a model over a picture on a different hardware path; a number derived from the
# encoding curve would look as trustworthy as the real readings without being one. Measuring it
# properly needs the model present, which a default install does not have.


def recommend(measurement: Measurement, *, current: dict[str, int]) -> list[Recommendation]:
    """Turn what was measured into what to change. Pure, so a test can check it.

    Two settings, and both come from the measurement rather than from the core count:

    - **How many jobs run at once**: above the measured level, because a job is not always
      encoding, and never above the cores.
    - **How many previews are built at once**: the measured level itself. Encoding is what was
      measured and building previews is the encoding, so there is no derivation in between.

    Two settings and not three: see the note above the keys about why recognition's share is not
    recommended from an encoding measurement.

    A measurement that failed recommends nothing: half an answer from a test that did not run
    would look like a whole one.
    """
    best = measurement.best
    if measurement.failed is not None or best is None:
        return []

    measured = best.at_once
    threads = measurement.cores

    # A margin over the measured level, not a multiple of it: a job is not always encoding, so a
    # modest margin is right, but a multiple would recommend a level the test never ran. One
    # processor is left over, as the automatic answer leaves one.
    jobs = min(max(1, threads - 1), measured + max(2, measured // 2))

    # The preview cap never exceeds the job count. Building a preview is a job and the cap is a
    # per-type limit inside the worker pool (`limits[PREVIEW]` in `sift/wiring/workers.py`), so a cap above the
    # worker count could never take effect while still reading as a measurement.
    generation = min(measured, jobs)

    if measurement.at_ceiling:
        # A floor, not a peak. See `Measurement.at_ceiling`.
        found = (
            f"Measured: this device was still finishing more work at {measured} encodes at once, "
            f"which is as wide as the test goes \u2014 one for every thread it reports. Nothing wider "
            f"was tried, so {measured} is the most this run can show rather than the point where "
            f"it stopped helping."
        )
    else:
        found = (
            f"Measured: this device encoded {measured} clips at once without falling behind, and "
            f"running more than that made every encode slower and finished no more work."
        )

    kept_back = f"one of the {threads} threads this device reports is kept for everything that is not a task"
    if jobs > measured:
        margin = (
            f"Tasks are not all encoding, so this is set a little above that \u2014 that margin is a "
            f"judgment rather than something the test measured \u2014 and {kept_back}."
        )
    else:
        margin = f"This is lower than that on purpose: {kept_back}."

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


def recommend_share_reads(
    storages: Sequence[StorageCurve], *, current: dict[str, int]
) -> Recommendation | None:
    """How many files to read at once from a network share, from the shares that were measured.

    Only the storages that were measured and are on another machine, and the smallest knee among
    them: one setting governs every share, and a number safe for the weakest is safe for all. A
    storage that could not be measured recommends nothing (`StorageCurve.failed`); if none could,
    there is no recommendation.
    """
    measured = [one for one in storages if one.remote and one.best is not None]
    if not measured:
        return None
    knee = min(measured, key=lambda one: one.best.at_once if one.best else 0)
    best = knee.best
    if best is None:  # pragma: no cover (cannot happen past the filter above; said, not assumed)
        return None
    readings = ", ".join(
        f"{level.at_once} at once {level.megabytes_per_second:.0f} MB/s" for level in knee.levels
    )
    # "more than 1 file", never "more than 1 files". The readings beside it are a person's first
    # reason to trust or distrust the number, and a sentence that cannot count reads as neither.
    files = "file" if best.at_once == 1 else "files"
    if len(knee.levels) > 1 and best.at_once == knee.levels[-1].at_once:
        found = (
            f"Measured on {knee.label}: reading {best.at_once} {files} at once was still the "
            f"fastest level tried ({readings}). Nothing wider was tried."
        )
    elif knee.collapsed:
        found = (
            f"Measured on {knee.label}: reading more than {best.at_once} {files} at once delivered "
            f"less, not more ({readings})."
        )
    else:
        # A curve that climbs a little and is still not worth it: no step gained the margin a step
        # has to gain, so the extra readers bought run-to-run noise, not a collapse.
        found = (
            f"Measured on {knee.label}: reading more than {best.at_once} {files} at once delivered "
            f"no more than the noise between runs ({readings})."
        )
    if len(measured) > 1:
        found += f" The lowest of {len(measured)} shares, so every share is kept safe."
    return Recommendation(
        key=SHARE_READS_KEY,
        label="How many files are read at the same time from a network share",
        current=current.get(SHARE_READS_KEY, 0),
        suggested=best.at_once,
        reason=found,
    )


# --- taking the measurement ---------------------------------------------------------------------


@dataclass
class _Readings:
    """The two live readings, sampled around a level rather than for the whole run.

    Read as deltas so a machine that was already struggling before the test began does not have
    every level blamed for it: what each level is judged on is what IT did.
    """

    worst_lag: Callable[[], float]
    worst_wait: Callable[[], float]
    lag_before: float = 0.0
    wait_before: float = 0.0

    def start(self) -> None:
        self.lag_before = self.worst_lag()
        self.wait_before = self.worst_wait()

    def since(self) -> tuple[float, float]:
        return (
            max(0.0, self.worst_lag() - self.lag_before),
            max(0.0, self.worst_wait() - self.wait_before),
        )


async def build_clip(into: Path, settings: Settings) -> Path:
    """Write the one clip every level encodes, and hand back where it is.

    Built rather than shipped: a file small enough to put in the repository is one the encoder
    finishes with before anything could be measured, and a library's files are not in the
    repository either.
    """
    target = into / "self-test-source.mp4"
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


#: Threads one encode in the test is allowed. **Fixed, and that is the whole design of the
#: measurement.**
#:
#: What is being chosen is how many encodes to run at once, so that has to be the only thing that
#: varies. Either of the obvious alternatives produces a number that looks authoritative and is not:
#:
#: - **No cap at all.** ffmpeg helps itself to roughly two-thirds of the machine, so one encode
#:   already fills a large machine, running four finishes barely more than one, and the curve
#:   flattens at once into a recommendation far below what the machine can do.
#: - **A cap of cores over the level.** That holds the whole machine busy at every level, so total
#:   throughput is near flat by construction and the only differences left are noise, which
#:   recommends 1.
#:
#: Fixed instead. Level N uses about 2N cores, so throughput climbs while the machine has room and
#: stops when it runs out, and where it stops is the number being looked for. Two rather than one
#: because a single-threaded encode is not what a job gets in practice, and not more because a large
#: share per encode puts the ceiling below the smallest level worth testing.
THREADS_PER_ENCODE = 2


def cores_in_use(at_once: int) -> int:
    """Roughly how much of the machine a level occupies. What makes the curve mean anything."""
    return at_once * THREADS_PER_ENCODE


def planned_levels(cores: int, levels: Sequence[int] = LEVELS) -> tuple[int, ...]:
    """The rungs THIS machine can reach, so a screen can say how far through a run is.

    An upper bound, and said as one on the screen: a run stops early when a level stops helping or
    when the application stops keeping up. It is never a total that moves.

    The first rung is always reachable, however small the machine: `measure` only breaks out once
    something has been done, so a one-thread box still runs the level of one.
    """
    reachable = tuple(one for one in levels if cores_in_use(one) <= cores * 2)
    return reachable or (levels[0],)


async def _encode_once(
    source: Path, into: Path, index: int, settings: Settings, threads: int = 0
) -> bool:
    """One unit of the work being measured: re-encode the clip. True when it finished.

    `threads` caps this one encode. The same flag twice, because ffmpeg has more than one thread
    pool and one flag does not reach them all: before the input it caps decoding and
    the filter graph, and in the output position it caps the encoder, which is the one doing the
    work here. Capping only the first would leave the encoder taking the machine anyway.
    """
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
                # Slower than the preset a preview is really built with, deliberately. What is being
                # measured is how the machine behaves when several encodes compete, and a preset fast
                # enough to finish before they overlap measures nothing.
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


# --- measuring the decoder ------------------------------------------------------------------------
#
# The encoding curve says how many previews to build at once. It says nothing about the choice a
# Build makes for every video: seek to the moments it wants, or decode the file once and keep the
# frames as they pass. That choice is a sum of two rates of this machine (frames decoded a
# second, and what one seek costs) against the share's own numbers, and neither rate can be
# derived from the core count any more than the encoding curve could.

#: Where the seek half of the measurement takes its moments from, as fractions of the clip. Spread
#: rather than bunched, the way a fingerprint's or a scrubber strip's moments are, and enough of
#: them that the one launch they share is a small part of the run.
SEEK_MOMENTS = tuple((index + 0.5) / 10 for index in range(10))


async def _decode_once(source: Path, settings: Settings) -> float:
    """Decode the whole clip and throw the frames away. Seconds it took.

    With the thread share a background job's ffmpeg gets (`background_flags`) rather than the
    whole machine, because the number is for one task of a Build, which runs beside others.
    """
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
    """Take one frame at each of `ats` by seeking, from ONE process. Seconds for the run.

    One process with a seeked input per moment (`-ss T1 -i clip -ss T2 -i clip ...`), because
    that is the shape everything in Sift seeks with (`media.moments_to_files`), and a launch per
    seek would charge every moment for the process rather than for the seek. What is wanted is
    what one more moment costs, and that is this run's seconds over its moments.
    """
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
    """The two rates the Build's reader is shaped by, each the middle of `repeats` runs.

    None when the decoder could not run, which is an answer: a machine that cannot decode is not
    a slow one, and a reader with no rate to go on seeks, the shape that needs none.
    """
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


# --- measuring the storage ------------------------------------------------------------------------
#
# A probe reads a file by seeking into it: fifty-five places in a video, a megabyte or so decoded
# at each. A share that serves that pattern well for two readers can collapse for twelve, and the
# collapse is invisible to the encoding test above, which never touches the library. So each
# storage the library sits on is read the same way (several files at once, several seeks into
# each) and the number of readers it can serve at once is read off the curve.

#: How many places in a file one reader seeks to, and how much it reads at each. A megabyte is
#: about what a decoder pulls to reconstruct one frame from the keyframe before it.
SEEKS_PER_FILE = 6
BYTES_PER_SEEK = 1 << 20

#: The smallest file worth seeking into. Below this the six reads overlap and measure the cache.
SAMPLE_FLOOR_BYTES = 16 << 20

#: How many entries a walk of a library folder looks at to find files to sample, and how many it
#: keeps. A walk over a share is a round trip per folder, and a benchmark that took minutes to
#: choose its files would be the slow thing it was measuring.
SAMPLE_WALK_LIMIT = 4000
SAMPLE_FILES = 40

#: The readers-at-once levels a storage is tried at. The first rung is the measured safe value
#: for a home share and the last is past anything a share has been seen to serve.
STORAGE_LEVELS = (1, 2, 4, 8)

#: How many distinct salts one run hands out: one per level per repeat. `_seek_and_read` spaces
#: its places by this, so no run of the walk re-reads what an earlier one left in the operating
#: system's cache and measures the memory in front of the share instead of the share.
_SALTS = len(STORAGE_LEVELS) * REPEATS


@dataclass(frozen=True)
class StorageToMeasure:
    """One storage and the library folders on it, as the composition root hands them in."""

    storage: str
    label: str
    remote: bool
    roots: tuple[Path, ...]


def sample_files(roots: Sequence[Path], *, wanted: int = SAMPLE_FILES) -> list[Path]:
    """Large files under these folders, spread across them, found within a bounded walk.

    Blocking, for a thread. Bounded on entries rather than on files so a folder of a million
    small files does not walk for ever finding none big enough; the biggest of what was seen are
    kept so the sample seeks into real video rather than into thumbnails somebody copied in.
    """
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


def _seek_and_read(path: Path, *, salt: int) -> int:
    """Read `SEEKS_PER_FILE` pieces of a file at places spread across it. How many bytes came back.

    `salt` moves the places by run, so a run never re-reads what the run before it left in the
    operating system's cache and measures the share rather than the memory in front of it. Each
    salt lands a distinct fraction of the way into the seek's span (see `_SALTS`), so the
    places of two runs are as far apart as the file allows.
    """
    read = 0
    with open(path, "rb", buffering=0) as handle:
        size = os.fstat(handle.fileno()).st_size
        span = max(1, size - BYTES_PER_SEEK)
        within = (salt % _SALTS + 0.5) / _SALTS
        for index in range(SEEKS_PER_FILE):
            place = int(((index + within) % SEEKS_PER_FILE) / SEEKS_PER_FILE * span)
            handle.seek(place)
            read += len(handle.read(BYTES_PER_SEEK))
    return read


async def _read_level(files: Sequence[Path], *, at_once: int, salt: int) -> StorageLevel:
    """Read `files` with `at_once` readers going at the same time, the way a probe does."""
    pending = list(files)
    total = 0
    seeks = 0

    async def reader() -> None:
        nonlocal total, seeks
        while pending:
            path = pending.pop()
            try:
                # Read into a local first: `total += await ...` takes the old total before the
                # await and writes it back after, so two readers finishing together would keep
                # one file's bytes and lose the other's.
                came_back = await asyncio.to_thread(_seek_and_read, path, salt=salt)
                total += came_back
                seeks += SEEKS_PER_FILE
            except OSError as error:
                log.info("performance.selftest.storage_read_failed", detail=str(error))

    started = time.monotonic()
    await asyncio.gather(*(reader() for _ in range(at_once)))
    return StorageLevel(
        at_once=at_once, seconds=time.monotonic() - started, bytes_read=total, seeks=seeks
    )


async def measure_storage(
    one: StorageToMeasure,
    *,
    levels: Sequence[int] = STORAGE_LEVELS,
    read_level: Callable[..., Awaitable[StorageLevel]] | None = None,
    files: Sequence[Path] | None = None,
    repeats: int = REPEATS,
) -> StorageCurve:
    """How many files this storage can serve at once. Never raises; a failure is part of the answer.

    Each run reads its own files, cycling through the sample, so two runs never read the same
    bytes; a run reads twice its readers' worth of files so every reader is busy for the whole of
    it. Each level is run `repeats` times and the middle run is what the level records. See
    `REPEATS`. The walk stops as soon as a level delivers less than the one before: that is the
    collapse being looked for, and running wider would only make the share slower for longer.
    """
    sample = files if files is not None else await asyncio.to_thread(sample_files, list(one.roots))
    if len(sample) < 2:
        return StorageCurve(
            storage=one.storage,
            label=one.label,
            remote=one.remote,
            failed="too few large files to seek into",
        )
    run_level = read_level or _read_level
    done: list[StorageLevel] = []
    offset = 0
    for index, at_once in enumerate(levels):
        if at_once > len(sample):
            break
        runs: list[StorageLevel] = []
        for attempt in range(repeats):
            chosen = [sample[(offset + i) % len(sample)] for i in range(at_once * 2)]
            offset += at_once * 2
            runs.append(await run_level(chosen, at_once=at_once, salt=index * repeats + attempt))
        level = middle_by(runs, key=lambda run: run.megabytes_per_second)
        done.append(level)
        log.info("performance.selftest.storage_level", storage=one.storage, **level.as_dict())
        if len(done) > 1 and level.megabytes_per_second < done[-2].megabytes_per_second:
            break
    return StorageCurve(storage=one.storage, label=one.label, remote=one.remote, levels=tuple(done))


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
) -> Measurement:
    """Run the test and hand back what happened. Never raises: a failure is part of the answer.

    `encode` is the unit of work, injectable so a test can measure something instant and
    deterministic instead of really running a video encoder: what is being checked in that case is
    the shape of the walk between levels, which is where the decisions are.

    `report` is handed the measurement so far after each level, so the screen showing a run in
    flight can say what has actually happened rather than a sentence that never changes. It is the
    same object this returns at the end, one rung short, not a second progress shape to keep in
    step with the first.

    Each level is run `repeats` times and the middle run is what the level records. See
    `REPEATS`. Levels above the core count are skipped: encoding more at once than there are
    cores has never once been the right answer, and running them only makes the test longer on
    the machines least able to afford it.
    """
    run_one = encode or _encode_once
    try:
        source = await build_clip(workspace, settings)
    except (media.FFmpegError, OSError) as error:
        # A machine with no working encoder is a real thing, and it is not a slow machine. Saying
        # so is the whole difference between "leave the settings alone" and "turn everything down".
        log.warning("performance.selftest.no_encoder", error=str(error))
        return Measurement(cores=cores, failed="the video encoder couldn't be run")

    readings = _Readings(worst_lag=worst_lag, worst_wait=worst_wait)
    done: list[Level] = []
    for at_once in levels:
        # Past roughly twice the machine there is nothing left to learn: the curve has already
        # flattened and every further level is slower for the same answer. This is also what keeps
        # the run short on a small machine, which is the one least able to spare the time.
        if cores_in_use(at_once) > cores * 2 and done:
            break
        runs: list[Level] = []
        for _ in range(repeats):
            readings.start()
            started = time.monotonic()
            finished = await asyncio.gather(
                *(
                    run_one(source, workspace, index, settings, THREADS_PER_ENCODE)
                    for index in range(at_once)
                )
            )
            elapsed = time.monotonic() - started
            lag, wait = readings.since()
            runs.append(
                Level(
                    at_once=at_once,
                    seconds=elapsed,
                    finished=sum(1 for ok in finished if ok),
                    worst_lag_seconds=lag,
                    worst_wait_seconds=wait,
                )
            )
        level = middle_by(runs, key=lambda run: run.throughput)
        done.append(level)
        log.info("performance.selftest.level", runs=len(runs), **level.as_dict())
        if report is not None:
            report(Measurement(cores=cores, levels=tuple(done)))
        if not level.responsive:
            # Past here the machine is already struggling; wider levels would only struggle more and
            # would keep somebody waiting to be told something already known.
            break
    # Then the decoder, on the same clip, once the encodes have let go of the machine.
    decode = await (measure_decoder or measure_decode)(source, settings, repeats=repeats)
    if report is not None:
        report(Measurement(cores=cores, levels=tuple(done), decode=decode))
    # Then each storage the library sits on, after the encoder rather than beside it, so neither
    # measurement is taken while the other has the machine. Only the ones on another machine: a
    # local disk was never the thing that collapsed, and reading it would only lengthen the test.
    curves: list[StorageCurve] = []
    for one in storages:
        if not one.remote:
            continue
        curve = await (measure_one_storage or measure_storage)(one, repeats=repeats)
        curves.append(curve)
        if report is not None:
            report(
                Measurement(cores=cores, levels=tuple(done), storages=tuple(curves), decode=decode)
            )
    return Measurement(cores=cores, levels=tuple(done), storages=tuple(curves), decode=decode)


@dataclass
class SelfTest:
    """The one run there may be at a time, and what it found.

    The finished reading is kept per hardware profile beside the rates (`rates` v2) and put back
    by `SelfTestRunner.recall`. What is held here is this process's view: the run in flight, or
    the last one.
    """

    running: bool = False
    measurement: Measurement | None = None
    recommendations: list[Recommendation] = field(default_factory=list)
    finished_at: float | None = None


#: The one self-test run there may be at a time. On the application rather than in the module, so
#: it goes away with the application and a second one does not inherit the first one's answer.
#: Held by the runner (`runner.SelfTestRunner`), which is what the screen and the Build share.
SELF_TEST: Part[SelfTest] = Part("self_test")
