# SPDX-License-Identifier: AGPL-3.0-or-later
"""What every People, Usernames and Sites route stands on: the service, the refusals, and the
views a person, an alias or a Site is drawn as."""

from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import (
    HTTPException,
    Request,
    status,
)

from sift.kernel import wiring
from sift.kernel.access import (
    GrantMark,
    ObjectType,
    PersonSuggestion,
    Repository,
    SiteSuggestion,
    Viewer,
)
from sift.kernel.cover_frame import CoverFrame
from sift.kernel.seams import DisagreementSeam, RecognitionSeam
from sift.kernel.serving import face_version
from sift.kernel.site_icons import icon_token as site_icon_token
from sift.kernel.wiring import RECOGNITION, part_of
from sift.slices.people.models import (
    AliasView,
    PersonView,
    SiteView,
)
from sift.slices.people.service import (
    SERVICE,
    Alias,
    PeopleService,
    Person,
    Site,
)

_NOT_FOUND = "not found"


def _disagreement_seam(request: Request) -> DisagreementSeam:
    """Whoever can say which records a stash-box disagrees with."""
    return wiring.part_of(request, wiring.DISAGREEMENTS)


def _service(request: Request) -> PeopleService:
    return part_of(request, SERVICE)


def _missing() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, _NOT_FOUND)


def _site_loop() -> HTTPException:
    """A parent that is the Site itself or already part of it. Said so it can be acted on."""
    return HTTPException(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        "A Site can't be part of itself or of a Site that's already part of it. "
        "Choose a Site from another network.",
        # The field the refusal is about, so the form says it beside Part of and not at its foot.
        headers={"Sift-Field": "parent"},
    )


def _person_card(
    person: Person,
    *,
    asset_count: int = 0,
    cover_asset_id: str | None = None,
    cover_track_id: str | None = None,
    cover_upload_id: str | None = None,
    cover_at_ms: int | None = None,
    cover_frame: CoverFrame | None = None,
    art: str | None = None,
) -> PersonView:
    """A person as anybody signed in may see them: named, not described."""
    return PersonView(
        id=person.id,
        name=person.name,
        vault=person.vault,
        asset_count=asset_count,
        cover_asset_id=cover_asset_id,
        cover_track_id=cover_track_id,
        cover_upload_id=cover_upload_id,
        cover_at_ms=cover_at_ms,
        cover_frame=cover_frame,
        art=art,
        keep_local=person.keep_local,
        keep_from_swaps=person.keep_from_swaps,
        pmv_creator=bool(person.record.get("pmv_creator")),
    )


def _person_view(
    person: Person,
    *,
    asset_count: int = 0,
    mark: GrantMark | None = None,
    art: str | None = None,
) -> PersonView:
    """One person as a write route hands them back."""
    return PersonView(
        id=person.id,
        name=person.name,
        vault=person.vault,
        notes=person.notes,
        cover_asset_id=person.cover_asset_id,
        cover_upload_id=person.cover_upload_id,
        cover_at_ms=person.cover_at_ms,
        cover_frame=person.cover_frame,
        cover_track_id=person.cover_track_id,
        art=art,
        asset_count=asset_count,
        shared=mark.shared if mark else False,
        restricted=mark.restricted if mark else False,
        keep_local=person.keep_local,
        keep_from_swaps=person.keep_from_swaps,
        pmv_creator=bool(person.record.get("pmv_creator")),
        # The record comes back with the person, so the screen that just saved one is showing what
        # was STORED rather than what it sent. They differ: a year typed as a word is dropped, an
        # emptied box becomes nothing at all, and a form still holding the text somebody typed is a
        # form claiming a save that did not happen.
        record=dict(person.record),
    )


def _person_from_suggestion(
    found: PersonSuggestion, mark: GrantMark | None = None, art: str | None = None
) -> PersonView:
    """A scoped person row, as the screen reads one."""
    return PersonView(
        id=found.id,
        name=found.name,
        vault=found.vault,
        asset_count=found.asset_count,
        size_bytes=found.size_bytes,
        cover_asset_id=found.cover_asset_id,
        cover_upload_id=found.cover_upload_id,
        cover_at_ms=found.cover_at_ms,
        cover_frame=found.cover_frame,
        cover_track_id=found.cover_track_id,
        art=art,
        favorite=found.favorite,
        pinned=found.pinned,
        rating=found.rating,
        shared=mark.shared if mark else False,
        restricted=mark.restricted if mark else False,
        keep_local=found.keep_local,
        keep_from_swaps=found.keep_from_swaps,
        pmv_creator=found.pmv_creator,
        locked=found.locked,
    )


def _alias_view(alias: Alias) -> AliasView:
    return AliasView(id=alias.id, person_id=alias.person_id, alias=alias.alias)


