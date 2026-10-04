# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Stash routes. Admin only, and every one that changes anything is CSRF-guarded.

A Stash library is brought into the library as a whole, the same kind of act as a restore, so a
guest is refused before anything is read. The database is the file the admin chose in
the desktop application's own file dialog, or Stash's folder picked in a browser, and either is
confined to the folders Sift has been given, which is the rule the folder picker follows.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from sift.kernel import wiring
from sift.kernel.access import Viewer
from sift.kernel.paging import MAX_PAGE_SIZE
from sift.kernel.seams import SettingsSeam
from sift.kernel.where import blurred, profile_to_blur
from sift.kernel.wiring import QUEUE, part_of
from sift.slices.auth import csrf_protect, require_admin
from sift.slices.stash_migration.models import (
    BringStash,
    NewStashLibrary,
    ReadStash,
    StashRead,
    StashRunStarted,
    StashSwitch,
    StashWaitingPage,
    StashWaitingRow,
)
from sift.slices.stash_migration.service import SERVICE, StashMigration, StashRefused

router = APIRouter(prefix="/stash-migration", tags=["stash_migration"])


def _service(request: Request) -> StashMigration:
    return part_of(request, SERVICE)


@router.get("", response_model=StashRead | None)
async def last_read(
    request: Request,
    _admin: Annotated[Viewer, Depends(require_admin)],
) -> StashRead | None:
    """What the last read found, while its copy is kept; null when nothing has been read."""
    found = await _service(request).last_read(part_of(request, QUEUE))
    return None if found is None else StashRead(**found)


#: How many waiting rows a page holds when the caller does not say.
WAITING_PAGE = 50


@router.get("/waiting", response_model=StashWaitingPage)
async def waiting(
    request: Request,
    admin: Annotated[Viewer, Depends(require_admin)],
    preferences: Annotated[SettingsSeam, Depends(wiring.settings_hub)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = WAITING_PAGE,
) -> StashWaitingPage:
    """The scenes and pictures that wait for their files, by name and by Stash's path, with what
    waits on each. A path is Stash's, with the profile folder's name taken out where this admin
    asked for that, as every path Sift says is."""
    total, rows = await _service(request).waiting_page(offset, limit)
    profile = await profile_to_blur(admin, preferences)
    return StashWaitingPage(
        total=total,
        offset=offset,
        rows=[
            StashWaitingRow(**{**one, "paths": [blurred(path, profile) for path in one["paths"]]})
            for one in rows
        ],
    )


@router.post("/read", response_model=StashRead, dependencies=[Depends(csrf_protect)])
async def read_stash(
    body: ReadStash,
    request: Request,
    _admin: Annotated[Viewer, Depends(require_admin)],
) -> StashRead:
    """Copy Stash's database in and say what it holds. Nothing in this library is changed."""
    try:
        return StashRead(**await _service(request).read(body.path))
    except StashRefused as refused:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(refused)) from refused


@router.post(
    "/run",
    response_model=StashRunStarted,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(csrf_protect)],
)
async def run_stash(
    request: Request,
    admin: Annotated[Viewer, Depends(require_admin)],
    body: BringStash | None = None,
) -> StashRunStarted:
    """Bring the Stash library last read into this one, as a task. The body is optional: without
    one the pictures stay behind."""
    chosen = body or BringStash()
    try:
        job_id = await _service(request).ask_to_run(
            admin, part_of(request, QUEUE), pictures=chosen.pictures, blobs=chosen.blobs
        )
    except StashRefused as refused:
        raise HTTPException(status.HTTP_409_CONFLICT, str(refused)) from refused
    return StashRunStarted(job_id=job_id)


@router.post(
    "/new-library",
    response_model=StashSwitch,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(csrf_protect)],
)
async def new_library_from_stash(
    body: NewStashLibrary,
    request: Request,
    admin: Annotated[Viewer, Depends(require_admin)],
) -> StashSwitch:
    """Make a library beside this one with the folders the last read matched, and start Sift on
    it. The run is queued there and waits for that library's first scan.

    202: the switch is arranged, not done, as when a library is made from the libraries list.
    """
    try:
        made = await _service(request).bring_into_new(
            body.name, admin, pictures=body.pictures, blobs=body.blobs
        )
    except StashRefused as refused:
        raise HTTPException(refused.status, str(refused)) from refused
    return StashSwitch(switching=True, library=made)
