# SPDX-License-Identifier: AGPL-3.0-or-later
"""The endpoints this feature has. The ones that SPEND anything are admin-only.

Reading a library's watermarks is a property of the whole install rather than a personal
preference, and everything that reports on it or spends the machine's time and network on it takes
an admin. There is no asset a caller chooses in any of those, so refusing a guest outright leaks
nothing.

There is no per-file read here: what was read off one file is the watermark line of that file's
History, which already answers per file, to exactly the users who may see it.

Nor is there a library sweep of this feature's own: reading the library is the watermark task's
Run now (`/api/tasks`), which runs the Build for this one product, the way Smart Search's describe
does: a second walk would read every file twice.
"""

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
    """What this install can do, and why not when it cannot."""
    enabled = await service.enabled()
    ready, problem = await service.ready()
    store = weights.store(service.settings)
    return WatermarkStatus(
        enabled=enabled,
        ready=ready,
        device=await service.device(),
        read_files=await service.read_files(),
        marks_found=await service.marks_found(),
        # Counted no further than a page. A screen that said "100,000 files waiting" would be
        # reading the whole library to say something nobody acts on.
        waiting_files=await service.waiting(),
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
    """Fetch the models. Hands back the job doing it.

    **Sift ships no models**, so this is how a fresh install becomes able to read anything, and it
    is a deliberate act by an admin rather than something that happens on enabling, because what it
    downloads is published by somebody else on their own terms.

    Answers 409 with the feature off. Downloading models for a feature nobody switched on is
    exactly the network call the switch exists to prevent.

    `again=true` fetches files that are already on disk. A model is called installed if it EXISTS;
    whether it is the RIGHT file is a separate, expensive question, and a damaged one refuses to
    load with "delete it and fetch it again", which nobody running a container should have to do
    at a shell.
    """
    if not await service.enabled():
        raise _off()
    job_id = await queue.enqueue(WATERMARK_FETCH_MODELS, {"again": again})
    log.info("watermarks.models.requested", job_id=job_id)
    return ModelsFetching(job_id=job_id)


@router.delete("/watermarks/reads", dependencies=[Depends(csrf_protect)])
async def forget_reads(
    service: Annotated[WatermarkService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> ReadingsRemoved:
    """Throw away what was read, so the library is read again.

    **The filings are left exactly where they are**, and that is the difference between this and
    undoing them. What goes is the record of having looked, which is what makes the next sweep read
    the library again: somebody who has changed a setting or suspects a bad pass wants that, and
    they do not want every site Sift filed a file under to vanish with it. A filing is taken back
    one file at a time, on that file, where the evidence for it is.

    Answers on an install where the feature is off, because clearing what an earlier decision left
    behind is exactly what somebody does after switching it off.
    """
    return ReadingsRemoved(removed=await service.forget_reads())
