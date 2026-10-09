# SPDX-License-Identifier: AGPL-3.0-or-later
"""Proposed shoots, admin-only, each picture scoped to this admin: an admin can hide things too."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status

from sift.kernel import wiring
from sift.kernel.access import Repository, Viewer
from sift.kernel.paging import MAX_PAGE_SIZE, resume_at
from sift.kernel.wire import link_of
from sift.kernel.wiring import part_of
from sift.slices.auth import csrf_protect, require_admin
from sift.slices.shoots.models import (
    MadeView,
    MakeWrite,
    NamedView,
    RefusedView,
    ShootList,
    ShootPictureView,
    ShootView,
)
from sift.slices.shoots.queue import asking
from sift.slices.shoots.service import (
    PAGE,
    SERVICE,
    AlreadyFiled,
    NotFound,
    ShootError,
    ShootService,
)
from sift.slices.shoots.store import Proposal

router = APIRouter(tags=["shoots"])


def _service(request: Request) -> ShootService:
    return part_of(request, SERVICE)


@router.get("/shoots")
async def list_shoots(
    service: Annotated[ShootService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = PAGE,
    offset: Annotated[int, Query(ge=0)] = 0,
    start: Annotated[str | None, Query(alias="from")] = None,
    near: Annotated[int | None, Query(ge=0)] = None,
) -> ShootList:
    """The shoots Sift is proposing, newest first; `from` resumes at a proposal, or near it."""
    if start is not None:
        at = await service.position_of(start)
        offset = resume_at(at, near)
    proposals, total = await service.waiting(limit=limit, offset=offset)
    drawn = []
    for proposal in proposals:
        full = await service.one(proposal.id)
        if full is None:  # pragma: no cover (read a line ago, in the same request)
            continue
        drawn.append(await _drawn(full, access, viewer))
    return ShootList(shoots=drawn, total=total, offset=offset, auto_file=await service.auto_file())


@router.get("/shoots/{proposal_id}")
async def one_shoot(
    proposal_id: str,
    service: Annotated[ShootService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> ShootView:
    """One proposed shoot, drawn as the card is; answered or wholly hidden is not found."""
    full = await service.one(proposal_id)
    # The proposal row outlives its answer, so the link is what says it is answered.
    if full is None or await service.made_from(proposal_id) is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That shoot is no longer waiting.")
    view = await _drawn(full, access, viewer)
    if not view.items:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That shoot is no longer waiting.")
    return view


async def _drawn(full: Proposal, access: Repository, viewer: Viewer) -> ShootView:
    """One proposal as a card, its pictures and every number on it scoped to this viewer."""
    views = await access.assets_of(viewer, full.asset_ids)
    items = [
        ShootPictureView(
            id=asset_id,
            art=views[asset_id].art_version,
            named=asset_id not in full.unnamed_ids,
            media_type=views[asset_id].asset.media_type,
        )
        for asset_id in full.asset_ids
        if asset_id in views
    ]
    pictures = len(items)
    unnamed = sum(1 for item in items if not item.named)
    asked = asking(full.person_id, full.name, pictures, unnamed)
    return ShootView(
        id=full.id,
        person_id=full.person_id,
        name=full.name,
        found_at=full.found_at,
        # Off the pictures drawn, not the store's tallies: those would count hidden files.
        pictures=pictures,
        unnamed=unnamed,
        question=asked.question,
        detail=asked.detail,
        links=[link_of(one) for one in asked.names],
        items=items,
    )


@router.post("/shoots/{proposal_id}/make", dependencies=[Depends(csrf_protect)])
async def make_the_set(
    proposal_id: str,
    service: Annotated[ShootService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    body: Annotated[MakeWrite | None, Body()] = None,
) -> MadeView:
    """Create the Photo Set, named as typed or after the proposal; 409 if already filed."""
    try:
        made = await service.make(viewer, proposal_id, name=None if body is None else body.name)
    except AlreadyFiled as refused:
        raise HTTPException(status.HTTP_409_CONFLICT, str(refused)) from refused
    except NotFound as refused:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(refused)) from refused
    except ShootError as refused:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(refused)) from refused
    return MadeView(
        photo_set_id=made.photo_set_id,
        pictures=made.pictures,
        decision_id=made.decision_id,
        name=made.name,
    )


@router.post("/shoots/{proposal_id}/refuse", dependencies=[Depends(csrf_protect)])
async def not_a_set(
    proposal_id: str,
    service: Annotated[ShootService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> RefusedView:
    """Not a shoot: refused per picture, as the greedy grouping moves between passes."""
    try:
        refused = await service.refuse(viewer, proposal_id)
    except NotFound as missing:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(missing)) from missing
    return RefusedView(refused=refused)


@router.post("/shoots/{proposal_id}/name-the-rest", dependencies=[Depends(csrf_protect)])
async def name_the_rest(
    proposal_id: str,
    service: Annotated[ShootService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> NamedView:
    """Put the creator on the nameless pictures: its own decision, receipt and undo."""
    try:
        named = await service.name_the_rest(viewer, proposal_id)
    except NotFound as missing:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(missing)) from missing
    except ShootError as refused:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(refused)) from refused
    return NamedView(files=named.files, decision_id=named.decision_id)
