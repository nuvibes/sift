# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where a timed task's next run is placed: one scheduler, for every task that runs on a clock.

A timed task (the backup, the two clean-ups, the update check) is a row in the queue with a
time on it. Three writers of that row (boot, the end of its own run, the route that saves its
schedule), each with its own idea of the moment, would leave a backup changed from daily to weekly
pending at its old time, and one switched off queued to wake up and log "off".

So the moment is decided HERE and nowhere else, from the task's declaration and its When:

* **After a run settles** (`settled`) the next is placed from when the last run THE SCHEDULE
  STARTED ended, unless something is already waiting: a run the task put back itself, a retry.
* **When its settings or quiet hours change** (`reschedule`) the waiting row is taken back and the
  next is placed again, so the new answer is the one that runs, and "Only when I press it" leaves
  nothing waiting at all.
* **At boot** (`ensure_all`) every timed task is checked the same way `settled` checks it.

A press is never touched: a Run now waiting in the queue is somebody's decision, not the schedule's.

AND A PRESS NEVER MOVES THE SCHEDULE. "Every day at 3 PM" means 3 PM whatever was pressed in
between, so every placement (a settle, a setting change, a boot) counts from the last run the
schedule itself started: a head row nobody pressed (`requested_by` and `timing` both empty).
Counted from a press, a backup pressed at four in the morning would put that afternoon's off to the
next day, and a setting changed and put back would move it again. The one place a press counts is
a run already due: a schedule that is behind (the device off at its time) or has never run catches
up immediately, and a press that began after the run fell due IS that catch-up, so the schedule goes on
from it rather than running a second one straight after.

The scheduler holds no timer. The row with its `run_after` is still what is going to happen, and a
device that was off comes back to a row whose moment has passed and runs it. Quiet hours hold it
until the range opens if that is its When, which is what the claim already does for any work held
to them.
"""

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

#: Reads one app setting by key. The settings store's `get_app`, handed in.
ReadSetting = Callable[[str], Awaitable[Any]]

#: Reads quiet hours as the two `HH:MM` strings the person set.
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
            # Always true of a registered task (`register_schedule` refuses one with no job type)
            # and asked only because the declaration's type allows None.
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
        """When this task should next run on its own, or None: press-only, or nothing to do.

        `since` is when the last run ended; left out, it is read from the queue's record of the
        task's runs, and only of the runs its schedule started (the module's docstring says why).
        A task the schedule has never run falls due immediately (in quiet hours, at the range's next
        opening; with a time of day, at its next one), which is what somebody switching a daily
        backup on this afternoon expects: tonight, not tomorrow night.

        A run that is due NOW (behind, or never run, with no time of day to wait for) is the one
        case a press is read: a press that began after the run fell due is that run, and the
        next is placed from its end. A run still to come is never moved by a press.
        """
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
        # When the schedule's run fell due, unclamped: a run falls due at least half a cadence
        # after the last one ended, so placing it "as of" that end never clamps it.
        if own_end is not None and pressed.started_at < placed(own_end, own_end):
            return moment
        return placed(pressed.finished_at, now)

    async def ensure(
        self, task_id: str, *, since: int | None = None, move: bool = False
    ) -> int | None:
        """Put the next run in the queue if none is waiting. Returns its moment, or None.

        A task that must not run on its own has anything waiting taken back instead, so switching a
        task to "Only when I press it" by any door cannot leave a last run behind it. With `move`,
        a run already waiting is moved to the moment worked out now. See `reschedule`; without
        it, a run already waiting is left where it is (a run the task put back itself, a retry).
        """
        task = get_schedule(task_id)
        if task is None or task.every is None or task.job_type is None:
            return None
        moment = await self.next_run(task, since=since)
        if moment is None:
            await self._queue.withdraw_waiting(task.job_type)
            return None
        waiting = await self._queue.waiting_unpressed(task.job_type)
        if len(waiting) > 1 and move:
            # Two of one schedule waiting is one too many however it happened; one is placed again.
            await self._queue.withdraw_waiting(task.job_type)
            waiting = []
        if waiting:
            if move and waiting[0].run_after != moment:
                await self._queue.retime_waiting(waiting[0].id, moment)
            return moment if move else waiting[0].run_after
        if await self._pending(task.job_type):
            # A press is waiting, or a run is under way; the next run is placed once it has
            # settled (`settled`). A run placed beside one already running is a second backup
            # queued behind the one somebody pressed a moment ago.
            return moment
        await self._queue.enqueue(task.job_type, dict(task.payload), run_after=moment)
        log.info("tasks.scheduled", task=task.id, run_after=moment)
        return moment

    async def reschedule(self, task_id: str) -> int | None:
        """Place the next run again, from the settings as they are now, moving the one waiting.

        What a schedule change, a When change, a change to quiet hours and every start call. The
        run that was waiting was placed from the OLD answer; keeping it would run a daily-to-weekly
        change once more at the daily time, and "Only when I press it" takes it back.
        """
        return await self.ensure(task_id, move=True)

    async def reschedule_reading(self, keys: set[str]) -> None:
        """Reschedule every timed task that reads any of these settings."""
        for task in self.timed():
            if keys & set(task.keys_read()):
                await self.reschedule(task.id)

    async def reschedule_all(self) -> None:
        """Reschedule every timed task: quiet hours moved, and every quiet-hours moment with them."""
        for task in self.timed():
            await self.reschedule(task.id)

    async def ensure_all(self) -> None:
        """At boot: every timed task's next run placed from the settings as they are now.

        Moved rather than only topped up, so a run placed by an older version of Sift (or before
        quiet hours moved while Sift was closed) is at the moment the task's When says.
        """
        for task in self.timed():
            try:
                await self.reschedule(task.id)
            except Exception:
                # One task's settings failing to read must not leave every other task unscheduled.
                log.exception("tasks.schedule_failed", task=task.id)

    async def settled(self, job_id: str) -> None:
        """A run of a timed task has ended: place the next one from when the schedule's last ended.

        A run the schedule started is that last run, so the next is placed from its end. A press
        is not: the next is placed from the schedule's own record, as a boot or a setting change
        places it (`next_run`), so a press settling leaves the schedule where it was.

        A waiting row that was taken back before it ever ran is not a run: somebody cancelled the
        coming backup, and the next is placed from NOW rather than put straight back where it was,
        which would be a cancel that cancelled nothing.
        """
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
    """Make this the scheduler a slice's own route reaches. Called once, by the composition root.

    A slice may not import the one that builds this, and a route that saves a schedule of its own
    (the backup's) has to be able to say "that moved", so it is installed here, the way the
    landing step is, and a process that never installed one simply schedules nothing.
    """
    global _INSTALLED
    _INSTALLED = clock


async def reschedule(task_id: str) -> int | None:
    """Reschedule one task through the installed scheduler. None when nothing is installed."""
    if _INSTALLED is None:
        return None
    return await _INSTALLED.reschedule(task_id)
