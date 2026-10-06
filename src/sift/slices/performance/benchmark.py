# SPDX-License-Identifier: AGPL-3.0-or-later
"""The benchmark job: every run of the self-test, and what an automatic one sets with an Undo.

Every run pauses the other work (`drain`). A first folder waits only for the part its scan needs;
the rest runs once nothing waits."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Iterable, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, Final, Literal, NoReturn, Protocol

from sift.kernel import attention
from sift.kernel.access import Viewer
from sift.kernel.jobs import (
    MAX_PAGE_SIZE,
    WAITED_ON_PRIORITY,
    Job,
    JobContext,
    JobFailedPermanently,
    JobQueue,
    JobState,
    job_name,
)
from sift.kernel.jobs.quiet_hours import AT_QUIET
from sift.kernel.log import get_logger
from sift.kernel.settings_registry import get_registered
from sift.kernel.vocabulary import VIA_BENCHMARK
from sift.kernel.wiring import Part
from sift.kernel.workbench import DOER, Preview, Recorded, Worded
from sift.slices.performance import selftest
from sift.slices.performance.runner import FULL_RUN_ONLY, SelfTestRunner
from sift.slices.performance.selftest import StorageCurve, StorageToMeasure

log = get_logger(__name__)

#: The kind, as the queue knows it.
BENCHMARK: Final = "performance_benchmark"

#: What the queue calls it on Activity: the Performance screen's own heading for a run.
BENCHMARK_NAME: Final = "Benchmarking this device"

#: The name its receipts are taken back under (`kernel.workbench.Reverser`).
RECEIPTS: Final = "benchmark"

# --- the words: the job's note on Activity and every admin window's toasts read them from here ----

#: What a whole run does, and what a run of the storage a new folder is on does.
WHOLE: Final = "Benchmarking this device so Sift can make the best use of it."
ONE_STORAGE: Final = (
    "Measuring the storage your new folder is on to find the fastest way to read it."
)

#: Until a run of its kind here: a whole run keeps to `budget.WHOLE_SECONDS`; one storage, 1 to 28 s.
RUNNING: Final = f"{WHOLE} It takes up to 5 minutes."
MEASURING_STORAGE: Final = f"{ONE_STORAGE} Under a minute."

#: A first folder's run measures only what its scan needs, and its files arrive after about this.
FIRST_PART: Final = "first_part"
FIRST: Final = "Benchmarking this device and the storage your new folder is on, so its files can start arriving."
MEASURING_FIRST: Final = f"{FIRST} About a minute."
WHOLE_LATER: Final = (
    "The full benchmark runs by itself once Sift has nothing else to do and nobody's using this "
    "device."
)

#: The payload key of that full run, which Sift asks for when nothing waits.
QUIET: Final = "quiet"

#: No input and no clip read this long: past a screen's usual sleep.
AWAY_SECONDS: Final = 600.0

#: How often Sift looks for that moment, and for a reason to stop the run it started.
LOOK_SECONDS: Final = 5.0

FOLDER_ADDED: Final = "Sift stopped the full benchmark so your new folder's files can arrive."
ASKED_FOR: Final = "Sift stopped the full benchmark so the task you asked for can run."
IN_USE: Final = "Sift stopped the full benchmark because this device is in use."
PLAYING: Final = "Sift stopped the full benchmark because a video is playing in Sift."
AGAIN: Final = (
    "It runs again by itself once Sift has nothing else to do and nobody's using this device."
)

#: The payload key of a run of only the storage its folder (`root_id`) is on, and its kind.
STORAGE: Final = "storage"
WHOLE_RUN: Final = "whole"

#: The payload key of a run that suggests and sets nothing.
PRESSED: Final = "pressed"

#: How long a run waits for the work it paused to stop before it measures anyway.
DRAIN_SECONDS: Final = 20.0

#: While it waits or runs, of the folder whose add queued it: none of it is read until it settles.
HELD: Final = "Your folder is added, and its files appear once this device has been benchmarked."

#: Finished, and every value it would choose was already chosen.
AGREED: Final = "The benchmark found your settings already suit this device."

#: A run of one storage that measured it and had nothing to suggest, as on a local disk.
STORAGE_KEPT: Final = "Sift measured the storage your new folder is on and kept what it found."

#: Finished on a device measured before it was claimed, by a pressed run.
ALREADY: Final = "This device was benchmarked already, so there was nothing to run."

#: Why a run found nothing to go on: no level finished in time.
TOO_BUSY: Final = "this device was too busy to measure"

#: Why a run that raised did not finish.
WENT_WRONG: Final = "something went wrong while it ran"

#: Why a run taken off the queue before it ended did not finish.
STOPPED: Final = "it was stopped"

#: Why a run of one storage found nothing to measure.
GONE: Final = "no library folder is on that storage any more"

#: The end of a run that suggests.
SUGGESTED: Final = "The benchmark finished. What it suggests is on Settings > Performance."


def length_said(seconds: float) -> str:
    if seconds < 60:
        return "under a minute"
    minutes = round(seconds / 60)
    return "about a minute" if minutes == 1 else f"about {minutes} minutes"


def running_said(*, storage: bool, last: float | None) -> str:
    """The note while a run waits and runs, with how long the last run of its kind here took."""
    if last is None:
        return MEASURING_STORAGE if storage else RUNNING
    return f"{ONE_STORAGE if storage else WHOLE} The last one here took {length_said(last)}."


def set_sentence(count: int) -> str:
    """The end of a run that set something: how many settings, from what."""
    return f"Sift set {count} {'setting' if count == 1 else 'settings'} from the benchmark."


def failed_sentence(why: str) -> str:
    """The end of a run that could not finish, with why, and where it can be run by hand."""
    return (
        f"Sift couldn't benchmark this device because {why}. "
        "You can run it from Settings > Performance."
    )


def value_said(value: int) -> str:
    """A number of things at once as the Performance screen says it: 0 is automatic."""
    return "automatic" if value == 0 else str(value)


def receipt_title(changes: Sequence[selftest.Recommendation]) -> str:
    """The History line: each setting, the value Sift set and the value it had."""
    parts = [
        f"{one.label[:1].lower()}{one.label[1:]} to {value_said(one.suggested)} "
        f"(it was {value_said(one.current)})"
        for one in changes
    ]
    listed = parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])} and {parts[-1]}"
    return f"Sift set {listed} from the benchmark of this device"


RECEIPT_DETAIL: Final = (
    "Sift ran the benchmark by itself when the first library folder was added. Undo puts back "
    "the values each setting had before, where nobody has changed it since."
)

QUIET_RECEIPT_DETAIL: Final = (
    "Sift ran the full benchmark by itself the first time it had nothing else to do. Undo puts "
    "back the values each setting had before, where nobody has changed it since."
)

STORAGE_RECEIPT_DETAIL: Final = (
    "Sift measured the storage a new library folder is on by itself when the folder was added. "
    "Undo puts back the value the setting had before, where nobody has changed it since."
)


def tasks_said(count: int) -> str:
    return f"{count} {'task' if count == 1 else 'tasks'}"


# --- what the screens read -----------------------------------------------------------------------

State = Literal["waiting", "running", "set", "agreed", "already", "failed", "gave_way"]


@dataclass(frozen=True)
class Changed:
    """One setting the run set: its key, the words the screen calls it, and the two values."""

    key: str
    label: str
    before: int
    after: int


@dataclass(frozen=True)
class AutomaticRun:
    """The run Sift started by itself, as the toasts read it; held in this process only."""

    job_id: str
    state: State
    said: str
    changes: tuple[Changed, ...] = ()
    receipt_id: str | None = None
    holds: bool = True
    """Whether a folder's files wait for it; the full run Sift asks for when nothing waits holds none."""

    @property
    def going(self) -> bool:
        return self.state in ("waiting", "running")


