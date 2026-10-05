# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Sites screen: the wall, its facets, and one Site read, made, changed, hidden or deleted."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)

from sift.kernel import wiring
from sift.kernel.access import (
    ADMIN_ENTITY_FACETS,
    ENTITY_FACETS,
    ENTITY_SORT_KEYS,
    ENTITY_SORT_SEEN,
    EntityNarrowing,
    ObjectType,
    Repository,
    Viewer,
    asks_disagreements,
    related_filter,
)
from sift.kernel.access.history import DEFAULT_LIMIT, MAX_LIMIT
from sift.kernel.access.history_entity import history_of_site
from sift.kernel.db import Database
from sift.kernel.ledger import Actor
from sift.kernel.paging import MAX_PAGE_SIZE, resume_at
from sift.kernel.seams import DisagreementSeam, ForgetGoneSeam, ReindexSeam
from sift.kernel.serving import face_version
from sift.kernel.text import clean_token_text
from sift.kernel.wire import FacetCounts, FacetValue, HistoryEvent, history_event
from sift.kernel.workbench import Workbench
from sift.slices.auth import csrf_protect, current_viewer, require_admin, require_vault_pin
from sift.slices.people.models import (
    MAX_NAME,
    SiteDetailsWrite,
    SiteList,
    SiteRecordWrite,
    SiteView,
    VaultWrite,
)
from sift.slices.people.router_base import (
    _NOT_FOUND,
    _disagreement_seam,
    _missing,
    _service,
    _site_from_suggestion,
    _site_loop,
    _site_scoped,
    _site_written,
    _visible_site_or_404,
)
from sift.slices.people.service import (
    PeopleService,
    SiteLoop,
)

router = APIRouter(tags=["people"])


class SitesNarrowing:
    """What the SITES on the wall are: the network they are part of, and their own tags."""

    def __init__(
        self,
        parent: Annotated[list[str] | None, Query()] = None,
        tags: Annotated[list[str] | None, Query()] = None,
        linked: Annotated[list[str] | None, Query()] = None,
        # See `PeopleNarrowing` above: which boxes wrote, beside the older yes-or-no.
        enriched: Annotated[list[str] | None, Query()] = None,
        usernames: Annotated[list[str] | None, Query()] = None,
        cover: Annotated[list[str] | None, Query()] = None,
        created: Annotated[list[str] | None, Query()] = None,
        sharing: Annotated[list[str] | None, Query()] = None,
        # See `PeopleNarrowing` above: the one key here that is not a column of the table.
        disagrees: Annotated[list[str] | None, Query()] = None,
    ) -> None:
        self.picks: dict[str, list[str] | None] = {
            "parent": parent,
            "tags": tags,
            "linked": linked,
            "enriched": enriched,
            "usernames": usernames,
            "cover": cover,
            "created": created,
            "sharing": sharing,
            "disagrees": disagrees,
        }

    async def of(
        self,
        viewer: Viewer,
        waiting: DisagreementSeam,
        *,
        counting: str = "",
    ) -> EntityNarrowing:
        """The picks as the one conjunct the statement takes. See `PeopleNarrowing.of` above, which
        carries the whole of the reasoning about the one facet that is not a column."""
        disagreeing = (
            await waiting.subjects_with_disagreements(viewer, "site")
            if asks_disagreements(self.picks, counting)
            else None
        )
        return EntityNarrowing.of(
            "site", self.picks, is_admin=viewer.is_admin, disagreeing=disagreeing
        )


@router.get("/sites")
async def list_sites(
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    narrowed: Annotated[SitesNarrowing, Depends()],
    waiting: Annotated[DisagreementSeam, Depends(_disagreement_seam)],
    prefix: Annotated[str, Query(max_length=MAX_NAME)] = "",
    anywhere: Annotated[bool, Query()] = False,
    sort: Annotated[str, Query()] = ENTITY_SORT_SEEN,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    start: Annotated[str | None, Query(alias="from")] = None,
    near: Annotated[int | None, Query(ge=0)] = None,
    person: Annotated[str | None, Query()] = None,
    tag: Annotated[str | None, Query()] = None,
    collection: Annotated[str | None, Query()] = None,
    photo_set: Annotated[str | None, Query()] = None,
    song: Annotated[str | None, Query()] = None,
    count: Annotated[Literal["whole", "narrowed"], Query()] = "whole",
) -> SiteList:
    """One page of the sites this viewer may know about, with what they can see from each."""
    if sort not in ENTITY_SORT_KEYS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"unknown sort {sort!r}")
    narrowing = related_filter(
        person=person, tag=tag, collection=collection, photo_set=photo_set, song=song
    )
    # Read once and handed to both, because the position and the page have to be taken in the same
    # list, and this one costs a query of its own.
    rows = await narrowed.of(viewer, waiting)
    if start is not None:
        at = await access.position_of_site(
            viewer,
            start,
            prefix,
            anywhere=anywhere,
            sort=sort,
            asset_filter=narrowing,
            narrowing=rows,
            count_narrowed=count == "narrowed",
        )
        offset = resume_at(at, near)
    page = await access.list_sites(
        viewer,
        prefix,
        anywhere=anywhere,
        limit=limit,
        offset=offset,
        sort=sort,
        asset_filter=narrowing,
        narrowing=rows,
        count_narrowed=count == "narrowed",
    )
    marks = await access.visible_marks(viewer, ObjectType.SITE, [site.id for site in page.items])
    # What each card draws beside its name, read for this page only. See `Repository.card_counts`.
    counts = await access.card_counts(viewer, "site", [site.id for site in page.items])
    return SiteList(
        items=[
            _site_from_suggestion(
                site,
                counts.get(site.id, {}),
                marks.get(site.id),
                art=face_version(viewer.cache_stamp),
            )
            for site in page.items
        ],
        total=page.total,
        limit=limit,
        offset=offset,
    )


