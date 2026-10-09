# SPDX-License-Identifier: AGPL-3.0-or-later
"""Agreeing with every proposal a press named for one person, as a task: the press is answered
with the count immediately and the agreement runs here, where the Task Queue shows it."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sift.kernel.jobs import JobContext, JobQueue, register_handler
from sift.kernel.log import get_logger
from sift.slices.faces.jobs import ask_for_rematching
from sift.slices.faces.service import FaceService

log = get_logger(__name__)

FACE_AGREE = "face_agree"


async def agree(
    context: JobContext, *, service: FaceService, ask: Callable[[JobQueue], Awaitable[None]]
) -> None:
    """Agree with the faces the press named (`track_ids`) for its person, as the user who pressed,
    then ask for the re-match each agreement calls for. A face answered since the press is left as
    it was answered (`confirm_look_alikes` reads them again)."""
    if not await service.enabled():
        log.info("faces.job.skipped", job=FACE_AGREE, reason="switched off")
        return
    viewer = await service.viewer_for(context.pressed_by or "")
    person_id = str(context.payload.get("person_id") or "")
    named = context.payload.get("track_ids")
    if viewer is None or not person_id or not isinstance(named, list):
        log.info("faces.job.skipped", job=FACE_AGREE, reason="nobody to act for")
        return
    done = await service.confirm_look_alikes(viewer, person_id, only=[str(one) for one in named])
    if done.changed:
        await ask(context.queue)
    await context.set_progress(1.0)
    faces = "face" if done.changed == 1 else "faces"
    await context.set_note(f"Agreed with {done.changed:,} {faces}.")


def register_agreeing(service: FaceService) -> None:
    """The task a Yes to a person's proposals runs as; beside `jobs.register_handlers`."""
    register_handler(
        FACE_AGREE,
        lambda context: agree(context, service=service, ask=ask_for_rematching),
        name="Agreeing with faces",
    )
