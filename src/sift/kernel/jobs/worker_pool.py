# SPDX-License-Identifier: AGPL-3.0-or-later
"""The workers, and the handlers they run: claim a row, call the handler, write what happened.

The heartbeat is fenced on the claim, so a job taken away cancels its handler mid-flight.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import structlog

from sift.kernel.content import ContentStore, LibraryStore
from sift.kernel.ids import new_id
from sift.kernel.jobs.families import Family
from sift.kernel.jobs.holding import Holding
from sift.kernel.jobs.ledger import CURRENT_FAMILY, Ledger
from sift.kernel.jobs.queue import (
    STOP_TO_CANCEL,
    TERMINAL_STATES,
    Job,
    JobBlocked,
    JobCanceled,
    JobFailedPermanently,
    JobPaused,
    JobQueue,
    JobState,
)
from sift.kernel.jobs.queue_core import ASKED_AT
from sift.kernel.jobs.registry import (
    _ALONE,
    _BY_ITSELF,
    _CARRIERS,
    _COUNTS,
    _EXCLUSIVE,
    _FAMILIES,
    _FOLLOWS,
    _HANDLERS,
    _HOLDS,
    _NAMES,
    _NOT_GATED,
    _TRAILS,
    _UNLISTED,
    _URGENCY,
    TRAILING_RANK,
    UNDECLARED_RANK,
    by_itself_job_types,
    claim_rank,
    counted_as,
    exclusive_job_types,
    family_of,
    gated_by_readiness,
    held_for,
    hold_on,
    in_claim_order,
    job_name,
    products_named,
    register_handler,
    registered_alone,
    registered_families,
    registered_follows,
    registered_handlers,
    registered_job_names,
    registered_product_carriers,
    registered_urgency,
    unlisted_job_types,
)
from sift.kernel.jobs.retrying import backoff, cannot_change
from sift.kernel.jobs.tuning import (
    HEARTBEAT_SECONDS,
    PROGRESS_INTERVAL_SECONDS,
    RECONFIGURE_SECONDS,
    SHUTDOWN_GRACE_SECONDS,
    STALE_AFTER_SECONDS,
    SWEEP_INTERVAL_SECONDS,
)
from sift.kernel.jobs.waking import LONGEST_IDLE_SECONDS, Listen, Waking, first_of
from sift.kernel.jobs.watchdog import run_watchdog
from sift.kernel.jobs.workspaces import Workspaces
from sift.kernel.log import JobCost, costing, get_logger, timing_hook
from sift.kernel.presses import Pressed, pressed_job

log = get_logger(__name__)

# The registry's own objects are read here by name, so they are exported from here too.
__all__ = [
    "TRAILING_RANK",
    "UNDECLARED_RANK",
    "_ALONE",
    "_BY_ITSELF",
    "_CARRIERS",
    "_COUNTS",
    "_EXCLUSIVE",
    "_FAMILIES",
    "_FOLLOWS",
    "_HANDLERS",
    "_HOLDS",
    "_NAMES",
    "_NOT_GATED",
    "_NO_HANDLER",
    "_TRAILS",
    "_UNLISTED",
    "_URGENCY",
    "Handler",
    "JobContext",
    "SystemCapabilities",
    "SystemSecrets",
    "WorkerPool",
    "by_itself_job_types",
    "claim_rank",
    "counted_as",
    "exclusive_job_types",
    "family_of",
    "gated_by_readiness",
    "held_for",
    "hold_on",
    "in_claim_order",
    "job_name",
    "products_named",
    "register_handler",
    "registered_alone",
    "registered_families",
    "registered_follows",
    "registered_handlers",
    "registered_job_names",
    "registered_product_carriers",
    "registered_urgency",
    "unlisted_job_types",
]

_NO_HANDLER = "this job's type no longer exists in this version of Sift"
_WAITING_FOR_LOGIN = "waiting for someone to log in"

_NO_WORKSPACES = (
    "this worker pool was built without a place for a job to keep what it is part way through "
    "writing. Pass capabilities=SystemCapabilities(..., workspaces=Workspaces(path)) to "
    "WorkerPool; the composition root does this at boot (sift/wiring/workers.py)."
)

_PAUSED_MID_JOB = "stopped part way through because somebody paused it"

_NO_CAPABILITIES = (
    "this worker pool was built without system capabilities, so its handlers cannot reach the "
    "content store or the master key. Pass capabilities=SystemCapabilities(...) to WorkerPool; "
    "the composition root does this at boot (sift/wiring/workers.py)."
)


@runtime_checkable
class SystemSecrets(Protocol):
    """The master key a job needs to open a stored secret; None until somebody has logged in."""

    async def master_key(self) -> bytes | None: ...


@dataclass(frozen=True, slots=True)
class SystemCapabilities:
    """What a handler may do as the system: no viewer, so no permission check.

    The one place content is reachable unscoped, so it is handed out only inside a handler.
    """

    content: ContentStore
    #: The roots and the folder tree, whose paths nothing outside the kernel may read.
    library: LibraryStore
    secrets: SystemSecrets | None = None
    #: Where a handler keeps what it is part way through writing, one directory per job.
    workspaces: Workspaces | None = None


@dataclass(slots=True)
class JobContext:
    """What a handler is given: its job, and the few things it may do to it."""

    job: Job
    worker_id: str
    queue: JobQueue
    capabilities: SystemCapabilities | None = None
    #: Who pressed the work this job carries out, or None for Sift's own.
    pressed_by: str | None = None
    _progress_written: float = field(default=-1.0e9, repr=False)
    #: What the handler said the job is about, or the row's own count until it does.
    _units: int | None = field(default=None, repr=False)
    #: Files this job handed to other jobs of its own family. See `units_done`.
    _handed_on: int = field(default=0, repr=False)
    #: Files this job brought into the library for the first time. See `arrived`.
    _arrived: int = field(default=0, repr=False)
    #: The last note this job wrote, for the ledger.
    noted: str | None = field(default=None, repr=False)
    #: What the last heartbeat heard somebody asking of this job. See `stopping`.
    _stop: str | None = field(default=None, repr=False)
    #: Whether this handler asked for its workspace, so one that never did costs no sweep.
    _workspace_used: bool = field(default=False, repr=False)

    @property
    def content(self) -> ContentStore:
        """The content store, unscoped. See `SystemCapabilities` for why a job gets one."""
        if self.capabilities is None:
            raise RuntimeError(_NO_CAPABILITIES)
        return self.capabilities.content

    @property
    def library(self) -> LibraryStore:
        """The roots and the folder tree, unscoped."""
        if self.capabilities is None:
            raise RuntimeError(_NO_CAPABILITIES)
        return self.capabilities.library

    async def master_key(self) -> bytes | None:
        """The master key for the system's secrets, or None to wait on with `JobBlocked`."""
        if self.capabilities is None:
            raise RuntimeError(_NO_CAPABILITIES)
        if self.capabilities.secrets is None:
            return None
        return await self.capabilities.secrets.master_key()

    @property
    def workspace(self) -> Path:
        """A directory of this job's own that outlives the attempt, removed once the job is over."""
        if self.capabilities is None or self.capabilities.workspaces is None:
            raise RuntimeError(_NO_WORKSPACES)
        self._workspace_used = True
        return self.capabilities.workspaces.of(self.job.id)

    @property
    def used_a_workspace(self) -> bool:
        """Whether this handler ever asked for one."""
        return self._workspace_used

    def told_to_stop(self, reason: str | None) -> None:
        """What the last heartbeat heard; the worker pool's to call."""
        self._stop = reason

    def stopping(self) -> str | None:
        """What this job is asked to do as of the last heartbeat: None, `pause` or `cancel`."""
        return self._stop

    @property
    def payload(self) -> dict[str, Any]:
        return self.job.payload

    def require_str(self, key: str, message: str) -> str:
        """A non-empty string from the job payload, or a `ValueError` carrying `message`."""
        value = self.payload.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(message)
        return value

    @property
    def attempt(self) -> int:
        """Which attempt this is, counting from 1."""
        return self.job.attempts

    async def set_progress(self, fraction: float) -> None:
        """Report progress, 0 to 1."""
        await self.queue.set_progress(self.job.id, self.worker_id, fraction)

    async def set_units(self, units: int) -> None:
        """Say how many files this job is about, once that is known: a scan after its walk."""
        self._units = max(0, units)
        await self.queue.set_units(self.job.id, self.worker_id, self._units)

    @property
    def units(self) -> int:
        return self.job.units if self._units is None else self._units

    def arrived(self, count: int) -> None:
        """Say this job brought `count` new files into the library, never a second copy."""
        self._arrived += max(0, count)

    @property
    def files_arrived(self) -> int:
        """What `arrived` has been told, for the ledger."""
        return self._arrived

    @property
    def units_done(self) -> int:
        """How many files this job finished, less what it handed on to its own family."""
        return max(0, self.units - self._handed_on)

    async def report_progress(self, fraction: float) -> None:
        """Report progress from a loop, writing at most once per interval and for the last step."""
        now = time.monotonic()
        if fraction < 1.0 and now - self._progress_written < PROGRESS_INTERVAL_SECONDS:
            return
        self._progress_written = now
        await self.set_progress(fraction)

    async def set_note(self, note: str) -> None:
        """Say what this job did in a sentence, such as finding nothing to do."""
        self.noted = note
        await self.queue.set_note(self.job.id, self.worker_id, note)

    async def enqueue_child(
        self, job_type: str, payload: Mapping[str, Any] | None = None, **options: Any
    ) -> str:
        """Fan out, as a scan hands out a probe per file; a child in its family is work handed on.

        `OTHER` is no family, so there the job type is compared instead.
        """
        mine = family_of(self.job.type)
        same = job_type == self.job.type if mine is Family.OTHER else family_of(job_type) is mine
        if same:
            handed = options.get("units", 1)
            self._handed_on += max(0, handed) if isinstance(handed, int) else 1
        # Work handed on within the family is still the press it came from; another family's is not.
        if self.job.timing is not None and family_of(job_type) is mine:
            options.setdefault("at", self.job.timing)
        return await self.queue.enqueue(job_type, payload, parent_id=self.job.id, **options)

    async def hold_own_family(self, *, spared: Sequence[str]) -> None:
        """Keep this job's family waiting until `lift_own_hold` or the job ends."""
        await self.queue.hold_family(self.job.id, spared=spared)

    def lift_own_hold(self) -> bool:
        return self.queue.lift_hold(self.job.id)

    async def raise_if_canceled(self) -> None:
        """Stop if the job was cancelled or taken away; also a heartbeat, refreshing `stopping`."""
        beat = await self.queue.beat(self.job.id, self.worker_id)
        if beat is None:
            self._stop = STOP_TO_CANCEL
            raise JobCanceled(f"job {self.job.id} is no longer this worker's")
        self._stop = beat.stop