@dataclass
class FirstBenchmark:
    """The one automatic run there may be, for the route the toasts read."""

    run: AutomaticRun | None = None

    def now(self, run: AutomaticRun) -> None:
        self.run = run


#: The automatic run, on the application: the trigger writes it, the job moves it, the route reads it.
FIRST_BENCHMARK: Part[FirstBenchmark] = Part("first_benchmark")

#: What asks for the full run, on the application, for the boot's loop (`WhenQuiet.keep_looking`).
WHEN_QUIET: Part[WhenQuiet] = Part("when_quiet")


# --- the trigger ---------------------------------------------------------------------------------


class FirstFolder:
    """A folder added: the first part on a device never measured, or a run of its new storage."""

    def __init__(
        self,
        *,
        runner: SelfTestRunner,
        queue: JobQueue,
        roots: Callable[[], Awaitable[int]],
        first: FirstBenchmark,
        storage: Callable[[str], Awaitable[StorageToMeasure | None]],
        give_way: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._runner = runner
        self._queue = queue
        self._roots = roots
        self._first = first
        self._storage = storage
        self._give_way = give_way

    async def __call__(self, root_id: str, requested_by: str | None, scan: bool) -> bool:
        """Whether the benchmark was queued, so the folder's scan is this reaction's to queue."""
        if requested_by is None:
            return False
        if self._give_way is not None:
            await self._give_way()
        if (await self._queue.unfinished_by_type()).get(BENCHMARK, 0) > 0:
            return False
        payload: dict[str, Any] = {"root_id": root_id, "scan": scan}
        said = MEASURING_FIRST
        rates = await self._runner.rates()
        if rates is None:
            if await self._roots() != 1:
                return False
            payload[FIRST_PART] = True
        else:
            where = await self._storage(root_id)
            if where is None or where.storage in rates.storages:
                return False
            payload[STORAGE] = True
            said = running_said(storage=True, last=await self._runner.lasted(STORAGE))
        job_id = await self._queue.enqueue(
            BENCHMARK, payload, requested_by=requested_by, max_attempts=1
        )
        self._first.now(AutomaticRun(job_id=job_id, state="waiting", said=said))
        log.info(
            "performance.benchmark.queued_for_folder",
            job_id=job_id,
            root_id=root_id,
            storage_only=STORAGE in payload,
        )
        return True


def _pressed(job: Job, now: float) -> bool:
    """Work a person is waiting for now: their press, or the priority given to one."""
    if job.timing == AT_QUIET or (job.run_after is not None and job.run_after > now):
        return False
    return job.requested_by is not None or job.priority <= WAITED_ON_PRIORITY


class WhenQuiet:
    """The full run a first part left, asked for when Sift and the device are idle; it gives way."""

    def __init__(
        self,
        *,
        runner: SelfTestRunner,
        queue: JobQueue,
        first: FirstBenchmark,
        kinds: Callable[[], Iterable[str]],
        since_input: Callable[[], float | None] = attention.seconds_since_input,
        since_played: Callable[[], float | None] = lambda: None,
    ) -> None:
        self._runner = runner
        self._queue = queue
        self._first = first
        self._kinds = kinds
        self._since_input = since_input
        self._since_played = since_played
        self._heard: set[str] = {BENCHMARK}
        self._gave_way: set[str] = set()
        self._asking = asyncio.Lock()

    def listen(self) -> None:
        for kind in sorted(set(self._kinds()) - self._heard):
            self._heard.add(kind)
            self._queue.listen_for_settled(kind, self)

    async def __call__(self, _job_id: str) -> None:
        self.listen()
        await self.look()

    async def keep_looking(self, stop: asyncio.Event, *, every: float = LOOK_SECONDS) -> None:
        """On a clock too: after a restart or a quiet start no task ends to say it's quiet."""
        while not stop.is_set():
            try:
                self.listen()
                await self.look()
            except Exception:
                log.exception("performance.benchmark.look_failed")
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=every)

    def _away(self, seconds: float) -> bool:
        # No way to read input (no desktop): the queue alone decides.
        since = self._since_input()
        return since is None or since >= seconds

    def _watched(self, seconds: float = AWAY_SECONDS) -> bool:
        since = self._since_played()
        return since is not None and since < seconds

    async def _live(self) -> Job | None:
        return next(
            (
                job
                for job in await self._queue.newest_of(BENCHMARK, limit=3)
                if job.payload.get(QUIET) and job.state in (JobState.QUEUED, JobState.RUNNING)
            ),
            None,
        )

    async def look(self) -> None:
        if not await self._runner.whole_due():
            return
        # One at a time, so the second look sees the run the first one asked for.
        async with self._asking:
            live = await self._live()
            if live is not None:
                why = await self._stopped_by(live)
                if why is not None:
                    await self._stop(live.id, why)
                return
            if not self._away(AWAY_SECONDS) or self._watched():
                return
            if (await self._queue.list(state=JobState.RUNNING, limit=1)).total:
                return
            if any((await self._queue.due_by_type()).values()):
                return
            job_id = await self._queue.enqueue(BENCHMARK, {QUIET: True}, max_attempts=1)
            said = running_said(storage=False, last=await self._runner.lasted(WHOLE_RUN))
            self._first.now(AutomaticRun(job_id=job_id, state="waiting", said=said, holds=False))
            log.info("performance.benchmark.asked_when_quiet", job_id=job_id)

    async def _stopped_by(self, live: Job) -> str | None:
        if not self._away(attention.ATTENTION_SECONDS):
            return IN_USE
        if self._watched(attention.ATTENTION_SECONDS):
            return PLAYING
        queued = await self._queue.list(state=JobState.QUEUED, limit=50)
        now = time.time()
        if any(
            one.id > live.id and one.type != BENCHMARK and _pressed(one, now) for one in queued.jobs
        ):
            return ASKED_FOR
        return None

    async def give_way_to_folder(self) -> None:
        async with self._asking:
            live = await self._live()
            if live is not None:
                await self._stop(live.id, FOLDER_ADDED)

    async def _stop(self, job_id: str, why: str) -> None:
        """Stop the full run Sift asked for; it's asked again at the next quiet moment."""
        self._gave_way.add(job_id)
        said = f"{why} {AGAIN}"
        self._first.now(AutomaticRun(job_id=job_id, state="gave_way", said=said, holds=False))
        await self._queue.cancel(job_id, why=said)
        log.info("performance.benchmark.gave_way", job_id=job_id, why=why)

    async def ended(self, job_id: str) -> None:
        """Only a person's Cancel or a failure here keeps Sift from starting it again."""
        job = await self._queue.get(job_id)
        if job is None or not job.payload.get(QUIET) or job_id in self._gave_way:
            return
        run = self._first.run
        failed = job.state is JobState.FAILED and run is not None and run.job_id == job_id
        if job.state is JobState.CANCELED or failed:
            await self._runner.hold_whole()


