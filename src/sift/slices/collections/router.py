# SPDX-License-Identifier: AGPL-3.0-or-later
"""Endpoints for collections.

Four rules run through all of it.

**A collection is shared, so editing one is admin-only.** There is one `collections` table for the
whole install and a collection carries access grants: a share on a collection reaches every item
in it. Making, renaming, filling, rearranging and deleting one all change what other users see.
Reading is open to any signed-in user, scoped to what they may see.

**Every read is scoped, including the numbers.** The list, a single collection and a collection's
contents all come out of the access layer. A collection showing twelve items above a count of fifty
has said that thirty-eight things exist without showing one of them, and a count is enough to
publish a library.

**Denied and missing are the same answer.** Every refusal is the 404 an unknown id would get. A 403
on a collection somebody was never shown confirms it exists, which is most of what was being asked.

**Nothing on disk moves.** Adding an item writes one row in a join table. The file keeps its path
and its bytes, which is the point of organising logically rather than by rearranging directories.
"""

from __future__ import annotations

from collections.abc import Mapping
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
from starlette.datastructures import MultiDict

from sift.kernel import wiring
from sift.kernel.access import (
    ADMIN_ENTITY_FACETS,
    ENTITY_FACETS,
    ENTITY_SORT_KEYS,
    ENTITY_SORT_SEEN,
    NO_FILTER,
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
from sift.kernel.seams import FilterEngine, ReindexSeam, StillSeam
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
from sift.slices.collections.service import SERVICE, CollectionService, UnknownItem

router = APIRouter(tags=["collections"])

_NOT_FOUND = "not found"


def _service(request: Request) -> CollectionService:
    return part_of(request, SERVICE)


#: The filtering fields a collection's contents take: the five kinds of thing a card on its tabs can
#: stand for. In the query language's own spelling, because they ARE its fields: `parse_modal`
#: reads them, and a saved search, a person's Files tab and this route all mean one thing by them.
NARROWING_FIELDS = ("people", "tags", "sites", "collections", "photo_sets", "songs")


def narrowing_of(asked: Mapping[str, list[str] | None]) -> Mapping[str, str] | None:
    """The filtering parameters as the engine reads them, or None when none was given.

    Every value kept, a field given twice included: the parser builds one node per value and
    demands all of them, which is what AND across picks means. A multidict rather than a plain one,
    because a plain mapping keeps only the last value of a name written twice: the widening
    direction, which is the one thing a filter may not do.
    """
    pairs = [(name, value) for name in NARROWING_FIELDS for value in asked.get(name) or []]
    return MultiDict(pairs) if pairs else None


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
    """One row of a collection, from what the access layer returned.

    `view.concealed` says this item is vaulted, not that it must be withheld from THIS viewer:
    an unlocked vault reveals it here exactly as it does on the grid and the Hidden screen.
    Locked, it reaches here only in the mode that keeps placeholders (the default takes it out
    of the read entirely) and in that mode the point is a gap that says nothing about what is
    in it, which is what `revealed=False` still produces.

    `state` is this user's heart and stars, or None where it has never said anything about the
    file, which is nearly every file, so the absence is the ordinary case rather than a failure.
    It is handed in rather than read here, because it is read once for the whole page.
    """
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
        position=view.arranged_at,
    )


async def _require_collection(
    access: Repository, viewer: Viewer, collection_id: str
) -> CollectionView:
    """The collection, if this viewer may be shown it. 404 otherwise, either way.

    Resolved through the access layer rather than through the table, even on admin-only
    writes. A vaulted collection is concealed from admins too, and an edit that could still
    reach one would be a way to confirm it is there.
    """
    collection = await access.visible_collection(viewer, collection_id)
    if collection is None:
        raise _missing()
    return collection


