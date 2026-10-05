# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a person or a Site is marked with: favorite, rating, pin, tags, cover and details."""

from __future__ import annotations

from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    UploadFile,
    status,
)

from sift.kernel import wiring
from sift.kernel.access import (
    Repository,
    Viewer,
)
from sift.kernel.content import EntityStateStore, PinnableKind, PinView, PinWrite
from sift.kernel.covers import (
    CoverPictures,
    forget_displaced,
    receive_cover,
    upload_kept_by_put,
)
from sift.kernel.ledger import Actor
from sift.kernel.seams import ReindexSeam, StillSeam
from sift.kernel.serving import face_version
from sift.slices.auth import csrf_protect, current_viewer, require_admin
from sift.slices.people.models import (
    CoverWrite,
    EntityStateView,
    EntityTagWrite,
    FavoriteWrite,
    PersonView,
    RatingWrite,
    SiteDetailsWrite,
    SiteView,
    TagOnEntity,
)
from sift.slices.people.router_base import (
    _NOT_FOUND,
    _person_from_suggestion,
    _service,
    _site_loop,
    _site_now,
    _visible_person_or_404,
    _visible_site_or_404,
)
from sift.slices.people.router_sites import _write_site_details
from sift.slices.people.service import (
    PeopleService,
    SiteLoop,
)

router = APIRouter(tags=["people"])


