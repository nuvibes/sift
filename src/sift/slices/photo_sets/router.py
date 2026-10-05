# SPDX-License-Identifier: AGPL-3.0-or-later
"""Endpoints for photo sets.

The same four rules the collections endpoints run on, because they are rules about writing to a
shared catalog rather than rules about what a collection is.

**A set is shared, so editing one is admin-only.** There is one `photo_sets` table for the install
and a set decides what its pictures are. Making, renaming, filling and deleting one all change what
other users see. Reading is open to any signed-in user, scoped to what they may see.

**Every read is scoped, including the numbers.** A set showing twelve pictures above a count of
fifty has said that thirty-eight exist without showing one of them, and a count is enough to
publish a library.

**Denied and missing are the same answer.** Every refusal is the 404 an unknown id would get.

**Nothing on disk moves.** Adding a picture writes one row in a join table.
"""

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
    """The set, if this viewer may be shown it. 404 otherwise, either way.

    Resolved through the access layer even on admin-only writes: a hidden set is concealed from
    admins too, and an edit that could still reach one would be a way to confirm it is there.
    """
    photo_set = await access.visible_photo_set(viewer, photo_set_id)
    if photo_set is None:
        raise _missing()
    return photo_set


class PhotoSetsNarrowing:
    """What the PHOTO SETS on the wall are: their tags, whether they have a cover, who made them,
    and what has been shared.

    A photo set has no owner column (`photo_sets` records where a set came from), so there is no
    "mine" to ask, and inventing one would mean inventing the fact behind it. Who MADE one is
    recorded, which is the Created by column.

    See `PeopleNarrowing` in the people slice for the repeated-key rule, and
    `CollectionsNarrowing` for what an admin-only key does when somebody else sends it.
    """

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
    """One page of the photo sets this viewer may know about.

    `person`, `tag` and `site` are what make this a related list as well as a wall: passed one,
    the answer is the sets whose pictures that thing reaches, counted over the same narrowed set.
    They are ids, resolved by the filter rather than by name, because a name is ambiguous and this
    is called from a page that already holds the id.

    `prefix` narrows to the names beginning with what somebody is typing. See the collection
    listing beside this one for why a picker needs that of the server rather than of its own cache.

    `count` is which tally a card prints: `whole` (every file under that row this viewer may see),
    or `narrowed`, how many of them are on THIS wall. A press on a card carries the page it was
    pressed from, so a card reached through somebody else opens the two together, and its number
    has to be the size of THAT wall or it describes a set the press cannot reach. `whole` by
    default, which is what a plain wall means.

    `from` names a set to start the page at, instead of an offset. The wall pages by whole rows, so
    how many cards a page holds depends on the size of the screen, which means a page NUMBER is
    not a durable thing to put in an address, and the set somebody was looking at is. It is
    resolved against this same question (the same prefix, order, narrowing and tally), because a
    position only means anything in the list it was taken from.

    A `from` that resolves to nothing serves the page it was on (`near`), or the TOP,
    rather than refusing: a set that has since been
    renamed, hidden or deleted is a stale link and not an error. That also means a caller cannot
    learn anything by trying ids: the answer for a set being kept back is the same as for one that
    never existed, and both are the first page. The People wall says all of this too.
    """
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
    """What the photo sets this wall reaches are made of, along one dimension, with counts.

    Declared before `/photo-sets/{photo_set_id}`: routes match in declaration order, and the other
    way round this address would be read as a set called "facets".

    The counts are of SETS, over the statement that decides which of them the wall holds, with
    every narrowing the listing takes. See `people_facets` in the people slice.
    """
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
    """Make an empty set by hand.

    Names are not unique. Two shoots can genuinely both be called "beach", and refusing the second
    would be asserting something about the world that is not true.

    It arrives visible, empty and with no cover, and the answer is built from what was just written
    rather than read back through the access layer: there is nothing to scope: it holds nothing,
    wears no cover, and the name is the one the caller sent.
    """
    photo_set = await service.create(body.name, by_user=viewer.id)
    return PhotoSetSummary(id=photo_set.id, name=photo_set.name, origin=photo_set.origin)