async def ask_for_run(queue: JobQueue, *, requested_by: str | None) -> str | None:
    """Queue a whole run that suggests, unless a whole run is already coming. Its id, or None."""
    for payload in await queue.live_payloads(BENCHMARK):
        if STORAGE not in payload and FIRST_PART not in payload:
            return None
    job_id = await queue.enqueue(
        BENCHMARK, {PRESSED: True}, requested_by=requested_by, max_attempts=1
    )
    log.info("performance.benchmark.asked", job_id=job_id, requested_by=requested_by)
    return job_id


# --- the queue to itself -------------------------------------------------------------------------


@dataclass
class Drain:
    """What a run paused, waiting or running, how long it waited, and what kept running."""

    asked: list[str] = field(default_factory=list)
    paused: int = 0
    kept_running: list[str] = field(default_factory=list)
    waited: float = 0.0

    def holding(self) -> str:
        return f"Sift paused {tasks_said(self.paused)} until it's done."

    def said(self) -> list[str]:
        """The sentences for the screen and the job's note."""
        told: list[str] = []
        if self.paused:
            them = "it" if self.paused == 1 else "them"
            waited = (
                f", waited {round(self.waited)} s for the ones running to stop,"
                if self.asked
                else ""
            )
            told.append(
                f"Sift paused {tasks_said(self.paused)} while it measured{waited} and started "
                f"{them} again after."
            )
        if self.kept_running:
            told.append(
                f"{tasks_said(len(self.kept_running))} couldn't be paused and ran beside it: "
                f"{', '.join(sorted(set(self.kept_running)))}."
            )
        return told


