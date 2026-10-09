# SPDX-License-Identifier: AGPL-3.0-or-later
"""The endpoints this feature has; the ones that spend anything are admin-only."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from sift.kernel import wiring
from sift.kernel.access import (
    SIMILARITY,
    AssetFilter,
    Repository,
    Viewer,
    Where,
)
from sift.kernel.jobs import WAITED_ON_PRIORITY, JobQueue, family_of
from sift.kernel.jobs.families import Family
from sift.kernel.jobs.switchboard import one_reading
from sift.kernel.log import get_logger
from sift.kernel.wiring import part_of
from sift.slices.auth import csrf_protect, current_viewer, require_admin
from sift.slices.semantic import weights
from sift.slices.semantic.jobs import (
    SEMANTIC_DESCRIBE,
    SEMANTIC_FETCH_MODELS,
    SEMANTIC_FORGET,
)
from sift.slices.semantic.models import (
    IndexRemoved,
    ModelsFetchStarted,
    SemanticAvailable,
    SemanticCoverage,
    SemanticStatus,
    SimilarItem,
    SimilarPage,
)
from sift.slices.semantic.service import SERVICE, SemanticService
from sift.slices.semantic.similar import Tier

log = get_logger(__name__)

router = APIRouter(tags=["semantic"])


def _service(request: Request) -> SemanticService:
    return part_of(request, SERVICE)


def _off() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="Search by meaning is switched off.",
    )


async def _building(queue: JobQueue) -> int:
    """How many of a Build's tasks are outstanding, by family rather than by job name."""
    unfinished = await queue.unfinished_by_type()
    return sum(
        count for job_type, count in unfinished.items() if family_of(job_type) is Family.IDENTIFY
    )


@router.get("/semantic/status")
async def read_status(
    service: Annotated[SemanticService, Depends(_service)],
    queue: Annotated[JobQueue, Depends(wiring.queue)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> SemanticStatus:
    """What this install can do, and why not when it cannot; answers on every install."""
    async with one_reading():
        readiness = await service.readiness()
        store = weights.store(service.settings)
        return SemanticStatus(
            supported=readiness.supported,
            enabled=readiness.enabled,
            ready=readiness.ready,
            family=readiness.family,
            device=readiness.device,
            indexed_frames=await service.indexed_frames(),
            described_files=await service.described_count(),
            waiting_files=await service.waiting_count(viewer) if readiness.supported else 0,
            unread_files=await service.unread_count() if readiness.ready else 0,
            # Asked of the queue: a stopped run leaves as many files undone as a running one.
            running_jobs=await queue.outstanding(SEMANTIC_DESCRIBE) + await _building(queue),
            problem=readiness.problem,
            described_by_another_model=(
                await service.described_by_others() if readiness.by_another_model else 0
            ),
            installed=sorted(
                weight_id
                for weight_id, weight in weights.CATALOG.items()
                if store.installed(weight)
            ),
        )


@router.get("/semantic/available")
async def read_available(
    service: Annotated[SemanticService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> SemanticAvailable:
    """Whether the search box should offer to search by meaning; yes or no, never why."""
    readiness = await service.readiness()
    return SemanticAvailable(available=readiness.ready)


@router.get("/semantic/coverage")
async def read_coverage(
    service: Annotated[SemanticService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> SemanticCoverage:
    """How much of what this user can see has been described, scoped to this viewer."""
    readiness = await service.readiness()
    if not readiness.ready:
        return SemanticCoverage()
    counted = await service.coverage(viewer)
    return SemanticCoverage(described=counted.described, library=counted.library)


@router.post("/semantic/models/fetch", dependencies=[Depends(csrf_protect)])
async def fetch_models(
    service: Annotated[SemanticService, Depends(_service)],
    queue: Annotated[JobQueue, Depends(wiring.queue)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    again: bool = False,
) -> ModelsFetchStarted:
    """Fetch the models this install is set to use, as a job; `again` fetches present files too."""
    if not await service.enabled():
        raise _off()
    newest = [job.id for job in await queue.newest_of(SEMANTIC_FETCH_MODELS, limit=1)]
    live = await queue.unfinished_among(newest)
    job_id = (
        live.pop()
        if live
        else await queue.enqueue(SEMANTIC_FETCH_MODELS, {"again": again}, dedupe=True)
    )
    log.info("semantic.models.requested", job_id=job_id)
    return ModelsFetchStarted(job_id=job_id)


@router.delete("/semantic/index", dependencies=[Depends(csrf_protect)])
async def remove_index(
    service: Annotated[SemanticService, Depends(_service)],
    queue: Annotated[JobQueue, Depends(wiring.queue)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> IndexRemoved:
    """Throw the whole index away, as a job: how many moments go, and the job."""
    removed = await service.indexed_frames()
    job_id = await queue.enqueue(
        SEMANTIC_FORGET, {}, dedupe=True, priority=WAITED_ON_PRIORITY, requested_by=viewer.id
    )
    return IndexRemoved(removed_frames=removed, job_id=job_id)


SIMILAR_PAGE = 24


def _nothing_found() -> SimilarPage:
    """One empty answer for an unknown id and an unseen file, so the two cannot be told apart."""
    return SimilarPage(tier=Tier.MATCHES.value)


@router.get("/assets/{asset_id}/similar")
async def find_similar(
    asset_id: str,
    service: Annotated[SemanticService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> SimilarPage:
    """What else looks like this file, ranked among this user's files once `can_view` says yes."""
    if not await access.can_view(viewer, asset_id):
        return _nothing_found()

    found = await service.similar_to(asset_id, asker=viewer)
    if not found.neighbours:
        return SimilarPage(tier=found.tier.value)

    ids = [candidate for candidate, _ in found.neighbours]
    page = await access.visible_assets(
        viewer,
        limit=SIMILAR_PAGE,
        offset=0,
        asset_filter=AssetFilter(where=Where("assets", tuple(ids)), neighbours=found.neighbours),
        sort=SIMILARITY,
    )
    return SimilarPage(
        tier=found.tier.value,
        items=[
            SimilarItem(
                id=view.asset.id,
                media_type=view.asset.media_type,
                width=view.asset.width,
                height=view.asset.height,
                duration_ms=view.asset.duration_ms,
                art=view.art_version,
            )
            for view in page.items
            # A concealed file is not offered as a lookalike while this user's Hidden is shut.
            if not (view.concealed and not viewer.show_hidden)
        ],
    )
