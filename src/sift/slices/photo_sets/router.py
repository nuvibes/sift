# SPDX-License-Identifier: AGPL-3.0-or-later
"""Endpoints for photo sets: admin-only writes, scoped reads and counts, denied reads as 404."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)

from sift.kernel import wiring
from sift.kernel.access import (
    ADMIN_ENTITY_FACETS,
    ENTITY_FACETS,
    ENTITY_SORT_KEYS,
    ENTITY_SORT_SEEN,
    EntityNarrowing,
    GrantMark,
    ObjectType,
    PhotoSetView,
    Repository,
    Viewer,
    related_filter,
)
from sift.kernel.access.catalog import made_by
from sift.kernel.access.history import DEFAULT_LIMIT, MAX_LIMIT
from sift.kernel.access.history_entity import history_of_photo_set
from sift.kernel.content import EntityStateStore, PinnableKind, PinView, PinWrite
from sift.kernel.covers import (
    CoverPictures,
    forget_displaced,
    receive_cover,
    serve_cover,
    upload_kept_by_put,
)
from sift.kernel.db import Database
from sift.kernel.ledger import Actor
from sift.kernel.paging import resume_at
from sift.kernel.reach import BulkWriteDone, require_reachable
from sift.kernel.seams import ForgetGoneSeam, StillSeam
from sift.kernel.serving import face_version
from sift.kernel.wire import (
    FacetCounts,
    FacetValue,
    HistoryEvent,
    MadeBy,
    history_event,
    made_by_wire,
)
from sift.kernel.wiring import part_of
from sift.kernel.workbench import Workbench
from sift.slices.auth import csrf_protect, current_viewer, require_admin, require_vault_pin
from sift.slices.photo_sets.models import (
    MAX_PHOTO_SET_NAME,
    CoverWrite,
    FavoriteWrite,
    ItemsWrite,
    NotesWrite,
    PhotoSetList,
    PhotoSetStateView,
    PhotoSetSummary,
    PhotoSetTagWrite,
    PhotoSetWrite,
    RatingWrite,
    TagOnPhotoSet,
    VaultWrite,
)
from sift.slices.photo_sets.service import SERVICE, PhotoSetService

router = APIRouter(tags=["photo sets"])

_NOT_FOUND = "not found"


def _service(request: Request) -> PhotoSetService:
    return part_of(request, SERVICE)


def _missing() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, _NOT_FOUND)


def _view(photo_set: PhotoSetView, mark: GrantMark | None = None, *, art: str) -> PhotoSetSummary:
    return PhotoSetSummary(
        id=photo_set.id,
        name=photo_set.name,
        cover_asset_id=photo_set.cover_asset_id,
        cover_upload_id=photo_set.cover_upload_id,
        cover_at_ms=photo_set.cover_at_ms,
        cover_frame=photo_set.cover_frame,
        art=art,
        vault=photo_set.vault,
        item_count=photo_set.item_count,
        size_bytes=photo_set.size_bytes,
        origin=photo_set.origin,
        origin_url=photo_set.origin_url,
        notes=photo_set.notes,
        created_at=photo_set.created_at,
        favorite=photo_set.favorite,
        pinned=photo_set.pinned,
        rating=photo_set.rating,
        shared=mark.shared if mark else False,
        restricted=mark.restricted if mark else False,
        locked=photo_set.locked,
    )


async def _require_set(access: Repository, viewer: Viewer, photo_set_id: str) -> PhotoSetView:
    """The set, if this viewer may be shown it; 404 otherwise, admins included for a hidden one."""
    photo_set = await access.visible_photo_set(viewer, photo_set_id)
    if photo_set is None:
        raise _missing()
    return photo_set


class PhotoSetsNarrowing:
    """What the photo sets on the wall are: their tags, cover, maker, and what has been shared."""

    def __init__(
        self,
        tags: Annotated[list[str] | None, Query()] = None,
        cover: Annotated[list[str] | None, Query()] = None,
        created: Annotated[list[str] | None, Query()] = None,
        sharing: Annotated[list[str] | None, Query()] = None,
    ) -> None:
        self.picks: dict[str, list[str] | None] = {
            "tags": tags,
            "cover": cover,
            "created": created,
            "sharing": sharing,
        }

    def of(self, viewer: Viewer) -> EntityNarrowing:
        """The picks as the one conjunct the statement takes. See `EntityNarrowing`."""
        return EntityNarrowing.of("photo_set", self.picks, is_admin=viewer.is_admin)


@router.get("/photo-sets")
async def list_photo_sets(
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    narrowed: Annotated[PhotoSetsNarrowing, Depends()],
    prefix: Annotated[str, Query(max_length=MAX_PHOTO_SET_NAME)] = "",
    anywhere: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort: Annotated[str, Query()] = ENTITY_SORT_SEEN,
    start: Annotated[str | None, Query(alias="from")] = None,
    near: Annotated[int | None, Query(ge=0)] = None,
    person: Annotated[str | None, Query()] = None,
    tag: Annotated[str | None, Query()] = None,
    site: Annotated[str | None, Query()] = None,
    asset: Annotated[str | None, Query()] = None,
    count: Annotated[Literal["whole", "narrowed"], Query()] = "whole",
) -> PhotoSetList:
    """One page of the photo sets this viewer may know about; `person`, `tag` or `site` narrow it
    to sets reaching that id, `count` picks the tally, and `from` starts the page at a set."""
    if sort not in ENTITY_SORT_KEYS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"unknown sort {sort!r}")
    narrowing = related_filter(person=person, tag=tag, site=site, asset=asset)
    rows = narrowed.of(viewer)
    if start is not None:
        at = await access.position_of_photo_set(
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
    page = await access.list_photo_sets(
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
    marks = await access.visible_marks(
        viewer, ObjectType.PHOTO_SET, [photo_set.id for photo_set in page.items]
    )
    # What each card draws beside its name, read for this page only. See `Repository.card_counts`.
    counts = await access.card_counts(
        viewer, "photo_set", [photo_set.id for photo_set in page.items]
    )
    return PhotoSetList(
        items=[
            _view(
                photo_set, marks.get(photo_set.id), art=face_version(viewer.cache_stamp)
            ).model_copy(update={"counts": counts.get(photo_set.id, {})})
            for photo_set in page.items
        ],
        total=page.total,
        limit=limit,
        offset=offset,
    )


@router.get("/photo-sets/facets")
async def photo_set_facets(
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    narrowed: Annotated[PhotoSetsNarrowing, Depends()],
    facet: Annotated[str, Query()],
    limit: Annotated[int, Query(ge=1, le=200)] = 24,
    prefix: Annotated[str, Query(max_length=MAX_PHOTO_SET_NAME)] = "",
    anywhere: Annotated[bool, Query()] = False,
    person: Annotated[str | None, Query()] = None,
    tag: Annotated[str | None, Query()] = None,
    site: Annotated[str | None, Query()] = None,
    asset: Annotated[str | None, Query()] = None,
) -> FacetCounts:
    """What the photo sets this wall reaches are made of, along one dimension, with counts."""
    if facet not in ENTITY_FACETS["photo_set"] or (
        facet in ADMIN_ENTITY_FACETS and not viewer.is_admin
    ):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"unknown facet {facet!r}")
    counted = await access.photo_set_facets(
        viewer,
        facet,
        limit=limit,
        prefix=prefix,
        anywhere=anywhere,
        asset_filter=related_filter(person=person, tag=tag, site=site, asset=asset),
        narrowing=narrowed.of(viewer),
    )
    return FacetCounts(
        facet=facet,
        values=[FacetValue(value=one.value, count=one.count, label=one.label) for one in counted],
    )


@router.post(
    "/photo-sets", status_code=status.HTTP_201_CREATED, dependencies=[Depends(csrf_protect)]
)
async def create_photo_set(
    body: PhotoSetWrite,
    service: Annotated[PhotoSetService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> PhotoSetSummary:
    """Make an empty set by hand; names need not be unique."""
    photo_set = await service.create(body.name, by_user=viewer.id)
    return PhotoSetSummary(id=photo_set.id, name=photo_set.name, origin=photo_set.origin)


@router.get("/photo-sets/{photo_set_id}")
async def get_photo_set(
    photo_set_id: str,
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> PhotoSetSummary:
    """One set's own row: its name, its cover, where it came from, sharing, and its scoped count."""
    photo_set = await _require_set(access, viewer, photo_set_id)
    marks = await access.visible_marks(viewer, ObjectType.PHOTO_SET, [photo_set_id])
    # This user's O tally, asked here only: a sum per card would cost the wall one each.
    return _view(
        photo_set, marks.get(photo_set_id), art=face_version(viewer.cache_stamp)
    ).model_copy(update={"o_count": await access.o_count_of_photo_set(viewer, photo_set_id)})


