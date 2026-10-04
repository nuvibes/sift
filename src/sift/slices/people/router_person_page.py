# SPDX-License-Identifier: AGPL-3.0-or-later
"""A person's page: the covers, links, notes, History and aliases drawn on it."""

from __future__ import annotations

from typing import Annotated

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
    Repository,
    Viewer,
)
from sift.kernel.access.history import DEFAULT_LIMIT, MAX_LIMIT
from sift.kernel.access.history_person import history_of_person
from sift.kernel.covers import (
    CoverPictures,
    serve_cover,
)
from sift.kernel.db import Database
from sift.kernel.ledger import Actor
from sift.kernel.seams import ReindexSeam
from sift.kernel.site_icons import icon_for as site_icon_for
from sift.kernel.wire import HistoryEvent, history_event
from sift.kernel.workbench import Workbench
from sift.slices.auth import csrf_protect, current_viewer, require_admin
from sift.slices.people.models import (
    AliasView,
    AliasWrite,
    LinkView,
    LinkWrite,
    PersonNotes,
)
from sift.slices.people.router_base import (
    _alias_view,
    _missing,
    _require_person,
    _service,
    _site_of,
    _visible_person_or_404,
    _visible_site_or_404,
)
from sift.slices.people.service import (
    DuplicateAlias,
    Link,
    PeopleService,
)

router = APIRouter(tags=["people"])

# --- the covers, served -------------------------------------------------------------------------


