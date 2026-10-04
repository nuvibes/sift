# SPDX-License-Identifier: AGPL-3.0-or-later
"""The rename, move and undo endpoints.

Every route here changes a file somebody else put there, so every route here is admin-only and the
server is what enforces it. None of them use `require_admin`, and that is deliberate: it
answers before anything has looked at the asset, so a guest naming a file they are not allowed to
see would be told "admins only", which confirms the file is there. The service settles visibility
first and turns an invisible asset into a 404, and only then asks whether this user may organize
it. Somebody who cannot see a file gets the same answer whether or not it exists.

The folder move is the exception and takes `require_admin` directly. A folder id is not
scoped to what a person can see in the way an asset is, and the route's own 404 covers the rest.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from sift.kernel import wiring
from sift.kernel.access import Viewer
from sift.kernel.db import Database
from sift.kernel.jobs import JobQueue
from sift.kernel.log import get_logger
from sift.kernel.reach import BulkWriteDone, ConcealedByVault, vault_locked
from sift.kernel.seams import ReindexSeam
from sift.kernel.wiring import part_of
from sift.kernel.workbench import Recorder
from sift.slices.auth import csrf_protect, current_viewer, require_admin
from sift.slices.organize.batch import PREVIEW_ROWS, WORDS, BatchRenamer
from sift.slices.organize.models import (
    MoveManyRequest,
    OrganizeDone,
    OrganizeOptions,
    RenameBatchDone,
    RenameBatchRequest,
    RenamePreview,
    RenameRequest,
    RenameRow,
)
from sift.slices.organize.service import (
    ORGANIZER,
    NotAllowed,
    NotFound,
    Organized,
    Organizer,
    OrganizeRefused,
)

log = get_logger(__name__)

router = APIRouter(tags=["organize"])


def _organizer(request: Request) -> Organizer:
    return part_of(request, ORGANIZER)


def _refusal(error: OrganizeRefused) -> HTTPException:
    """Turn a refusal from the service into the right status, keeping its own sentence.

    Three genuinely different answers, which a client acts on differently: 404 means there is
    nothing there for you, 403 means you may not, and 409 means the request was reasonable but the
    state of the disk says no: a read-only folder, a name already taken.

    THE VAULT IS ASKED FIRST and answers 423, which is the one place the undifferentiated 404 is
    deliberately relaxed. `kernel.reach.vault_locked` carries the whole argument for why telling
    that one user gives nothing away; the short of it is that the vault conceals from onlookers
    and never claimed to keep a secret from the person who locked it, and answering 404 tells them
    their file has been deleted, which is a lie, and the more alarming one.
    """
    if isinstance(error, ConcealedByVault):
        return vault_locked()
    if isinstance(error, NotFound):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(error))
    if isinstance(error, NotAllowed):
        return HTTPException(status.HTTP_403_FORBIDDEN, str(error))
    return HTTPException(status.HTTP_409_CONFLICT, str(error))


def _done(result: Organized) -> OrganizeDone:
    return OrganizeDone(
        asset_id=result.asset_id,
        location_id=result.location_id,
        filename=result.filename,
        folder_id=result.folder_id,
        move_id=result.move_id or None,
    )


@router.get("/assets/{asset_id}/organize")
async def options(
    asset_id: str,
    organizer: Annotated[Organizer, Depends(_organizer)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> OrganizeOptions:
    """Whether this file can be renamed or moved, and what would undo its last move.

    Asked by the asset view before it draws its menu. On a folder that was not handed over
    read-write the actions are left off the menu entirely rather than shown and refusing.
    """
    try:
        answer = await organizer.organizability(asset_id, actor=viewer)
    except OrganizeRefused as refused:
        raise _refusal(refused) from refused
    return OrganizeOptions(
        can_organize=answer.can_organize,
        reason=answer.reason,
        undo_move_id=await organizer.last_move(asset_id) if answer.can_organize else None,
    )


# The body on these two is optional at this layer, and that is not laxity: it is what keeps the
# order of the answers right. A required body is validated before the handler runs, so a caller who
# may not touch this asset at all would be told their JSON was malformed: a different answer for a
# well-formed request than for a broken one, from a route they are not allowed to use either way.
# Absent, it reaches the service, which settles who is asking and what they can see before it looks
# at what they sent, and an empty name is then refused in the same sentence as any other bad one.


@router.post("/assets/{asset_id}/rename", dependencies=[Depends(csrf_protect)])
async def rename(
    asset_id: str,
    organizer: Annotated[Organizer, Depends(_organizer)],
    reindexer: Annotated[ReindexSeam, Depends(wiring.reindexer)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    body: RenameRequest | None = None,
) -> OrganizeDone:
    """Give a file a different name, in the folder it is already in."""
    try:
        organized = await organizer.rename(
            asset_id,
            new_name=body.name if body else "",
            actor=viewer,
            location_id=body.location_id if body else None,
        )
        # The name and the path are both indexed. Known by id, so it is rewritten now.
        await reindexer.touched(asset_id)
        return _done(organized)
    except OrganizeRefused as refused:
        raise _refusal(refused) from refused


@router.post("/assets/move", dependencies=[Depends(csrf_protect)])
async def move_many(
    body: MoveManyRequest,
    organizer: Annotated[Organizer, Depends(_organizer)],
    reindexer: Annotated[ReindexSeam, Depends(wiring.reindexer)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> BulkWriteDone:
    """Move a selection into one folder, in ONE request.

    Admin at the door, like every route that changes a file somebody else put there. The
    per-file refusals below are counted rather than raised, so without the door a guest would be
    answered 200 with every file skipped: a success that did nothing, and the one answer the
    authorization matrix cannot tell from having been let in.

    Every file is moved through the same service call, one at a time: the disk first and the
    index after, with the folder's write access checked on each. A refusal does not stop the run:
    one file that cannot be moved should not leave the other hundred where they were, and the
    counts say what happened. The service still takes a `location_id` naming which COPY to move,
    for a screen that shows copies; this route does not.
    """
    moved: list[str] = []
    skipped = 0
    reason: str | None = None
    locked = False
    for asset_id in dict.fromkeys(body.asset_ids):
        try:
            organized = await organizer.move(asset_id, folder_id=body.folder_id, actor=viewer)
            moved.append(organized.asset_id)
        except OrganizeRefused as refused:
            skipped += 1
            if isinstance(refused, ConcealedByVault) and not locked:
                locked = True
                reason = str(refused)
            elif reason is None:
                reason = str(refused)
    if moved:
        # The path is indexed as well as the name, so moving a file changes what finds it. Once
        # for the whole selection rather than once per file.
        await reindexer.touched_many(moved)
    log.info("organize.moved_many", moved=len(moved), skipped=skipped, locked=locked)
    return BulkWriteDone(changed=len(moved), skipped=skipped, reason=reason, vault_locked=locked)


def _renamer(
    organizer: Annotated[Organizer, Depends(_organizer)],
    database: Annotated[Database, Depends(wiring.database)],
    recorder: Annotated[Recorder, Depends(wiring.recorder)],
    reindexer: Annotated[ReindexSeam, Depends(wiring.reindexer)],
    queue: Annotated[JobQueue, Depends(wiring.queue)],
) -> BatchRenamer:
    return BatchRenamer(organizer, database, recorder, touched=reindexer.touched_many, queue=queue)


@router.post("/organize/rename/preview", dependencies=[Depends(csrf_protect)])
async def preview_batch(
    body: RenameBatchRequest,
    renamer: Annotated[BatchRenamer, Depends(_renamer)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> RenamePreview:
    """Every file's new name under a template, with the clashes marked. Writes nothing.

    Admin at the door, like the bulk move and for the same reason: the per-file refusals are
    answered in the rows, so without the door a guest would be answered 200 about files it may
    not touch. A POST because the batch is a list of up to a thousand ids.
    """
    planned = await renamer.plan(
        body.asset_ids, body.template, on_clash=body.on_clash, actor=viewer
    )
    return RenamePreview(
        rows=[
            RenameRow(
                asset_id=row.asset_id,
                before=row.before,
                after=row.after,
                state=row.state,
                reason=row.reason,
            )
            for row in planned.rows[:PREVIEW_ROWS]
        ],
        total=len(planned.rows),
        renaming=planned.moving,
        numbered=planned.count("numbered"),
        same=planned.count("same"),
        clashes=planned.count("numbered", "taken", "twice"),
        refused=planned.count("refused"),
        as_task=planned.as_task,
        words=WORDS,
    )


@router.post("/organize/rename", dependencies=[Depends(csrf_protect)])
async def rename_batch(
    body: RenameBatchRequest,
    renamer: Annotated[BatchRenamer, Depends(_renamer)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> RenameBatchDone:
    """Rename every file of the batch, planned again from the disk as it is now.

    One receipt for the whole batch, whose Undo puts every file back. A large batch, or one on a
    folder served from another machine, is handed to a task and the answer names it instead.
    """
    applied = await renamer.apply(
        body.asset_ids, body.template, on_clash=body.on_clash, actor=viewer
    )
    return RenameBatchDone(
        renamed=applied.renamed,
        skipped=applied.skipped,
        receipt_id=applied.receipt_id,
        job_id=applied.job_id,
        reason=applied.reason,
    )


@router.post("/moves/{move_id}/undo", dependencies=[Depends(csrf_protect)])
async def undo(
    move_id: str,
    organizer: Annotated[Organizer, Depends(_organizer)],
    reindexer: Annotated[ReindexSeam, Depends(wiring.reindexer)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> OrganizeDone:
    """Put a file back at the name and folder it had before its last rename or move."""
    try:
        organized = await organizer.undo(move_id, actor=viewer)
        # Undo puts the file back at its old name, which is another change to indexed text.
        await reindexer.touched(organized.asset_id)
        return _done(organized)
    except OrganizeRefused as refused:
        raise _refusal(refused) from refused