class CollectionsNarrowing:
    """What the COLLECTIONS on the wall are: whose they are, their tags, whether they have a
    cover, who made them, and what has been shared.

    The twin of the People wall's. See `PeopleNarrowing` in the people slice for the
    repeated-key rule and for why these are declared on a class rather than twice on two routes.

    `sharing` is admin-only, and asked for by anybody else it filters to NOTHING rather than being
    ignored: ignoring it would widen the answer, and a wall that shows more than was asked for when
    it fails to understand the question is the one direction nothing here may fail in.
    """

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

    A vaulted collection is absent rather than locked, and so is one holding nothing this viewer
    may see. Absent is the point: a greyed-out row still says something is there.

    `prefix` narrows to the names beginning with what somebody is typing, which is what a picker
    wants: the list it draws is a PAGE of this one, so the narrowing has to happen here or the page
    is the first fifty names alphabetically and the answer somebody is typing towards is not in it.

    The total is what this viewer may see, counted over the whole scoped list rather than over the
    page: the same figure and the same reasoning as the People listing.

    A fresh install answers with an empty list. There is no starter set: every collection here
    was made by somebody.

    `from` names a row to start the page at, instead of an offset. The wall pages by whole rows, so
    how many cards a page holds depends on the size of the screen, which means a page NUMBER is
    not a durable thing to put in an address, and the row somebody was looking at is. It is resolved
    against this same question (the same prefix, order and narrowing), because a position only
    means anything in the list it was taken from.

    A `from` that resolves to nothing serves the page it was on (`near`), or the TOP,
    rather than refusing: a row that has since been
    renamed, hidden or deleted is a stale link and not an error. That also means a caller cannot
    learn anything by trying ids: the answer for a row being kept back is the same as for one that
    never existed, and both are the first page.
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

    Declared before `/collections/{collection_id}`: routes match in declaration order, and the
    other way round this address would be read as a collection called "facets".

    The counts are of COLLECTIONS, over the statement that decides which of them the wall holds,
    with every narrowing the listing takes. See `people_facets` in the people slice for the
    reasoning, which is written out once there.
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
    """Make an empty collection.

    Names are not unique. Two shortlists can genuinely both be called "best of", and refusing the
    second would be asserting something about the world that is not true.

    It arrives visible and empty, and the answer is built from what was just written rather than
    read back through the access layer. There is nothing to scope: it holds nothing, wears no
    cover, and the name is the one the caller sent.
    """
    collection = await service.create(body.name, owner_id=viewer.id, actor=Actor.user(viewer.id))
    return CollectionSummary(id=collection.id, name=collection.name)