async def drain(context: JobContext, *, wait: float = DRAIN_SECONDS) -> Drain:
    """Pause the waiting work the queue would hand out and every running job but this one, and wait
    up to `wait` seconds for those to stop; each is marked as the benchmark's pause."""
    queue = context.queue
    drained = Drain(paused=await queue.pause_waiting_for_benchmark())
    running = await queue.list(state=JobState.RUNNING, limit=MAX_PAGE_SIZE)
    names: dict[str, str] = {}
    for job in running.jobs:
        if job.id != context.job.id and await queue.pause(job.id, for_benchmark=True):
            drained.asked.append(job.id)
            names[job.id] = job_name(job.type)
    started = time.monotonic()
    left = list(drained.asked)
    while left:
        states = {one: await queue.get(one) for one in left}
        left = [one for one, row in states.items() if row and row.state is JobState.RUNNING]
        if not left or time.monotonic() - started >= wait:
            break
        await asyncio.sleep(0.25)
    drained.waited = time.monotonic() - started
    drained.kept_running = [names[one] for one in left]
    for one in drained.asked:
        landed = await queue.get(one)
        if landed is not None and landed.state is JobState.PAUSED:
            drained.paused += 1
    log.info(
        "performance.benchmark.drained",
        job_id=context.job.id,
        asked=len(drained.asked),
        paused=drained.paused,
        kept_running=drained.kept_running,
        waited_seconds=round(drained.waited, 2),
    )
    return drained


