# SPDX-License-Identifier: AGPL-3.0-or-later
"""The front door for bytes: drop, paste, upload, and a dragged-in link.

Three ways in, one rule shared between them. A file's bytes are staged and queued for import; a
usable link is handed to the downloader, which fetches the original and then feeds it through the
same import. Where both a link and bytes arrive together, which is what dragging an image out of
a browser tab gives, the link wins, because it fetches the full-resolution original rather than
the thumbnail the drag carried.

All of it is admin-only. Adding content decides what sits in the library, and the link path reaches
the downloader, which fetches from the internet on the server's behalf: neither is a guest's to
do. What a route may not do is import a file itself: reading and writing the content tables happens
with no permission check, which belongs to a job, so a route stages and queues and no more.
"""

from __future__ import annotations

from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status

from sift.kernel.access import Viewer
from sift.kernel.ids import is_id
from sift.kernel.ingress import NoDestination, Origin
from sift.kernel.log import get_logger
from sift.kernel.seams import UrlImporter
from sift.kernel.wiring import URL_IMPORTER, part_of, part_or_none
from sift.slices.auth import csrf_protect, require_admin
from sift.slices.capture.models import (
    MAX_URL,
    CaptureAccepted,
    DownloadAccepted,
    ImportAccepted,
    UrlImport,
)
from sift.slices.capture.pipeline import (
    Route,
    route_capture,
    usable_source_url,
)
from sift.slices.capture.service import SERVICE, CaptureService

log = get_logger(__name__)

router = APIRouter(prefix="/capture", tags=["capture"])


def _service(request: Request) -> CaptureService:
    return part_of(request, SERVICE)


def _downloader(request: Request) -> UrlImporter | None:
    """The downloader, if it is wired. It is a separate feature and may not be present yet."""
    return part_or_none(request, URL_IMPORTER)


@router.post(
    "/import/file",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(csrf_protect)],
)
async def import_upload(
    request: Request,
    viewer: Annotated[Viewer, Depends(require_admin)],
    file: Annotated[UploadFile, File()],
    dest_folder_id: Annotated[str | None, Form()] = None,
    screenshot_of: Annotated[str | None, Form()] = None,
) -> ImportAccepted:
    """Take an uploaded file in. The bytes are staged and an import is queued; watch the job.

    Nothing is decided about the file here, not whether it is really media, which is the import's
    first act and the one gate every path shares. A disguised file is accepted by this route and
    refused by the import, which is where a refusal can be acted on without a decision about the
    file's type ever being made from its name.

    `screenshot_of` is the id of the file a screenshot was taken of: the import names the new file
    after it (`<name>-ss.png`).
    """
    if screenshot_of is not None and not is_id(screenshot_of):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That isn't the id of a file.")
    try:
        job_id = await _service(request).stage_and_enqueue(
            file,
            origin=Origin.UPLOAD,
            dest_folder_id=dest_folder_id,
            screenshot_of=screenshot_of,
        )
    except NoDestination as refusal:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(refusal)) from refusal
    return ImportAccepted(job_id=job_id)


@router.post(
    "/import/url",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(csrf_protect)],
)
async def import_url(
    body: UrlImport,
    request: Request,
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> DownloadAccepted:
    """Hand a link to the downloader. It fetches the original and imports it; watch the download."""
    if not usable_source_url(body.url):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "That does not look like a link Sift can download."
        )
    download_id = await _submit_download(
        request, url=body.url, dest_folder_id=body.dest_folder_id, requested_by=viewer.id
    )
    return DownloadAccepted(download_id=download_id)


@router.post(
    "/import/clipboard",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(csrf_protect)],
)
async def import_clipboard(
    request: Request,
    viewer: Annotated[Viewer, Depends(require_admin)],
    url: Annotated[str | None, Form(max_length=MAX_URL)] = None,
    file: Annotated[UploadFile | None, File()] = None,
    dest_folder_id: Annotated[str | None, Form()] = None,
    origin: Annotated[Literal["drop", "paste"], Form()] = "paste",
) -> CaptureAccepted:
    """Take in whatever was pasted or dragged: a link becomes a download, bytes become an import.

    The same preference as a drag: a usable link wins over bytes that arrived with it. `origin`
    says whether this was a drop or a paste; it changes nothing about how the file is handled and is
    only recorded, so a client cannot gain anything by misreporting it.
    """
    decision = route_capture(url=url, has_bytes=file is not None)
    if decision is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "There was nothing to add.")

    if decision is Route.DOWNLOAD:
        # route_capture returns DOWNLOAD only for a usable url, and IMPORT only when bytes are here;
        # the casts state that guarantee to the type checker rather than re-checking it at runtime.
        download_id = await _submit_download(
            request, url=cast(str, url), dest_folder_id=dest_folder_id, requested_by=viewer.id
        )
        return CaptureAccepted(download_id=download_id)

    try:
        job_id = await _service(request).stage_and_enqueue(
            cast(UploadFile, file), origin=Origin(origin), dest_folder_id=dest_folder_id
        )
    except NoDestination as refusal:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(refusal)) from refusal
    return CaptureAccepted(job_id=job_id)


async def _submit_download(
    request: Request, *, url: str, dest_folder_id: str | None, requested_by: str
) -> str:
    """Resolve the destination and hand the link to the downloader, or refuse if it is not wired.

    `requested_by` is the user who captured the link, so its landing is recorded as theirs.
    """
    downloader = _downloader(request)
    if downloader is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Downloading from a link is not available."
        )
    try:
        destination = await _service(request).resolve_destination(dest_folder_id)
    except NoDestination as refusal:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(refusal)) from refusal
    return await downloader.submit_url(
        url=url, dest_folder_id=destination.folder_id, requested_by=requested_by
    )
