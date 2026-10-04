# SPDX-License-Identifier: AGPL-3.0-or-later
"""The scheduled backup, as a job rather than a timer.

A timer lives in the process, so a machine that was switched off overnight comes back having
simply missed its backup, and nothing anywhere says so. A row in the queue with a time on it
outlives the process: the moment passes while the machine is off, and the first worker to look
after it comes back finds a job whose time is long gone and runs it. Catching up is not a feature
written here, it is what a claimable-after column already does.

WHEN the next one runs is not decided here. The backup is a task (see the slice's declaration), and
every timed task's next run is placed by the one scheduler (`kernel.jobs.clock`): from the moment
this run ends, at its time of day on a schedule, at the opening of quiet hours when that is its
When, and not at all when only a press runs it. A change to the schedule takes the waiting run back
and places it again, so daily-to-weekly moves it and Only when I press it leaves nothing behind
rather than a run that wakes at its old time only to log "off".
"""

from __future__ import annotations

from sift.kernel.jobs import JobContext, JobQueue, register_handler
from sift.kernel.log import get_logger
from sift.slices.backup.service import BackupService, Busy

log = get_logger(__name__)

BACKUP_RUN = "backup_run"

#: How long a scheduled backup waits when it comes due while the library is being restored,
#: duplicated or switched. Long enough for most of those to finish; a backup ten minutes late is
#: still the backup somebody scheduled.
BUSY_RETRY_SECONDS = 10 * 60

__all__ = [
    "BACKUP_RUN",
    "BUSY_RETRY_SECONDS",
    "register_handlers",
    "run_backup",
]


async def run_backup(context: JobContext, *, service: BackupService, queue: JobQueue) -> None:
    """Take one backup and rotate the folder. The next is placed by the scheduler when this ends.

    A PRESS RUNS WHATEVER THE SCHEDULE SAYS. "Run now" on a backup set to "Only when I press it"
    is somebody asking for a backup now, and must not return having saved nothing. Only a run
    nobody pressed asks whether the When still starts it on its own. The path it wrote is
    not logged: the destination is a folder somebody chose and its name is as revealing as anything
    else about them.
    """
    if context.job.timing is None and not await service.starts_on_its_own():
        log.info("backup.schedule.off")
        return

    try:
        # A press is a backup made by hand, kept as one: named so and never rotated away.
        saved = await service.run_scheduled(pressed=context.job.timing is not None)
    except Busy:
        # Put back a little later rather than failed: nothing is wrong with the backup, and the
        # library is being restored, duplicated or switched. With the SAME timing, so a press stays
        # a press and a scheduled run stays one; and because a row the task put back itself is
        # waiting, the scheduler places no second one beside it (`TaskClock.settled`).
        log.info("backup.deferred_while_busy", seconds=BUSY_RETRY_SECONDS)
        await queue.enqueue(
            BACKUP_RUN,
            run_after=int(service.now()) + BUSY_RETRY_SECONDS,
            at=context.job.timing,
        )
        return
    # A PRESS IS SAID ON HISTORY AS THE PERSON'S OWN SAVE, the line the pane's Save a backup writes,
    # with the folder it went to (`record_saved`). A run nobody pressed says nothing there: the
    # Tasks row and the Backup pane say how the last one went.
    if context.job.timing is not None and context.pressed_by is not None:
        await service.record_saved(saved, context.pressed_by)
    # The run's own sentence: the file it saved, by name (never its path), which the Tasks row and
    # the Backup pane say as the last run.
    await context.set_note(f"Saved {saved.name}.")
    await context.set_progress(1.0)


def register_handlers(*, service: BackupService, queue: JobQueue) -> None:
    """Claim the backup job type. Called once, at boot, before anything can enqueue one."""

    async def handle(context: JobContext) -> None:
        await run_backup(context, service=service, queue=queue)

    # Upkeep, left off Activity's list of what is happening now: the Tasks row and the Backup pane
    # say how the last one went, and History says the file a press saved.
    register_handler(BACKUP_RUN, handle, name="Backing up", unlisted=True)
