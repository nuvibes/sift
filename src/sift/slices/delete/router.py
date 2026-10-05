# SPDX-License-Identifier: AGPL-3.0-or-later
"""The delete endpoint.

One route doing one of two very different things depending on the mode it is given. Both are
admin-only: forgetting a file Sift has indexed leaves the bytes alone but drops the shared index
entry and everything curated onto it, and removing the bytes is a real disk delete: neither is a
guest's to do. What differs is only how far the removal reaches on disk.

The route does not use `require_admin`, and that is deliberate. It answers before
anything has looked at the asset, so a guest naming a file they are not allowed to see would be
told "admins only", which confirms the file is there. Instead the service settles visibility
first and turns an invisible asset into a 404, and only then asks whether this user may delete.
Somebody who cannot see a file gets the same answer whether or not it exists, and a guest who can
see one is told they may not delete it rather than that it does not exist.

The safe mode is the default in the signature as well as in the interface. A caller who forgets the
parameter forgets a file; they do not delete one.
"""

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

#: The most files one request may delete; each bulk slice keeps its own copy of the number.
MAX_BULK_ASSETS = 500


def _deleter(request: Request) -> Deleter:
    return part_of(request, DELETER)


def _refusal(error: DeleteRefused) -> HTTPException:
    """Turn a refusal from the service into the right status, with its own sentence kept.

    The three cases are genuinely different answers and the client acts on each differently: 404
    means there is nothing there for you, 403 means you may not, and 409 means the request was
    reasonable but the state of the disk says no: a read-only folder, a name already taken.

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


@router.delete("/assets/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_asset(
    asset_id: str,
    deleter: Annotated[Deleter, Depends(_deleter)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    _csrf: Annotated[None, Depends(csrf_protect)],
    mode: Mode = "sift",
    location_id: str | None = None,
) -> None:
    """Forget a file, or, on a folder Sift was given write access to, delete it. Admin-only.

    `mode=sift` is the default and leaves every byte where it is, dropping only Sift's record of the
    file. `mode=disk` removes the file for good: there is nowhere it goes and nothing to put it back
    from, and it is refused for any folder that was not handed over read-write. A guest is refused
    either mode, but only after visibility is settled, so the refusal never confirms a file exists.
    """
    try:
        await deleter.remove(asset_id, mode=mode, actor=viewer, location_id=location_id)
    except DeleteRefused as refused:
        raise _refusal(refused) from refused


class DeleteMany(Wire):
    """Several files, in one request.

    The ids are a list and the mode is one word for all of them, because a selection is one act:
    somebody picked a set and pressed a button once, and a per-file mode would be a screen nobody
    has ever seen.
    """

    asset_ids: list[str] = Field(min_length=1, max_length=MAX_BULK_ASSETS)
    mode: Mode = "sift"


@router.post("/assets/delete", dependencies=[Depends(csrf_protect)])
async def delete_several(
    body: DeleteMany,
    deleter: Annotated[Deleter, Depends(_deleter)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> BulkWriteDone:
    """Delete a selection, in ONE request. Admin-only, and refused at the door.

    **One request for the selection, never one PER FILE.** A selection of a hundred files sent a
    file at a time is a hundred round trips, each awaited before the next begins, each dropping its
    own tile the instant it answers, so a bulk delete flickers its way down the wall one picture at
    a time, and each one tells every screen holding a list that the library has changed. The work
    itself is never the cost.

    Every file is checked and removed exactly as the single route does it, and the index rows of
    the whole selection go in one write with one announcement. See `Deleter.remove_many`. **It
    is still not a transaction over the disk and must not become one**: deleting bytes cannot be
    rolled back, so a set that fails partway has genuinely deleted what it deleted, and saying so
    honestly is the only correct answer. The counts are that answer.

    Refusals do not stop the run, for the same reason: one read-only folder in a selection of two
    hundred should not refuse the other hundred and ninety-nine. A caller who may not delete
    anything at all is refused outright instead, with a 403. See the note above this function.
    """
    # ## Why the CALLER is checked at the door here, unlike the single route
    #
    # A comment and not a docstring, deliberately: FastAPI puts a docstring into the OpenAPI schema
    # and from there into the generated client types, and this is a note to whoever edits this
    # function rather than something a client reads.
    #
    # `require_admin` rather than `current_viewer`, and it is the one place these two routes differ
    # in mechanism. `NotAllowed` is a `DeleteRefused`, so without it a guest's refusal would be
    # caught by the loop below and COUNTED: a 200 with `removed: 0` to somebody who may not call
    # the route at all, which a caller reading the counts and not the status takes for success.
    #
    # The single route cannot do this. Its 403-or-404 is per FILE, and that is deliberate: a guest
    # asking about a file they cannot see is told there is no such file rather than that they may
    # not have it, so a refusal never confirms a file exists. A blanket check here loses none of
    # that, because it names no file: it is a fact about the CALLER, decided before any id is
    # read. A list cannot carry a per-file status in one code anyway, which is why the counts exist.
    done = await deleter.remove_many(body.asset_ids, mode=body.mode, actor=viewer)
    removed, skipped, reason, locked = done.removed, done.skipped, done.reason, done.vault_locked
    log.info("delete.many", removed=removed, skipped=skipped, locked=locked, mode=body.mode)
    # `BulkWriteDone` rather than a shape of its own: every bulk write answers partial-with-a-reason
    # in the same three fields. `changed` is how many files went.
    return BulkWriteDone(
        changed=removed,
        skipped=skipped,
        reason=reason,
        reason_many=done.reason_many,
        vault_locked=locked,
    )


class DeleteCheck(Wire):
    """The files a delete sheet is about to offer its two answers for."""

    asset_ids: list[str] = Field(min_length=1, max_length=MAX_BULK_ASSETS)


class DeleteReach(Wire):
    """What the sheet needs to know before it draws Delete from disk."""

    #: How many of them are pictures inside an archive, which Delete from disk refuses (Sift does
    #: not change a person's archive). Only files this user can see are counted.
    inside_archives: int
    #: The refusal the delete routes would answer with, worded for that count, so the sheet says
    #: it in the server's words. None when nothing would be refused.
    why: str | None = None


@router.post("/assets/delete/check", dependencies=[Depends(csrf_protect)])
async def check_delete(
    body: DeleteCheck,
    deleter: Annotated[Deleter, Depends(_deleter)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> DeleteReach:
    """Says whether the sheet may offer the disk tier for these files. Admin-only; writes nothing.

    Asked when the sheet opens, so a picture inside an archive is drawn as Remove from Sift only,
    with the reason, rather than offered and then refused. The delete routes still refuse it on
    their own: this is what the screen knows, never the guard.
    """
    inside = await deleter.inside_archives(body.asset_ids, actor=viewer)
    why = None if inside == 0 else INSIDE_AN_ARCHIVE if inside == 1 else INSIDE_AN_ARCHIVE_MANY
    return DeleteReach(inside_archives=inside, why=why)


class FolderDeleted(Wire):
    """What deleting a folder actually did.

    A body rather than a 204, because this is the one delete in Sift that can do most of what was
    asked and not all of it. Sift indexes media and walks past everything else, so a folder can hold
    files it never took: artwork, subtitles, somebody's notes. Those are not Sift's to remove, and
    `left_behind` is how the screen says the folder is still on the disk with them in it rather than
    reporting a success that was not one.
    """

    #: Copies whose bytes were removed. Copies, not files: one that also sits in another folder
    #: loses the copy in here and stays a file Sift knows about.
    files: int
    #: Directories taken off the disk, the folder itself included.
    directories: int
    #: Something Sift does not index is still in there, so the folder is still on the disk.
    left_behind: bool


@router.delete("/folders/{folder_id}", dependencies=[Depends(csrf_protect)])
async def delete_folder(
    folder_id: str,
    deleter: Annotated[Deleter, Depends(_deleter)],
    forgets: Annotated[ForgetGoneSeam, Depends(wiring.forget_gone)],
    library: Annotated[LibraryStore, Depends(wiring.library)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> FolderDeleted:
    """Delete a folder from the disk: every file Sift indexed under it, and then the directories.

    Here rather than beside the other folder routes, and that is the architecture rather than an
    accident. Removing a file somebody else put on a disk goes through one service and one file
    (`Deleter`), which a static rule in the build enforces; a slice may not import another slice, so
    a route in the library feature could not reach it. This is the second thing in Sift that can be
    deleted, so it is declared beside the first. The vault feature already owns `/folders/{id}/vault`
    on the same terms.

    `current_viewer` and not `require_admin`, the same as the single-asset route above: the service
    settles whether this user can SEE the folder before it asks whether they may delete it, so
    somebody who cannot see one is told it is not there rather than that they are not allowed to
    remove it. The second answer would confirm it exists.

    There is no `mode`. Forgetting a folder without touching the disk is not a thing anybody can
    want: the next walk finds the directory still there and puts the row straight back.
    """
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
    """Every folder under this one, read before the delete takes their rows."""
    folder = await library.get_folder(folder_id)
    rows = (
        [] if folder is None else await library.folders_in_subtree(folder.root_id, folder.rel_path)
    )
    return [row.id for row in rows if row.id != folder_id]