async def undrain(queue: JobQueue, drained: Drain) -> None:
    """Start again what the run paused, or withdraw a pause that has not landed yet."""
    for one in drained.asked:
        await queue.resume(one)
    await queue.resume_after_benchmark()


# --- the run -------------------------------------------------------------------------------------


class _SavesAsSift(Protocol):
    async def apply_as_sift(
        self, updates: dict[str, Any], *, via: str, queue: str, title: str, detail: str
    ) -> str | None: ...


async def run_benchmark(
    context: JobContext,
    *,
    runner: SelfTestRunner,
    first: FirstBenchmark,
    saves: _SavesAsSift,
    current: Callable[[], Awaitable[dict[str, int]]],
    notify: Callable[[set[str]], Awaitable[None]],
    storage_of: Callable[[str], Awaitable[StorageToMeasure | None]],
) -> None:
    """Measure with the queue drained, then, for a run Sift started, set what was found through the
    one door and say so. A pressed run only suggests."""
    job_id = context.job.id
    if context.payload.get(PRESSED):
        await _suggest_only(context, runner)
        return
    quiet = bool(context.payload.get(QUIET))
    storage: str | None = None
    if context.payload.get(STORAGE):
        where = await storage_of(str(context.payload.get("root_id")))
        if where is None:
            _fail(first, job_id, GONE)
        storage = where.storage
    done = not await runner.whole_to_come() if quiet else await runner.measured()
    if storage is None and done and not runner.state.running:
        first.now(AutomaticRun(job_id=job_id, state="already", said=ALREADY, holds=not quiet))
        await context.set_note(ALREADY)
        return
    kind = STORAGE if storage else FIRST_PART if context.payload.get(FIRST_PART) else WHOLE_RUN
    note = await _begin(context, runner, first, kind, quiet)
    left = await runner.first_values() if quiet else {}
    try:
        drained, curve = await _measure(context, runner, storage, note, kind)
    except Exception:
        log.exception("performance.benchmark.raised", job_id=job_id)
        _fail(first, job_id, WENT_WRONG)
    await context.raise_if_canceled()
    found = _found(first, job_id, runner, storage, curve, await current())
    # The full run moves only what the first part left as it was; the rest stays a suggestion.
    changes = [
        one
        for one in found
        if one.changes_anything
        and (not quiet or one.current == left.get(one.key, 0))
        and not (kind == FIRST_PART and one.key in FULL_RUN_ONLY)
        and one.key not in runner.unsure
    ]
    later = [WHOLE_LATER] if kind == FIRST_PART else []
    if not changes:
        ended = " ".join([AGREED if storage is None else STORAGE_KEPT, *later])
        first.now(AutomaticRun(job_id=job_id, state="agreed", said=ended, holds=not quiet))
        await context.set_note(" ".join([ended, *drained.said()]))
        return
    detail = QUIET_RECEIPT_DETAIL if quiet else RECEIPT_DETAIL
    receipt = await saves.apply_as_sift(
        {one.key: one.suggested for one in changes},
        via=VIA_BENCHMARK,
        queue=RECEIPTS,
        title=receipt_title(changes),
        detail=STORAGE_RECEIPT_DETAIL if storage else detail,
    )
    await notify({one.key for one in changes})
    said = " ".join([set_sentence(len(changes)), *later])
    first.now(
        AutomaticRun(
            job_id=job_id,
            state="set",
            said=said,
            changes=tuple(
                Changed(key=one.key, label=one.label, before=one.current, after=one.suggested)
                for one in changes
            ),
            receipt_id=receipt,
            holds=not quiet,
        )
    )
    await context.set_progress(1.0)
    await context.set_note(" ".join([said, *drained.said()]))
    log.info("performance.benchmark.set", job_id=job_id, keys=sorted(one.key for one in changes))


