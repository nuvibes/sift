# SPDX-License-Identifier: AGPL-3.0-or-later
"""The lists the Faces screens draw: a file's faces, the groups, Identified and To check."""

from __future__ import annotations

from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    Query,
)

from sift.kernel import wiring
from sift.kernel.access import Repository, Viewer
from sift.kernel.paging import MAX_PAGE_SIZE, resume_at
from sift.kernel.serving import face_version
from sift.slices.auth import current_viewer, require_admin
from sift.slices.faces.models import (
    Attribution,
    PileStatus,
    StartersShow,
    ToCheckKind,
    ToCheckShow,
)
from sift.slices.faces.models_http import (
    MAX_PILE_FACES,
    PILES_PER_PAGE,
    GroupPage,
    GroupReasonView,
    IdentifiedCard,
    IdentifiedPage,
    MayBeGroup,
    SightingView,
    ToCheckCard,
    ToCheckPage,
)
from sift.slices.faces.router_common import (
    _card,
    _group,
    _missing,
    _service,
)
from sift.slices.faces.service import (
    FaceService,
)

router = APIRouter(tags=["faces"])


async def _searched(
    service: Annotated[FaceService, Depends(_service)],
    q: Annotated[str, Query(max_length=120)] = "",
) -> frozenset[str] | None:
    """The People a tab's search box found: whose name or an alias holds the words, or None for
    no words. A list narrows to them on the server, so the count and the pages follow it."""
    return await service.people_called(q)


# --- reading ----------------------------------------------------------------------------------


