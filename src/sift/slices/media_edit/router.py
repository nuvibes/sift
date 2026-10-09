# SPDX-License-Identifier: AGPL-3.0-or-later
"""The compression and edit endpoints, all admin-only.
A route naming an asset settles visibility first, so it never confirms a hidden file exists."""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse

from sift.kernel import wiring
from sift.kernel.access import Viewer
from sift.kernel.config import Settings
from sift.kernel.ids import is_id
from sift.kernel.jobs import JobQueue
from sift.kernel.library_write import LibraryWriteRefused
from sift.kernel.log import get_logger
from sift.kernel.reach import ConcealedByVault, vault_locked
from sift.kernel.wiring import part_of
from sift.slices.auth import csrf_protect, current_viewer, require_admin
from sift.slices.media_edit.editor import EDITOR, EditService
from sift.slices.media_edit.jobs import samples_directory
from sift.slices.media_edit.models import (
    CompressRequest,
    CompressStarted,
    EditFrame,
    EditRequest,
    EditStarted,
    EditVerdict,
    MadeCopies,
    Preflight,
    Produced,
    SampleStarted,
)
from sift.slices.media_edit.refusals import NotAllowed, NotFound, Refused
from sift.slices.media_edit.service import COMPRESS_SAMPLE, COMPRESSOR, CompressService
from sift.slices.media_edit.tuning import COMPATIBLE_CONTAINER

log = get_logger(__name__)

router = APIRouter(tags=["compress"])


def _service(request: Request) -> CompressService:
    return part_of(request, COMPRESSOR)


def _editor(request: Request) -> EditService:
    return part_of(request, EDITOR)


def _refusal(error: Exception) -> HTTPException:
    """A refusal from the service as the right status. The vault answers 423, see `kernel.reach`."""
    if isinstance(error, ConcealedByVault):
        return vault_locked()
    if isinstance(error, NotFound):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(error))
    if isinstance(error, NotAllowed):
        return HTTPException(status.HTTP_403_FORBIDDEN, str(error))
    return HTTPException(status.HTTP_409_CONFLICT, str(error))


@router.post("/compress/preflight", dependencies=[Depends(csrf_protect)])
async def preflight(
    service: Annotated[CompressService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    body: CompressRequest | None = None,
) -> Preflight:
    """What compressing this selection would do. A POST for the long id list; it writes nothing."""
    if body is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Say which files to look at.")
    try:
        return await service.preflight(body, viewer=viewer)
    except (Refused, LibraryWriteRefused) as refused:
        raise _refusal(refused) from refused


@router.post("/compress", dependencies=[Depends(csrf_protect)])
async def start(
    service: Annotated[CompressService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    body: CompressRequest | None = None,
) -> CompressStarted:
    """Queue the work. One job per file, so one failure does not take the rest with it."""
    if body is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Say which files to compress.")
    try:
        return await service.start(body, viewer=viewer)
    except (Refused, LibraryWriteRefused) as refused:
        raise _refusal(refused) from refused


@router.post("/assets/{asset_id}/compress/sample", dependencies=[Depends(csrf_protect)])
async def sample(
    asset_id: str,
    service: Annotated[CompressService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    body: CompressRequest | None = None,
) -> SampleStarted:
    """Encode a few seconds so the quality can be looked at before the whole file is committed to."""
    # Optional body, so a caller who may not touch the asset is not told their JSON was malformed.
    try:
        wanted = body or CompressRequest(asset_ids=[asset_id], compatibility=True)
        return await service.sample(asset_id, wanted, viewer=viewer)
    except (Refused, LibraryWriteRefused) as refused:
        raise _refusal(refused) from refused


@router.get("/assets/{asset_id}/produced")
async def produced(
    asset_id: str,
    service: Annotated[CompressService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> Produced | None:
    """What this file was made from, if Sift made it. Null when it is an ordinary file."""
    try:
        return await service.produced_for(asset_id, viewer=viewer)
    except Refused as refused:
        raise _refusal(refused) from refused


@router.get("/assets/{asset_id}/made-from")
async def made_from(
    asset_id: str,
    service: Annotated[CompressService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> MadeCopies:
    """What Sift has made FROM this file; copies the user may not see are left out."""
    try:
        return MadeCopies(copies=await service.made_from(asset_id, viewer=viewer))
    except Refused as refused:
        raise _refusal(refused) from refused


@router.get("/assets/{asset_id}/edit/frame")
async def edit_frame(
    asset_id: str,
    editor: Annotated[EditService, Depends(_editor)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> EditFrame:
    """How big this picture is as somebody sees it. Asked once, when the editor opens."""
    try:
        return await editor.frame_of(asset_id, viewer=viewer)
    except (Refused, LibraryWriteRefused) as refused:
        raise _refusal(refused) from refused


@router.post("/assets/{asset_id}/edit/preflight", dependencies=[Depends(csrf_protect)])
async def edit_preflight(
    asset_id: str,
    editor: Annotated[EditService, Depends(_editor)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    body: EditRequest | None = None,
) -> EditVerdict:
    """What this edit would produce, before it produces it. Re-asked as the numbers change."""
    try:
        # Who is asking, before what they asked.
        await editor.settle(asset_id, viewer=viewer)
        if body is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "Say what to do to the file.")
        return await editor.verdict(asset_id, body, viewer=viewer)
    except (Refused, LibraryWriteRefused) as refused:
        raise _refusal(refused) from refused


@router.post("/assets/{asset_id}/edit", dependencies=[Depends(csrf_protect)])
async def start_edit(
    asset_id: str,
    editor: Annotated[EditService, Depends(_editor)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    body: EditRequest | None = None,
) -> EditStarted:
    """Make the edited copy. A new file beside the original: nothing is written over."""
    try:
        await editor.settle(asset_id, viewer=viewer)
        if body is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "Say what to do to the file.")
        return await editor.start(asset_id, body, viewer=viewer)
    except (Refused, LibraryWriteRefused) as refused:
        raise _refusal(refused) from refused


@router.get("/compress/samples/{job_id}")
async def read_sample(
    job_id: str,
    queue: Annotated[JobQueue, Depends(wiring.queue)],
    settings: Annotated[Settings, Depends(wiring.settings)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> FileResponse:
    """The few seconds a sample job produced, found by the job's own id and nothing else a caller
    sent."""
    if not is_id(job_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "There is no such sample.")
    job = await queue.get(job_id)
    if job is None or job.type != COMPRESS_SAMPLE:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "There is no such sample.")

    path = samples_directory(settings) / f"{job_id}.{COMPATIBLE_CONTAINER}"
    if not await asyncio.to_thread(path.is_file):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "That sample is not ready, or has been cleared away."
        )
    # A sample is thrown away; nothing worth caching.
    return FileResponse(path, media_type="video/mp4", headers={"Cache-Control": "no-store"})
