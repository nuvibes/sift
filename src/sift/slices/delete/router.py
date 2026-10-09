# SPDX-License-Identifier: AGPL-3.0-or-later
"""The delete endpoints; visibility is settled first, so a refusal never confirms a file exists."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import Field

from sift.kernel import wiring
from sift.kernel.access import Viewer
from sift.kernel.content import LibraryStore
from sift.kernel.log import get_logger
from sift.kernel.reach import BulkWriteDone, ConcealedByVault, vault_locked
from sift.kernel.seams import ForgetGoneSeam
from sift.kernel.wire import Wire
from sift.kernel.wiring import part_of
from sift.slices.auth import csrf_protect, current_viewer, require_admin
from sift.slices.delete.service import (
    DELETER,
    INSIDE_AN_ARCHIVE,
    INSIDE_AN_ARCHIVE_MANY,
    Deleter,
    DeleteRefused,
    Mode,
    NotAllowed,
    NotFound,
)

log = get_logger(__name__)

router = APIRouter(tags=["delete"])

#: Each bulk slice keeps its own copy of the number.
MAX_BULK_ASSETS = 500


def _deleter(request: Request) -> Deleter:
    return part_of(request, DELETER)


def _refusal(error: DeleteRefused) -> HTTPException:
    """Turn a refusal into its status: 423 for the vault, 404, 403 or 409, with its own sentence."""
    if isinstance(error, ConcealedByVault):
        return vault_locked()
    if isinstance(error, NotFound):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(error))
    if isinstance(error, NotAllowed):
        return HTTPException(status.HTTP_403_FORBIDDEN, str(error))
    return HTTPException(status.HTTP_409_CONFLICT, str(error))


@router.delete("/assets/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_asset(
    asset_id: str,
    deleter: Annotated[Deleter, Depends(_deleter)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    _csrf: Annotated[None, Depends(csrf_protect)],
    mode: Mode = "sift",
    location_id: str | None = None,
) -> None:
    """Forget a file, or, on a folder Sift may write to, delete it from disk. Admin-only."""
    try:
        await deleter.remove(asset_id, mode=mode, actor=viewer, location_id=location_id)
    except DeleteRefused as refused:
        raise _refusal(refused) from refused


class DeleteMany(Wire):
    """Several files, in one request, with one mode for all of them."""

    asset_ids: list[str] = Field(min_length=1, max_length=MAX_BULK_ASSETS)
    mode: Mode = "sift"


@router.post("/assets/delete", dependencies=[Depends(csrf_protect)])
async def delete_several(
    body: DeleteMany,
    deleter: Annotated[Deleter, Depends(_deleter)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> BulkWriteDone:
    """Delete a selection in one request; refusals are counted, not fatal. Admin-only."""
    # Checked at the door: a guest's `NotAllowed` would otherwise be counted into a 200.
    done = await deleter.remove_many(body.asset_ids, mode=body.mode, actor=viewer)
    removed, skipped, reason, locked = done.removed, done.skipped, done.reason, done.vault_locked
    log.info("delete.many", removed=removed, skipped=skipped, locked=locked, mode=body.mode)
    return BulkWriteDone(
        changed=removed,
        skipped=skipped,
        reason=reason,
        reason_many=done.reason_many,
        vault_locked=locked,
    )


class DeleteCheck(Wire):
    asset_ids: list[str] = Field(min_length=1, max_length=MAX_BULK_ASSETS)


class DeleteReach(Wire):
    #: Delete from disk refuses a picture inside an archive; only visible files are counted.
    inside_archives: int
    #: The refusal in the server's words, or None.
    why: str | None = None


@router.post("/assets/delete/check", dependencies=[Depends(csrf_protect)])
async def check_delete(
    body: DeleteCheck,
    deleter: Annotated[Deleter, Depends(_deleter)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> DeleteReach:
    """Whether the sheet may offer the disk tier for these files. Admin-only; writes nothing."""
    inside = await deleter.inside_archives(body.asset_ids, actor=viewer)
    why = None if inside == 0 else INSIDE_AN_ARCHIVE if inside == 1 else INSIDE_AN_ARCHIVE_MANY
    return DeleteReach(inside_archives=inside, why=why)


class FolderDeleted(Wire):
    """What deleting a folder did; `left_behind` when files Sift never indexed keep it on disk."""

    #: Copies, not files: a file also in another folder stays known.
    files: int
    directories: int
    left_behind: bool


@router.delete("/folders/{folder_id}", dependencies=[Depends(csrf_protect)])
async def delete_folder(
    folder_id: str,
    deleter: Annotated[Deleter, Depends(_deleter)],
    forgets: Annotated[ForgetGoneSeam, Depends(wiring.forget_gone)],
    library: Annotated[LibraryStore, Depends(wiring.library)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> FolderDeleted:
    """Delete a folder from the disk: every file Sift indexed under it, then the directories."""
    under = await _subfolders(library, folder_id)
    try:
        cleared = await deleter.remove_folder(folder_id, actor=viewer)
    except DeleteRefused as refused:
        raise _refusal(refused) from refused
    await forgets.forget_gone("folder", folder_id, name=None, by=viewer, also=under)
    return FolderDeleted(
        files=cleared.files,
        directories=cleared.directories,
        left_behind=cleared.left_behind,
    )


async def _subfolders(library: LibraryStore, folder_id: str) -> list[str]:
    folder = await library.get_folder(folder_id)
    rows = (
        [] if folder is None else await library.folders_in_subtree(folder.root_id, folder.rel_path)
    )
    return [row.id for row in rows if row.id != folder_id]
