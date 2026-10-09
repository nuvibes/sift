# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every task Sift runs over the library, when each may start, and the one door for running one."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sift.kernel.access import Viewer
from sift.kernel.access.history_events import LedgerEvent, latest_events_of_entities
from sift.kernel.db import Database
from sift.kernel.jobs import (
    CANCELABLE_STATES,
    DEFAULT_PRIORITY,
    WAITED_ON_PRIORITY,
    JobQueue,
    JobState,
    TaskRun,
    family_of,
)
from sift.kernel.jobs.failure_words import in_plain_words
from sift.kernel.jobs.families import LONG_PASSES, Family
from sift.kernel.jobs.ledger import Ledger, RunRecord
from sift.kernel.jobs.queue import LiveProducts
from sift.kernel.jobs.quiet_hours import (
    AT_NOW,
    AT_QUIET,
    WHEN_QUIET,
    is_open,
    next_closing,
    next_opening,
)
from sift.kernel.jobs.schedules import (
    ScheduledTask,
    get_schedule,
    registered_schedules,
)
from sift.kernel.jobs.switchboard import QuietHold
from sift.kernel.log import get_logger
from sift.kernel.wiring import Part
from sift.slices.tasks.jobs import TASK_DRY_RUN
from sift.slices.tasks.parts import (
    EVERYTHING,
    DryReport,
    NotAPart,
    Planner,
    Selection,
    TaskPart,
    TaskParts,
    narrowed,
)
from sift.slices.tasks.power import KeepAwake
from sift.slices.tasks.settings import FROM_KEY, KEEP_AWAKE_KEY, UNTIL_KEY

log = get_logger(__name__)

ReadSetting = Callable[[str], Awaitable[Any]]

#: Starts a multi-job task's Run now for `at`, the presser and the part; raises `TaskRefused`.
Starter = Callable[[str, Viewer, Selection], Awaitable[list[str]]]

#: Read when asked, so a folder added a minute ago may be named.
ReadFolders = Callable[[], Awaitable[list[TaskPart]]]

#: The dry run's user, or None if gone; hidden files stay hidden so no report names one.
ViewerFor = Callable[[str], Awaitable[Viewer | None]]

#: Dry runs are pressed by hand, so this many covers every task many times over.
DRY_RUNS_READ = 100

#: Half a minute either side of the range is invisible, and it is one small read.
KEEP_AWAKE_EVERY_SECONDS = 30.0


class TaskRefused(Exception):
    """A press that cannot run on this device just now. The message is meant to be read."""


class UnknownTask(LookupError):
    """No task is declared under that id."""


@dataclass(frozen=True, slots=True)
class LastRun:
    """How a task's last run ended, from the record that does not forget."""

    ended_at: int
    outcome: str
    """done, failed or canceled."""
    seconds: int | None
    said: str | None
    """What it did, in its own sentence, or why it failed. None where it said nothing."""
    report: DryReport | None = None


@dataclass(frozen=True, slots=True)
class TaskState:
    """One task, as the Tasks screen and each owning pane draw it."""

    task: ScheduledTask
    when: str
    on: bool
    cadence: str
    labels: dict[str, str]
    last: LastRun | None
    next_run: int | None
    waiting: int
    held: int
    running: bool
    switched_off: bool = False
    drawn_keys: tuple[str, ...] = ()
    dry_run: LastRun | None = None
    #: Whether a dry run is queued or working, so the row says so until its report lands.
    dry_running: bool = False


@dataclass(frozen=True, slots=True)
class QuietHours:
    """The range as it stands now."""

    starts: str
    ends: str
    open: bool
    opens_at: int
    closes_at: int | None


def _last_job_of(run: TaskRun | None) -> LastRun | None:
    """A one-job task's last finished run in the row's words, or None."""
    if run is None or run.finished_at is None:
        return None
    return LastRun(
        ended_at=run.finished_at, outcome=run.state.value, seconds=run.seconds, said=run.note
    )