@router.get("/assets/{asset_id}/faces")
async def faces_of_asset(
    asset_id: str,
    service: Annotated[FaceService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> list[SightingView]:
    """Who is in this file, and when.

    The asset is resolved first, and a miss is the 404 an unknown id gets, so the route never
    confirms a file exists. An empty list rather than a 409 with the feature off: to the block that
    draws it, no faces and never looked are the same.
    """
    if not await access.can_view(viewer, asset_id):
        raise _missing()
    if not await service.enabled():
        return []
    art = face_version(viewer.cache_stamp)
    return [_card(s, art) for s in await service.appearances_in(viewer, asset_id)]


@router.get("/faces/groups")
async def face_groups(
    service: Annotated[FaceService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    status_filter: Annotated[PileStatus, Query(alias="status")] = PileStatus.OPEN,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = PILES_PER_PAGE,
    offset: Annotated[int, Query(ge=0)] = 0,
    start: Annotated[str | None, Query(alias="from")] = None,
    near: Annotated[int | None, Query(ge=0)] = None,
) -> GroupPage:
    """The piles of unidentified faces: Unidentified, or Discarded (stored as `ignored`).

    One route for both statuses, so they cannot be scoped differently. Paged; each pile counted as
    this viewer may see it, and one they may see none of is absent. **Admin-only**: naming the
    faces in a library is deciding who is in it.

    `from` names a row to start the page at (a durable address, unlike a page number), resolved
    against this same list. One that resolves to nothing serves its page (`near`) or the TOP,
    whatever the reason, so a link cannot ask whether something is there.
    """
    if not await service.enabled():
        return GroupPage()
    if start is not None:
        at = await service.position_of_pile(viewer, status_filter, start)
        offset = resume_at(at, near)
    views, total = await service.piles(
        viewer, status_filter, limit=MAX_PILE_FACES, page_size=limit, offset=offset
    )
    art = face_version(viewer.cache_stamp)
    return GroupPage(groups=[_group(view, art) for view in views], total=total, offset=offset)


@router.get("/faces/identified/people")
async def identified_people(
    service: Annotated[FaceService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    who: Annotated[frozenset[str] | None, Depends(_searched)],
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 24,
    offset: Annotated[int, Query(ge=0)] = 0,
    attribution: Annotated[Attribution | None, Query()] = None,
    start: Annotated[str | None, Query(alias="from")] = None,
    near: Annotated[int | None, Query(ge=0)] = None,
    starters: Annotated[StartersShow | None, Query()] = None,
) -> IdentifiedPage:
    """What Sift has attached somebody to lately, gathered by person, one card each.

    `from` anchors the page as `face_groups` explains.
    `attribution` narrows to *Matched* (Sift on its own) or *Confirmed* (somebody agreed), in SQL,
    so the count and the contents describe one set. Everybody this user may not be told about
    gathers under one nameless card, which has no anchor and which a narrowing drops.
    `starters` sets apart the People known from starter pictures alone (`only`, `without`); the
    page carries both counts (`StartersShow`). `q` is the tab's search box: the People whose name
    or an alias holds the words, narrowed on the server so the count and the pages follow it.
    """
    if not await service.enabled():
        return IdentifiedPage()
    if start is not None:
        at = await service.position_of_identified(
            viewer, start, attribution=attribution, starters=starters, who=who
        )
        offset = resume_at(at, near)
    rows, total = await service.identified_people(
        viewer, limit=limit, offset=offset, attribution=attribution, starters=starters, who=who
    )
    starters_only, others = await service.starters_apart(viewer, attribution=attribution, who=who)
    art = face_version(viewer.cache_stamp)
    return IdentifiedPage(
        people=[
            IdentifiedCard(
                person_id=card.person_id,
                person_name=card.person_name,
                size=card.size,
                waiting=card.waiting,
                matched=card.matched,
                confirmed=card.confirmed,
                surest=card.surest,
                faces=[_card(sighting, art) for sighting in card.faces],
            )
            for card in rows
        ],
        total=total,
        offset=offset,
        starters_only=starters_only,
        others=others,
    )


@router.get("/faces/to-check")
async def to_check(
    service: Annotated[FaceService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    who: Annotated[frozenset[str] | None, Depends(_searched)],
    show: Annotated[ToCheckShow, Query()] = ToCheckShow.WAITING,
    kind: Annotated[list[ToCheckKind] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = PILES_PER_PAGE,
    offset: Annotated[int, Query(ge=0)] = 0,
    start: Annotated[str | None, Query(alias="from")] = None,
    near: Annotated[int | None, Query(ge=0)] = None,
) -> ToCheckPage:
    """What is left to check: one list, the proposals first and the groups by size after them.

    The read behind the review screen and its card (`FaceService.to_check`). `show` is the list,
    the groups the floor holds back, or what was set aside. `kind` is which questions, repeatable
    so one tab can draw two tiers (Needs your input asks `person` and `may_be`); none is the whole
    list. Admin-only.

    `from` names a ROW (a group, a person, a disagreement's file), resolved in this same list by
    `FaceService.position_in_to_check`; one that resolves to nothing serves its page (`near`, see
    `resume_at`) or the top, so a link cannot ask whether something is there. `q` is the tab's
    search box: only the rows naming a person whose name or an alias holds the words.
    """
    if not await service.enabled():
        return ToCheckPage()
    if start is not None:
        at = await service.position_in_to_check(viewer, start, show=show, kind=kind, who=who)
        offset = resume_at(at, near)
    items, total, small = await service.to_check(
        viewer,
        show=show,
        kind=kind,
        limit=limit,
        offset=offset,
        faces_per_card=MAX_PILE_FACES,
        who=who,
    )
    art = face_version(viewer.cache_stamp)
    return ToCheckPage(
        items=[
            ToCheckCard(
                kind=item.kind,
                id=item.id,
                size=item.size,
                person_name=item.person_name,
                best=item.best,
                status=item.status,
                person_id=item.person_id,
                source=item.source,
                faces=[_card(sighting, art) for sighting in item.faces],
                # The groups a may-be card asks about, closest first, with the faces a Yes
                # confirms and why each is offered. Empty on the other kinds.
                groups=[
                    MayBeGroup(
                        pile_id=group.pile_id,
                        size=group.size,
                        likeness=group.likeness,
                        ticked=group.ticked,
                        faces=[_card(sighting, art) for sighting in group.faces],
                        reasons=[
                            GroupReasonView(
                                kind=reason.kind,
                                folder_id=reason.folder_id,
                                folder_name=reason.folder_name,
                                in_folder=reason.in_folder,
                                group_files=reason.group_files,
                                box_names=list(reason.box_names),
                            )
                            for reason in group.reasons
                        ],
                    )
                    for group in item.groups
                ],
            )
            for item in items
        ],
        total=total,
        offset=max(0, offset),
        small_groups=small,
    )
