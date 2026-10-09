# SPDX-License-Identifier: AGPL-3.0-or-later
"""The watermark endpoints; everything that spends anything is admin-only and names no asset."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from sift.kernel import wiring
from sift.kernel.access import Viewer
from sift.kernel.jobs import JobQueue
from sift.kernel.log import get_logger
from sift.kernel.wiring import part_of
from sift.slices.auth import csrf_protect, require_admin
from sift.slices.watermarks import weights
from sift.slices.watermarks.jobs import WATERMARK_FETCH_MODELS, WATERMARK_READ
from sift.slices.watermarks.models import ModelsFetching, ReadingsRemoved, WatermarkStatus
from sift.slices.watermarks.service import SERVICE, WatermarkService

log = get_logger(__name__)

router = APIRouter(tags=["watermarks"])


def _service(request: Request) -> WatermarkService:
    return part_of(request, SERVICE)


def _off() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="Reading watermarks is switched off.",
    )


@router.get("/watermarks/status")
async def read_status(
    service: Annotated[WatermarkService, Depends(_service)],
    queue: Annotated[JobQueue, Depends(wiring.queue)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> WatermarkStatus:
    enabled = await service.enabled()
    ready, problem = await service.ready()
    store = weights.store(service.settings)
    return WatermarkStatus(
        enabled=enabled,
        ready=ready,
        device=await service.device(),
        read_files=await service.read_files(),
        marks_found=await service.marks_found(),
        # Counted no further than a page.
        waiting_files=await service.waiting(),
        unread_files=await service.unread() if ready else 0,
        running_jobs=await queue.outstanding(WATERMARK_READ),
        problem=problem,
        installed=sorted(
            weight_id for weight_id, weight in weights.CATALOG.items() if store.installed(weight)
        ),
    )


@router.post("/watermarks/models/fetch", dependencies=[Depends(csrf_protect)])
async def fetch_models(
    service: Annotated[WatermarkService, Depends(_service)],
    queue: Annotated[JobQueue, Depends(wiring.queue)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    again: bool = False,
) -> ModelsFetching:
    """Fetch the models (Sift ships none); 409 with the feature off; returns the job."""
    if not await service.enabled():
        raise _off()
    newest = [job.id for job in await queue.newest_of(WATERMARK_FETCH_MODELS, limit=1)]
    live = await queue.unfinished_among(newest)
    job_id = (
        live.pop()
        if live
        else await queue.enqueue(WATERMARK_FETCH_MODELS, {"again": again}, dedupe=True)
    )
    log.info("watermarks.models.requested", job_id=job_id)
    return ModelsFetching(job_id=job_id)


@router.delete("/watermarks/reads", dependencies=[Depends(csrf_protect)])
async def forget_reads(
    service: Annotated[WatermarkService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> ReadingsRemoved:
    """Throw away what was read so the library is read again; the filings stay."""
    return ReadingsRemoved(removed=await service.forget_reads())