async def _begin(
    context: JobContext, runner: SelfTestRunner, first: FirstBenchmark, kind: str, quiet: bool
) -> str:
    """Say the run has begun, on Activity and to the toasts; the note it measures under."""
    if kind == FIRST_PART:
        said = MEASURING_FIRST
    else:
        said = running_said(storage=kind == STORAGE, last=await runner.lasted(kind))
    first.now(AutomaticRun(job_id=context.job.id, state="running", said=said, holds=not quiet))
    note = said if quiet else f"{said} {HELD}"
    await context.set_note(note)
    return note


async def _suggest_only(context: JobContext, runner: SelfTestRunner) -> None:
    said = running_said(storage=False, last=await runner.lasted(WHOLE_RUN))
    await context.set_note(said)
    drained, _ = await _measure(context, runner, None, said, WHOLE_RUN)
    measurement = runner.state.measurement
    if measurement is None or measurement.failed is not None:
        why = WENT_WRONG if measurement is None else measurement.failed
        raise JobFailedPermanently(failed_sentence(why or WENT_WRONG))
    await context.set_progress(1.0)
    await context.set_note(" ".join([SUGGESTED, *drained.said()]))


def _found(
    first: FirstBenchmark,
    job_id: str,
    runner: SelfTestRunner,
    storage: str | None,
    curve: StorageCurve | None,
    current: dict[str, int],
) -> list[selftest.Recommendation]:
    """What a finished automatic run recommends, failing the run where it found nothing."""
    measurement = runner.state.measurement
    if measurement is None:
        _fail(first, job_id, WENT_WRONG)
    if storage is None:
        if measurement.failed is not None:
            _fail(first, job_id, measurement.failed)
        found = runner.recommend(measurement, current=current)
    else:
        if curve is None:
            _fail(first, job_id, GONE)
        if curve.failed is not None:
            _fail(first, job_id, curve.failed)
        if curve.best is None:
            _fail(first, job_id, TOO_BUSY)
        share = selftest.recommend_share_reads(measurement.storages, current=current)
        return [] if share is None else [share]
    if not found:
        _fail(first, job_id, TOO_BUSY)
    return found


async def _measure(
    context: JobContext, runner: SelfTestRunner, storage: str | None, note: str, kind: str
) -> tuple[Drain, StorageCurve | None]:
    """The run, its first part or one storage and its curve, with everything else paused."""
    began = time.monotonic()
    drained = await drain(context)
    if drained.paused:
        await context.set_note(f"{note} {drained.holding()}")
    curve = None
    try:
        if storage is None:
            await runner.run(first_part=kind == FIRST_PART, since=began)
        else:
            curve = await runner.measure_storage(storage)
    finally:
        await undrain(context.queue, drained)
    runner.notes.extend(drained.said())
    measured = runner.state.measurement
    if (curve.failed is None) if curve is not None else (measured and not measured.failed):
        await runner.keep_length(kind, time.monotonic() - began)
    return drained, curve