def _site_from_suggestion(
    site: SiteSuggestion, cells: dict[str, int], mark: GrantMark | None = None, *, art: str
) -> SiteView:
    """A scoped site row, as the screen reads one."""
    return SiteView(
        id=site.id,
        name=site.name,
        asset_count=site.asset_count,
        size_bytes=site.size_bytes,
        site_url=site.site_url,
        notes=site.notes,
        cover_asset_id=site.cover_asset_id,
        cover_upload_id=site.cover_upload_id,
        cover_at_ms=site.cover_at_ms,
        cover_frame=site.cover_frame,
        art=art,
        icon=site_icon_token(site.site_url, site.name),
        favorite=site.favorite,
        pinned=site.pinned,
        rating=site.rating,
        people_count=cells.get("people", 0),
        counts=dict(cells),
        keep_local=site.keep_local,
        keep_from_swaps=site.keep_from_swaps,
        shared=mark.shared if mark else False,
        restricted=mark.restricted if mark else False,
        shared_here=mark.shared_here if mark else False,
        restricted_here=mark.restricted_here if mark else False,
        hidden=site.vault,
        locked=site.locked,
    )


def _site_view(site: Site, mark: GrantMark | None = None) -> SiteView:
    """One site as a write route hands it back, mark included."""
    return SiteView(
        id=site.id,
        name=site.name,
        # By the address AND the name, in that order: the same two keys the listing
        # (`_site_from_suggestion`) and the cover route (`site_cover`) ask the pack by. Asked by the
        # name alone, a site whose address names one entry of the pack and whose name names another
        # would be handed a token here that its own cover route never serves, and its logo would be
        # re-checked after every rename.
        icon=site_icon_token(site.site_url, site.name),
        people_count=0,
        keep_local=site.keep_local,
        keep_from_swaps=site.keep_from_swaps,
        shared=mark.shared if mark else False,
        restricted=mark.restricted if mark else False,
        shared_here=mark.shared_here if mark else False,
        restricted_here=mark.restricted_here if mark else False,
    )


async def _require_person(service: PeopleService, viewer: Viewer, person_id: str) -> Person:
    """The person, if this viewer may act on them at all. 404 otherwise, either way."""
    person = await service.get_person(viewer, person_id)
    if person is None:
        raise _missing()
    if not await service.is_visible_to(viewer, person_id):
        raise _missing()
    return person


# --- people ---------------------------------------------------------------------------------


def _recognition(request: Request) -> RecognitionSeam:
    """The face feature's "somebody was made" seam. Held on the app, not imported."""
    return part_of(request, RECOGNITION)


# --- links ----------------------------------------------------------------------------------


async def _site_of(service: PeopleService, url: str) -> str | None:
    """The site an address is on, when Sift already knows one by that name."""
    host = urlsplit(url).hostname or ""
    labels = [label for label in host.split(".") if label not in {"www", "m", "mobile"}]
    if len(labels) < 2:
        return None
    return await service.site_named(labels[-2])


# --- what a viewer thinks of a person or a site -----------------------------------------


async def _visible_person_or_404(access: Repository, viewer: Viewer, person_id: str) -> None:
    if await access.visible_person(viewer, person_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NOT_FOUND)


async def _visible_site_or_404(access: Repository, viewer: Viewer, site_id: str) -> SiteSuggestion:
    """A site is visible when this viewer can see something that came from it."""
    site = await access.visible_site(viewer, site_id)
    if site is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NOT_FOUND)
    return site


async def _site_scoped(access: Repository, viewer: Viewer, site_id: str) -> SiteView | None:
    """The site's row as the wall reads it for this viewer, or None where the scoped read withholds
    it. The one body behind `_site_now` and `_site_written`, so a cover write and a rename cannot
    hand back two shapes of the same card.
    """
    found = await access.visible_site(viewer, site_id)
    if found is None:
        return None
    marks = await access.visible_marks(viewer, ObjectType.SITE, [found.id])
    cells = await access.card_counts(viewer, "site", [found.id])
    return _site_from_suggestion(
        found, cells.get(found.id, {}), marks.get(found.id), art=face_version(viewer.cache_stamp)
    )


async def _site_now(access: Repository, viewer: Viewer, site_id: str) -> SiteView:
    """The site's own row as a cover write hands it back. Both cover routes send this."""
    view = await _site_scoped(access, viewer, site_id)
    if view is None:  # pragma: no cover (resolved a statement ago)
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NOT_FOUND)
    return view


async def _site_written(access: Repository, viewer: Viewer, site: Site) -> SiteView:
    """The site a create or a rename just wrote, as the reply hands it back."""
    scoped = await _site_scoped(access, viewer, site.id)
    if scoped is not None:
        return scoped
    marks = await access.visible_marks(viewer, ObjectType.SITE, [site.id])
    return _site_view(site, marks.get(site.id))