@router.put("/people/{person_id}/favorite", dependencies=[Depends(csrf_protect)])
async def set_person_favorite(
    person_id: str,
    body: FavoriteWrite,
    service: Annotated[PeopleService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> EntityStateView:
    """Heart a person. Not admin-only: it is this viewer's own opinion, on their own row."""
    await _visible_person_or_404(access, viewer, person_id)
    state = await service.set_person_favorite(person_id, viewer.id, body.favorite)
    return EntityStateView(favorite=state.favorite, rating=state.rating)


@router.put("/people/{person_id}/rating", dependencies=[Depends(csrf_protect)])
async def set_person_rating(
    person_id: str,
    body: RatingWrite,
    service: Annotated[PeopleService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> EntityStateView:
    await _visible_person_or_404(access, viewer, person_id)
    state = await service.set_person_rating(person_id, viewer.id, body.rating)
    return EntityStateView(favorite=state.favorite, rating=state.rating)


@router.put("/sites/{site_id}/favorite", dependencies=[Depends(csrf_protect)])
async def set_site_favorite(
    site_id: str,
    body: FavoriteWrite,
    service: Annotated[PeopleService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> EntityStateView:
    await _visible_site_or_404(access, viewer, site_id)
    state = await service.set_site_favorite(site_id, viewer.id, body.favorite)
    return EntityStateView(favorite=state.favorite, rating=state.rating)


@router.put("/people/{person_id}/pin", dependencies=[Depends(csrf_protect)])
async def set_person_pinned(
    person_id: str,
    body: PinWrite,
    pins: Annotated[EntityStateStore, Depends(wiring.entity_state)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> PinView:
    """Keep a person at the top of the People wall."""
    await _visible_person_or_404(access, viewer, person_id)
    return PinView(
        pinned=await pins.set_pinned(PinnableKind.PERSON, person_id, viewer.id, pinned=body.pinned)
    )


@router.put("/sites/{site_id}/pin", dependencies=[Depends(csrf_protect)])
async def set_site_pinned(
    site_id: str,
    body: PinWrite,
    pins: Annotated[EntityStateStore, Depends(wiring.entity_state)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> PinView:
    await _visible_site_or_404(access, viewer, site_id)
    return PinView(
        pinned=await pins.set_pinned(PinnableKind.SITE, site_id, viewer.id, pinned=body.pinned)
    )


@router.put("/sites/{site_id}/rating", dependencies=[Depends(csrf_protect)])
async def set_site_rating(
    site_id: str,
    body: RatingWrite,
    service: Annotated[PeopleService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> EntityStateView:
    await _visible_site_or_404(access, viewer, site_id)
    state = await service.set_site_rating(site_id, viewer.id, body.rating)
    return EntityStateView(favorite=state.favorite, rating=state.rating)


# --- tags on a person or a site ---------------------------------------------------------


@router.get("/people/{person_id}/tags")
async def tags_of_person(
    person_id: str,
    service: Annotated[PeopleService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> list[TagOnEntity]:
    await _visible_person_or_404(access, viewer, person_id)
    return [
        TagOnEntity(id=row["id"], name=row["name"])
        for row in await service.tags_of_person(person_id, viewer)
    ]


@router.post("/people/{person_id}/tags", dependencies=[Depends(csrf_protect)])
async def tag_person(
    person_id: str,
    body: EntityTagWrite,
    service: Annotated[PeopleService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> list[TagOnEntity]:
    """Put a tag on a person, or take it off."""
    await _visible_person_or_404(access, viewer, person_id)
    await service.tag_person(person_id, body.tag_id, add=body.add)
    return [
        TagOnEntity(id=row["id"], name=row["name"])
        for row in await service.tags_of_person(person_id)
    ]


@router.get("/sites/{site_id}/tags")
async def tags_of_site(
    site_id: str,
    service: Annotated[PeopleService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> list[TagOnEntity]:
    await _visible_site_or_404(access, viewer, site_id)
    return [
        TagOnEntity(id=row["id"], name=row["name"])
        for row in await service.tags_of_site(site_id, viewer)
    ]


@router.post("/sites/{site_id}/tags", dependencies=[Depends(csrf_protect)])
async def tag_site(
    site_id: str,
    body: EntityTagWrite,
    service: Annotated[PeopleService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> list[TagOnEntity]:
    await _visible_site_or_404(access, viewer, site_id)
    await service.tag_site(site_id, body.tag_id, add=body.add)
    return [
        TagOnEntity(id=row["id"], name=row["name"]) for row in await service.tags_of_site(site_id)
    ]


# --- the picture, and a site's own details ---------------------------------------------------


@router.put("/people/{person_id}/cover", dependencies=[Depends(csrf_protect)])
async def set_person_cover(
    person_id: str,
    body: CoverWrite,
    service: Annotated[PeopleService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    stills: Annotated[StillSeam, Depends(wiring.stills)],
    pictures: Annotated[CoverPictures, Depends(wiring.cover_pictures)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> PersonView:
    """Choose the still somebody is drawn as, and which moment of it."""
    await _visible_person_or_404(access, viewer, person_id)
    if body.asset_id is not None and not await access.can_view(viewer, body.asset_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NOT_FOUND)
    before = await service.chosen_cover("person", person_id)
    kept = upload_kept_by_put(
        body.upload_id, asset_id=body.asset_id, frame=body.frame, before=before
    )
    if not await service.set_person_cover(
        person_id,
        body.asset_id,
        None if kept else body.at_ms,
        kept,
        actor=Actor.user(viewer.id),
        frame=body.frame,
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NOT_FOUND)
    await forget_displaced(pictures, before, after=kept)
    # Ask for the picture of that moment. Queued rather than rendered here: a request that
    # shells out to ffmpeg is a request that takes seconds, and the client falls back to the
    # file's own still until it lands, exactly as a mark's tile does.
    if body.asset_id is not None and body.at_ms is not None:
        await stills.wants_still(body.asset_id, body.at_ms)

    found = await access.visible_person(viewer, person_id)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NOT_FOUND)
    return _person_from_suggestion(found, art=face_version(viewer.cache_stamp))


@router.post("/people/{person_id}/cover-picture", dependencies=[Depends(csrf_protect)])
async def upload_person_cover(
    person_id: str,
    file: Annotated[UploadFile, File()],
    service: Annotated[PeopleService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    pictures: Annotated[CoverPictures, Depends(wiring.cover_pictures)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> PersonView:
    """A picture from outside the library, as this person's cover."""
    await _visible_person_or_404(access, viewer, person_id)
    before = await service.chosen_cover("person", person_id)
    await receive_cover(
        pictures,
        file.read,
        before=before,
        point_at=lambda upload_id: service.set_person_cover(
            person_id, None, None, upload_id, actor=Actor.user(viewer.id)
        ),
    )
    found = await access.visible_person(viewer, person_id)
    if found is None:  # pragma: no cover (resolved a statement ago)
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NOT_FOUND)
    return _person_from_suggestion(found, art=face_version(viewer.cache_stamp))


@router.put("/sites/{site_id}/cover", dependencies=[Depends(csrf_protect)])
async def set_site_cover(
    site_id: str,
    body: CoverWrite,
    service: Annotated[PeopleService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    stills: Annotated[StillSeam, Depends(wiring.stills)],
    pictures: Annotated[CoverPictures, Depends(wiring.cover_pictures)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> SiteView:
    """The still a site is shown as, and which moment of it.

    **It answers with the site's own row**, the shape the wall draws, so the screen puts the
    reply in place of the card it holds.
    """
    await _visible_site_or_404(access, viewer, site_id)
    if body.asset_id is not None and not await access.can_view(viewer, body.asset_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NOT_FOUND)
    before = await service.chosen_cover("site", site_id)
    kept = upload_kept_by_put(
        body.upload_id, asset_id=body.asset_id, frame=body.frame, before=before
    )
    if not await service.set_site_cover(
        site_id,
        body.asset_id,
        None if kept else body.at_ms,
        kept,
        actor=Actor.user(viewer.id),
        frame=body.frame,
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NOT_FOUND)
    await forget_displaced(pictures, before, after=kept)
    # Ask for the picture of that moment. Queued rather than rendered here: a request that
    # shells out to ffmpeg is a request that takes seconds, and the client falls back to the
    # file's own still until it lands, exactly as a mark's tile does.
    if body.asset_id is not None and body.at_ms is not None:
        await stills.wants_still(body.asset_id, body.at_ms)
    return await _site_now(access, viewer, site_id)


@router.post("/sites/{site_id}/cover-picture", dependencies=[Depends(csrf_protect)])
async def upload_site_cover(
    site_id: str,
    file: Annotated[UploadFile, File()],
    service: Annotated[PeopleService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    pictures: Annotated[CoverPictures, Depends(wiring.cover_pictures)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> SiteView:
    """A picture from outside the library, as this site's cover. See `receive_cover`."""
    await _visible_site_or_404(access, viewer, site_id)
    before = await service.chosen_cover("site", site_id)
    await receive_cover(
        pictures,
        file.read,
        before=before,
        point_at=lambda upload_id: service.set_site_cover(
            site_id, None, None, upload_id, actor=Actor.user(viewer.id)
        ),
    )
    return await _site_now(access, viewer, site_id)


@router.put("/sites/{site_id}/details", dependencies=[Depends(csrf_protect)])
async def set_site_details(
    site_id: str,
    body: SiteDetailsWrite,
    service: Annotated[PeopleService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    reindexer: Annotated[ReindexSeam, Depends(wiring.reindexer)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> None:
    await _visible_site_or_404(access, viewer, site_id)
    # A parent that would make a loop is refused before anything is written, so a refused save
    # leaves the Site exactly as it was rather than with new notes and the old parent.
    if "parent" in body.model_fields_set and body.parent:
        try:
            await service.refuse_a_site_loop(site_id, body.parent)
        except SiteLoop:
            raise _site_loop() from None
    # The notes are always written here: this route's callers send them every time, and null
    # clears them.
    sent = body.model_fields_set | {"notes"}
    await _write_site_details(service, viewer, site_id, body, sent)
    if "aliases" in sent or "parent" in sent:
        # Other names are indexed on every file filed under this site, reached through its
        # usernames, so those files are named and rewritten instead of the library being rebuilt.
        await reindexer.touched_many(await service.assets_of_site(site_id))
