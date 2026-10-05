# SPDX-License-Identifier: AGPL-3.0-or-later
"""The benchmark job: every run of the self-test, and what an automatic one sets.

Every run has the queue to itself and pauses the work waiting and running while it measures
(`drain`). A press or a Build queues one that suggests; adding a folder queues one that sets, as a
receipt on History with an Undo, and the folder's first scan waits behind it.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Literal, NoReturn, Protocol

from sift.kernel.access import Viewer
from sift.kernel.jobs import (
    MAX_PAGE_SIZE,
    JobContext,
    JobFailedPermanently,
    JobQueue,
    JobState,
    job_name,
)
from sift.kernel.log import get_logger
from sift.kernel.settings_registry import get_registered
from sift.kernel.vocabulary import VIA_BENCHMARK
from sift.kernel.wiring import Part
from sift.kernel.workbench import DOER, Preview, Recorded, Worded
from sift.slices.performance import selftest
from sift.slices.performance.runner import SelfTestRunner
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

#: Until a run of its kind here: 12 min measured for a whole run, 1 s to 28 s for one storage.
RUNNING: Final = f"{WHOLE} It takes several minutes."
MEASURING_STORAGE: Final = f"{ONE_STORAGE} Under a minute."

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

STORAGE_RECEIPT_DETAIL: Final = (
    "Sift measured the storage a new library folder is on by itself when the folder was added. "
    "Undo puts back the value the setting had before, where nobody has changed it since."
)


def tasks_said(count: int) -> str:
    return f"{count} {'task' if count == 1 else 'tasks'}"


# --- what the screens read -----------------------------------------------------------------------

State = Literal["waiting", "running", "set", "agreed", "already", "failed"]


@dataclass(frozen=True)
class Changed:
    """One setting the run set: its key, the words the screen calls it, and the two values."""

    key: str
    label: str
    before: int
    after: int


@dataclass(frozen=True)
class AutomaticRun:
    """The run Sift started by itself, as the toasts read it. Held in this process only: what
    outlives it is the job's row on Activity and the receipt on History, which say the same."""

    job_id: str
    state: State
    said: str
    changes: tuple[Changed, ...] = ()
    receipt_id: str | None = None

    @property
    def going(self) -> bool:
        return self.state in ("waiting", "running")


@dataclass
class FirstBenchmark:
    """The one automatic run there may be, for the route the toasts read. The job's row and the
    settings save tell the screens of each move."""

    run: AutomaticRun | None = None

    def now(self, run: AutomaticRun) -> None:
        self.run = run


#: The automatic run, on the application: the trigger writes it, the job moves it, the route reads it.
FIRST_BENCHMARK: Part[FirstBenchmark] = Part("first_benchmark")


# --- the trigger ---------------------------------------------------------------------------------


class FirstFolder:
    """A library folder added: the whole benchmark for the first folder on a device never
    measured, or a run of the folder's storage where it has no number kept; the scan runs after."""

    def __init__(
        self,
        *,
        runner: SelfTestRunner,
        queue: JobQueue,
        roots: Callable[[], Awaitable[int]],
        first: FirstBenchmark,
        storage: Callable[[str], Awaitable[StorageToMeasure | None]],
    ) -> None:
        self._runner = runner
        self._queue = queue
        self._roots = roots
        self._first = first
        self._storage = storage

    async def __call__(self, root_id: str, requested_by: str | None, scan: bool) -> bool:
        """Whether the benchmark was queued, and so whether the folder's scan (when one was asked
        for) is this reaction's to queue once the benchmark has settled."""
        if requested_by is None:
            return False
        if (await self._queue.unfinished_by_type()).get(BENCHMARK, 0) > 0:
            return False
        payload: dict[str, Any] = {"root_id": root_id, "scan": scan}
        said = RUNNING
        rates = await self._runner.rates()
        if rates is None:
            if await self._roots() != 1:
                return False
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


