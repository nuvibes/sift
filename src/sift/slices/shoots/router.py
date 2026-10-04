# SPDX-License-Identifier: AGPL-3.0-or-later
"""The review surface for proposed shoots. Admin-only, and the server is what says so.

`require_admin` sits on every route. That is necessary and it is not sufficient, which is why the
service resolves every picture of a proposal against the user asking as well: an admin can
conceal things from themselves, and a list built for "an admin" rather than for *this* admin would
show them back what they hid. A proposal holding one file this user may not be shown answers as
though it were not there, which is the same answer an invented id gets.
"""

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
    """The shoots Sift is proposing, newest first, with the pictures of each.

    The pictures come back through the read that decides visibility, so a proposal drawn here shows
    exactly what this user may be shown. `total` is the count of proposals and not of pictures:
    a figure larger than what the page draws would publish, in the difference, how many files this
    user is not being told about.

    **And so is every number on a card.** The rule is one rule and it applies to each of the three:
    a proposal's own tallies are the store's, and the store scopes nothing. See the note where the
    card is built.

    `from` names a proposal to start the page at, instead of an offset: the row the wall was left
    at, carried in its address so the way back lands there (the other walls' `from`, one rule). A
    `from` that is not on the list serves the page it was on (`near`), or the TOP,
    rather than refusing: answering a proposal is
    what takes it off, so on a queue that is the ordinary case, not an error.
    """
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
    """One proposed shoot, with every picture of it: the page a card on the wall opens.

    The same view the wall's card is built from (`_drawn`), so the page and the card cannot say two
    things about one shoot. A proposal already answered, or one this user may be shown nothing
    of, is not found: a card with no pictures is not a question anybody can answer.
    """
    full = await service.one(proposal_id)
    # Answered is the link, and the proposal row outlives it (see `ShootService.take_back`), so the
    # row being there says nothing about whether the question still stands.
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
        # BOTH NUMBERS COME OFF THE PICTURES THIS CARD ACTUALLY DRAWS, and that closes a leak
        # rather than tidying up. The proposal's own tallies (`full.pictures` and
        # `len(full.unnamed_ids)`) are counted by the store, which decides nothing about who
        # may be told anything. Read off them, a shoot holding a file in a locked vault would
        # say "6 pictures" over a strip of 2, and the difference would be how many pictures
        # the vault is holding back, written on the card in a number.
        #
        # The list route scopes the count of PROPOSALS for exactly that reason, and these two
        # numbers follow the same rule.
        pictures=pictures,
        unnamed=unnamed,
        # The card's two sentences, off the same scoped numbers. See `asking`.
        question=asked.question,
        detail=asked.detail,
        # Who those two sentences name, so the page links the person's name to their page.
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
    """Yes: make the Photo Set, through the same derivation a folder of pictures goes through.

    The body is optional and so is the name in it. The main half of the page's "Create Photo Set"
    presses this with nothing, and the set takes the proposal's name. "Create with a name..." sends `{"name": ...}` and the set is
    called that instead; the receipt records which (`service._payload`).

    A 409 for a card whose pictures are in a Photo Set already: the press conflicts with the
    library as it stands, and the card is answered by that set as it is refused, so a re-read of
    the page no longer draws it.
    """
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
    """Not a shoot. Remembered against the pictures, so no rearrangement of them comes back.

    Permanent, and by picture rather than by grouping: the rule that finds a shoot is greedy and
    its grouping moves, so a no tied to one grouping is a no the next pass would route around.
    """
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
    """Put the creator on the pictures of this shoot that carry nobody.

    Its own route rather than a flag on the one above, because it is its own decision: agreeing that
    pictures belong together is not agreeing who is in them, and each has its own receipt so either
    can be taken back without the other.
    """
    try:
        named = await service.name_the_rest(viewer, proposal_id)
    except NotFound as missing:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(missing)) from missing
    except ShootError as refused:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(refused)) from refused
    return NamedView(files=named.files, decision_id=named.decision_id)