@router.get("/photo-sets/{photo_set_id}/history")
async def history_of_a_photo_set(
    photo_set_id: str,
    access: Annotated[Repository, Depends(wiring.access)],
    database: Annotated[Database, Depends(wiring.database)],
    workbench: Annotated[Workbench, Depends(wiring.workbench)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> list[HistoryEvent]:
    """What happened to this Photo Set, oldest first."""
    await _require_set(access, viewer, photo_set_id)
    # Decisions that can never be taken back are offered no Undo, asked of the reversers.
    final = [one.name for one in workbench.reversers if not one.reversible]
    return [
        history_event(event)
        for event in await history_of_photo_set(
            database,
            viewer,
            photo_set_id,
            limit=limit,
            final_queues=final,
            bench=workbench,
            access=access,
        )
    ]


@router.get("/photo-sets/{photo_set_id}/made-by")
async def maker_of_a_photo_set(
    photo_set_id: str,
    access: Annotated[Repository, Depends(wiring.access)],
    database: Annotated[Database, Depends(wiring.database)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> MadeBy | None:
    """Who made this Photo Set: Sift and its pass, the user who asked, or nobody recorded."""
    await _require_set(access, viewer, photo_set_id)
    made = await made_by(database, viewer, "photo_set", photo_set_id)
    return None if made is None else made_by_wire(made)


@router.put("/photo-sets/{photo_set_id}", dependencies=[Depends(csrf_protect)])
async def rename_photo_set(
    photo_set_id: str,
    body: PhotoSetWrite,
    service: Annotated[PhotoSetService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> PhotoSetSummary:
    """Rename a set. Hiding one is a separate request."""
    await _require_set(access, viewer, photo_set_id)
    if await service.rename(photo_set_id, body.name, actor=Actor.user(viewer.id)) is None:
        raise _missing()  # pragma: no cover (resolved above, so the row is there)
    visible = await access.visible_photo_set(viewer, photo_set_id)
    if visible is None:
        raise _missing()  # pragma: no cover (a rename cannot conceal anything)
    return _view(visible, art=face_version(viewer.cache_stamp))


@router.delete("/photo-sets/{photo_set_id}", dependencies=[Depends(csrf_protect)])
async def delete_photo_set(
    photo_set_id: str,
    service: Annotated[PhotoSetService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    forgets: Annotated[ForgetGoneSeam, Depends(wiring.forget_gone)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> Response:
    """Delete the set. The pictures stay exactly where they are: a set is a grouping."""
    photo_set = await _require_set(access, viewer, photo_set_id)
    await service.delete(photo_set_id, actor=Actor.user(viewer.id))
    await forgets.forget_gone("photo_set", photo_set_id, name=photo_set.name, by=viewer)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/photo-sets/{photo_set_id}/notes", dependencies=[Depends(csrf_protect)])
async def set_photo_set_notes(
    photo_set_id: str,
    body: NotesWrite,
    service: Annotated[PhotoSetService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> PhotoSetSummary:
    await _require_set(access, viewer, photo_set_id)
    await service.set_notes(photo_set_id, body.notes, actor=Actor.user(viewer.id))
    visible = await access.visible_photo_set(viewer, photo_set_id)
    if visible is None:
        raise _missing()  # pragma: no cover
    return _view(visible, art=face_version(viewer.cache_stamp))


@router.get("/photo-sets/{photo_set_id}/cover")
async def photo_set_cover(
    photo_set_id: str,
    request: Request,
    service: Annotated[PhotoSetService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    pictures: Annotated[CoverPictures, Depends(wiring.cover_pictures)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> Response:
    """The picture this album is drawn as, at whichever moment was chosen. See `serve_cover`."""
    await _require_set(access, viewer, photo_set_id)
    chosen = await service.chosen_cover(photo_set_id)
    return await serve_cover(request, access, viewer, chosen=chosen, pictures=pictures)


@router.put("/photo-sets/{photo_set_id}/cover", dependencies=[Depends(csrf_protect)])
async def set_photo_set_cover(
    photo_set_id: str,
    body: CoverWrite,
    service: Annotated[PhotoSetService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    stills: Annotated[StillSeam, Depends(wiring.stills)],
    pictures: Annotated[CoverPictures, Depends(wiring.cover_pictures)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> PhotoSetSummary:
    """Choose the picture the set is drawn as, and which moment of it."""
    await _require_set(access, viewer, photo_set_id)
    asset_id = (
        None
        if body.asset_id is None
        else await require_reachable(access, viewer, body.asset_id, _missing)
    )
    before = await service.chosen_cover(photo_set_id)
    kept = upload_kept_by_put(body.upload_id, asset_id=asset_id, frame=body.frame, before=before)
    await service.set_cover(
        photo_set_id,
        asset_id,
        None if kept else body.at_ms,
        kept,
        actor=Actor.user(viewer.id),
        frame=body.frame,
    )
    await forget_displaced(pictures, before, after=kept)
    # Queued rather than rendered here, so the request never waits on ffmpeg.
    if body.asset_id is not None and body.at_ms is not None:
        await stills.wants_still(body.asset_id, body.at_ms)
    return await _set_now(access, viewer, photo_set_id)


@router.post("/photo-sets/{photo_set_id}/cover-picture", dependencies=[Depends(csrf_protect)])
async def upload_photo_set_cover(
    photo_set_id: str,
    file: Annotated[UploadFile, File()],
    service: Annotated[PhotoSetService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    pictures: Annotated[CoverPictures, Depends(wiring.cover_pictures)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> PhotoSetSummary:
    """A picture from outside the album, as the album's cover. See `receive_cover`."""
    await _require_set(access, viewer, photo_set_id)
    before = await service.chosen_cover(photo_set_id)
    await receive_cover(
        pictures,
        file.read,
        before=before,
        point_at=lambda upload_id: _pointed(
            service, photo_set_id, upload_id, Actor.user(viewer.id)
        ),
    )
    return await _set_now(access, viewer, photo_set_id)


async def _pointed(
    service: PhotoSetService, photo_set_id: str, upload_id: str, actor: Actor
) -> bool:
    """`set_cover` answers with the row; `receive_cover` asks whether it landed."""
    return await service.set_cover(photo_set_id, None, None, upload_id, actor=actor) is not None


async def _set_now(access: Repository, viewer: Viewer, photo_set_id: str) -> PhotoSetSummary:
    """The album's own row as a cover write hands it back. Both cover routes send this."""
    visible = await access.visible_photo_set(viewer, photo_set_id)
    if visible is None:
        raise _missing()  # pragma: no cover
    return _view(visible, art=face_version(viewer.cache_stamp))


@router.put("/photo-sets/{photo_set_id}/favorite", dependencies=[Depends(csrf_protect)])
async def set_photo_set_favorite(
    photo_set_id: str,
    body: FavoriteWrite,
    service: Annotated[PhotoSetService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> PhotoSetStateView:
    """Heart a set. This viewer's opinion, not the set's, so not admin-only."""
    await _require_set(access, viewer, photo_set_id)
    favorite, rating = await service.set_favorite(viewer, photo_set_id, favorite=body.favorite)
    return PhotoSetStateView(favorite=favorite, rating=rating)


@router.put("/photo-sets/{photo_set_id}/pin", dependencies=[Depends(csrf_protect)])
async def set_photo_set_pinned(
    photo_set_id: str,
    body: PinWrite,
    pins: Annotated[EntityStateStore, Depends(wiring.entity_state)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> PinView:
    """Keep a photo set at the top of the Photo sets wall, for this user. See the heart."""
    await _require_set(access, viewer, photo_set_id)
    return PinView(
        pinned=await pins.set_pinned(
            PinnableKind.PHOTO_SET, photo_set_id, viewer.id, pinned=body.pinned
        )
    )


@router.put("/photo-sets/{photo_set_id}/rating", dependencies=[Depends(csrf_protect)])
async def set_photo_set_rating(
    photo_set_id: str,
    body: RatingWrite,
    service: Annotated[PhotoSetService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> PhotoSetStateView:
    await _require_set(access, viewer, photo_set_id)
    favorite, rating = await service.set_rating(viewer, photo_set_id, rating=body.rating)
    return PhotoSetStateView(favorite=favorite, rating=rating)


@router.put(
    "/photo-sets/{photo_set_id}/vault",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_protect)],
)
async def set_photo_set_vault(
    photo_set_id: str,
    body: VaultWrite,
    request: Request,
    service: Annotated[PhotoSetService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> Response:
    """Hide the set and its pictures from this user, or stop hiding it; behind the PIN."""
    await _require_set(access, viewer, photo_set_id)
    await require_vault_pin(request, viewer)
    await service.set_vault(viewer, photo_set_id, vault=body.vault)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/photo-sets/{photo_set_id}/items", dependencies=[Depends(csrf_protect)])
async def edit_photo_set_items(
    photo_set_id: str,
    body: ItemsWrite,
    service: Annotated[PhotoSetService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    remove: Annotated[bool, Query()] = False,
) -> BulkWriteDone:
    """Add pictures to a set, or take them out; a picture that cannot be resolved is counted."""
    await _require_set(access, viewer, photo_set_id)
    actionable = await access.actionable_of(viewer, body.asset_ids)
    wanted = list(actionable.allowed)
    changed = 0
    if wanted:
        changed = (
            await service.remove(photo_set_id, wanted, actor=Actor.user(viewer.id))
            if remove
            else await service.add(photo_set_id, wanted, actor=Actor.user(viewer.id))
        )
    return BulkWriteDone.after(actionable, changed)


@router.get("/photo-sets/{photo_set_id}/tags")
async def photo_set_tags(
    photo_set_id: str,
    service: Annotated[PhotoSetService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> list[TagOnPhotoSet]:
    """The tags on this set, in the address shape every entity uses."""
    await _require_set(access, viewer, photo_set_id)
    return [
        TagOnPhotoSet(id=row["id"], name=row["name"]) for row in await service.tags_on(photo_set_id)
    ]


@router.post("/photo-sets/{photo_set_id}/tags", dependencies=[Depends(csrf_protect)])
async def set_photo_set_tag(
    photo_set_id: str,
    body: PhotoSetTagWrite,
    service: Annotated[PhotoSetService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> list[TagOnPhotoSet]:
    """Put a tag on the set or take one off. Admin-only, like every write to shared vocabulary."""
    await _require_set(access, viewer, photo_set_id)
    await service.set_tag(photo_set_id, body.tag_id, on=body.add)
    return [
        TagOnPhotoSet(id=row["id"], name=row["name"]) for row in await service.tags_on(photo_set_id)
    ]
