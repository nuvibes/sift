# SPDX-License-Identifier: AGPL-3.0-or-later
"""The compression endpoints.

Every route here either reads what would happen to somebody's files or starts work that writes new
ones, so all of it is admin-only, and WHERE that is enforced differs by route, on one rule: a
route that names an asset in its address must not answer "admins only", because that answer
confirms the file is there to somebody who was not allowed to know.

So the two routes that name one (the sample, and reading what a file was made from) take the
ordinary viewer and let the service settle visibility first, which turns a file the user cannot
see into the same answer a missing one gets. The rest name no asset in the address, so the
admin-only door sits on the route itself where there is nothing for it to leak.

The bodies are optional at this layer for the same reason. A required body is validated before the
handler runs, so a caller who may not touch the asset would be told their JSON was malformed:
a different answer for a well-formed request than for a broken one, from a route they cannot use
either way.
"""

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
    """A refusal from the service, as the right status, keeping its own sentence.

    Three answers a client acts on differently: nothing there for you, you may not, and the request
    was reasonable but the state of the disk says no.

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


@router.post("/compress/preflight", dependencies=[Depends(csrf_protect)])
async def preflight(
    service: Annotated[CompressService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    body: CompressRequest | None = None,
) -> Preflight:
    """What compressing this selection would do, before anything is encoded.

    A POST rather than a GET because the question carries a list of hundreds of ids and a target,
    which is a body rather than a query string. It writes nothing.
    """
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
    # The body is optional at this layer, and that is what keeps the order of the answers right. A
    # required body is validated before the handler runs, so somebody who may not touch this asset
    # at all would be told their JSON was malformed: a different answer for a well-formed request
    # than for a broken one, from a route they cannot use either way.
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
    """What Sift has made FROM this file: the other end of the line a copy carries.

    Scoped the same way the copy's own line is: a copy this user may not see is left out of the
    list rather than named, so the page cannot become a way of reading filenames sideways.
    """
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
    """How big this picture is as somebody sees it. Asked once, when the editor opens.

    A GET, unlike everything else here, because it carries no body and asks about the file rather
    than about anything that was sent. The ordinary viewer, and the service settles visibility, for
    the reason every route naming an asset in its address does: "admins only" against a file the
    user was not allowed to know about is the answer that confirms it is there.
    """
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
    """What this edit would produce, before it produces it. Re-asked as the numbers change.

    A POST although it writes nothing, for the same reason the compression preflight is one: it
    carries a body, and the answer changes with every number in it.
    """
    try:
        # Who is asking, before what they asked. The body is optional at this layer for the reason
        # the compression routes' bodies are, and settling first is the other half of it: a caller
        # who may not touch this file is told so whether or not their JSON was any good.
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
    """The few seconds a sample job produced.

    The name of the file is built from the job's own id and nothing a caller sent, which is what
    keeps this from being a way to read an arbitrary path: the id has to be a real one, in this
    application's own id format, naming a job of this one type. A caller sending anything else gets
    the same answer as a caller naming a job that finished and was swept.
    """
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
    # Never kept by a browser. A sample is thrown away and a second one lands at a different
    # address anyway, so there is nothing here worth a cache and something worth not leaving behind.
    return FileResponse(path, media_type="video/mp4", headers={"Cache-Control": "no-store"})
