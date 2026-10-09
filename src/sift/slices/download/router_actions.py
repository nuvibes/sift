# SPDX-License-Identifier: AGPL-3.0-or-later
"""The verbs on one download: cancel, fetch anyway, retry, remove, pause, resume, restore, first."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from sift.kernel.access import Viewer
from sift.slices.auth import csrf_protect, require_admin
from sift.slices.download.router_parts import _service
from sift.slices.download.service import (
    DownloadService,
)

router = APIRouter()


@router.post(
    "/downloads/{download_id}/cancel",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_protect)],
)
async def cancel_download(
    download_id: str,
    service: Annotated[DownloadService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> None:
    """Cancel a queued or running download; idempotent, so a repeated click is harmless."""
    await service.cancel(download_id, by=viewer.id)


@router.post(
    "/downloads/{download_id}/anyway",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_protect)],
)
async def download_anyway(
    download_id: str,
    service: Annotated[DownloadService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> None:
    """Fetch a link skipped as fetched before; idempotent, so it never says which ids exist."""
    await service.fetch_anyway(download_id)


@router.post(
    "/downloads/{download_id}/retry",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_protect)],
)
async def retry_download(
    download_id: str,
    service: Annotated[DownloadService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> None:
    """Put a failed, cancelled or quarantined download back in the queue; idempotent."""
    await service.retry(download_id)


@router.post(
    "/downloads/{download_id}/remove",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_protect)],
)
async def remove_download(
    download_id: str,
    service: Annotated[DownloadService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> None:
    """Take a settled download out of the list; the row and the file stay. 409 while still going."""
    still_going = await service.hide(download_id)
    if still_going is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Cancel it first")


@router.post(
    "/downloads/{download_id}/pause",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_protect)],
)
async def pause_download(
    download_id: str,
    service: Annotated[DownloadService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> None:
    """Stop a running or waiting download, keeping what arrived; refused when it is not running."""
    if not await service.pause(download_id, by=viewer.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "This download is not running")


@router.post(
    "/downloads/{download_id}/resume",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_protect)],
)
async def resume_download(
    download_id: str,
    service: Annotated[DownloadService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> None:
    """Start a paused download again from what it had, in its place; refused when not paused."""
    if not await service.resume(download_id, by=viewer.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "This download is not paused")


@router.post(
    "/downloads/{download_id}/restore",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_protect)],
)
async def restore_download(
    download_id: str,
    service: Annotated[DownloadService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> None:
    """Put a removed download back in the list; 404 for a row that was not removed."""
    if not await service.restore(download_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This download is not one that was removed")


@router.post(
    "/downloads/{download_id}/first",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_protect)],
)
async def promote_download(
    download_id: str,
    service: Annotated[DownloadService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> None:
    """Move a waiting download to the front of the queue; anything else is left as it is."""
    await service.promote(download_id)
