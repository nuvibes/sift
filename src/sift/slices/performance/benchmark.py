# SPDX-License-Identifier: AGPL-3.0-or-later
"""The benchmark Sift runs by itself when the first library folder is added, and what it sets.

A device nobody has measured reads every file the guessed way: the rates that shape each read, and
the numbers of tasks and previews at once, are the self-test's to find (`selftest`). So the first
folder added to a library on a device with no measurement for its hardware queues the benchmark at
once (`FirstFolder`), and the folder's first scan waits behind it in the line, so the scan reads
under the settings the benchmark chose. A second folder, a measured device, or a library restored
with a measurement never queues it, and only an admin can add a folder at all.

When that run ends, what it recommends is saved through the settings' own door for a batch Sift
chose (`SettingsService.apply_as_sift`): the same validation a press goes through, one receipt on
History saying each value and what it was, and an Undo that puts the earlier values back
(`BenchmarkReceipts`). A run somebody PRESSES on `Settings > Performance` is not this: it suggests,
and waits for Apply.

NOT A WHOLE-LIBRARY PASS. It works the processor for a few minutes and reads a small sample of
large files on each share the library is on (`selftest.measure_storage`); it touches no file's
record, and it starts from a person's press (adding the folder), never from a settle or at boot.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal, NoReturn, Protocol

from sift.kernel.access import Viewer
from sift.kernel.jobs import JobContext, JobFailedPermanently, JobQueue
from sift.kernel.log import get_logger
from sift.kernel.settings_registry import get_registered
from sift.kernel.vocabulary import VIA_BENCHMARK
from sift.kernel.wiring import Part
from sift.kernel.workbench import DOER, Preview, Recorded, Worded
from sift.slices.performance import selftest
from sift.slices.performance.runner import SelfTestRunner

log = get_logger(__name__)

#: The kind, as the queue knows it.
BENCHMARK: Final = "performance_benchmark"

#: What the queue calls it on Activity: the Performance screen's own heading for a run.
BENCHMARK_NAME: Final = "Benchmarking this device"

#: The name its receipts are taken back under (`kernel.workbench.Reverser`).
RECEIPTS: Final = "benchmark"

# --- the words ------------------------------------------------------------------------------------
#
# Said in three places from one author: the job's note on Activity, and the toasts every admin
# window shows (`GET /performance/benchmark` hands the sentence over). A toast written by the
# browser beside a note written here would be two copies of one sentence, free to drift.

#: While it waits and while it runs.
RUNNING: Final = "Benchmarking this device so Sift can make the best use of it. A few minutes."

#: While it waits or runs, what is true of the folder whose add queued it: nothing of it is read
#: until the run has settled (the queue runs nothing beside it, `register_handler(exclusive=)`).
#: Said by the wall that would show its files, by the toast of whoever added it, and on Activity
#: after `RUNNING`, so the three are one sentence.
HELD: Final = "Your folder is added, and its files appear once this device has been benchmarked."

#: Finished, and every value it would choose was already chosen.
AGREED: Final = "The benchmark found your settings already suit this device."

#: Finished on a device measured before it was claimed: a pressed run got there first, and its
#: suggestions wait for Apply as a pressed run's always do.
ALREADY: Final = "This device was benchmarked already, so there was nothing to run."

#: Why a run that ran found nothing to go on (`Measurement.best` is None): no level finished in
#: time, which on a working encoder means the device was too busy to measure.
TOO_BUSY: Final = "this device was too busy to measure"

#: Why a run that raised did not finish.
WENT_WRONG: Final = "something went wrong while it ran"

#: Why a run taken off the queue before it ended did not finish.
STOPPED: Final = "it was stopped"


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
    """The one automatic run there may be, for the route the toasts read.

    Nothing here tells a screen: every move is told by a write beside it, the job's own row (the
    jobs bell) and, at the end, the settings save (the settings bell).
    """

    run: AutomaticRun | None = None

    def now(self, run: AutomaticRun) -> None:
        self.run = run


#: The automatic run, on the application: the trigger writes it, the job moves it, the route reads it.
FIRST_BENCHMARK: Part[FirstBenchmark] = Part("first_benchmark")


# --- the trigger ---------------------------------------------------------------------------------


class FirstFolder:
    """The reaction to a library folder added: queue the benchmark when it is the first folder on a
    device never measured, and take the folder's scan over so it runs after.

    Every rule here is one way of NOT starting it: a folder that is not the only one, a device
    with a measurement for its hardware (a restored library brings its own), a run already
    waiting, nobody's press. What starts it is the one case left.
    """

    def __init__(
        self,
        *,
        runner: SelfTestRunner,
        queue: JobQueue,
        roots: Callable[[], Awaitable[int]],
        first: FirstBenchmark,
    ) -> None:
        self._runner = runner
        self._queue = queue
        self._roots = roots
        self._first = first

    async def __call__(self, root_id: str, requested_by: str | None, scan: bool) -> bool:
        """Whether the benchmark was queued, and so whether the folder's scan (when one was asked
        for) is this reaction's to queue once the benchmark has settled."""
        if requested_by is None:
            return False
        if await self._roots() != 1:
            return False
        if await self._runner.measured():
            return False
        if (await self._queue.unfinished_by_type()).get(BENCHMARK, 0) > 0:
            return False
        job_id = await self._queue.enqueue(
            BENCHMARK,
            {"root_id": root_id, "scan": scan},
            requested_by=requested_by,
            max_attempts=1,
        )
        self._first.now(AutomaticRun(job_id=job_id, state="waiting", said=RUNNING))
        log.info("performance.benchmark.queued_for_first_folder", job_id=job_id, root_id=root_id)
        return True


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
) -> None:
    """Measure, then set what was found through the one door, and say so.

    The run itself is the runner's, so a run pressed on the screen while this waited is the run
    this waits for, and the screen shows this one as it goes. What it recommends is worked out
    again against the settings as they stand at the end, the way the screen reads a finished run.
    """
    job_id = context.job.id
    if await runner.measured() and not runner.state.running:
        first.now(AutomaticRun(job_id=job_id, state="already", said=ALREADY))
        await context.set_note(ALREADY)
        return
    first.now(AutomaticRun(job_id=job_id, state="running", said=RUNNING))
    await context.set_note(f"{RUNNING} {HELD}")
    try:
        await runner.measure()
    except Exception:
        log.exception("performance.benchmark.raised", job_id=job_id)
        _fail(first, job_id, WENT_WRONG)
    await context.raise_if_canceled()
    measurement = runner.state.measurement
    if measurement is None:
        _fail(first, job_id, WENT_WRONG)
    if measurement.failed is not None:
        _fail(first, job_id, measurement.failed)
    found = selftest.recommend(measurement, current=await current())
    if not found:
        _fail(first, job_id, TOO_BUSY)
    changes = [one for one in found if one.changes_anything]
    if not changes:
        first.now(AutomaticRun(job_id=job_id, state="agreed", said=AGREED))
        await context.set_note(AGREED)
        return
    receipt = await saves.apply_as_sift(
        {one.key: one.suggested for one in changes},
        via=VIA_BENCHMARK,
        queue=RECEIPTS,
        title=receipt_title(changes),
        detail=RECEIPT_DETAIL,
    )
    # What has to happen the moment a preference is saved, as the settings route asks after a
    # press: the same reactions, so a value Sift set acts as one somebody typed.
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
    await context.set_note(said)
    log.info("performance.benchmark.set", job_id=job_id, keys=sorted(one.key for one in changes))


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
