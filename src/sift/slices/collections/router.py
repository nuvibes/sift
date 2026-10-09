# SPDX-License-Identifier: AGPL-3.0-or-later
"""Endpoints for collections.

A collection is shared, so editing one is admin-only; every read is scoped, numbers included;
every refusal is the 404 an unknown id gets; and adding a file moves nothing on disk.
"""

from __future__ import annotations

from typing import Annotated

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
    NO_FILTER,
    SHUFFLE_MODULUS,
    SIMILARITY,
    SORT_KEYS,
    AssetView,
    CollectionView,
    EntityNarrowing,
    GrantMark,
    ObjectType,
    Repository,
    Viewer,
    related_filter,
)
from sift.kernel.access.catalog import made_by
from sift.kernel.access.history import DEFAULT_LIMIT, MAX_LIMIT
from sift.kernel.access.history_entity import history_of_collection
from sift.kernel.access.repository.asset_orders import ordering_for
from sift.kernel.content import (
    AssetUserState,
    EntityStateStore,
    PinnableKind,
    PinView,
    PinWrite,
    UserStateStore,
)
from sift.kernel.covers import (
    CoverPictures,
    forget_displaced,
    receive_cover,
    serve_cover,
    upload_kept_by_put,
)
from sift.kernel.db import Database, IntegrityError
from sift.kernel.ledger import Actor
from sift.kernel.paging import resume_at
from sift.kernel.reach import BulkWriteDone, require_reachable
from sift.kernel.seams import FilterEngine, ForgetGoneSeam, ReindexSeam, StillSeam
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
from sift.slices.collections.models import (
    MAX_COLLECTION_NAME,
    CollectionContents,
    CollectionItem,
    CollectionList,
    CollectionStateView,
    CollectionSummary,
    CollectionTagWrite,
    CollectionWrite,
    CoverWrite,
    FavoriteWrite,
    ItemsWrite,
    RatingWrite,
    TagOnCollection,
    VaultWrite,
)
from sift.slices.collections.service import SERVICE, CollectionService

router = APIRouter(tags=["collections"])

_NOT_FOUND = "not found"


def _service(request: Request) -> CollectionService:
    return part_of(request, SERVICE)


#: The contents route's own parameters; any other name in its address is the query language's.
_OWN = frozenset({"limit", "offset", "sort", "seed", "meaning", "pinned_first"})


def _missing() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, _NOT_FOUND)


def _view(
    collection: CollectionView, mark: GrantMark | None = None, *, art: str
) -> CollectionSummary:
    return CollectionSummary(
        id=collection.id,
        name=collection.name,
        cover_asset_id=collection.cover_asset_id,
        cover_upload_id=collection.cover_upload_id,
        cover_at_ms=collection.cover_at_ms,
        cover_frame=collection.cover_frame,
        art=art,
        vault=collection.vault,
        item_count=collection.item_count,
        size_bytes=collection.size_bytes,
        shared=mark.shared if mark else False,
        restricted=mark.restricted if mark else False,
        favorite=collection.favorite,
        pinned=collection.pinned,
        rating=collection.rating,
        locked=collection.locked,
    )


def _item(
    view: AssetView, *, revealed: bool, state: AssetUserState | None = None
) -> CollectionItem:
    """One row of a collection; `state` is this user's heart and stars, read once per page."""
    if view.concealed and not revealed:
        return CollectionItem(id=view.asset.id, media_type="", concealed=True)
    return CollectionItem(
        id=view.asset.id,
        media_type=view.asset.media_type,
        width=view.asset.width,
        height=view.asset.height,
        duration_ms=view.asset.duration_ms,
        thumb=view.has_thumb,
        art=view.art_version,
        original_filename=view.asset.original_filename,
        pinned=view.pinned,
        favorite=state.favorite if state else False,
        rating=state.rating if state else None,
    )


async def _require_collection(
    access: Repository, viewer: Viewer, collection_id: str
) -> CollectionView:
    """The collection, if this viewer may be shown it, else 404; a vaulted one even from admins."""
    collection = await access.visible_collection(viewer, collection_id)
    if collection is None:
        raise _missing()
    return collection