@router.get("/collections/{collection_id}")
async def get_collection(
    collection_id: str,
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> CollectionSummary:
    """One collection's own row: its name, its cover, its scoped count and this user's O tally.

    The tally is asked for HERE and nowhere else. It is a sum over the collection's files, so the
    wall would pay one of these per card for a number no card draws.
    """
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
    """What happened to this collection, oldest first.

    Authenticated rather than admin, the same rule the collection's own row follows. The sharing
    half is an admin's and the kernel withholds it, for the reason written there, and on a shelf
    that half is most of the thread, because `collection_items` records no moment for a file going
    in and there is therefore nothing else after the shelf was made.

    Resolved through `_require_collection` first, so a shelf this viewer may not be shown answers
    the same 404 an id that was never minted would.
    """
    await _require_collection(access, viewer, collection_id)
    # The registry is asked which kinds of decision can never be taken back, and the answer is
    # handed to the read so it offers no Undo on those: the same question the file's history and
    # a person's ask, from the same place, because an affordance the server would refuse is worse
    # than none. Asked of the REVERSERS rather than of the queues: a decision written by a queue
    # that has since been retired is still one somebody can take back.
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
    """WHO MADE IT: Sift and the pass that did it, the user who asked, or nobody.

    Its own route rather than a field on the row, and that is the one judgement here. `CollectionSummary` is
    the shape the wall is drawn from as well as the page, so a maker on it would be filled for the
    one and left null for the other: a field meaning "nothing recorded it" on a page and "nobody
    asked" on a wall, with nothing on the wire to tell them apart. A page that wants the line asks
    for it; a wall that does not, does not pay a point read a row for it.

    Authenticated rather than admin, the same rule the collection's own row and its history follow.
    The kernel read is unscoped and relies on the subject having been resolved first, which is what
    `_require_collection` does, so a collection this viewer may not be shown answers the same 404 an id that was
    never minted would, rather than saying who made a thing they cannot see.

    Null is the ordinary answer and never an error: every row made before the catalog recorded this
    says nothing, and a line drawn from nothing would be an invention.
    """
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
    """Rename a collection. Concealing one is a separate request.

    The count and the cover in the answer are read back through the access layer rather than
    carried over from before the write, so a rename reports the same scoped numbers every other
    read of this collection would.
    """
    await _require_collection(access, viewer, collection_id)
    if await service.rename(collection_id, body.name, actor=Actor.user(viewer.id)) is None:
        raise _missing()  # pragma: no cover (resolved above, so the row is there)
    visible = await access.visible_collection(viewer, collection_id)
    if visible is None:
        raise _missing()  # pragma: no cover (a rename cannot conceal anything)
    # The name is indexed on every asset in the collection and on no other, so those are exactly
    # what the rename changed. Unlike a tag rename this route CAN name them, and asking is one
    # indexed read against a whole-library rebuild that holds the write lock for seconds.
    await reindexer.touched_many(await service.members(collection_id))
    # With the mark, because the screen replaces its row with this reply, and a reply that omits
    # a restrict is a badge that stops being drawn while the grant is still in force.
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
    """Heart a collection, or take the heart off, for this user.

    Offered to everybody, guests included, and that is the point rather than an oversight: an
    opinion is about the user holding it and reaches nobody else's screen. It changes where a row
    appears on a wall and never whether it appears, so there is nothing here for a permission to
    protect. Restricting is what keeps something from another user.

    Resolved through the access layer first, the same as every other write here: that is what stops
    a row being written against an id this user may not be shown, or one that names nothing.
    """
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
    """Hide a collection, or bring it back, for this user.

    Hiding conceals the collection and everything in it from the screens of the user who did
    it, until they open Hidden. Nobody else is affected; to keep something from another user,
    restrict it. There is no body to answer with: the row this describes is, by the time the answer
    is written, one the caller may no longer be shown.

    Resolved through the access layer first, the same as every other write here and the same as the
    other object types. That is also what makes bringing one back reachable, without a rule of its
    own: a collection this user hid resolves for them only while their Hidden is open, so it can
    only be unhidden by somebody who has already entered the PIN. Before that the request gets the
    404 an unknown id gets, because answering at all would confirm it is there and unhiding it would
    put every item in it back on the grid with Hidden still shut.

    Hiding requires a PIN to exist first. Without one there would be nothing to open it with again,
    which is not concealment: it is losing the collection.
    """
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
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> Response:
    """Delete a collection, its membership rows, and every grant that named it.

    **The files are not touched.** `collection_items` names the asset with `ON DELETE CASCADE`
    pointing at the collection, so this removes the rows joining a collection to files and never
    the files. Deleting a shortlist is tidying up, not deleting media.
    """
    await _require_collection(access, viewer, collection_id)
    held = await service.delete(collection_id, actor=Actor.user(viewer.id))
    if held is None:
        raise _missing()  # pragma: no cover (resolved above, so the row is there)
    # The cascade took the membership rows, so every asset that was in it indexes without its name
    # now: those assets, and not the library. `renamed()` would queue a whole-library rebuild,
    # which holds the write lock for seconds on a large library; each delete would then wait behind
    # the last, and tidying up a few dozen EMPTY shortlists (which change no indexed text) would
    # take most of a minute. An empty list reindexes nothing and never takes the lock.
    await reindexer.touched_many(held)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/collections/{collection_id}/items")
async def collection_items(
    collection_id: str,
    access: Annotated[Repository, Depends(wiring.access)],
    state: Annotated[UserStateStore, Depends(wiring.user_state)],
    engine: Annotated[FilterEngine, Depends(wiring.filter_engine)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    people: Annotated[list[str] | None, Query()] = None,
    tags: Annotated[list[str] | None, Query()] = None,
    sites: Annotated[list[str] | None, Query()] = None,
    collections: Annotated[list[str] | None, Query()] = None,
    photo_sets: Annotated[list[str] | None, Query()] = None,
    songs: Annotated[list[str] | None, Query()] = None,
) -> CollectionContents:
    """A collection's contents, in the arranged order, scoped to this viewer.

    The order is the collection's own, not the grid's: a collection is a sequence somebody put
    together, and showing it newest-first is showing something else. What this user has PINNED
    comes above that sequence, which is the same thing the pin does on every other wall that offers
    it; each item carries its stored position so that rearranging still works off the sequence
    rather than off what happens to be drawn.

    The rows and the total come from one statement in the access layer, so the count is the number
    of items on the screen and never the number of rows in the table.

    **Narrowed by the cards picked on the collection's tabs.** `people`, `tags`, `sites`,
    `collections`, `photo_sets` and `songs` (the Music tab) are the query language's own fields, each a name as the language
    writes one, given once per pick and combined with AND, read by the same engine and the same
    parser the grid reads them with, so a pick narrows a collection's Files tab exactly as it narrows
    a person's. The narrowing goes into the same statement as the scoping and the order, so the
    total counts the narrowed set and the arranged order is kept within it. Declared rather than
    read off the raw address, so a name this route does not take is visibly not taken: the rest of
    the query language (a rating, a date) is the grid's, and this wall offers no bar to write it.
    """
    await _require_collection(access, viewer, collection_id)
    asked = narrowing_of(
        {
            "people": people,
            "tags": tags,
            "sites": sites,
            "collections": collections,
            "photo_sets": photo_sets,
            "songs": songs,
        }
    )
    # Nothing asked is the whole collection, and costs no compile: the engine is asked only when a
    # filtering is in force, which is also what keeps the plain read exactly the read it was.
    narrowed = NO_FILTER if asked is None else await engine.constrain(viewer, asked)
    page = await access.visible_assets(
        viewer,
        limit=limit,
        offset=offset,
        collection_id=collection_id,
        asset_filter=narrowed,
        # This wall honours the pin, and it offers the verb: the two are one fact. What somebody has
        # kept comes to the top of the sequence, and the sequence itself is unharmed: each item
        # carries its stored position, and that is what a rearrange is computed from.
        pinned_first=True,
    )
    # ONE statement for the page's hearts and stars, beside the items rather than per row: the
    # same read and the same reason the grid gives for it. Asked only for what is actually
    # described: a concealed placeholder says nothing about the file behind it, and asking about
    # one would be this route reading something it has just declined to describe.
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
    """Add to, remove from, or rearrange a collection.

    **No file is moved.** This writes rows in the join table and nothing else: every path, every
    byte and every location row is exactly as it was. That is the promise the storage model makes,
    and the test asserting it is the one worth keeping.

    Adding and removing SKIP an asset that cannot be resolved, and the reply says how many were
    left out and why. Rearranging does not, and the difference is not an oversight: an order is a
    whole list, and applying a partial one silently rewrites the positions of files nobody moved.
    A locked vault cannot produce that call anyway, because a client that cannot see a file does
    not send its id in the order.

    An asset can still go between being resolved and being written: two requests, one adding a file
    and one deleting it. The membership row's foreign key catches that and the answer is the same
    404 the resolve would have given a moment later, rather than the 500 an uncaught constraint
    would surface. It is the same answer for the same reason: the asset is not there.
    """
    await _require_collection(access, viewer, collection_id)

    try:
        if body.action in {"add", "remove"}:
            actionable = await access.actionable_of(viewer, body.asset_ids)
            wanted = list(actionable.allowed)
            changed = 0
            if wanted:
                changed = (
                    await service.add(collection_id, wanted, actor=Actor.user(viewer.id))
                    if body.action == "add"
                    else await service.remove(collection_id, wanted, actor=Actor.user(viewer.id))
                )
                # Known by id, so these are rewritten now rather than queued, and as one call:
                # this list runs to five hundred. Reordering is left out on purpose: it moves rows
                # within a collection an asset is already in, so no asset gains or loses the name.
                await reindexer.touched_many(wanted)
            return BulkWriteDone.after(actionable, changed)
        for asset_id in body.asset_ids:
            await require_reachable(access, viewer, asset_id, _missing)
        return BulkWriteDone(changed=await service.reorder(collection_id, body.asset_ids))
    except UnknownItem:
        raise _missing() from None
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
    """The shelf's own row as a cover write hands it back. Both cover routes send this.

    One function rather than four lines written twice: the two callers are the PUT and the POST of
    the same picture, and a difference between them would be a shelf drawn one way after a pick and
    another after an upload.
    """
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
    """Wear one of the items as the cover, at whichever moment of it, or null for none.

    The asset has to be something the collection already holds and something this viewer may open.
    A cover pointing anywhere else is a second, weaker kind of membership that nothing else in the
    model knows about, and it would put a picture of that asset on a screen by a route that never
    checked whether it belonged there.
    """
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
    # Ask for the picture of that moment. Queued rather than rendered here: a request that shells
    # out to ffmpeg is a request that takes seconds, and the client falls back to the file's own
    # still until it lands, exactly as a mark's tile does.
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
    """A picture from outside the library, as this shelf's cover. See `receive_cover`.

    No membership check, unlike the route above, and the difference is the point: that one refuses
    a cover pointing at a file the collection does not hold, because a cover that is a pointer at an
    asset is a second, weaker kind of membership. This is not a pointer at an asset at all: it is
    a picture of the shelf, which is exactly the case the membership rule had no answer for.
    """
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
#
# The same `tags` a file, a person and a site carry, so a collection is not the one kind of thing
# that cannot carry one.


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
    """Put a tag on a collection, or take it off.

    Admin-only, unlike hiding one, and the difference is what the thing IS. Hiding is personal to a
    user; a tag is shared vocabulary that changes what everyone else's searches return.
    """
    await _require_collection(access, viewer, collection_id)
    await service.tag(collection_id, body.tag_id, add=body.add)
    return [
        TagOnCollection(id=row["id"], name=row["name"])
        for row in await service.tags_of(collection_id)
    ]
