# SPDX-License-Identifier: AGPL-3.0-or-later
"""The endpoints this feature has. The ones that SPEND anything are admin-only.

Describing a library is a property of the whole install rather than a personal preference, and
everything that reports on that or spends the machine's time and network on it takes an admin.
There is no asset a caller chooses in any of those, so refusing a guest outright leaks nothing,
unlike a per-file route, where "admins only" would confirm that a file exists.

Three are open to anybody signed in, and each is a question a SEARCH has: whether the box should
offer the control, how much of what this user can see has been described, and what one file
looks like. They answer counts and files that are scoped to whoever asked, never the install's
reasons: those are a sentence about models, devices and add-ons that belongs to an admin.

Hiding the section in the client is a courtesy to whoever is using it, never the control. These
checks are the control.
"""

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
    """How many of a Build's tasks are outstanding.

    By family rather than by job name, because a feature does not import another's job names,
    and a Build is the kernel's own family, known to every feature that reads the queue.
    """
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
    """What this install can do, and why not when it cannot.

    Answers on every install, including one whose SQLite cannot load the add-on: that is the
    whole point of it. A screen that could not ask would have to guess, and guessing "unavailable"
    for a feature that is merely switched off is how somebody ends up restarting a container to fix
    a switch.
    """
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
        # Asked of the queue rather than inferred from the file counts. A run stopped halfway
        # leaves exactly as many files undone as a run still going, so without this a screen
        # cannot tell a bar that should be moving from one that never will again. A Build's
        # tasks count: its Meaning row describes files through this feature's own per-file work.
        running_jobs=await queue.outstanding(SEMANTIC_DESCRIBE) + await _building(queue),
        problem=readiness.problem,
        described_by_another_model=(
            await service.described_by_others() if readiness.by_another_model else 0
        ),
        installed=sorted(
            weight_id for weight_id, weight in weights.CATALOG.items() if store.installed(weight)
        ),
    )


@router.get("/semantic/available")
async def read_available(
    service: Annotated[SemanticService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> SemanticAvailable:
    """Whether the search box should offer to search by meaning.

    Open to anybody signed in, unlike the rest of this feature, because it is what a search box
    needs and a search box is not an admin surface. It says yes or no and never why: the reason is
    about models, devices and add-ons, which is admin business.

    A client that ignores this and asks for the order anyway gets the ordinary one, so this is a
    courtesy that stops a control being offered where it would do nothing, not a permission.
    """
    readiness = await service.readiness()
    return SemanticAvailable(available=readiness.ready)


@router.get("/semantic/coverage")
async def read_coverage(
    service: Annotated[SemanticService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> SemanticCoverage:
    """How much of what this user can see has been described.

    Open to anybody signed in, for the reason the courtesy above is, and safely for a different
    one: both counts are scoped to this viewer by the statement that produces them, so neither
    says whether anything hidden exists.

    Asked by the screen that shows a set of results found by meaning, and by nothing else. That is
    where it earns its cost (one count over the library) and it is why this is a route of its
    own rather than two more fields on the courtesy read: that one is asked once by every client
    on every page load, including the many that never search by meaning at all.

    Zero on an install that cannot run the feature. There is no model, so there is no revision to
    count against, and a sentence about how much has been described is not one to draw at all,
    which the caller decides from the numbers rather than from a third field saying so.
    """
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
    """Fetch the models this install is set to use. Hands back the job doing it.

    **Sift ships no models**, so this is how a fresh install becomes able to describe anything,
    and it is a deliberate act by an admin rather than something that happens on enabling, because
    what it downloads is published by somebody else on their own terms.

    Queued, as several hundred megabytes outlast a request; a second press joins the download
    already waiting or under way.

    Answers 409 with the feature off. Downloading models for a feature nobody switched on is
    exactly the network call the switch exists to prevent.

    `again=true` fetches files that are already on disk. A model is called installed if it EXISTS;
    whether it is the RIGHT file is a separate, expensive question, and a damaged one refuses to
    load with "delete it and fetch it again", which nobody running a container should have to do
    at a shell. Without this the button that offers exactly that would download nothing and
    report success, which is the worst of the three possible behaviours.
    """
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
    """Throw the whole index away, as a job. Hands back how many moments go and the job.

    Deliberately separate from switching the feature off, which keeps what was built, and
    answered with the feature off, since removing what it left is what somebody does next. A job,
    because on a large library it is minutes of batched writes that nothing else may wait on.
    """
    removed = await service.indexed_frames()
    job_id = await queue.enqueue(
        SEMANTIC_FORGET, {}, dedupe=True, priority=WAITED_ON_PRIORITY, requested_by=viewer.id
    )
    return IndexRemoved(removed_frames=removed, job_id=job_id)


#: How many lookalikes one page carries. A handful is what the question is for: somebody looking
#: at a file wants the few nearest, not a second library.
SIMILAR_PAGE = 24


def _nothing_found() -> SimilarPage:
    """The answer to "what looks like this" when there is nothing to say.

    Two cases reach it: an id that names no file, and an id naming one this user may not have.
    They must be the SAME reply down to the tier: a caller able to tell them apart could ask
    whether a file exists one id at a time, so it is written once here rather than at each of
    them. The cheap tier, because that is what an unknown id already produces on every path through
    the service: nothing has described it, and there is no fingerprint of it to compare.

    A function rather than a constant: a response model carries a list, and one shared instance
    handed to every caller is a mutable default waiting to be found.
    """
    return SimilarPage(tier=Tier.MATCHES.value)


@router.get("/assets/{asset_id}/similar")
async def find_similar(
    asset_id: str,
    service: Annotated[SemanticService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> SimilarPage:
    """What else looks like this file.

    **Not admin-only, unlike everything else here**, and the difference is the point: this is a way
    of browsing rather than a control over the install. Anybody signed in may ask it.

    Which is why the lookalikes are ranked among this user's own files, so a hidden one can neither
    fill the strip nor shorten it, and the answer still goes back through the ordinary read, the
    one opinion about concealment every other screen uses.

    A file the viewer cannot see answers with nothing found rather than refusing, for the same
    reason every per-asset route does: a refusal that differs from an empty answer is a way to ask
    whether a file exists.

    That answer is given HERE, before the lookalikes are worked out at all, and the order is the
    whole of it. Asking first and resolving afterwards narrows the RESULTS correctly and still
    answers the question that was asked: the arithmetic runs on a file the caller may not have, and
    a populated page comes back for it while an invented id comes back empty. Those two replies
    differ, so the pair of them is an existence oracle, and the page itself says which of the
    files this user CAN see resemble one it cannot, which is a description of the concealed file
    assembled out of visible ones.

    `can_view` and not `get_asset`, so a concealed subject answers nothing while Hidden is shut.
    That is the same rule the results are filtered by below; asking it of the subject by a looser
    test would let the placeholder tile be used to describe what it is a placeholder for.
    """
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
            # A concealed file is not offered as a lookalike while this user's Hidden is shut:
            # the same rule the grid applies, applied here rather than invented here.
            if not (view.concealed and not viewer.show_hidden)
        ],
    )