@router.get("/sites/facets")
async def site_facets(
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    narrowed: Annotated[SitesNarrowing, Depends()],
    waiting: Annotated[DisagreementSeam, Depends(_disagreement_seam)],
    facet: Annotated[str, Query()],
    limit: Annotated[int, Query(ge=1, le=200)] = 24,
    prefix: Annotated[str, Query(max_length=MAX_NAME)] = "",
    anywhere: Annotated[bool, Query()] = False,
    person: Annotated[str | None, Query()] = None,
    tag: Annotated[str | None, Query()] = None,
    collection: Annotated[str | None, Query()] = None,
    photo_set: Annotated[str | None, Query()] = None,
    song: Annotated[str | None, Query()] = None,
) -> FacetCounts:
    """What the sites this wall reaches are made of, along one dimension, with counts."""
    if facet not in ENTITY_FACETS["site"] or (facet in ADMIN_ENTITY_FACETS and not viewer.is_admin):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"unknown facet {facet!r}")
    counted = await access.site_facets(
        viewer,
        facet,
        limit=limit,
        prefix=prefix,
        anywhere=anywhere,
        asset_filter=related_filter(
            person=person, tag=tag, collection=collection, photo_set=photo_set, song=song
        ),
        narrowing=await narrowed.of(viewer, waiting, counting=facet),
    )
    return FacetCounts(
        facet=facet,
        values=[FacetValue(value=one.value, count=one.count, label=one.label) for one in counted],
    )