def _check_concurrency(concurrency: int) -> None:
    if concurrency < 1:
        raise ValueError("a worker pool needs at least one worker")


def _check_limits(limits: Mapping[str, int] | None) -> None:
    """Refuse a negative cap; zero means paused, as an overnight-only setting resolves by day."""
    for job_type, limit in (limits or {}).items():
        if limit < 0:
            raise ValueError(
                f"the limit for job type {job_type!r} is {limit}, and a negative cap is not a "
                "smaller one. Use 0 to pause the type, or leave it out to leave it uncapped."
            )


Handler = Callable[[JobContext], Awaitable[None]]


@dataclass(slots=True)
class _Worker:
    """One running worker task and the switch that retires it, without touching the others."""

    task: asyncio.Task[None]
    stop: asyncio.Event


def _settled_as(outcome: str, *, paused: bool, state: JobState | None) -> str:
    """The summary line's word for how a settled job ended."""
    if paused:
        return "paused"
    if state is not None:
        return "failed" if state is JobState.FAILED else "retrying"
    return outcome


class WorkerPool:
    """Runs jobs until told to stop; the worker count and per-type caps change live."""

    def __init__(
        self,
        queue: JobQueue,
        *,
        concurrency: int,
        capabilities: SystemCapabilities | None = None,
        limits: Mapping[str, int] | None = None,
        poll_interval: float = LONGEST_IDLE_SECONDS,
        heartbeat_interval: float = HEARTBEAT_SECONDS,
        shutdown_grace: float = SHUTDOWN_GRACE_SECONDS,
        watchdog: bool = True,
        watchdog_interval: float = SWEEP_INTERVAL_SECONDS,
        stale_after: int = STALE_AFTER_SECONDS,
        read_config: Callable[[], Awaitable[tuple[int, Mapping[str, int]]]] | None = None,
        reconcile_interval: float = RECONFIGURE_SECONDS,
        ledger: Ledger | None = None,
        woken_by: Sequence[Listen] = (),
    ) -> None:
        _check_concurrency(concurrency)
        _check_limits(limits)

        self._queue = queue
        self._concurrency = concurrency
        self._capabilities = capabilities
        self._limits = dict(limits or {})
        self._poll_interval = poll_interval
        self._heartbeat_interval = heartbeat_interval
        self._shutdown_grace = shutdown_grace
        self._watchdog = watchdog
        self._watchdog_interval = watchdog_interval
        self._stale_after = stale_after
        self._read_config = read_config
        self._reconcile_interval = reconcile_interval
        self._ledger = ledger
        self._stop = asyncio.Event()
        self._waking = Waking(woken_by)
        #: Changed only by synchronous code, so never under an await and never locked.
        self._workers: list[_Worker] = []
        #: Workers draining a last job, held so shutdown can wait for them.
        self._retiring: list[_Worker] = []
        self._watchdog_task: asyncio.Task[None] | None = None
        self._supervisor_task: asyncio.Task[None] | None = None
        #: The switch that makes a running job's heartbeat beat now, so a stop lands in one beat.
        self._wake: dict[str, asyncio.Event] = {}
        self._unlisten: Callable[[], None] | None = None
        self.holding = Holding(self._waking.wake_all)

    @property
    def concurrency(self) -> int:
        """How many workers are meant to be running right now."""
        return self._concurrency

    @property
    def limits(self) -> dict[str, int]:
        """The per-type caps in force right now, copied."""
        return dict(self._limits)

    @property
    def workspaces(self) -> Workspaces | None:
        """Where each job keeps what it has half-written: the instance the handlers are handed."""
        return None if self._capabilities is None else self._capabilities.workspaces

    async def start(self) -> None:
        if self._workers or self._watchdog_task or self._supervisor_task:
            raise RuntimeError("the worker pool is already running")

        self._stop.clear()
        await self._sweep_stale_workspaces()
        self._unlisten = self._queue.listen_for_stops(self._stop_asked)
        self._waking.listen(
            self._queue.listen_for_work,
            self._queue.listen_for_anything,
            self._queue.listen_for_retime,
        )
        for _ in range(self._concurrency):
            self._spawn_worker()
        if self._watchdog:
            self._watchdog_task = asyncio.create_task(
                run_watchdog(
                    self._queue,
                    self._stop,
                    stale_after=self._stale_after,
                    interval=self._watchdog_interval,
                ),
                name="jobs.watchdog",
            )
        if self._read_config is not None:
            self._supervisor_task = asyncio.create_task(
                self._supervise(self._read_config), name="jobs.pool.super"
            )
        log.info("jobs.pool.start", worker_count=self._concurrency, limits=self._limits)

    async def _sweep_stale_workspaces(self) -> None:
        """At boot, while nothing is claimed, remove the directories of jobs not coming back."""
        workspaces = None if self._capabilities is None else self._capabilities.workspaces
        if workspaces is None:
            return
        try:
            named = await asyncio.to_thread(_directories_under, workspaces.root)
            if named:
                workspaces.sweep_all_but(await self._queue.unfinished_among(named))
        except FileNotFoundError:
            return  # nothing has ever asked for a workspace on this machine
        except Exception:
            log.exception("jobs.workspaces.sweep_failed")

    async def stop(self) -> None:
        """Stop taking work, let what is in flight finish, then go.

        A job cut off by the shutdown gets its attempt back: the interruption was our own.
        """
        self._stop.set()
        tasks = [worker.task for worker in (*self._workers, *self._retiring)]
        if self._watchdog_task is not None:
            tasks.append(self._watchdog_task)
        if self._supervisor_task is not None:
            tasks.append(self._supervisor_task)
        self._workers = []
        self._retiring = []
        self._watchdog_task = None
        self._supervisor_task = None
        if not tasks:
            self._stop_listening()
            return

        _, unfinished = await asyncio.wait(tasks, timeout=self._shutdown_grace)
        for task in unfinished:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._stop_listening()
        # After the gather, so only what the shutdown interrupted is still running.
        await self._queue.release_running()
        log.info("jobs.pool.stop")

    def _stop_listening(self) -> None:
        if self._unlisten is not None:
            self._unlisten()
            self._unlisten = None
        self._waking.stop_listening()

    def _stop_asked(self, job_ids: Sequence[str]) -> None:
        """Somebody asked these jobs to stop: beat now for any this pool is running."""
        for job_id in job_ids:
            wake = self._wake.get(job_id)
            if wake is not None:
                wake.set()

    # --- live reconfigure ------------------------------------------------------------------

    def reconcile(
        self, *, concurrency: int | None = None, limits: Mapping[str, int] | None = None
    ) -> None:
        """Bring the pool in line with a new worker count or caps; synchronous and idempotent."""
        if self._stop.is_set():
            return
        if limits is not None:
            self._set_limits(limits)
        if concurrency is not None:
            self._set_concurrency(concurrency)

    def _set_limits(self, limits: Mapping[str, int]) -> None:
        _check_limits(limits)
        before = self._limits
        raised = any(
            job_type not in limits or limits[job_type] > limit for job_type, limit in before.items()
        )
        # Rebound, never mutated: a claim reads the old dict across awaits.
        self._limits = dict(limits)
        if raised:
            # A cap raised or lifted frees work no arrival will name.
            self._waking.wake_all()

    def _set_concurrency(self, target: int) -> None:
        _check_concurrency(target)
        self._reap_retired()
        current = len(self._workers)
        if target > current:
            for _ in range(target - current):
                self._spawn_worker()
        elif target < current:
            retiring = self._workers[target:]
            self._workers = self._workers[:target]
            for worker in retiring:
                # Not cancelled, which would abandon its job to be redone at the next boot.
                worker.stop.set()
            self._retiring.extend(retiring)
        self._concurrency = target

    def _spawn_worker(self) -> None:
        stop = asyncio.Event()
        worker_id = new_id()
        task = asyncio.create_task(self._work(worker_id, stop), name=f"jobs.worker.{worker_id}")
        self._workers.append(_Worker(task=task, stop=stop))

    def _reap_retired(self) -> None:
        """Drop the retiring workers that have finished draining."""
        self._retiring = [worker for worker in self._retiring if not worker.task.done()]

    def work_arrived(self) -> None:
        self._waking.work_arrived()

    async def _supervise(
        self, read_config: Callable[[], Awaitable[tuple[int, Mapping[str, int]]]]
    ) -> None:
        """Poll the config reader and converge the pool on it; a bad settings row is only logged."""
        while not self._stop.is_set():
            await first_of((self._stop, self._waking.reconfigure), self._reconcile_interval)
            if self._stop.is_set():
                continue  # shutdown began during the wait; the loop condition ends it
            # Cleared before the read, so a press landing during it is not lost.
            self._waking.reconfigure.clear()
            try:
                concurrency, limits = await read_config()
                self.reconcile(concurrency=concurrency, limits=limits)
            except Exception:
                log.exception("jobs.pool.reconcile_failed")

    # --- The loop --------------------------------------------------------------------------

    async def _work(self, worker_id: str, own_stop: asyncio.Event) -> None:
        """Claim, run, settle, repeat: an error escaping here would lose the worker for good."""
        while not self._stop.is_set() and not own_stop.is_set():
            try:
                if self.holding.held_all:
                    await self._idle(own_stop)
                    continue
                job = await self._queue.claim(
                    worker_id,
                    limits=self.holding.caps(self._limits),
                    held_products=self.holding.products,
                )
                if job is None:
                    await self._idle(own_stop)
                    continue
                await self._run(job, worker_id)
            except Exception:
                log.exception("jobs.worker_failed", worker_id=worker_id)
                await self._idle(own_stop)

    async def _idle(self, own_stop: asyncio.Event) -> None:
        """Wait for work a worker could take, a stop, or the next row put off to a moment, whichever
        is first; `poll_interval` at the longest, so a missed wake costs that, never a stall."""
        while True:
            due = await self._queue.seconds_until_due()
            within = self._poll_interval if due is None else min(self._poll_interval, due)
            if await self._waking.idle((self._stop, own_stop), within):
                return

    async def _run(self, job: Job, worker_id: str) -> None:
        # Asked again: a switch turned off must stop rows already queued. Cancelled, not failed.
        refused = await self._queue.switchboard.refusal(job.type, pressed=job.timing is not None)
        if refused is not None:
            log.info("job.switched_off", job_id=job.id, job_type=job.type, worker_id=worker_id)
            await self._queue.cancel(job.id)
            return

        handler = _HANDLERS.get(job.type)
        if handler is None:
            await self._queue.fail(job.id, worker_id, _NO_HANDLER, permanent=True)
            return

        context = JobContext(
            job=job,
            worker_id=worker_id,
            queue=self._queue,
            capabilities=self._capabilities,
            pressed_by=await self._pressed_by(job),
        )
        if self._ledger is not None:
            self._ledger.started(
                job.type,
                requested_by=job.requested_by,
                products=products_named(job.type, job.payload),
            )
        began = time.monotonic()
        wake = self._wake[job.id] = asyncio.Event()
        # The runner's tasks and threads file their time to this job; the heartbeat's do not.
        cost = JobCost()
        with costing(cost):
            runner = asyncio.create_task(self._invoke(handler, context), name=f"job.{job.type}")
        beat = asyncio.create_task(self._beat(context, wake), name=f"job.beat.{job.id}")

        try:
            await asyncio.wait({runner, beat}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            # A reclaim by another worker of this pool owns the event now.
            if self._wake.get(job.id) is wake:  # pragma: no branch
                del self._wake[job.id]
            beat.cancel()
            if not runner.done():
                runner.cancel()
            await asyncio.gather(runner, beat, return_exceptions=True)

        if runner.cancelled():
            # The job was cancelled or taken back: write nothing.
            log.info("job.lost", job_id=job.id, job_type=job.type, worker_id=worker_id)
            _summarize(job, cost, "lost")
            await self._sweep_workspace(context)
            return

        with costing(cost):
            outcome = await self._record(
                job,
                worker_id,
                runner.exception(),
                took_ms=(time.monotonic() - began) * 1000,
                units=context.units_done,
                arrived=context.files_arrived,
                pressed=pressed_job(job.type, job.payload, context.pressed_by, job.started_at),
                noted=context.noted,
            )
        _summarize(job, cost, outcome)
        await self._sweep_workspace(context)

    async def _pressed_by(self, job: Job) -> str | None:
        """Who pressed the work this job carries out, or the press at the top of its family."""
        if job.requested_by is not None:
            return job.requested_by
        if job.timing is None:
            return None
        top = await self._queue.top_of(job.id)
        if top is None:
            return None
        top_type, presser = top
        return presser if family_of(top_type) is family_of(job.type) else None

    async def _sweep_workspace(self, context: JobContext) -> None:
        """Remove this job's working directory, unless the row says the job is coming back to it."""
        workspaces = None if self._capabilities is None else self._capabilities.workspaces
        if workspaces is None or not context.used_a_workspace:
            return
        job = await self._queue.get(context.job.id)
        if job is None or job.state in TERMINAL_STATES:
            workspaces.sweep(context.job.id)

    async def _record(
        self,
        job: Job,
        worker_id: str,
        error: BaseException | None,
        *,
        took_ms: float = 0.0,
        units: int = 1,
        arrived: int = 0,
        pressed: Pressed | None = None,
        noted: str | None = None,
    ) -> str:
        """Settle the job's row; what became of it, in the summary line's word."""
        if isinstance(error, JobCanceled):
            log.info("job.lost", job_id=job.id, job_type=job.type, worker_id=worker_id)
            return "lost"

        paused = False
        held = False
        state: JobState | None = None
        outcome = "done"
        if isinstance(error, JobPaused):
            landed = await self._queue.pause_running(
                job.id, worker_id, str(error) or _PAUSED_MID_JOB
            )
            paused = landed
        elif error is None:
            # Finished work is done, whatever pause was asked meanwhile.
            landed = await self._queue.complete(job.id, worker_id, pressed=pressed)
        elif isinstance(error, JobBlocked):
            landed = await self._queue.block(job.id, worker_id, str(error) or _WAITING_FOR_LOGIN)
            outcome = "blocked"
        elif (hold := held_for(error)) is not None:
            held = await self._queue.hold(job.id, worker_id, str(error), retry_in=hold)
            landed = held
            outcome = "held"
        elif isinstance(error, JobFailedPermanently) or cannot_change(error):
            state = await self._queue.fail(job.id, worker_id, str(error), permanent=True)
            landed = state is not None
        else:
            # A pause asked since the last heartbeat makes `fail` answer `paused`.
            failed = f"{type(error).__name__}: {error}"
            wait = backoff(job.type, job.attempts, error)
            state = await self._queue.fail(job.id, worker_id, failed, retry_in=wait)
            landed = state is not None
            paused = state is JobState.PAUSED
        outcome = _settled_as(outcome, paused=paused, state=state)

        if not landed:
            # The fence refused it: the job stopped being this worker's while it finished.
            log.info("job.lost", job_id=job.id, job_type=job.type, worker_id=worker_id)
            return "lost"
        # Paused, blocked and held attempts would drag every estimate of what work costs.
        if (
            self._ledger is not None
            and not isinstance(error, JobBlocked)
            and not paused
            and not held
        ):
            await self._account(
                self._ledger,
                job,
                took_ms=took_ms,
                ok=error is None,
                units=units,
                arrived=arrived,
                failed_with=f"{type(error).__name__}: {error}"
                if state is JobState.FAILED
                else None,
                noted=noted,
            )
        return outcome

    async def _account(
        self,
        ledger: Ledger,
        job: Job,
        *,
        took_ms: float,
        ok: bool,
        units: int = 1,
        arrived: int = 0,
        failed_with: str | None = None,
        noted: str | None = None,
    ) -> None:
        """Tell the ledger what this job was about; never the reason a job fails."""
        media_type: str | None = None
        size_bytes: int | None = None
        asset_id = job.payload.get("asset_id")
        if isinstance(asset_id, str) and self._capabilities is not None:
            try:
                asset = await self._capabilities.content.get(asset_id)
            except Exception:
                log.exception("ledger.subject_unread", job_id=job.id)
            else:
                if asset is not None:
                    media_type = str(asset.media_type)
                    size_bytes = asset.size_bytes
        ledger.finished(
            job.type,
            duration_ms=took_ms,
            ok=ok,
            media_type=media_type,
            size_bytes=size_bytes,
            units=units,
            arrived=arrived,
            failed_with=failed_with,
            noted=noted,
        )

    async def _invoke(self, handler: Handler, context: JobContext) -> None:
        # So the timing hook files each stage against its run, unknown to the handler.
        token = CURRENT_FAMILY.set(family_of(context.job.type))
        asked_at = ASKED_AT.set(context.job.priority)
        try:
            # Every record the handler writes names its job, so a stage joins to it.
            with (
                structlog.contextvars.bound_contextvars(
                    job_id=context.job.id, job_type=context.job.type
                ),
                timing_hook("job", job_type=context.job.type, job_id=context.job.id),
            ):
                await handler(context)
        finally:
            CURRENT_FAMILY.reset(token)
            ASKED_AT.reset(asked_at)
            self._queue.lift_hold(context.job.id)

    async def _beat(self, context: JobContext, wake: asyncio.Event | None = None) -> None:
        """Keep saying the job is alive, and return the moment it is no longer ours.

        A beat that fails to write is not that: the watchdog reclaims a job whose beats never land.
        """
        job_id, worker_id = context.job.id, context.worker_id
        wake = wake or asyncio.Event()
        while True:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(wake.wait(), timeout=self._heartbeat_interval)
            wake.clear()
            try:
                beat = await self._queue.beat(job_id, worker_id)
            except Exception:
                log.exception("job.heartbeat_failed", job_id=job_id, worker_id=worker_id)
                continue
            if beat is None:
                context.told_to_stop(STOP_TO_CANCEL)
                return
            context.told_to_stop(beat.stop)


def _queued_s(job: Job) -> int | None:
    """Seconds from when the job could first be claimed to its claim."""
    if job.started_at is None:
        return None
    ready = max(job.created_at, job.run_after or 0)
    return max(0, job.started_at - ready)


def _summarize(job: Job, cost: JobCost, outcome: str) -> None:
    """The job's one line: what became of it and where its time went."""
    asset_id = job.payload.get("asset_id")
    log.info(
        "job.summary",
        job_id=job.id,
        job_type=job.type,
        asset_id=asset_id if isinstance(asset_id, str) else None,
        attempt=job.attempts,
        outcome=outcome,
        queued_s=_queued_s(job),
        **cost.summary(),
    )


def _directories_under(root: Path) -> list[str]:
    """The names of the directories directly under `root`."""
    return [entry.name for entry in root.iterdir() if entry.is_dir()]
