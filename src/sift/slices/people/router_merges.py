# SPDX-License-Identifier: AGPL-3.0-or-later
"""Merging several people or several Sites into one, and weighing a merge before it is made."""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    status,
)

from sift.kernel import wiring
from sift.kernel.access import (
    Repository,
    Viewer,
)
from sift.kernel.ledger import Actor
from sift.kernel.seams import ReindexSeam
from sift.slices.auth import csrf_protect, require_admin
from sift.slices.people.merge import merge_many, weigh_many
from sift.slices.people.models import (
    MergeSeveral,
    MergeSeveralSites,
    MergeWeighed,
)
from sift.slices.people.router_base import _missing, _require_person, _service, _visible_site_or_404
from sift.slices.people.service import (
    PeopleService,
)
from sift.slices.people.site_merge import BothHaveALogin
from sift.slices.people.site_merge import merge_many as merge_sites
from sift.slices.people.site_merge import weigh_many as weigh_sites

router = APIRouter(tags=["people"])

# --- two people who turn out to be one --------------------------------------------------------


@router.post("/people/merge", dependencies=[Depends(csrf_protect)])
async def merge_several(
    body: MergeSeveral,
    request: Request,
    service: Annotated[PeopleService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    reindexer: Annotated[ReindexSeam, Depends(wiring.reindexer)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> MergeWeighed:
    """Fold several people into one, in a single transaction.

    **This cannot be taken back, and that is exactly why it is one call.**
    """
    await _require_person(service, viewer, body.into)
    going = [one for one in dict.fromkeys(body.people) if one != body.into]
    if not going:
        raise HTTPException(status.HTTP_409_CONFLICT, "that is the same person")
    for one in going:
        await _require_person(service, viewer, one)
    weighed = await merge_many(
        wiring.part_of(request, wiring.DATABASE),
        access,
        losing=going,
        keeping=body.into,
        actor=Actor.user(viewer.id),
    )
    if weighed is None:  # pragma: no cover (every id was resolved a line ago)
        raise _missing()
    # The one write in this slice that genuinely CANNOT name its files, which is why it queues a
    # rebuild where the renames around it name theirs. A merge does not only move `asset_people`
    # rows: it moves the USERNAMES too, and a username can point at somebody while none of its files
    # carry them, so the files that gain the survivor's name are not the union of the people's
    # files, and reading them beforehand would mean keeping a second copy of what a merge moves,
    # here, in step with the transaction that does it. A merge is rare and deliberate; a rebuild
    # after one is the honest price.
    await reindexer.renamed()
    return MergeWeighed(**asdict(weighed))


@router.post("/people/weigh-merge", dependencies=[Depends(csrf_protect)])
async def weigh_several(
    body: MergeSeveral,
    request: Request,
    service: Annotated[PeopleService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> MergeWeighed:
    """What folding several people into one would move, added up. Nothing is written."""
    await _require_person(service, viewer, body.into)
    going = [one for one in dict.fromkeys(body.people) if one != body.into]
    if not going:
        raise HTTPException(status.HTTP_409_CONFLICT, "that is the same person")
    for one in going:
        await _require_person(service, viewer, one)
    weighed = await weigh_many(
        wiring.part_of(request, wiring.DATABASE), losing=going, keeping=body.into
    )
    if weighed is None:  # pragma: no cover (every id was resolved a line ago)
        raise _missing()
    return MergeWeighed(**asdict(weighed))


# --- two sites that turn out to be one ----------------------------------------------------------


async def _require_site(access: Repository, viewer: Viewer, site_id: str) -> None:
    """The site, if this viewer may act on it at all. 404 otherwise, either way."""
    await _visible_site_or_404(access, viewer, site_id)


def _sites_going(body: MergeSeveralSites) -> list[str]:
    """The set, with the survivor dropped rather than refused."""
    going = [one for one in dict.fromkeys(body.sites) if one != body.into]
    if not going:
        raise HTTPException(status.HTTP_409_CONFLICT, "that is the same site")
    return going


@router.post("/sites/weigh-merge", dependencies=[Depends(csrf_protect)])
async def weigh_several_sites(
    body: MergeSeveralSites,
    request: Request,
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> MergeWeighed:
    """What folding several sites into one would move, added up. Nothing is written."""
    await _require_site(access, viewer, body.into)
    going = _sites_going(body)
    for one in going:
        await _require_site(access, viewer, one)
    weighed = await weigh_sites(
        wiring.part_of(request, wiring.DATABASE), losing=going, keeping=body.into
    )
    if weighed is None:  # pragma: no cover (every id was resolved a line ago)
        raise _missing()
    return MergeWeighed(**asdict(weighed))


@router.post("/sites/merge", dependencies=[Depends(csrf_protect)])
async def merge_several_sites(
    body: MergeSeveralSites,
    request: Request,
    access: Annotated[Repository, Depends(wiring.access)],
    reindexer: Annotated[ReindexSeam, Depends(wiring.reindexer)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> MergeWeighed:
    """Fold several sites into one, in a single transaction.

    **This cannot be taken back, and that is exactly why it is one call.**
    """
    await _require_site(access, viewer, body.into)
    going = _sites_going(body)
    for one in going:
        await _require_site(access, viewer, one)
    try:
        weighed = await merge_sites(
            wiring.part_of(request, wiring.DATABASE),
            access,
            losing=going,
            keeping=body.into,
            actor=Actor.user(viewer.id),
        )
    except BothHaveALogin as both:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Both of these sites have cookies saved, and only one can be kept. "
            "Remove one of the two logins first, then merge them.",
        ) from both
    if weighed is None:  # pragma: no cover (every id was resolved a line ago)
        raise _missing()
    # Queued, for the same reason the people merge above queues: this moves usernames between sites,
    # and the files that change are decided inside the transaction that moves them rather than by
    # any list this route could read first.
    await reindexer.renamed()
    return MergeWeighed(**asdict(weighed))
