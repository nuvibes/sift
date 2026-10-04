# SPDX-License-Identifier: AGPL-3.0-or-later
"""The dry run: a task's plan worked out as a job, and said, with nothing written.

A job rather than an answer to the request, because the plan of a pass over the library reads the
whole library, and a request that did that would hold the screen for as long as it took. As a job
it is on Activity while it runs, can be stopped there, and leaves its report on the row it
finished, where the task's row on Tasks reads it back.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sift.kernel.jobs import JobContext, register_handler
from sift.kernel.log import get_logger
from sift.slices.tasks.parts import DryReport, Selection

log = get_logger(__name__)

TASK_DRY_RUN = "task_dry_run"

#: Answers a dry run's report for one task and selection, as the user who pressed, or raises.
Rehearse = Callable[[str, Selection, str | None], Awaitable[DryReport]]


def selection_of(payload: dict[str, object]) -> Selection:
    """The part of the task a dry run's payload names; absent keys are the whole of it."""
    parts = payload.get("parts")
    folders = payload.get("locations")
    return Selection(
        parts=tuple(str(one) for one in parts) if isinstance(parts, list) else None,
        locations=tuple(str(one) for one in folders) if isinstance(folders, list) else None,
    )


def register_handlers(rehearse: Rehearse) -> None:
    """Claim the dry run's job type, answered by the tasks service's own plan."""

    async def handle(context: JobContext) -> None:
        task_id = str(context.payload.get("task") or "")
        report = await rehearse(task_id, selection_of(context.payload), context.job.requested_by)
        await context.set_progress(1.0)
        # As fields, so the task's row can lay the report out; History reads its sentence.
        await context.set_note(report.note())
        log.info("tasks.dry_run", task=task_id)

    register_handler(TASK_DRY_RUN, handle, name="Working out what a task would do")