class CollectionsNarrowing:
    """What the collections on the wall are; `sharing` asked by a non-admin filters to nothing."""

    def __init__(
        self,
        mine: Annotated[list[str] | None, Query()] = None,
        tags: Annotated[list[str] | None, Query()] = None,
        cover: Annotated[list[str] | None, Query()] = None,
        created: Annotated[list[str] | None, Query()] = None,
        sharing: Annotated[list[str] | None, Query()] = None,
    ) -> None:
        self.picks: dict[str, list[str] | None] = {
            "mine": mine,
            "tags": tags,
            "cover": cover,
            "created": created,
            "sharing": sharing,
        }

    def of(self, viewer: Viewer) -> EntityNarrowing:
        """The picks as the one conjunct the statement takes. See `EntityNarrowing`."""
        return EntityNarrowing.of("collection", self.picks, is_admin=viewer.is_admin)


@router.get("/collections")
async def list_collections(
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    narrowed: Annotated[CollectionsNarrowing, Depends()],
    prefix: Annotated[str, Query(max_length=MAX_COLLECTION_NAME)] = "",
    anywhere: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort: Annotated[str, Query()] = ENTITY_SORT_SEEN,
    start: Annotated[str | None, Query(alias="from")] = None,
    near: Annotated[int | None, Query(ge=0)] = None,
    person: Annotated[str | None, Query()] = None,
    tag: Annotated[str | None, Query()] = None,
    site: Annotated[str | None, Query()] = None,
    photo_set: Annotated[str | None, Query()] = None,
    song: Annotated[str | None, Query()] = None,
    asset: Annotated[str | None, Query()] = None,
) -> CollectionList:
    """One page of the collections this viewer may know about, by name, each with a scoped count.

    `prefix` narrows to names starting with what is typed. `from` starts the page at a row,
    resolved in this same list; one that resolves to nothing serves `near`, or the top.
    """
    # Refused rather than quietly ignored. See the People wall for the whole of the reasoning.
    if sort not in ENTITY_SORT_KEYS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"unknown sort {sort!r}")
    narrowing = related_filter(
        person=person, tag=tag, site=site, photo_set=photo_set, song=song, asset=asset
    )
    rows = narrowed.of(viewer)
    if start is not None:
        at = await access.position_of_collection(
            viewer,
            start,
            prefix,
            anywhere=anywhere,
            sort=sort,
            asset_filter=narrowing,
            narrowing=rows,
        )
        offset = resume_at(at, near)
    page = await access.list_collections(
        viewer,
        prefix,
        anywhere=anywhere,
        limit=limit,
        offset=offset,
        sort=sort,
        asset_filter=narrowing,
        narrowing=rows,
    )
    marks = await access.visible_marks(
        viewer, ObjectType.COLLECTION, [collection.id for collection in page.items]
    )
    # What each card draws beside its name, read for this page only. See `Repository.card_counts`.
    counts = await access.card_counts(
        viewer, "collection", [collection.id for collection in page.items]
    )
    return CollectionList(
        items=[
            _view(
                collection, marks.get(collection.id), art=face_version(viewer.cache_stamp)
            ).model_copy(update={"counts": counts.get(collection.id, {})})
            for collection in page.items
        ],
        total=page.total,
        limit=limit,
        offset=offset,
    )


@router.get("/collections/facets")
async def collection_facets(
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    narrowed: Annotated[CollectionsNarrowing, Depends()],
    facet: Annotated[str, Query()],
    limit: Annotated[int, Query(ge=1, le=200)] = 24,
    prefix: Annotated[str, Query(max_length=MAX_COLLECTION_NAME)] = "",
    anywhere: Annotated[bool, Query()] = False,
    person: Annotated[str | None, Query()] = None,
    tag: Annotated[str | None, Query()] = None,
    site: Annotated[str | None, Query()] = None,
    photo_set: Annotated[str | None, Query()] = None,
    song: Annotated[str | None, Query()] = None,
    asset: Annotated[str | None, Query()] = None,
) -> FacetCounts:
    """What the collections this wall reaches are made of, along one dimension, with counts.

    Declared before `/collections/{collection_id}`, which would otherwise read this as an id.
    """
    if facet not in ENTITY_FACETS["collection"] or (
        facet in ADMIN_ENTITY_FACETS and not viewer.is_admin
    ):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"unknown facet {facet!r}")
    counted = await access.collection_facets(
        viewer,
        facet,
        limit=limit,
        prefix=prefix,
        anywhere=anywhere,
        asset_filter=related_filter(
            person=person, tag=tag, site=site, photo_set=photo_set, song=song, asset=asset
        ),
        narrowing=narrowed.of(viewer),
    )
    return FacetCounts(
        facet=facet,
        values=[FacetValue(value=one.value, count=one.count, label=one.label) for one in counted],
    )