def _last_line(event: LedgerEvent | None) -> LastRun | None:
    """How a task that writes its own History line last ended, from that line."""
    if event is None:
        return None
    try:
        said = json.loads(event.payload or "{}")
    except json.JSONDecodeError:
        said = {}
    seconds = said.get("seconds")
    return LastRun(
        ended_at=event.at,
        outcome=str(said.get("outcome") or "done"),
        seconds=int(seconds) if isinstance(seconds, int) else None,
        said=str(said["said"]) if said.get("said") else None,
    )


def _last_of(run: RunRecord | None) -> LastRun | None:
    """How a run from the work ledger ended, in the row's words, or None for no finished run."""
    if run is None or run.finished_at is None:
        return None
    # A run stopped part way is CANCELED, never "done".
    outcome = "done"
    if run.stopped:
        outcome = "canceled"
    elif run.jobs_failed and not run.jobs_done:
        outcome = "failed"
    return LastRun(ended_at=run.finished_at, outcome=outcome, seconds=run.seconds, said=None)


class TasksService:
    """The tasks, read back and run. One per application."""

    def __init__(
        self,
        *,
        queue: JobQueue,
        read: ReadSetting,
        database: Database,
        ledger: Ledger | None,
        governs: Mapping[str, tuple[str, ...]],
        starters: Mapping[str, Starter],
        keep_awake: KeepAwake,
        order: Sequence[str],
        products: Mapping[str, Sequence[str]] | None = None,
        carriers: Sequence[str] = (),
        passes: Sequence[str] = (),
        parts: Mapping[str, TaskParts] | None = None,
        planners: Mapping[str, Planner] | None = None,
        folders: ReadFolders | None = None,
        viewer_for: ViewerFor | None = None,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._queue = queue
        # Which parts of each task run alone, and which can be rehearsed (`parts.py`).
        self._parts = dict(parts or {})
        self._planners = dict(planners or {})
        self._folders = folders
        self._viewer_for = viewer_for
        self._products = {task_id: tuple(keys) for task_id, keys in (products or {}).items()}
        # Product-carrier types and library-walking types, named by the features that own them.
        self._carriers = frozenset(carriers)
        self._passes = frozenset(passes)
        self._read = read
        self._db = database
        self._ledger = ledger
        self._governs = {task_id: tuple(types) for task_id, types in governs.items()}
        self._starters = dict(starters)
        self._awake = keep_awake
        self._now = now
        unknown = sorted(set(self._governs) - set(registered_schedules()))
        if unknown:
            raise ValueError(f"work is mapped to tasks nobody declared: {', '.join(unknown)}")
        missing = sorted(
            task.id
            for task in registered_schedules().values()
            if task.needs_starter and task.id not in self._starters
        )
        if missing:
            raise ValueError(f"tasks that need a starter have none: {', '.join(missing)}")
        # A part nothing can start, or folders nothing can read, would be unchecked choices.
        unstartable = sorted(
            task_id
            for task_id, declared in self._parts.items()
            if (declared.subtasks or declared.locations) and task_id not in self._starters
        )
        if unstartable:
            raise ValueError(f"tasks with parts have no starter: {', '.join(unstartable)}")
        if folders is None and any(one.locations for one in self._parts.values()):
            raise ValueError("a task runs over some folders and nothing reads the folders")
        undeclared = sorted((set(self._parts) | set(self._planners)) - set(registered_schedules()))
        if undeclared:
            raise ValueError(f"parts or plans for tasks nobody declared: {', '.join(undeclared)}")
        # The order the composition root hands in, refused unless it names every task exactly once.
        declared = set(registered_schedules())
        if len(set(order)) != len(order) or set(order) != declared:
            unplaced = sorted(declared - set(order))
            unknown_order = sorted(set(order) - declared)
            raise ValueError(
                "the task order must name every declared task once: "
                f"unplaced {', '.join(unplaced) or 'none'}; "
                f"not declared {', '.join(unknown_order) or 'none'}"
            )
        self._order = tuple(order)
        # A stage reads declared Whens only: an undeclared name would write nowhere.
        unread = sorted(
            f"{task.id} reads {one}"
            for task in registered_schedules().values()
            for one in task.reads
            if one not in declared
        )
        if unread:
            raise ValueError(f"a task reads Whens nobody declared: {', '.join(unread)}")

    # --- quiet hours -------------------------------------------------------------------------

    async def quiet_range(self) -> tuple[str, str]:
        """The range as the person set it, as two `HH:MM` strings."""
        return str(await self._read(FROM_KEY)), str(await self._read(UNTIL_KEY))

    async def quiet_hours(self) -> QuietHours:
        start, end = await self.quiet_range()
        now = int(self._now())
        return QuietHours(
            starts=start,
            ends=end,
            open=is_open(start, end, now),
            opens_at=next_opening(start, end, now),
            closes_at=next_closing(start, end, now),
        )

    def governed(self, task: ScheduledTask) -> tuple[str, ...]:
        """Every job type whose unpressed work follows this task's When, its own first."""
        own = (task.job_type,) if task.job_type is not None else ()
        return tuple(dict.fromkeys(own + self._governs.get(task.id, ())))

    async def quiet_hold(self) -> QuietHold:
        """Is the range open, and whose unpressed work waits for it: asked before every claim."""
        start, end = await self.quiet_range()
        held: set[str] = set()
        for task in registered_schedules().values():
            if str(await self._read(task.when_key)) == WHEN_QUIET:
                held.update(self.governed(task))
        return QuietHold(open=is_open(start, end, int(self._now())), types=frozenset(held))

    # --- keeping the device awake --------------------------------------------------------------

    async def keep_awake_once(self) -> bool:
        """Take or withdraw the power request: held only while allowed, open, and work waits."""
        try:
            allowed = bool(await self._read(KEEP_AWAKE_KEY))
            hold = await self.quiet_hold()
            quiet_work = 0
            if allowed and hold.open:
                # Only work due before the range closes, or the device stays awake for tomorrow's.
                start, end = await self.quiet_range()
                now = int(self._now())
                closes = next_closing(start, end, now)
                due_before = closes if closes is not None else now + int(KEEP_AWAKE_EVERY_SECONDS)
                held = await self._queue.held_by_type(hold.types, due_before=due_before)
                quiet_work = sum(held.values())
        except Exception:
            log.exception("tasks.keep_awake_unreadable")
            self._awake.release(why="unreadable")
            return False
        if allowed and hold.open and quiet_work > 0:
            self._awake.hold(why="quiet_hours_work")
        elif not allowed:
            self._awake.release(why="not_allowed")
        elif not hold.open:
            self._awake.release(why="quiet_hours_closed")
        else:
            self._awake.release(why="no_quiet_hours_work")
        return self._awake.held

    async def keep_awake(self, stop: asyncio.Event) -> None:
        """Look again every half a minute until told to stop, then withdraw the request."""
        try:
            while not stop.is_set():
                await self.keep_awake_once()
                try:
                    await asyncio.wait_for(stop.wait(), timeout=KEEP_AWAKE_EVERY_SECONDS)
                except TimeoutError:
                    continue
        finally:
            self._awake.release(why="shutting_down")

    async def setting(self, key: str) -> Any:
        """One app setting's value, for the route that draws it beside the tasks."""
        return await self._read(key)

    @property
    def awake_now(self) -> bool:
        """Whether the power request stands this moment."""
        return self._awake.held

    # --- reading every task back -----------------------------------------------------------------

    async def states(self, viewer: Viewer) -> list[TaskState]:
        """Every declared task, in the order handed in, with what happened last and what comes next."""
        declared = registered_schedules()
        tasks = [declared[task_id] for task_id in self._order]
        live = await self._queue.live_by_type()
        hold = await self.quiet_hold()
        held, held_waiting = await self._queue.held_and_waiting_by_type(hold.types)
        carried = await self._queue.live_products(sorted(self._carriers))
        quiet = await self.quiet_hours()
        rehearsed, rehearsing = await self._last_dry_runs()
        # Every task's next moment and last run, a statement per relation rather than per task.
        due = await self._queue.next_scheduled_of(
            [task.job_type for task in tasks if task.every is not None and task.job_type]
        )
        lasts = await self._lasts(tasks, viewer)
        answers: list[TaskState] = []
        for task in tasks:
            values = {key: await self._read(key) for key in task.keys_read()}
            work = _work_of(
                self._types_counted(task),
                self._products.get(task.id, ()),
                live=live,
                held=held,
                held_waiting=held_waiting,
                carried=carried,
                uncounted=self._passes if task.unit[0] == "file" else frozenset(),
            )
            waiting, held_now = work.waiting, work.held
            # A timed task's queued next run is said as "Next", not as work waiting.
            moment = due.get(task.job_type or "")
            if task.every is not None and moment is not None:
                waiting = max(0, waiting - 1)
                held_now = min(held_now, waiting)
            answers.append(
                TaskState(
                    task=task,
                    when=task.when(values),
                    on=task.is_on(values),
                    cadence=task.cadence(values),
                    labels=task.when_labels(values),
                    last=lasts.get(task.id),
                    next_run=self._next(task, values, quiet, work.holding, moment),
                    waiting=waiting,
                    held=held_now,
                    running=work.running,
                    switched_off=task.switched_off(values),
                    drawn_keys=task.drawn_keys(values),
                    dry_run=rehearsed.get(task.id),
                    dry_running=task.id in rehearsing,
                )
            )
        return answers

    def _types_counted(self, task: ScheduledTask) -> tuple[str, ...]:
        """The job types whose rows are this task's work by type alone, not product carriers."""
        types = self.governed(task)
        if task.id in self._products:
            return tuple(one for one in types if one not in self._carriers)
        return types

    async def _last_dry_runs(self) -> tuple[dict[str, LastRun], set[str]]:
        """Each task's newest finished dry run, and the tasks with one still queued or working."""
        if not self._planners:
            return {}, set()
        found: dict[str, LastRun] = {}
        going: set[str] = set()
        for job in await self._queue.newest_of(TASK_DRY_RUN, limit=DRY_RUNS_READ):
            task_id = str(job.payload.get("task") or "")
            if job.state in CANCELABLE_STATES:
                going.add(task_id)
            if task_id in found or job.state not in _DRY_RUN_ENDED:
                continue
            report = None if job.state is JobState.FAILED else DryReport.of_note(job.note)
            # A failure in plain words: the tool's own text stays on the job's row on Activity.
            said = (
                (None if job.error is None else in_plain_words(job.error))
                if job.state is JobState.FAILED
                else job.note
            )
            found[task_id] = LastRun(
                ended_at=job.updated_at,
                outcome=_DRY_RUN_ENDED[job.state],
                seconds=None if job.started_at is None else max(0, job.updated_at - job.started_at),
                said=report.said if report is not None else said,
                report=report,
            )
        return found, going

    # --- the parts of a task, and its dry run ---------------------------------------------------

    def parts_of(self, task_id: str) -> TaskParts:
        """What part of this task can run on its own; nothing for a task that only runs whole."""
        return self._parts.get(task_id, TaskParts())

    def rehearses(self, task_id: str) -> bool:
        """Whether this task has a dry run."""
        return task_id in self._planners

    async def folders(self) -> list[TaskPart]:
        """The library folders a press may name, as they are now."""
        return [] if self._folders is None else await self._folders()

    async def selection(
        self, task_id: str, parts: Sequence[str] | None, locations: Sequence[str] | None
    ) -> Selection:
        """A press's parts checked against the task's declaration. Raises `NotAPart`."""
        if get_schedule(task_id) is None:
            raise UnknownTask(task_id)
        if parts is None and locations is None:
            return EVERYTHING
        folders = [one.key for one in await self.folders()] if locations is not None else []
        return narrowed(self.parts_of(task_id), parts, locations, folders)

    async def described(self, task_id: str, only: Selection) -> str | None:
        """What a part run was for, in words for its receipt; None for the whole task."""
        if only.whole:
            return None
        declared = self.parts_of(task_id)
        named = [one.label for one in declared.subtasks if only.parts and one.key in only.parts]
        if only.locations:
            by_key = {one.key: one.label for one in await self.folders()}
            named += [by_key.get(one, one) for one in only.locations]
        return ", ".join(named) or None

    async def rehearse(self, task_id: str, only: Selection, user_id: str | None) -> DryReport:
        """What a run of this task would do for this selection, as a report; writes nothing."""
        task = get_schedule(task_id)
        planner = self._planners.get(task_id)
        if task is None or planner is None:
            raise UnknownTask(task_id)
        viewer = None
        if user_id is not None and self._viewer_for is not None:
            viewer = await self._viewer_for(user_id)
        if viewer is None:
            raise TaskRefused("The person who asked for this dry run is no longer a user here.")
        title = task.title
        if only.locations is not None:
            folders = {one.key: one.label for one in await self.folders()}
            try:
                narrowed(self.parts_of(task_id), None, only.locations, list(folders))
            except NotAPart as refused:
                raise TaskRefused(str(refused)) from refused
            title += " for " + ", ".join(folders[one] for one in only.locations)
        plan = await planner(only, viewer)
        return plan.reported(title)

    def _next(
        self,
        task: ScheduledTask,
        values: Mapping[str, Any],
        quiet: QuietHours,
        holding: int,
        moment: int | None,
    ) -> int | None:
        """When it next starts on its own, or None: press-only, or waiting for work to arrive."""
        if not task.is_on(values):
            return None
        if task.every is not None and task.job_type is not None:
            if moment is None:
                return None
            if task.when(values) == WHEN_QUIET and not quiet.open:
                return max(moment, quiet.opens_at)
            return moment
        if task.when(values) == WHEN_QUIET and holding > 0:
            return int(self._now()) if quiet.open else quiet.opens_at
        return None

    async def _lasts(
        self, tasks: Sequence[ScheduledTask], viewer: Viewer
    ) -> dict[str, LastRun | None]:
        """`_last` for every task, each source asked once for all the tasks that read it."""
        by_products: list[tuple[ScheduledTask, Sequence[str], Family]] = []
        one_job: list[ScheduledTask] = []
        lasts: dict[str, LastRun | None] = {}
        # The tasks that write their own line, every one's newest in one read.
        lines = await latest_events_of_entities(
            self._db, viewer, "run", [task.id for task in tasks if task.records_runs]
        )
        for task in tasks:
            products = self._products.get(task.id)
            if task.records_runs:
                lasts[task.id] = _last_line(lines.get(task.id))
                continue
            # `register_schedule` refuses a task with no job type, so every other task has one.
            assert task.job_type is not None  # noqa: S101 (refused at registration)
            family = family_of(task.job_type)
            if products and self._ledger is not None:
                by_products.append((task, products, family))
            elif family not in LONG_PASSES:
                one_job.append(task)
            elif self._ledger is not None:
                lasts[task.id] = _last_of(await self._ledger.last_run(family))
            else:
                lasts[task.id] = None
        if by_products and self._ledger is not None:
            runs = await self._ledger.last_runs_for([(p, f) for _t, p, f in by_products])
            for (task, _products, _family), run in zip(by_products, runs, strict=True):
                lasts[task.id] = _last_of(run)
        finished = await self._queue.last_finished_runs(
            sorted({task.job_type for task in one_job if task.job_type is not None})
        )
        for task in one_job:
            lasts[task.id] = _last_job_of(finished.get(task.job_type or ""))
        return lasts

    # --- running one -------------------------------------------------------------------------

    async def run(
        self,
        task_id: str,
        *,
        at: str,
        viewer: Viewer,
        only: Selection = EVERYTHING,
        dry: bool = False,
    ) -> tuple[list[str], int | None]:
        """Run a task now or when quiet hours open; a press always runs. Answers ids and when."""
        task = get_schedule(task_id)
        if task is None:
            raise UnknownTask(task_id)
        if at not in (AT_NOW, AT_QUIET):
            raise ValueError("at is now or quiet")
        if dry:
            return [await self._queue_dry_run(task_id, only, viewer)], None
        starter = self._starters.get(task.id)
        # A task run whole as one job but also in part has a starter for the part runs only.
        if starter is not None and (task.needs_starter or not only.whole):
            ids = await starter(at, viewer, only)
        else:
            # `register_schedule` refuses a task with no job type, so every declared task has one.
            assert task.job_type is not None  # noqa: S101 (refused at registration)
            ids = [
                await self._queue.enqueue(
                    task.job_type,
                    dict(task.payload),
                    # Somebody waits on "now"; quiet hours does not step in front of other work.
                    priority=WAITED_ON_PRIORITY if at == AT_NOW else DEFAULT_PRIORITY,
                    requested_by=viewer.id,
                    at=at,
                    # A task with no timer runs once however often pressed; a timed one's press runs
                    # beside.
                    dedupe=task.every is None,
                )
            ]
        starts: int | None = None
        if at == AT_QUIET and await self._any_waits_for_quiet_hours(ids):
            quiet = await self.quiet_hours()
            starts = int(self._now()) if quiet.open else quiet.opens_at
        log.info("tasks.run", task=task.id, at=at, queued=len(ids))
        return ids, starts

    async def _queue_dry_run(self, task_id: str, only: Selection, viewer: Viewer) -> str:
        """Queue one dry run of this task for this selection, named for the person who pressed."""
        if task_id not in self._planners:
            raise TaskRefused("This task has no dry run.")
        payload: dict[str, Any] = {"task": task_id}
        if only.parts is not None:
            payload["parts"] = list(only.parts)
        if only.locations is not None:
            payload["locations"] = list(only.locations)
        return await self._queue.enqueue(
            TASK_DRY_RUN,
            payload,
            priority=WAITED_ON_PRIORITY,
            requested_by=viewer.id,
            at=AT_NOW,
            # The same question asked twice while the first is still waiting is one answer.
            dedupe=True,
            # A plan that could not be worked out says why; asking again would say it again.
            max_attempts=1,
        )

    async def _any_waits_for_quiet_hours(self, ids: list[str]) -> bool:
        """Whether any row a press landed on waits for quiet hours, answered from the rows."""
        hold = await self.quiet_hold()
        for job_id in ids:
            row = await self._queue.get(job_id)
            if row is None or row.state is not JobState.QUEUED:
                continue
            if row.timing == AT_QUIET or (row.timing is None and row.type in hold.types):
                return True
        return False


@dataclass(frozen=True, slots=True)
class _Work:
    """What one task's row says of its live work."""

    waiting: int
    held: int
    holding: int
    running: bool


_NOT_STARTED = (JobState.QUEUED.value, JobState.BLOCKED.value)


def _work_of(
    types: Sequence[str],
    products: Sequence[str],
    *,
    live: Mapping[str, Mapping[str, int]],
    held: Mapping[str, int],
    held_waiting: Mapping[str, int],
    carried: Sequence[LiveProducts],
    uncounted: frozenset[str] = frozenset(),
) -> _Work:
    """A task's live work: rows of its own types, and run rows naming any of its products."""
    counted = [one for one in types if one not in uncounted]
    waiting = sum(live.get(one, {}).get(state, 0) for one in counted for state in _NOT_STARTED)
    running = any(live.get(one, {}).get(JobState.RUNNING.value, 0) for one in types)
    held_now = sum(held_waiting.get(one, 0) for one in counted)
    holding = sum(held.get(one, 0) for one in types)
    wanted = set(products)
    for line in carried if wanted else ():
        if not wanted.intersection(line.products):
            continue
        if line.state is JobState.RUNNING:
            running = True
            holding += line.count if line.quiet else 0
        elif line.state.value in _NOT_STARTED:
            mine = 0 if line.type in uncounted else line.count
            waiting += mine
            if line.quiet and line.state is JobState.QUEUED:
                held_now += mine
                holding += line.count
    return _Work(waiting=waiting, held=held_now, holding=holding, running=running)


_DRY_RUN_ENDED = {
    JobState.DONE: "done",
    JobState.FAILED: "failed",
    JobState.CANCELED: "canceled",
}

SERVICE: Part[TasksService] = Part("tasks")
