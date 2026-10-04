# SPDX-License-Identifier: AGPL-3.0-or-later
"""The people on a file and the Sites it is filed under, as a file's pane draws and changes them."""

from __future__ import annotations

from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    Response,
    status,
)

from sift.kernel import wiring
from sift.kernel.access import (
    Repository,
    SiteSuggestion,
    Viewer,
)
from sift.kernel.ledger import Actor
from sift.kernel.reach import BulkWriteDone, require_reachable
from sift.kernel.seams import ReindexSeam
from sift.kernel.serving import face_version
from sift.kernel.site_icons import icon_token as site_icon_token
from sift.slices.auth import csrf_protect, current_viewer, require_admin
from sift.slices.people.models import (
    FiledUnder,
    PeopleAssignment,
    PersonView,
    SiteAssignment,
)
from sift.slices.people.router_base import _missing, _person_card, _require_person, _service
from sift.slices.people.service import (
    Attribution,
    Filing,
    PeopleService,
)

router = APIRouter(tags=["people"])

# --- assigning people to assets ---------------------------------------------------------------


#: The word the enrichment pass writes onto a pairing or a filing a stash-box decided.
STASH_BOX = "stash_box"


#: What a folder-read attribution is called out loud.
BY_FOLDER = "its folder"


async def _box_that_recognised(access: Repository, asset_id: str) -> str | None:
    """The stash-box whose applied match on this file is the most recently decided, if any."""
    return next(
        (one.name for one in await access.enriched_by(asset_id) if one.via == "stash" and one.name),
        None,
    )


def _named_by(held: Attribution, box: str | None) -> str | None:
    """What to call the thing that put a name on a file, given what did it."""
    if held.source == STASH_BOX:
        return box
    if held.source == "username":
        return held.username
    if held.source == "folder":
        return BY_FOLDER
    # A pass this version has never heard of. The word still travels; the name does not, because
    # there is nothing honest to put in it.
    return None