@router.get("/photo-sets/{photo_set_id}")
async def get_photo_set(
    photo_set_id: str,
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> PhotoSetSummary:
    """One set's own row: its name, its cover, where it came from and its scoped count.

    With the sharing mark, because the page draws a badge from it, and a reply that omits a
    restrict is a badge that stops being drawn while the grant is still in force.
    """
    photo_set = await _require_set(access, viewer, photo_set_id)
    marks = await access.visible_marks(viewer, ObjectType.PHOTO_SET, [photo_set_id])
    # And this user's own O tally over the set's pictures, asked for here and nowhere else: it is
    # a sum over the set's files, so the wall would pay one per card for a number no card draws.
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
    """What happened to this Photo Set, oldest first.

    Authenticated rather than admin, the same rule the set's own row follows. The sharing half is an
    admin's and the kernel withholds it, for the reason written there, and on a set that half is
    most of the thread, because `photo_set_items` records no moment for a file going in.

    Resolved through `_require_set` first, so a set this viewer may not be shown answers the same
    404 an id that was never minted would.
    """
    await _require_set(access, viewer, photo_set_id)
    # The registry is asked which kinds of decision can never be taken back, and the answer is
    # handed to the read so it offers no Undo on those: the same question the file's history and
    # a person's ask, from the same place, because an affordance the server would refuse is worse
    # than none. Asked of the REVERSERS rather than of the queues: a decision written by a queue
    # that has since been retired is still one somebody can take back.
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
    """WHO MADE IT: Sift and the pass that did it, the user who asked, or nobody.

    Its own route rather than a field on the row, and that is the one judgement here. `PhotoSetSummary` is
    the shape the wall is drawn from as well as the page, so a maker on it would be filled for the
    one and left null for the other: a field meaning "nothing recorded it" on a page and "nobody
    asked" on a wall, with nothing on the wire to tell them apart. A page that wants the line asks
    for it; a wall that does not, does not pay a point read a row for it.

    Authenticated rather than admin, the same rule the Photo Set's own row and its history follow.
    The kernel read is unscoped and relies on the subject having been resolved first, which is what
    `_require_set` does, so a Photo Set this viewer may not be shown answers the same 404 an id that was
    never minted would, rather than saying who made a thing they cannot see.

    Null is the ordinary answer and never an error: every row made before the catalog recorded this
    says nothing, and a line drawn from nothing would be an invention.
    """
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
    """Rename a set. Hiding one is a separate request.

    The word index is NOT told: a photo set's name is in no column the index gathers (a file's
    title, its music, its filename, its paths, its tags, its people, its usernames, its collections
    and its sites), so a rename changes nothing the index could be wrong about. A whole-index
    rebuild would hold the write lock for many seconds on a large library and buy nothing. If a
    set's name is ever gathered into that text, this needs a `touched_many` of the set's items
    (which ARE nameable) and not a rebuild.
    """
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
    # Ask for the picture of that moment. Queued rather than rendered here: a request that shells
    # out to ffmpeg is a request that takes seconds, and the client falls back to the file's own
    # still until it lands, exactly as a mark's tile does.
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
    """Hide the set from this user, or stop hiding it.

    Behind the PIN, like every other hide: without one there would be nothing to unhide it with.
    Hiding a set conceals its PICTURES as well, which is what somebody hiding it meant.

    The PIN check is AWAITED here rather than declared as a dependency, and that is not a style
    choice: it is the one shape that works. `require_vault_pin` takes a plain `Viewer`, so as a
    `Depends` FastAPI reads that parameter as something the CALLER should send and answers every
    request with a 422 before the handler is reached: declared that way, this route would refuse
    everything, with an error toast as the only sign. Every caller of it in the application awaits
    it inline.
    """
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
    """Add pictures to a set, or take them out. No file is moved.

    A picture that cannot be resolved is SKIPPED and counted, and the reply says how many were left
    out and why. See `sift.kernel.reach`: the case against a partial success is a case against a
    SILENT one, and this one speaks.
    """
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
    """The tags on this set. The same address shape people, sites and collections use, so the one
    client store reaches all four rather than each growing its own."""
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