@router.get("/sites/{site_id}")
async def read_site(
    site_id: str,
    service: Annotated[PeopleService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> SiteView:
    """One Site, as its own page reads it. See `read_person` for why this exists."""
    found = await access.visible_site(viewer, site_id)
    if found is None:
        raise _missing()
    marks = await access.visible_marks(viewer, ObjectType.SITE, [found.id])
    cells = await access.card_counts(viewer, "site", [found.id])
    view = _site_from_suggestion(
        found, cells.get(found.id, {}), marks.get(found.id), art=face_version(viewer.cache_stamp)
    )
    # Read separately, for the reason a person's is: whether this site may be SHOWN is answered
    # above by the same statement the wall uses, and what it holds is asked of the slice that owns
    # those rows, which keeps a record off every row of the wall that shares that statement.
    return view.model_copy(
        update={
            "record": await service.site_record(site_id),
            "o_count": await access.o_count_of_site(viewer, found.id),
        }
    )


@router.get("/sites/{site_id}/history")
async def history_of_a_site(
    site_id: str,
    access: Annotated[Repository, Depends(wiring.access)],
    database: Annotated[Database, Depends(wiring.database)],
    workbench: Annotated[Workbench, Depends(wiring.workbench)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> list[HistoryEvent]:
    """What happened to this site, oldest first."""
    if await access.visible_site(viewer, site_id) is None:
        raise _missing()
    # The registry is asked which kinds of decision can never be taken back, and the answer is
    # handed to the read so it offers no Undo on those: the same question the file's history and
    # a person's ask, from the same place, because an affordance the server would refuse is worse
    # than none. Asked of the REVERSERS rather than of the queues: a decision written by a queue
    # that has since been retired is still one somebody can take back.
    final = [one.name for one in workbench.reversers if not one.reversible]
    return [
        history_event(event)
        for event in await history_of_site(
            database, viewer, site_id, limit=limit, final_queues=final, bench=workbench
        )
    ]


@router.post("/sites", status_code=status.HTTP_201_CREATED, dependencies=[Depends(csrf_protect)])
async def create_site(
    body: SiteRecordWrite,
    service: Annotated[PeopleService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> SiteView:
    """Add a site, or hand back the one already carrying that name.

    **A name that lands on a site this caller may not be shown is the 404 an unknown id gets**
    """
    created = await service.create_site(viewer, body.name)
    # The details ride on the same request, so an address that is not one or a parent that makes
    # a loop is refused by the body before any Site is made, and the New page is one write rather
    # than a create followed by a write that could leave a bare Site behind. Absent details are
    # left alone: a name on its own still hands back the Site already carrying it, untouched.
    sent = body.model_fields_set - {"name"}
    if sent:
        await _write_site_details(service, viewer, created.id, body, sent)
    written = await _site_scoped(access, viewer, created.id)
    if written is None:
        raise _missing()
    return written


@router.put("/sites/{site_id}", dependencies=[Depends(csrf_protect)])
async def update_site(
    site_id: str,
    body: SiteRecordWrite,
    service: Annotated[PeopleService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    reindexer: Annotated[ReindexSeam, Depends(wiring.reindexer)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> SiteView:
    """Rename a site, and write whichever of its details the caller sent, as one save."""
    await _visible_site_or_404(access, viewer, site_id)
    sent = body.model_fields_set - {"name"}
    if "parent" in sent and body.parent:
        if _one_site_name(body.parent) == _one_site_name(body.name):
            raise _site_loop()
        try:
            await service.refuse_a_site_loop(site_id, body.parent)
        except SiteLoop:
            raise _site_loop() from None
    updated = await service.update_site(viewer, site_id, body.name)
    if updated.name != body.name:
        raise _site_name_taken(body.name)
    await _write_site_details(service, viewer, site_id, body, sent)
    # A site's name and its other names are indexed on every file attributed to a username on it,
    # and this route can walk to those files through the usernames. Neither write moves one of
    # them, so reading afterwards gives the same answer.
    await reindexer.touched_many(await service.assets_of_site(site_id))
    # The wall's own row, cover fields and `art` included: the screen puts this reply in place of
    # the card it was holding, and a reply without them would draw that card's cover on a
    # re-checked address until the next listing. With the record, because the parent is a
    # reference whose id only the server knows (saving a network nobody has typed before creates
    # it).
    written = await _site_written(access, viewer, updated)
    return written.model_copy(update={"record": await service.site_record(site_id)})


def _site_name_taken(name: str) -> HTTPException:
    """Another Site already carries this name (names are unique without regard to case)."""
    return HTTPException(status.HTTP_409_CONFLICT, f"there is already a site called '{name}'")


def _one_site_name(name: str) -> str:
    """A Site name as the table compares it: cleaned as the upsert cleans it, A to Z folded."""
    cleaned = clean_token_text(name).strip()
    return "".join(one.lower() if "A" <= one <= "Z" else one for one in cleaned)


async def _write_site_details(
    service: PeopleService,
    viewer: Viewer,
    site_id: str,
    body: SiteDetailsWrite,
    sent: set[str],
) -> None:
    """Write the details the caller sent, each left alone when absent. Refusals are asked first."""
    actor = Actor.user(viewer.id)
    if "notes" in sent and not await service.update_site_details(site_id, body.notes, actor=actor):
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NOT_FOUND)
    # The whole list, when the caller sent one. Its first is the site's address: there is no
    # second copy of it to keep in step (`sites.SITE_ADDRESS`).
    if "links" in sent:
        await service.set_site_links(site_id, body.links or [], actor=actor)
    if "aliases" in sent or "parent" in sent:
        try:
            await service.set_site_record(
                site_id, aliases=body.aliases or [], parent=body.parent, actor=actor
            )
        except SiteLoop:  # pragma: no cover (refused above; only a race between the two reaches)
            raise _site_loop() from None


@router.put(
    "/sites/{site_id}/vault",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_protect)],
)
async def set_site_vault(
    site_id: str,
    body: VaultWrite,
    request: Request,
    service: Annotated[PeopleService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> Response:
    """Hide a site, or bring it back, for this user."""
    if body.vault:
        await require_vault_pin(request, viewer)
    # One id, one answer, through the scoped read every site write uses. A concealed site is not a
    # site this caller may write to, in either direction, and it gets the 404 an unknown id gets:
    # answering differently is the reveal. Reading the table and the hidden flag would answer 204
    # to a guest hiding a site the guest could not be shown at all: a way to ask whether a site by
    # that id exists.
    await _visible_site_or_404(access, viewer, site_id)
    await service.set_site_vault(viewer, site_id, vault=body.vault)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/sites/{site_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_protect)],
)
async def delete_site(
    site_id: str,
    service: Annotated[PeopleService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    reindexer: Annotated[ReindexSeam, Depends(wiring.reindexer)],
    forgets: Annotated[ForgetGoneSeam, Depends(wiring.forget_gone)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> Response:
    """Delete a site, every grant that named it, and what it recorded about where files came from.

    **No file is touched and no person goes.**
    """
    site = await _visible_site_or_404(access, viewer, site_id)
    # BEFORE the delete, for the reason deleting a person reads first: this takes the site's
    # usernames with it, and the walk from a site to its files goes THROUGH those usernames, so after
    # the write there is no path left to the files that just stopped carrying its name.
    was_under = await service.assets_of_site(site_id)
    if not await service.delete_site(viewer, site_id):
        raise _missing()
    # The site's name and its other names were indexed on each of those files, and the usernames that
    # carried them have gone.
    await reindexer.touched_many(was_under)
    await forgets.forget_gone("site", site_id, name=site.name, by=viewer)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