@router.get("/people/{person_id}/cover")
async def person_cover(
    person_id: str,
    request: Request,
    service: Annotated[PeopleService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    pictures: Annotated[CoverPictures, Depends(wiring.cover_pictures)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> Response:
    """The still this person is drawn as, at whichever moment was chosen."""
    await _visible_person_or_404(access, viewer, person_id)
    chosen = await service.chosen_cover("person", person_id)
    return await serve_cover(request, access, viewer, chosen=chosen, pictures=pictures)


@router.get("/sites/{site_id}/cover")
async def site_cover(
    site_id: str,
    request: Request,
    service: Annotated[PeopleService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    pictures: Annotated[CoverPictures, Depends(wiring.cover_pictures)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> Response:
    """The still this site is drawn as, or the site's own logo when nobody has chosen one."""
    site = await _visible_site_or_404(access, viewer, site_id)
    chosen = await service.chosen_cover("site", site_id)
    return await serve_cover(
        request,
        access,
        viewer,
        chosen=chosen,
        pictures=pictures,
        instead=site_icon_for(site.site_url, site.name),
    )


def _link_view(link: Link, *, shown: bool) -> LinkView:
    return LinkView(
        id=link.id,
        person_id=link.person_id,
        url=link.url,
        site_id=link.site_id if shown else None,
        site_name=link.site_name if shown else None,
        label=link.label,
    )


async def _links_seen(access: Repository, viewer: Viewer, links: list[Link]) -> list[LinkView]:
    """Links as this viewer is told about them: a link whose site they may not be shown reads as
    UNFILED (no site id, no site name), and the link itself stays."""
    filed = sorted({link.site_id for link in links if link.site_id is not None})
    shown = await access.visible_sites(viewer, filed) if filed else {}
    return [_link_view(link, shown=link.site_id in shown) for link in links]


@router.get("/people/{person_id}/links")
async def links_of_person(
    person_id: str,
    service: Annotated[PeopleService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> list[LinkView]:
    """Where this person can be found. A site this viewer may not be shown is left off its link
    (`_links_seen`)."""
    await _require_person(service, viewer, person_id)
    return await _links_seen(access, viewer, await service.links_of(person_id))


@router.post(
    "/people/{person_id}/links",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(csrf_protect)],
)
async def add_link(
    person_id: str,
    body: LinkWrite,
    service: Annotated[PeopleService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> LinkView:
    """Record an address for this person."""
    await _require_person(service, viewer, person_id)
    added = await service.add_link(
        person_id,
        body.url,
        site_id=await _site_of(service, body.url),
        label=body.label,
    )
    if added is None:
        held = [link for link in await service.links_of(person_id) if link.url == body.url]
        if not held:  # pragma: no cover (the conflict that produced None is exactly this row)
            raise _missing()
        added = held[0]
    (seen,) = await _links_seen(access, viewer, [added])
    return seen


@router.delete(
    "/people/{person_id}/links/{link_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_protect)],
)
async def remove_link(
    person_id: str,
    link_id: str,
    service: Annotated[PeopleService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> Response:
    await _require_person(service, viewer, person_id)
    if not await service.remove_link(person_id, link_id, actor=Actor.user(viewer.id)):
        raise _missing()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- notes ----------------------------------------------------------------------------------


@router.get("/people/{person_id}/notes")
async def notes_of_person(
    person_id: str,
    service: Annotated[PeopleService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> PersonNotes:
    """What an admin wrote about somebody.

    **Readable by anybody signed in, and writable only by an admin.**
    """
    person = await _require_person(service, viewer, person_id)
    return PersonNotes(notes=person.notes)


# --- history -------------------------------------------------------------------------------


@router.get("/people/{person_id}/history")
async def history_of_a_person(
    person_id: str,
    service: Annotated[PeopleService, Depends(_service)],
    database: Annotated[Database, Depends(wiring.database)],
    access: Annotated[Repository, Depends(wiring.access)],
    workbench: Annotated[Workbench, Depends(wiring.workbench)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> list[HistoryEvent]:
    """What happened to this person, oldest first."""
    await _require_person(service, viewer, person_id)
    # The registry is asked which kinds of decision can never be taken back, and the answer is
    # handed to the read so it offers no Undo on those: the same question the file's history and
    # the decisions screen ask, from the same place, because an affordance the server would refuse
    # is worse than none. Asked of the REVERSERS rather than of the queues: a decision written by a
    # pile that has since been retired is still one somebody can take back.
    final = [one.name for one in workbench.reversers if not one.reversible]
    return [
        history_event(event)
        for event in await history_of_person(
            database,
            viewer,
            person_id,
            limit=limit,
            final_queues=final,
            bench=workbench,
            access=access,
        )
    ]


# --- aliases --------------------------------------------------------------------------------


@router.get("/people/{person_id}/aliases")
async def aliases_of_person(
    person_id: str,
    service: Annotated[PeopleService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> list[AliasView]:
    """The explicit "also known as" list. Linked usernames are not in it."""
    await _require_person(service, viewer, person_id)
    return [_alias_view(alias) for alias in await service.aliases_of(person_id)]


@router.post(
    "/people/{person_id}/aliases",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(csrf_protect)],
)
async def add_alias(
    person_id: str,
    body: AliasWrite,
    service: Annotated[PeopleService, Depends(_service)],
    reindexer: Annotated[ReindexSeam, Depends(wiring.reindexer)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> AliasView:
    """Add an "also known as". Searchable immediately, with nothing to rebuild."""
    await _require_person(service, viewer, person_id)
    try:
        alias = await service.add_alias(person_id, body.alias)
    except DuplicateAlias:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "this person already has that alias"
        ) from None
    # An alias is indexed beside the name it belongs to, so adding one changes what every file that
    # person is on will match, and those files can be named.
    await reindexer.touched_many(await service.assets_of_person(person_id))
    return _alias_view(alias)


@router.delete(
    "/people/{person_id}/aliases/{alias_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_protect)],
)
async def remove_alias(
    person_id: str,
    alias_id: str,
    service: Annotated[PeopleService, Depends(_service)],
    reindexer: Annotated[ReindexSeam, Depends(wiring.reindexer)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> Response:
    await _require_person(service, viewer, person_id)
    if not await service.remove_alias(person_id, alias_id, actor=Actor.user(viewer.id)):
        raise _missing()
    # The other direction of the same thing: the alias was indexed on this person's files, and now
    # is not. Removing one moves no `asset_people` row, so the set is the same after as before.
    await reindexer.touched_many(await service.assets_of_person(person_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
