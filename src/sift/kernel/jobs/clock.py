# SPDX-License-Identifier: AGPL-3.0-or-later
"""The one scheduler placing each timed task's next run, from its declaration and its When.

A press never moves the schedule: placements count from the last run the schedule started."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from sift.kernel.jobs.queue import (
    STARTED_BY_PRESS,
    STARTED_BY_SCHEDULE,
    JobQueue,
    JobState,
    TaskRun,
)
from sift.kernel.jobs.quiet_hours import WHEN_PRESS, due_at
from sift.kernel.jobs.schedules import ScheduledTask, get_schedule, registered_schedules
from sift.kernel.log import get_logger

log = get_logger(__name__)

#: Reads one app setting by key.
ReadSetting = Callable[[str], Awaitable[Any]]

#: Reads quiet hours as two `HH:MM` strings.
ReadRange = Callable[[], Awaitable[tuple[str, str]]]


class TaskClock:
    """Places each timed task's next run. One per application, built beside the queue."""

    def __init__(
        self,
        queue: JobQueue,
        *,
        read: ReadSetting,
        quiet_range: ReadRange,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._queue = queue
        self._read = read
        self._range = quiet_range
        self._clock = clock
        for task in self.timed():
            # Always true of a registered task; the type merely allows None.
            if task.job_type is not None:  # pragma: no branch
                queue.listen_for_settled(task.job_type, self.settled)

    @staticmethod
    def timed() -> list[ScheduledTask]:
        """Every declared task that runs on a clock, in declaration order."""
        return [task for task in registered_schedules().values() if task.every is not None]

    async def values(self, task: ScheduledTask) -> dict[str, Any]:
        """Every setting the task's answers depend on, read now."""
        return {key: await self._read(key) for key in task.keys_read()}

    async def next_run(self, task: ScheduledTask, *, since: int | None = None) -> int | None:
        """When this task next runs on its own, or None; a press counts only for a run due now."""
        values = await self.values(task)
        seconds = task.interval(values)
        job_type = task.job_type
        if seconds is None or task.when(values) == WHEN_PRESS or job_type is None:
            return None
        now = int(self._clock())
        start, end = await self._range()
        when, at = task.when(values), task.time_of_day(values)

        def placed(after: int | None, at_the_earliest: int) -> int:
            return due_at(
                when=when,
                every=seconds,
                since=after,
                now=at_the_earliest,
                start=start,
                end=end,
                at=at,
            )

        if since is not None:
            return placed(since, now)
        own = await self._last_finished(job_type, STARTED_BY_SCHEDULE)
        own_end = None if own is None else own.finished_at
        moment = placed(own_end, now)
        if moment > now:
            return moment
        pressed = await self._last_finished(job_type, STARTED_BY_PRESS)
        if pressed is None or pressed.started_at is None or pressed.finished_at is None:
            return moment
        # When the schedule's run fell due, unclamped.
        if own_end is not None and pressed.started_at < placed(own_end, own_end):
            return moment
        return placed(pressed.finished_at, now)

    async def ensure(
        self, task_id: str, *, since: int | None = None, move: bool = False
    ) -> int | None:
        """Queue the next run if none waits; press-only withdraws it, `move` retimes the waiting."""
        task = get_schedule(task_id)
        if task is None or task.every is None or task.job_type is None:
            return None
        moment = await self.next_run(task, since=since)
        if moment is None:
            await self._queue.withdraw_waiting(task.job_type)
            return None
        waiting = await self._queue.waiting_unpressed(task.job_type)
        if len(waiting) > 1 and move:
            # Two of one schedule waiting is one too many; one is placed again.
            await self._queue.withdraw_waiting(task.job_type)
            waiting = []
        if waiting:
            if move and waiting[0].run_after != moment:
                await self._queue.retime_waiting(waiting[0].id, moment)
            return moment if move else waiting[0].run_after
        if await self._pending(task.job_type):
            # A press waits or a run is under way; the next is placed once it settles.
            return moment
        await self._queue.enqueue(task.job_type, dict(task.payload), run_after=moment)
        log.info("tasks.scheduled", task=task.id, run_after=moment)
        return moment

    async def reschedule(self, task_id: str) -> int | None:
        """Place the next run again from the settings now, moving the one waiting."""
        return await self.ensure(task_id, move=True)

    async def reschedule_reading(self, keys: set[str]) -> None:
        """Reschedule every timed task that reads any of these settings."""
        for task in self.timed():
            if keys & set(task.keys_read()):
                await self.reschedule(task.id)

    async def reschedule_all(self) -> None:
        """Reschedule every timed task, as quiet hours moved."""
        for task in self.timed():
            await self.reschedule(task.id)

    async def ensure_all(self) -> None:
        """At boot: every timed task's next run placed again from the settings now."""
        for task in self.timed():
            try:
                await self.reschedule(task.id)
            except Exception:
                # One task failing must not leave the others unscheduled.
                log.exception("tasks.schedule_failed", task=task.id)

    async def settled(self, job_id: str) -> None:
        """A timed run ended: place the next from the schedule's own record, a cancel from now."""
        job = await self._queue.get(job_id)
        if job is None:
            return
        task = next((one for one in self.timed() if one.job_type == job.type), None)
        if task is None:
            return
        if job.requested_by is not None or job.timing is not None:
            await self.ensure(task.id)
            return
        ran = job.started_at is not None or job.state is not JobState.CANCELED
        await self.ensure(task.id, since=job.updated_at if ran else int(self._clock()))

    async def _pending(self, job_type: str) -> bool:
        """Whether a run of this task is waiting or running: either settles into `settled`."""
        for state in (JobState.QUEUED, JobState.RUNNING):
            page = await self._queue.list(job_type=job_type, state=state, limit=1)
            if page.total > 0:
                return True
        return False

    async def _last_finished(self, job_type: str, started_by: str) -> TaskRun | None:
        """The last run of this type that ended, started by the schedule or by a press."""
        runs = await self._queue.last_finished_runs([job_type], started_by=started_by)
        return runs.get(job_type)


_INSTALLED: TaskClock | None = None


def install(clock: TaskClock | None) -> None:
    """Install the scheduler a slice's own route reaches; called once by the composition root."""
    global _INSTALLED
    _INSTALLED = clock


async def reschedule(task_id: str) -> int | None:
    """Reschedule one task through the installed scheduler. None when nothing is installed."""
    if _INSTALLED is None:
        return None
    return await _INSTALLED.reschedule(task_id)