@router.get("/assets/{asset_id}/people")
async def people_of_asset(
    asset_id: str,
    access: Annotated[Repository, Depends(wiring.access)],
    service: Annotated[PeopleService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> list[PersonView]:
    resolved = await require_reachable(access, viewer, asset_id, _missing)
    automatic = await service.automatic_on(resolved)
    # Asked once for the whole file rather than once per person: a box recognised the FILE, so
    # every name it put there was put there by the same box.
    box = await _box_that_recognised(access, resolved)
    cards: list[PersonView] = []
    for person in await service.people_of(viewer, resolved):
        # The cover is a picture of one of their files, and it is shown only when this viewer may
        # see that file, since otherwise its id would say a hidden asset exists. Gated here, per person,
        # the same way the People list gates it in SQL. Absent, it falls back to a monogram.
        cover = person.cover_asset_id
        face = person.cover_track_id
        moment = person.cover_at_ms
        if cover is not None and not await access.can_view(viewer, cover):
            cover, face, moment = None, None, None
        card = _person_card(
            person,
            cover_asset_id=cover,
            cover_track_id=face,
            # Passed straight through, unlike the two above. An uploaded cover is not a file, so
            # there is nothing to be granted on and nothing to withhold: anybody who may be shown
            # the person may see the picture chosen for them.
            cover_upload_id=person.cover_upload_id,
            cover_at_ms=moment,
            # The window goes where its picture goes: kept with an upload, withheld with a file
            # this viewer may not open. `person.cover_frame` is already bound to the row's picture.
            cover_frame=person.cover_frame if (person.cover_upload_id or cover) else None,
            art=face_version(viewer.cache_stamp),
        )
        # All three, and they are not the same fact. The flag says a pass put the name here; the
        # word says which pass, and a screen that has only the flag has to send somebody looking.
        # The NAME is what stops the word doing the same thing one level down (see `_named_by`).
        held = automatic.get(person.id)
        card.source = None if held is None else held.source
        card.source_name = None if held is None else _named_by(held, box)
        card.automatic = card.source is not None
        cards.append(card)
    return cards


@router.post("/assets/people", dependencies=[Depends(csrf_protect)])
async def assign_people(
    body: PeopleAssignment,
    access: Annotated[Repository, Depends(wiring.access)],
    service: Annotated[PeopleService, Depends(_service)],
    reindexer: Annotated[ReindexSeam, Depends(wiring.reindexer)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> BulkWriteDone:
    """Attach or detach people, for one asset or a selection of them.

    **No file is moved.**
    """
    actionable = await access.actionable_of(viewer, body.asset_ids)

    for person_id in body.person_ids:
        await _require_person(service, viewer, person_id)

    # The names come off the resolutions just made, so the record keeps what each person was
    # CALLED at this moment rather than a name read back later.
    named = {
        person_id: person.name
        for person_id in body.person_ids
        if (person := await service.get_person(viewer, person_id)) is not None
    }
    changed = (
        await service.assign(
            list(actionable.allowed),
            body.person_ids,
            add=body.add,
            actor=Actor.user(viewer.id),
            names=named,
        )
        if actionable.allowed
        else 0
    )
    # Known by id, so these are rewritten now rather than queued, as one call, because this
    # list runs to five hundred and each single-asset refresh is its own write transaction. Only
    # what changed: refreshing what the write skipped is a transaction bought for nothing.
    if actionable.allowed:
        await reindexer.touched_many(actionable.allowed)
    return BulkWriteDone.after(actionable, changed)


# --- sites ------------------------------------------------------------------------------


@router.post("/assets/sites", dependencies=[Depends(csrf_protect)])
async def file_under_sites(
    body: SiteAssignment,
    access: Annotated[Repository, Depends(wiring.access)],
    service: Annotated[PeopleService, Depends(_service)],
    reindexer: Annotated[ReindexSeam, Depends(wiring.reindexer)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> BulkWriteDone:
    """Say that a selection of files came from these sites, or that they did not.

    **No file is moved.**
    """
    actionable = await access.actionable_of(viewer, body.asset_ids)

    names: list[str] = []
    for site_id in body.site_ids:
        found = await access.visible_site(viewer, site_id)
        if found is None:
            raise _missing()
        names.append(found.name)

    changed = 0
    if actionable.allowed:
        changed = (
            await service.file_under_sites(
                list(actionable.allowed), names, actor=Actor.user(viewer.id)
            )
            if body.add
            else await service.unfile_from_sites(
                list(actionable.allowed), list(body.site_ids), actor=Actor.user(viewer.id)
            )
        )
    # Known by id, so these are rewritten now rather than queued, as one call, for the reason the
    # people assignment above gives, and over only what changed for the reason it gives too.
    if actionable.allowed:
        await reindexer.touched_many(actionable.allowed)
    return BulkWriteDone.after(actionable, changed)


@router.get("/assets/{asset_id}/filings")
async def filings_of_asset(
    asset_id: str,
    access: Annotated[Repository, Depends(wiring.access)],
    service: Annotated[PeopleService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> list[FiledUnder]:
    """Which sites this one file is filed under, and under whose username."""
    resolved = await require_reachable(access, viewer, asset_id, _missing)
    box = await _box_that_recognised(access, resolved)
    filings = await service.filings_of(resolved)
    # Each site's cover, off the Sites wall's own batched read: one statement for every filing,
    # never one per chip. A site the read does not return keeps a bare address: the careful answer,
    # and never a filter (the paragraph above says why no filing is dropped for its site).
    sites = await access.visible_sites(
        viewer, list(dict.fromkeys(one.site_id for one in filings if one.site_id is not None))
    )
    art = face_version(viewer.cache_stamp)
    return [
        _filed_under(filing, sites.get(filing.site_id or ""), box=box, art=art)
        for filing in filings
    ]


def _filed_under(
    filing: Filing, site: SiteSuggestion | None, *, box: str | None, art: str
) -> FiledUnder:
    """One filing as the wire spells it, carrying its site's cover as the Sites wall does."""
    return FiledUnder(
        username_id=filing.username_id,
        site_id=filing.site_id,
        site=filing.site_name,
        # The empty username is the library's way of writing "from here, poster unknown", and it
        # stops being a string here. See `FiledUnder`.
        username=filing.username or None,
        person_id=filing.person_id,
        source=filing.source,
        # Only a stash-box files anything without a person doing it, so there is one name to
        # give and it belongs to the FILE rather than to the filing: the match is what was
        # applied, and every row it wrote came from the same box.
        source_name=box if filing.source == STASH_BOX else None,
        art=art if site is not None else None,
        cover_asset_id=site.cover_asset_id if site is not None else None,
        cover_upload_id=site.cover_upload_id if site is not None else None,
        cover_at_ms=site.cover_at_ms if site is not None else None,
        cover_frame=site.cover_frame if site is not None else None,
        # The same key, in the same order, as the Sites wall and the cover route ask the pack by.
        icon=site_icon_token(site.site_url, site.name) if site is not None else None,
    )


@router.delete(
    "/assets/{asset_id}/filings/{username_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_protect)],
)
async def remove_filing(
    asset_id: str,
    username_id: str,
    access: Annotated[Repository, Depends(wiring.access)],
    service: Annotated[PeopleService, Depends(_service)],
    reindexer: Annotated[ReindexSeam, Depends(wiring.reindexer)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> Response:
    """Take this file off one site. **No file is moved**, and the site itself is untouched."""
    resolved = await require_reachable(access, viewer, asset_id, _missing)
    if await service.unfile_from_username(resolved, username_id, actor=Actor.user(viewer.id)):
        await reindexer.touched_many([resolved])
    return Response(status_code=status.HTTP_204_NO_CONTENT)