async def ask_for_run(queue: JobQueue, *, requested_by: str | None) -> str | None:
    """Queue a whole run that suggests, unless a whole run is already coming. Its id, or None."""
    for payload in await queue.live_payloads(BENCHMARK):
        if STORAGE not in payload:
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
    """Measure with the queue drained, then, for a run a folder queued, set what was found through
    the one door and say so. A pressed run, or a Build's, only suggests.
    """
    job_id = context.job.id
    if context.payload.get(PRESSED):
        await _suggest_only(context, runner)
        return
    storage: str | None = None
    if context.payload.get(STORAGE):
        where = await storage_of(str(context.payload.get("root_id")))
        if where is None:
            _fail(first, job_id, GONE)
        storage = where.storage
    if storage is None and await runner.measured() and not runner.state.running:
        first.now(AutomaticRun(job_id=job_id, state="already", said=ALREADY))
        await context.set_note(ALREADY)
        return
    said = running_said(
        storage=storage is not None, last=await runner.lasted(STORAGE if storage else WHOLE_RUN)
    )
    first.now(AutomaticRun(job_id=job_id, state="running", said=said))
    await context.set_note(f"{said} {HELD}")
    try:
        drained, curve = await _measure(context, runner, storage, f"{said} {HELD}")
    except Exception:
        log.exception("performance.benchmark.raised", job_id=job_id)
        _fail(first, job_id, WENT_WRONG)
    await context.raise_if_canceled()
    found = _found(first, job_id, runner, storage, curve, await current())
    changes = [one for one in found if one.changes_anything]
    if not changes:
        said = AGREED if storage is None else STORAGE_KEPT
        first.now(AutomaticRun(job_id=job_id, state="agreed", said=said))
        await context.set_note(" ".join([said, *drained.said()]))
        return
    receipt = await saves.apply_as_sift(
        {one.key: one.suggested for one in changes},
        via=VIA_BENCHMARK,
        queue=RECEIPTS,
        title=receipt_title(changes),
        detail=RECEIPT_DETAIL if storage is None else STORAGE_RECEIPT_DETAIL,
    )
    # The reactions a saved preference gets, so a value Sift set acts as one somebody typed.
    await notify({one.key for one in changes})
    said = set_sentence(len(changes))
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
        )
    )
    await context.set_progress(1.0)
    await context.set_note(" ".join([said, *drained.said()]))
    log.info("performance.benchmark.set", job_id=job_id, keys=sorted(one.key for one in changes))


async def _suggest_only(context: JobContext, runner: SelfTestRunner) -> None:
    said = running_said(storage=False, last=await runner.lasted(WHOLE_RUN))
    await context.set_note(said)
    drained, _ = await _measure(context, runner, None, said)
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
    context: JobContext, runner: SelfTestRunner, storage: str | None, note: str
) -> tuple[Drain, StorageCurve | None]:
    """The whole run, or one storage and its curve, with everything else paused for its length."""
    began = time.monotonic()
    drained = await drain(context)
    if drained.paused:
        await context.set_note(f"{note} {drained.holding()}")
    curve = None
    try:
        if storage is None:
            await runner.run()
        else:
            curve = await runner.measure_storage(storage)
    finally:
        await undrain(context.queue, drained)
    runner.notes.extend(drained.said())
    measured = runner.state.measurement
    if (curve.failed is None) if curve is not None else (measured and not measured.failed):
        await runner.keep_length(STORAGE if storage else WHOLE_RUN, time.monotonic() - began)
    return drained, curve


def _fail(first: FirstBenchmark, job_id: str, why: str) -> NoReturn:
    """End the run as failed, with the sentence the toast and Activity both say. Raises."""
    said = failed_sentence(why)
    first.now(AutomaticRun(job_id=job_id, state="failed", said=said))
    log.info("performance.benchmark.failed", job_id=job_id, why=why)
    raise JobFailedPermanently(said)


class ThenScan:
    """Once the benchmark has settled (done, failed or cancelled), the folder's own first scan.

    Asked of the queue's settled listeners rather than at the end of the handler, so a run
    cancelled before it was ever claimed still lets the folder be read, and a run that raised
    does too. A run that never reached an end of its own says so for the toasts.
    """

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
    """How the settings the benchmark set are taken back from History. A reverser with no card.

    Undo is the person's own save, through the same door as any press (`SettingsService.apply`),
    so History then says they changed each value back. Only a value that still says what Sift set
    goes back: one somebody has changed since is theirs.
    """

    name = RECEIPTS
    reversible = True

    def __init__(self, settings: _Settings, notify: Callable[[set[str]], Awaitable[None]]) -> None:
        self._settings = settings
        self._notify = notify

    async def pictures_of(self, viewer: Viewer, payload: str) -> tuple[Preview, ...]:
        """Nothing: a setting has no picture."""
        return ()

    def worded(self, recorded: Recorded) -> Worded | None:
        """The line, worded when shown from the changes the receipt recorded: each setting by the
        name it has NOW, the value Sift set and the value it had. None for a row that recorded no
        change this build can read, which keeps its stored title. See `kernel.workbench.Recorded`.
        """
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