@router.post(
    "/collections", status_code=status.HTTP_201_CREATED, dependencies=[Depends(csrf_protect)]
)
async def create_collection(
    body: CollectionWrite,
    service: Annotated[CollectionService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> CollectionSummary:
    """Make an empty collection; names are not unique."""
    collection = await service.create(body.name, owner_id=viewer.id, actor=Actor.user(viewer.id))
    return CollectionSummary(id=collection.id, name=collection.name)


@router.get("/collections/{collection_id}")
async def get_collection(
    collection_id: str,
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> CollectionSummary:
    """One collection's own row: its name, cover, scoped count and this user's O tally."""
    found = await _require_collection(access, viewer, collection_id)
    return _view(found, art=face_version(viewer.cache_stamp)).model_copy(
        update={"o_count": await access.o_count_of_collection(viewer, found.id)}
    )


@router.get("/collections/{collection_id}/history")
async def history_of_a_collection(
    collection_id: str,
    access: Annotated[Repository, Depends(wiring.access)],
    database: Annotated[Database, Depends(wiring.database)],
    workbench: Annotated[Workbench, Depends(wiring.workbench)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> list[HistoryEvent]:
    """What happened to this collection, oldest first; one this viewer may not see is a 404."""
    await _require_collection(access, viewer, collection_id)
    # No Undo is offered on what the reversers say can never be taken back.
    final = [one.name for one in workbench.reversers if not one.reversible]
    return [
        history_event(event)
        for event in await history_of_collection(
            database, viewer, collection_id, limit=limit, final_queues=final, bench=workbench
        )
    ]


@router.get("/collections/{collection_id}/made-by")
async def maker_of_a_collection(
    collection_id: str,
    access: Annotated[Repository, Depends(wiring.access)],
    database: Annotated[Database, Depends(wiring.database)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> MadeBy | None:
    """Who made it: Sift and the pass, the user who asked, or nobody (null, the ordinary answer)."""
    await _require_collection(access, viewer, collection_id)
    made = await made_by(database, viewer, "collection", collection_id)
    return None if made is None else made_by_wire(made)


@router.put("/collections/{collection_id}", dependencies=[Depends(csrf_protect)])
async def update_collection(
    collection_id: str,
    body: CollectionWrite,
    service: Annotated[CollectionService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    reindexer: Annotated[ReindexSeam, Depends(wiring.reindexer)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> CollectionSummary:
    """Rename a collection; the count and cover in the answer are read back, scoped."""
    await _require_collection(access, viewer, collection_id)
    if await service.rename(collection_id, body.name, actor=Actor.user(viewer.id)) is None:
        raise _missing()  # pragma: no cover (resolved above, so the row is there)
    visible = await access.visible_collection(viewer, collection_id)
    if visible is None:
        raise _missing()  # pragma: no cover (a rename cannot conceal anything)
    # The name is indexed on exactly this collection's assets: a job rewrites them.
    await reindexer.queue_many(await service.members(collection_id))
    # With the mark: the screen swaps its row for this reply.
    marks = await access.visible_marks(viewer, ObjectType.COLLECTION, [collection_id])
    return _view(visible, marks.get(collection_id), art=face_version(viewer.cache_stamp))


@router.put("/collections/{collection_id}/favorite", dependencies=[Depends(csrf_protect)])
async def set_collection_favorite(
    collection_id: str,
    body: FavoriteWrite,
    service: Annotated[CollectionService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> CollectionStateView:
    """Heart a collection, or take the heart off, for this user; guests too."""
    await _require_collection(access, viewer, collection_id)
    favorite, rating = await service.set_favorite(viewer, collection_id, favorite=body.favorite)
    return CollectionStateView(favorite=favorite, rating=rating)


@router.put("/collections/{collection_id}/pin", dependencies=[Depends(csrf_protect)])
async def set_collection_pinned(
    collection_id: str,
    body: PinWrite,
    pins: Annotated[EntityStateStore, Depends(wiring.entity_state)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> PinView:
    """Keep a collection at the top of the Collections wall, for this user. See the heart."""
    await _require_collection(access, viewer, collection_id)
    return PinView(
        pinned=await pins.set_pinned(
            PinnableKind.COLLECTION, collection_id, viewer.id, pinned=body.pinned
        )
    )


@router.put("/collections/{collection_id}/rating", dependencies=[Depends(csrf_protect)])
async def set_collection_rating(
    collection_id: str,
    body: RatingWrite,
    service: Annotated[CollectionService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> CollectionStateView:
    """Set the stars on a collection, or clear them with null. For this user; see the heart."""
    await _require_collection(access, viewer, collection_id)
    favorite, rating = await service.set_rating(viewer, collection_id, rating=body.rating)
    return CollectionStateView(favorite=favorite, rating=rating)


@router.put(
    "/collections/{collection_id}/vault",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_protect)],
)
async def set_vault(
    collection_id: str,
    body: VaultWrite,
    request: Request,
    service: Annotated[CollectionService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> Response:
    """Hide a collection, or bring it back, for this user; needs a PIN, or an open Hidden."""
    if body.vault:
        await require_vault_pin(request, viewer)
    await _require_collection(access, viewer, collection_id)
    await service.set_vault(viewer, collection_id, vault=body.vault)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/collections/{collection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_protect)],
)
async def delete_collection(
    collection_id: str,
    service: Annotated[CollectionService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    reindexer: Annotated[ReindexSeam, Depends(wiring.reindexer)],
    forgets: Annotated[ForgetGoneSeam, Depends(wiring.forget_gone)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> Response:
    """Delete a collection, its membership rows, and every grant that named it; never a file."""
    shelf = await _require_collection(access, viewer, collection_id)
    held = await service.delete(collection_id, actor=Actor.user(viewer.id))
    if held is None:
        raise _missing()  # pragma: no cover (resolved above, so the row is there)
    # Those assets only: a whole-library rebuild would hold the write lock for seconds.
    await reindexer.touched_many(held)
    await forgets.forget_gone("collection", collection_id, name=shelf.name, by=viewer)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/collections/{collection_id}/items")
async def collection_items(
    collection_id: str,
    request: Request,
    access: Annotated[Repository, Depends(wiring.access)],
    state: Annotated[UserStateStore, Depends(wiring.user_state)],
    engine: Annotated[FilterEngine, Depends(wiring.filter_engine)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort: Annotated[str | None, Query()] = None,
    seed: Annotated[int | None, Query(ge=0, le=SHUFFLE_MODULUS - 1)] = None,
    meaning: Annotated[bool | None, Query()] = None,
    # Read off the raw address by the engine; declared so the API description names them.
    q: Annotated[str | None, Query()] = None,
    people: Annotated[list[str] | None, Query()] = None,
    tags: Annotated[list[str] | None, Query()] = None,
    sites: Annotated[list[str] | None, Query()] = None,
    collections: Annotated[list[str] | None, Query()] = None,
    photo_sets: Annotated[list[str] | None, Query()] = None,
    songs: Annotated[list[str] | None, Query()] = None,
) -> CollectionContents:
    """A collection's contents, scoped, in `/assets` order with pins first; `q` filters it."""
    if sort is not None and sort not in SORT_KEYS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"unknown sort {sort!r}")
    await _require_collection(access, viewer, collection_id)
    by_meaning = meaning if meaning is not None else sort == SIMILARITY
    # Nothing asked costs no compile, which keeps the plain read exactly the read it was.
    asked = any(name not in _OWN for name in request.query_params)
    narrowed = (
        (
            await engine.narrow(
                viewer, request.query_params, by_meaning=by_meaning, need=offset + limit
            )
        ).asset_filter
        if asked
        else NO_FILTER
    )
    page = await access.visible_assets(
        viewer,
        limit=limit,
        offset=offset,
        collection_id=collection_id,
        asset_filter=narrowed,
        # This wall honours the pin, and it offers the verb: the two are one fact.
        pinned_first=True,
        sort=ordering_for(sort, narrowed, by_meaning=by_meaning),
        seed=seed,
    )
    # One statement for the page's hearts and stars, never about a concealed placeholder.
    named = [item.asset.id for item in page.items if viewer.show_hidden or not item.concealed]
    states = await state.states_of(named, viewer.id) if named else {}
    return CollectionContents(
        items=[
            _item(item, revealed=viewer.show_hidden, state=states.get(item.asset.id))
            for item in page.items
        ],
        total=page.total,
        limit=limit,
        offset=offset,
    )


@router.post("/collections/{collection_id}/items", dependencies=[Depends(csrf_protect)])
async def edit_items(
    collection_id: str,
    body: ItemsWrite,
    service: Annotated[CollectionService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    reindexer: Annotated[ReindexSeam, Depends(wiring.reindexer)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> BulkWriteDone:
    """Add to or remove from a collection, moving no file; unresolved files are counted."""
    await _require_collection(access, viewer, collection_id)

    try:
        actionable = await access.actionable_of(viewer, body.asset_ids)
        wanted = list(actionable.allowed)
        changed = 0
        if wanted:
            changed = (
                await service.add(collection_id, wanted, actor=Actor.user(viewer.id))
                if body.action == "add"
                else await service.remove(collection_id, wanted, actor=Actor.user(viewer.id))
            )
            # Rewritten now, as one call.
            await reindexer.touched_many(wanted)
        return BulkWriteDone.after(actionable, changed)
    except IntegrityError:
        raise _missing() from None


@router.get("/collections/{collection_id}/cover")
async def collection_cover(
    collection_id: str,
    request: Request,
    service: Annotated[CollectionService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    pictures: Annotated[CoverPictures, Depends(wiring.cover_pictures)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> Response:
    """The picture this collection is drawn as, at whichever moment was chosen. See `serve_cover`."""
    await _require_collection(access, viewer, collection_id)
    chosen = await service.chosen_cover(collection_id)
    return await serve_cover(request, access, viewer, chosen=chosen, pictures=pictures)


async def _collection_now(
    access: Repository, viewer: Viewer, collection_id: str
) -> CollectionSummary:
    """The shelf's own row as both cover writes hand it back."""
    visible = await access.visible_collection(viewer, collection_id)
    if visible is None:
        raise _missing()  # pragma: no cover (resolved above, and this edit cannot conceal it)
    return _view(visible, art=face_version(viewer.cache_stamp))


@router.put("/collections/{collection_id}/cover", dependencies=[Depends(csrf_protect)])
async def set_cover(
    collection_id: str,
    body: CoverWrite,
    service: Annotated[CollectionService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    stills: Annotated[StillSeam, Depends(wiring.stills)],
    pictures: Annotated[CoverPictures, Depends(wiring.cover_pictures)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> CollectionSummary:
    """Wear one of its items as the cover, at a moment, or null; it must be in it and visible."""
    await _require_collection(access, viewer, collection_id)
    if body.asset_id is not None:
        resolved = await require_reachable(access, viewer, body.asset_id, _missing)
        if not await service.holds(collection_id, resolved):
            raise _missing()

    before = await service.chosen_cover(collection_id)
    kept = upload_kept_by_put(
        body.upload_id, asset_id=body.asset_id, frame=body.frame, before=before
    )
    if (
        await service.set_cover(
            collection_id,
            body.asset_id,
            None if kept else body.at_ms,
            kept,
            actor=Actor.user(viewer.id),
            frame=body.frame,
        )
        is None
    ):
        raise _missing()  # pragma: no cover (resolved above, so the row is there)
    await forget_displaced(pictures, before, after=kept)
    # Queued, not rendered here; the client shows the file's own still until it lands.
    if body.asset_id is not None and body.at_ms is not None:
        await stills.wants_still(body.asset_id, body.at_ms)
    return await _collection_now(access, viewer, collection_id)


@router.post("/collections/{collection_id}/cover-picture", dependencies=[Depends(csrf_protect)])
async def upload_collection_cover(
    collection_id: str,
    file: Annotated[UploadFile, File()],
    service: Annotated[CollectionService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    pictures: Annotated[CoverPictures, Depends(wiring.cover_pictures)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> CollectionSummary:
    """A picture from outside the library as this shelf's cover, so no membership check."""
    await _require_collection(access, viewer, collection_id)
    before = await service.chosen_cover(collection_id)
    await receive_cover(
        pictures,
        file.read,
        before=before,
        point_at=lambda upload_id: _pointed(
            service, collection_id, upload_id, Actor.user(viewer.id)
        ),
    )
    return await _collection_now(access, viewer, collection_id)


async def _pointed(
    service: CollectionService, collection_id: str, upload_id: str, actor: Actor
) -> bool:
    """`set_cover` answers with the row; `receive_cover` asks whether it landed."""
    return await service.set_cover(collection_id, None, None, upload_id, actor=actor) is not None


# --- tags on a collection ---------------------------------------------------------------------


@router.get("/collections/{collection_id}/tags")
async def tags_of_collection(
    collection_id: str,
    service: Annotated[CollectionService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> list[TagOnCollection]:
    await _require_collection(access, viewer, collection_id)
    return [
        TagOnCollection(id=row["id"], name=row["name"])
        for row in await service.tags_of(collection_id)
    ]


@router.post("/collections/{collection_id}/tags", dependencies=[Depends(csrf_protect)])
async def tag_collection(
    collection_id: str,
    body: CollectionTagWrite,
    service: Annotated[CollectionService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> list[TagOnCollection]:
    """Put a tag on a collection, or take it off; admin-only, as tags are shared vocabulary."""
    await _require_collection(access, viewer, collection_id)
    await service.tag(collection_id, body.tag_id, add=body.add)
    return [
        TagOnCollection(id=row["id"], name=row["name"])
        for row in await service.tags_of(collection_id)
    ]