def _fail(first: FirstBenchmark, job_id: str, why: str) -> NoReturn:
    """End the run as failed, with the sentence the toast and Activity both say. Raises."""
    said = failed_sentence(why)
    first.now(AutomaticRun(job_id=job_id, state="failed", said=said))
    log.info("performance.benchmark.failed", job_id=job_id, why=why)
    raise JobFailedPermanently(said)


class ThenScan:
    """Once the benchmark has settled, the folder's own first scan: a settled listener, so a run
    canceled before it was claimed, or one that raised, still lets the folder be read."""

    def __init__(
        self,
        *,
        queue: JobQueue,
        first: FirstBenchmark,
        scan: Callable[[str, str | None], Awaitable[None]],
    ) -> None:
        self._queue = queue
        self._first = first
        self._scan = scan

    async def __call__(self, job_id: str) -> None:
        job = await self._queue.get(job_id)
        if job is None:
            return
        run = self._first.run
        if run is not None and run.job_id == job_id and run.going:
            self._first.now(
                AutomaticRun(job_id=job_id, state="failed", said=failed_sentence(STOPPED))
            )
        root_id = job.payload.get("root_id")
        if job.payload.get("scan") and isinstance(root_id, str) and root_id:
            await self._scan(root_id, job.requested_by)
            log.info("performance.benchmark.scan_released", job_id=job_id, root_id=root_id)


# --- Undo ------------------------------------------------------------------------------------------


class _Settings(Protocol):
    async def get_app(self, key: str) -> Any: ...
    async def apply(self, viewer: Viewer, updates: dict[str, Any]) -> None: ...


def _changes(payload: str) -> list[dict[str, str]]:
    """The receipt's `changes`, each with its key and the two encoded values, or none."""
    try:
        read = json.loads(payload)
    except (TypeError, ValueError):
        return []
    found = read.get("changes") if isinstance(read, dict) else None
    if not isinstance(found, list):
        return []
    return [
        one
        for one in found
        if isinstance(one, dict)
        and all(isinstance(one.get(part), str) for part in ("key", "before", "after"))
    ]


class BenchmarkReceipts:
    """Undo on History: the person's own save (`SettingsService.apply`) of each value that still
    says what Sift set; one changed since is theirs."""

    name = RECEIPTS
    reversible = True

    def __init__(self, settings: _Settings, notify: Callable[[set[str]], Awaitable[None]]) -> None:
        self._settings = settings
        self._notify = notify

    async def pictures_of(self, viewer: Viewer, payload: str) -> tuple[Preview, ...]:
        """Nothing: a setting has no picture."""
        return ()

    def worded(self, recorded: Recorded) -> Worded | None:
        """The line, each setting by the name it has now with both values; None where no change
        recorded can be read, which keeps the stored title."""
        parts: list[str] = []
        for one in _changes(recorded.payload):
            setting = get_registered(one["key"])
            try:
                before, after = json.loads(one["before"]), json.loads(one["after"])
            except ValueError:
                return None
            if setting is None or not isinstance(before, int) or not isinstance(after, int):
                return None
            label = setting.label
            parts.append(
                f"{label[:1].lower()}{label[1:]} to {value_said(after)} "
                f"(it was {value_said(before)})"
            )
        if not parts:
            return None
        listed = parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])} and {parts[-1]}"
        return Worded(
            said=(DOER, f" set {listed} from the benchmark of this device"),
            more=(recorded.detail,) if recorded.detail else (),
        )

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        """Put back each value that is still what Sift set. True when anything went back."""
        back: dict[str, Any] = {}
        for one in _changes(payload):
            now = json.dumps(await self._settings.get_app(one["key"]))
            if now == one["after"]:
                back[one["key"]] = json.loads(one["before"])
        if not back:
            return False
        await self._settings.apply(viewer, back)
        await self._notify(set(back))
        log.info("performance.benchmark.undone", receipt_id=receipt_id, keys=sorted(back))
        return True
