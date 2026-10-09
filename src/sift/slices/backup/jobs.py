# SPDX-License-Identifier: AGPL-3.0-or-later
"""The scheduled backup, as a queued job so a machine that was off catches up."""

from __future__ import annotations

from sift.kernel.jobs import JobContext, JobQueue, register_handler
from sift.kernel.log import get_logger
from sift.slices.backup.service import BackupService, Busy

log = get_logger(__name__)

BACKUP_RUN = "backup_run"

#: How long a backup that comes due during a restore, duplicate or switch waits.
BUSY_RETRY_SECONDS = 10 * 60

__all__ = [
    "BACKUP_RUN",
    "BUSY_RETRY_SECONDS",
    "register_handlers",
    "run_backup",
]


async def run_backup(context: JobContext, *, service: BackupService, queue: JobQueue) -> None:
    """Take one backup and rotate the folder; a press runs whatever the schedule says."""
    if context.job.timing is None and not await service.starts_on_its_own():
        log.info("backup.schedule.off")
        return

    try:
        # A press is a backup made by hand, kept as one: named so and never rotated away.
        saved = await service.run_scheduled(pressed=context.job.timing is not None)
    except Busy:
        # Put back with the same timing, so a press stays a press and no second run is placed.
        log.info("backup.deferred_while_busy", seconds=BUSY_RETRY_SECONDS)
        await queue.enqueue(
            BACKUP_RUN,
            run_after=int(service.now()) + BUSY_RETRY_SECONDS,
            at=context.job.timing,
        )
        return
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

    register_handler(BACKUP_RUN, handle, name="Backing up", unlisted=True)
