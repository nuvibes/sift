# SPDX-License-Identifier: AGPL-3.0-or-later
"""The update check, as a job: one read of the release feed, and a sentence saying what it found.

A job rather than a timer for the reason every recurring thing here is one: the moment it next runs
is a row in the queue, placed by the one scheduler from the task's When, and a device that was off
comes back to a row whose time has passed. Its sentence is the task's "last ran" on the Tasks screen,
read from the job row; a check writes no line in the history (see the task's declaration).
"""

from __future__ import annotations

from sift.kernel.jobs import JobContext, register_handler
from sift.slices.update_notify.service import UpdateService

UPDATE_CHECK = "update_check"

__all__ = ["UPDATE_CHECK", "register_handlers"]


def register_handlers(*, service: UpdateService) -> None:
    """Claim the check's job type. Called once, at boot, before the workers start."""

    async def handle(context: JobContext) -> None:
        said = await service.check()
        await context.set_progress(1.0)
        await context.set_note(said)

    # Upkeep, left off Activity's list of what is happening now: the Tasks row is where it is
    # read, and its answer is the update banner.
    register_handler(UPDATE_CHECK, handle, name="Checking for a new version", unlisted=True)
