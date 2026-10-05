# SPDX-License-Identifier: AGPL-3.0-or-later
"""Endpoints for tags, the heart and the stars.

Three rules run through all of it.

**Tags are shared; hearts and stars are not.** One `tags` table serves the whole install and a tag
carries access grants, so creating, renaming, deleting and assigning are admin-only: those writes
change what every other user sees. A rating and a favorite are one row per person, so any
signed-in user may write its own and can reach nobody else's.

**Denied and missing are the same answer.** Every refusal is the 404 an unknown id would get. A
403 on an asset somebody was never shown confirms it exists, and for a library organised by person
that is most of what was being asked.

**Nothing on disk moves.** Tagging writes one row in a join table. The file keeps its path and its
bytes, which is the point of organising logically rather than by rearranging a directory tree.
"""

from __future__ import annotations

from collections.abc import Mapping
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
    Repository,
    TagSuggestion,
    Viewer,
    asks_disagreements,
    related_filter,
)
from sift.kernel.access.catalog import by_user as made_by_user
from sift.kernel.access.history import DEFAULT_LIMIT, MAX_LIMIT
from sift.kernel.access.history_entity import history_of_tag
from sift.kernel.changes import AssetOpinion
from sift.kernel.content import EntityStateStore, PinnableKind, PinView, PinWrite
from sift.kernel.content.user_state import UserStateStore
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
from sift.kernel.seams import DisagreementSeam, ForgetGoneSeam, ReindexSeam, StillSeam
from sift.kernel.serving import face_version
from sift.kernel.wire import FacetCounts, FacetValue, HistoryEvent, history_event
from sift.kernel.wiring import part_of
from sift.kernel.workbench import Workbench
from sift.slices.auth import csrf_protect, current_viewer, require_admin, require_vault_pin
from sift.slices.tags_ratings.models import (
    MAX_TAG_NAME,
    O_DOWN,
    O_UP,
    CoverWrite,
    FavoriteMany,
    FavoriteWrite,
    OCountWrite,
    PinMany,
    RatingMany,
    RatingWrite,
    TagAssignment,
    TagList,
    TagStateView,
    TagView,
    TagWrite,
    VaultWrite,
)
from sift.slices.tags_ratings.service import SERVICE, DuplicateTag, Tag, TagLoop, TagService

router = APIRouter(tags=["tags"])

_NOT_FOUND = "not found"


def _disagreement_seam(request: Request) -> DisagreementSeam:
    """Who can say which records a stash-box disagrees with: a shape, as slices import no other."""
    return wiring.part_of(request, wiring.DISAGREEMENTS)


def _service(request: Request) -> TagService:
    return part_of(request, SERVICE)


def _missing() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, _NOT_FOUND)


def _taken(name: str) -> HTTPException:
    """A name already in use. A 409 discloses nothing: anybody listing tags reads the names."""
    return HTTPException(status.HTTP_409_CONFLICT, f"there is already a tag called '{name}'")


def _loop() -> HTTPException:
    """A parent that is the tag itself or already filed under it. Said so it can be acted on."""
    return HTTPException(
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        "A tag can't be filed under itself or under a tag that's already inside it. "
        "Choose a tag from another branch.",
    )


def _view(tag: Tag, *, asset_count: int = 0, mark: GrantMark | None = None) -> TagView:
    """One tag as a write route hands it back, mark included: the screen swaps it in for its row."""
    return TagView(
        id=tag.id,
        name=tag.name,
        asset_count=asset_count,
        keep_local=tag.keep_local,
        keep_from_swaps=tag.keep_from_swaps,
        shared=mark.shared if mark else False,
        restricted=mark.restricted if mark else False,
    )


def _tag_from_suggestion(tag: TagSuggestion, mark: GrantMark | None = None, *, art: str) -> TagView:
    """A scoped tag row, as the screen reads one. One builder, so the wall and the by-id read
    cannot come to disagree about what a tag carries."""
    return TagView(
        id=tag.id,
        name=tag.name,
        asset_count=tag.asset_count,
        size_bytes=tag.size_bytes,
        cover_asset_id=tag.cover_asset_id,
        cover_upload_id=tag.cover_upload_id,
        cover_at_ms=tag.cover_at_ms,
        cover_frame=tag.cover_frame,
        art=art,
        keep_local=tag.keep_local,
        keep_from_swaps=tag.keep_from_swaps,
        shared=mark.shared if mark else False,
        restricted=mark.restricted if mark else False,
        hidden=tag.vault,
        favorite=tag.favorite,
        pinned=tag.pinned,
        rating=tag.rating,
        locked=tag.locked,
        parent_id=tag.parent_id,
        parent_name=tag.parent_name,
    )


class TagsNarrowing:
    """What the TAGS on the wall are: their category, whether a stash-box is attached.

    The twin of the People wall's, and it works the same way. See `PeopleNarrowing` in the people
    slice for the repeated-key rule. Two facets only, because a tag is a word and nearly everything
    else about one is the word itself.
    """

    def __init__(
        self,
        category: Annotated[list[str] | None, Query()] = None,
        linked: Annotated[list[str] | None, Query()] = None,
        # WHICH boxes wrote to the tag, beside the yes-or-no `linked` answers. See
        # `_enriched_by_box` in the constraint table.
        enriched: Annotated[list[str] | None, Query()] = None,
        # WHICH BOX MADE the tag, by the box's own word: a different question from `linked`.
        created: Annotated[list[str] | None, Query()] = None,
        # Where a linked stash-box disagrees with what this tag says. Not a column of the table like
        # the two above. See `PeopleNarrowing.of` in the people slice.
        disagrees: Annotated[list[str] | None, Query()] = None,
        # The tags filed directly under this one, by its id: what a tag's own Tags tab is a page
        # of, and the column the "Part of" facet counts. The twin of the Sites wall's `parent`.
        parent: Annotated[list[str] | None, Query()] = None,
        cover: Annotated[list[str] | None, Query()] = None,
        # What has been shared and held back: admin only, refused to anybody else by the narrowing.
        sharing: Annotated[list[str] | None, Query()] = None,
    ) -> None:
        self.picks: dict[str, list[str] | None] = {
            "parent": parent,
            "category": category,
            "linked": linked,
            "enriched": enriched,
            "created": created,
            "disagrees": disagrees,
            "cover": cover,
            "sharing": sharing,
        }

    async def of(
        self,
        viewer: Viewer,
        waiting: DisagreementSeam,
        *,
        counting: str = "",
    ) -> EntityNarrowing:
        """The picks as the one conjunct the statement takes. See `PeopleNarrowing.of` in the people
        slice, which carries the whole of the reasoning about the one facet that is not a column."""
        disagreeing = (
            await waiting.subjects_with_disagreements(viewer, "tag")
            if asks_disagreements(self.picks, counting)
            else None
        )
        return EntityNarrowing.of(
            "tag", self.picks, is_admin=viewer.is_admin, disagreeing=disagreeing
        )


@router.get("/tags")
async def list_tags(
    service: Annotated[TagService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    narrowed: Annotated[TagsNarrowing, Depends()],
    waiting: Annotated[DisagreementSeam, Depends(_disagreement_seam)],
    prefix: Annotated[str, Query(max_length=MAX_TAG_NAME)] = "",
    anywhere: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort: Annotated[str, Query()] = ENTITY_SORT_SEEN,
    start: Annotated[str | None, Query(alias="from")] = None,
    near: Annotated[int | None, Query(ge=0)] = None,
    person: Annotated[str | None, Query()] = None,
    site: Annotated[str | None, Query()] = None,
    collection: Annotated[str | None, Query()] = None,
    photo_set: Annotated[str | None, Query()] = None,
    song: Annotated[str | None, Query()] = None,
    count: Annotated[Literal["whole", "narrowed"], Query()] = "whole",
) -> TagList:
    """The tags this viewer may know about, most-used first.

    Passed one of the four narrowing parameters, this is a RELATED list: the tags on that thing's
    files, counted over the same narrowed set. The count is the count in that context (twelve of
    Jane's files, not twelve in the library), because a scoped wall beside a global number is two
    populations on one screen.

    Both the chip editor and the autocomplete read this, and search will read the same thing. The
    counts come out of the access layer already scoped, so a tag never reports assets the person
    asking has not been shown.

    A fresh install answers with an empty list. There is no starter set: every tag here was made
    by somebody.

    `count` is which tally a card prints: `whole` (every file under that row this viewer may see),
    or `narrowed`, how many of them are on THIS wall. A press on a card carries the page it was
    pressed from, so a card reached through somebody else opens the two together, and its number
    has to be the size of THAT wall or it describes a set the press cannot reach. `whole` by
    default, which is what a plain wall means. The People wall's `count` reads the same way.

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
    # Refused rather than quietly ignored, exactly as the People wall's is: a caller who asked for
    # an order and silently got another has a wall that looks wrong for no visible reason.
    if sort not in ENTITY_SORT_KEYS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"unknown sort {sort!r}")
    narrowing = related_filter(
        person=person, site=site, collection=collection, photo_set=photo_set, song=song
    )
    # Read once and handed to both, because the position and the page have to be taken in the same
    # list, and this one costs a query of its own.
    rows = await narrowed.of(viewer, waiting)
    if start is not None:
        at = await access.position_of_tag(
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
    page = await service.list_tags(
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
    marks = await access.visible_marks(viewer, ObjectType.TAG, [tag.id for tag in page.items])
    # What each card draws beside its name, read for this page only. See `Repository.card_counts`.
    counts = await access.card_counts(viewer, "tag", [tag.id for tag in page.items])
    return TagList(
        items=[
            _tag_from_suggestion(
                tag, marks.get(tag.id), art=face_version(viewer.cache_stamp)
            ).model_copy(update={"counts": counts.get(tag.id, {})})
            for tag in page.items
        ],
        total=page.total,
        limit=limit,
        offset=offset,
    )


@router.get("/tags/facets")
async def tag_facets(
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    narrowed: Annotated[TagsNarrowing, Depends()],
    waiting: Annotated[DisagreementSeam, Depends(_disagreement_seam)],
    facet: Annotated[str, Query()],
    limit: Annotated[int, Query(ge=1, le=200)] = 24,
    prefix: Annotated[str, Query(max_length=MAX_TAG_NAME)] = "",
    anywhere: Annotated[bool, Query()] = False,
    person: Annotated[str | None, Query()] = None,
    site: Annotated[str | None, Query()] = None,
    collection: Annotated[str | None, Query()] = None,
    photo_set: Annotated[str | None, Query()] = None,
    song: Annotated[str | None, Query()] = None,
) -> FacetCounts:
    """What the tags this wall reaches are made of, along one dimension, with counts.

    Declared before `/tags/{tag_id}`: routes match in declaration order, and the other way round
    this address would be read as a tag called "facets".

    The counts are of TAGS, over the statement that decides which tags the wall holds, with every
    narrowing the listing takes. See `people_facets` in the people slice for the reasoning, which
    is the same here and is written out once there.
    """
    if facet not in ENTITY_FACETS["tag"] or (facet in ADMIN_ENTITY_FACETS and not viewer.is_admin):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"unknown facet {facet!r}")
    counted = await access.tag_facets(
        viewer,
        facet,
        limit=limit,
        prefix=prefix,
        anywhere=anywhere,
        asset_filter=related_filter(
            person=person, site=site, collection=collection, photo_set=photo_set, song=song
        ),
        narrowing=await narrowed.of(viewer, waiting, counting=facet),
    )
    return FacetCounts(
        facet=facet,
        values=[FacetValue(value=one.value, count=one.count, label=one.label) for one in counted],
    )


@router.get("/tags/{tag_id}")
async def read_tag(
    tag_id: str,
    service: Annotated[TagService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> TagView:
    """One tag, as its own page reads it.

    404 for a tag this viewer may not be shown and 404 for an id that was never minted, from the
    same rules, so a deep link cannot be used to ask whether a tag exists.

    Built from the scoped lister the wall reads, not from the table: a tag's count and its vault
    flag are both about the library, so an unscoped row here would publish what the wall does not.
    """
    found = await access.visible_tag(viewer, tag_id)
    if found is None:
        raise _missing()
    marks = await access.visible_marks(viewer, ObjectType.TAG, [found.id])
    view = _tag_from_suggestion(found, marks.get(found.id), art=face_version(viewer.cache_stamp))
    # The record after the visibility answer, never as part of it. Whether a tag may be SHOWN is
    # the scoped lister's question and it has one answer; what the tag holds is this slice's own
    # rows, and asking for them separately keeps them off the wall that shares that statement.
    #
    # The O tally rides with it, and only here: it is a sum over the tag's files, so a wall of sixty
    # tags would be sixty of these sums for a number no card draws.
    #
    # And the card's counts, read for this one tag by the call the wall makes for its page, so the
    # card and the page cannot say two different numbers for one tag.
    cells = await access.card_counts(viewer, "tag", [found.id])
    return view.model_copy(
        update={
            "record": await service.record_of(tag_id, viewer),
            "o_count": await access.o_count_of_tag(viewer, found.id),
            "counts": cells.get(found.id, {}),
        }
    )


@router.get("/tags/{tag_id}/history")
async def history_of_a_tag(
    tag_id: str,
    access: Annotated[Repository, Depends(wiring.access)],
    database: Annotated[Database, Depends(wiring.database)],
    workbench: Annotated[Workbench, Depends(wiring.workbench)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> list[HistoryEvent]:
    """What happened to this tag, oldest first.

    Authenticated rather than admin, the same rule the tag's own row follows: what has been done
    with a tag is part of what the tag IS, and everybody who may be shown it may read it. The one
    half that is not is the sharing, which is about other USERS rather than about the tag: the
    kernel leaves it out for anybody but an admin, for the reason written there.

    Resolved through the scoped lookup first, so a tag this viewer may not be shown answers the same
    404 an id that was never minted would. An empty list would say it exists and nothing has happened
    to it, which is a different answer and one this viewer is not entitled to.
    """
    if await access.visible_tag(viewer, tag_id) is None:
        raise _missing()
    # The registry is asked which kinds of decision can never be taken back, and the answer is
    # handed to the read so it offers no Undo on those: the same question the file's history and
    # a person's ask, from the same place, because an affordance the server would refuse is worse
    # than none. Asked of the REVERSERS rather than of the queues: a decision written by a queue
    # that has since been retired is still one somebody can take back.
    final = [one.name for one in workbench.reversers if not one.reversible]
    return [
        history_event(event)
        for event in await history_of_tag(
            database, viewer, tag_id, limit=limit, final_queues=final, bench=workbench
        )
    ]


@router.post("/tags", status_code=status.HTTP_201_CREATED, dependencies=[Depends(csrf_protect)])
async def create_tag(
    body: TagWrite,
    service: Annotated[TagService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> TagView:
    """Make a tag, at the top of the tree. Its parent is set on its record ("Part of")."""
    try:
        tag = await service.create(body.name, made=made_by_user(viewer.id))
    except DuplicateTag:
        raise _taken(body.name) from None
    return _view(tag)


def _text(record: Mapping[str, object], key: str) -> str | None:
    """One value out of a record, as text. The record is a mapping of `object` by design."""
    held = record.get(key)
    return held if isinstance(held, str) else None


def _words(record: Mapping[str, object], key: str) -> list[str]:
    """One LIST out of a record, as text. Anything else is nothing, never a crash."""
    held = record.get(key)
    return [str(one) for one in held] if isinstance(held, list) else []


@router.put("/tags/{tag_id}", dependencies=[Depends(csrf_protect)])
async def update_tag(
    tag_id: str,
    body: TagWrite,
    service: Annotated[TagService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    reindexer: Annotated[ReindexSeam, Depends(wiring.reindexer)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> TagView:
    """Rename, and write the record fields the caller sent."""
    # A parent that would make a loop is refused before anything is written, so a refused save
    # leaves the tag exactly as it was rather than renamed and unfiled.
    if "parent" in body.model_fields_set and body.parent:
        try:
            await service.refuse_a_loop(tag_id, body.parent)
        except TagLoop:
            raise _loop() from None
    try:
        tag = await service.update(viewer, tag_id, body.name)
    except DuplicateTag:
        raise _taken(body.name) from None
    if tag is None:
        raise _missing()
    # The record half, and only what the caller actually sent. A screen that renames a tag knows
    # nothing about its description, and reading its silence as "blank it" would make an edit
    # destroy what somebody wrote.
    sent = body.model_fields_set
    if {"description", "category", "aliases", "parent"} & sent:
        # Read-modify-write, because the statement behind this replaces all three columns at once
        # and a caller may have sent only one of them. What was not sent is put back exactly as it
        # was: a full-row writer blanks whatever it is not told about, and that is a real way to
        # lose a paragraph somebody typed.
        held = await service.record_of(tag_id)
        try:
            await service.set_record(
                tag_id,
                description=body.description
                if "description" in sent
                else _text(held, "description"),
                category=body.category if "category" in sent else _text(held, "category"),
                aliases=(body.aliases or []) if "aliases" in sent else _words(held, "aliases"),
                parent=body.parent if "parent" in sent else _text(held, "parent"),
                actor=Actor.user(viewer.id),
            )
        except TagLoop:
            raise _loop() from None
    # Every file carrying this tag now has different indexed text: other names are indexed the
    # same way the name is, so both halves of the write above are covered by one pass. The files CAN
    # be named here, so they are, rather than a rebuild of the whole index: many seconds of the
    # write lock on a large library, for a tag that may be on none of them. Read after the write,
    # because a rename moves no assignment.
    await reindexer.touched_many(await service.assets_with(tag_id))
    marks = await access.visible_marks(viewer, ObjectType.TAG, [tag.id])
    return _view(tag, mark=marks.get(tag.id)).model_copy(
        update={"record": await service.record_of(tag_id)}
    )


@router.put("/tags/{tag_id}/favorite", dependencies=[Depends(csrf_protect)])
async def set_tag_favorite(
    tag_id: str,
    body: FavoriteWrite,
    service: Annotated[TagService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> TagStateView:
    """Heart a tag, or take the heart off, for this user.

    Offered to everybody, guests included, and that is the point rather than an oversight: an
    opinion is about the user holding it and reaches nobody else's screen. It changes where a row
    appears on a wall and never whether it appears, so there is nothing here for a permission to
    protect. Restricting is what keeps something from another user.

    404 for a tag this viewer may not be shown, which is the answer an unknown id gets, so trying
    ids teaches nothing about what exists.
    """
    state = await service.set_favorite(viewer, tag_id, favorite=body.favorite)
    if state is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such tag")
    return TagStateView(favorite=state.favorite, rating=state.rating)


@router.put("/tags/{tag_id}/pin", dependencies=[Depends(csrf_protect)])
async def set_tag_pinned(
    tag_id: str,
    body: PinWrite,
    service: Annotated[TagService, Depends(_service)],
    pins: Annotated[EntityStateStore, Depends(wiring.entity_state)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> PinView:
    """Keep a tag at the top of the Tags wall, for this user.

    Everybody's, guests included, exactly as the heart above is: a pin changes where a row appears
    on this viewer's wall and never whether it appears at all.

    Looked up first rather than relying on the write to fail, and that is the same reasoning the
    heart gives: the write CANNOT fail. The state row is this user's own and would be created
    happily against an id that names nothing, and a row hanging off an id that was never minted
    is one nothing will ever clean up, because the cascade has nothing to cascade from.
    """
    if not await service.shown(viewer, tag_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such tag")
    return PinView(
        pinned=await pins.set_pinned(PinnableKind.TAG, tag_id, viewer.id, pinned=body.pinned)
    )


@router.put("/tags/{tag_id}/rating", dependencies=[Depends(csrf_protect)])
async def set_tag_rating(
    tag_id: str,
    body: RatingWrite,
    service: Annotated[TagService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> TagStateView:
    """Set the stars on a tag, or clear them with null. For this user; see the heart above."""
    state = await service.set_rating(viewer, tag_id, rating=body.rating)
    if state is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such tag")
    return TagStateView(favorite=state.favorite, rating=state.rating)


@router.get("/tags/{tag_id}/cover")
async def tag_cover(
    tag_id: str,
    request: Request,
    service: Annotated[TagService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    pictures: Annotated[CoverPictures, Depends(wiring.cover_pictures)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> Response:
    """The still this tag is drawn as, at whichever moment was chosen.

    GET beside the PUT of the same name, which is what makes the pair readable: one address is the
    cover of this thing, written one way and read the other. The body is `serve_cover` in the
    kernel: six things carry a cover and every one answers this identically; what differs is which
    entity has to be resolved against the viewer first, which is the part that cannot be shared.
    """
    if await access.visible_tag(viewer, tag_id) is None:
        raise _missing()
    chosen = await service.chosen_cover(tag_id)
    return await serve_cover(request, access, viewer, chosen=chosen, pictures=pictures)


@router.put("/tags/{tag_id}/cover", dependencies=[Depends(csrf_protect)])
async def set_tag_cover(
    tag_id: str,
    body: CoverWrite,
    service: Annotated[TagService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    stills: Annotated[StillSeam, Depends(wiring.stills)],
    pictures: Annotated[CoverPictures, Depends(wiring.cover_pictures)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> TagView:
    """Choose the still a tag is drawn as, and which moment of it.

    A tag carries a cover as People, Sites, Usernames, collections and photo sets do, and a wall of
    tags is where a picture helps most, because a tag's name says less about what is under it than
    a person's does.

    Two locks, and both matter. The tag is resolved through the scoped read first, so a tag this
    user may not be shown answers 404 rather than being written to. Then the ASSET is checked
    against the same viewer: setting a cover to a file you may not see would publish that file to
    everybody who can see the tag. The read side refuses to hand back a cover the asker may not
    open regardless, so this is the second of two rather than the only one.
    """
    if await access.visible_tag(viewer, tag_id) is None:
        raise _missing()
    if body.asset_id is not None and not await access.can_view(viewer, body.asset_id):
        raise _missing()
    # The write's own "no such tag" is not reported separately. It can only be false in the case
    # the re-read below already answers (the tag going away between two statements) and a
    # second branch for one condition is a branch nothing can reach to prove. The sibling routes on
    # photo sets and collections are written the same way. Kept as a comment rather than in the
    # docstring above, which is served as this route's public description.
    before = await service.chosen_cover(tag_id)
    kept = upload_kept_by_put(
        body.upload_id, asset_id=body.asset_id, frame=body.frame, before=before
    )
    await service.set_cover(
        tag_id,
        body.asset_id,
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
    return await _tag_now(access, viewer, tag_id)


@router.post("/tags/{tag_id}/cover-picture", dependencies=[Depends(csrf_protect)])
async def upload_tag_cover(
    tag_id: str,
    file: Annotated[UploadFile, File()],
    service: Annotated[TagService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    pictures: Annotated[CoverPictures, Depends(wiring.cover_pictures)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> TagView:
    """A picture from outside the library, as this tag's cover. See `receive_cover`."""
    if await access.visible_tag(viewer, tag_id) is None:
        raise _missing()
    before = await service.chosen_cover(tag_id)
    await receive_cover(
        pictures,
        file.read,
        before=before,
        point_at=lambda upload_id: service.set_cover(
            tag_id, None, None, upload_id, actor=Actor.user(viewer.id)
        ),
    )
    return await _tag_now(access, viewer, tag_id)


async def _tag_now(access: Repository, viewer: Viewer, tag_id: str) -> TagView:
    """The tag's own row as a cover write hands it back. Both cover routes send this."""
    found = await access.visible_tag(viewer, tag_id)
    if found is None:  # pragma: no cover (resolved a statement ago; a concurrent delete only)
        raise _missing()
    marks = await access.visible_marks(viewer, ObjectType.TAG, [found.id])
    return _tag_from_suggestion(found, marks.get(found.id), art=face_version(viewer.cache_stamp))


@router.put(
    "/tags/{tag_id}/vault",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_protect)],
)
async def set_tag_vault(
    tag_id: str,
    body: VaultWrite,
    request: Request,
    service: Annotated[TagService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> Response:
    """Hide a tag, or bring it back, for this user.

    Hiding a tag conceals every file carrying it and takes the tag off its own wall, on the screens
    of the user who did it. Nobody else is affected; to keep something from another user,
    restrict it. There is no body to answer with: the row this describes is, by the time the answer
    is written, one the caller may no longer be shown.

    Hiding needs a PIN to exist, because the PIN is the only thing that opens Hidden again:
    without one this is not concealment, it is losing the tag and everything filed under it.
    Bringing one back needs Hidden actually open, and that half is the more important one: a tag
    this user hid is absent from their scoped list, so before the PIN is entered they get the 404
    an unknown id gets. Answering at all would confirm the tag is there.
    """
    if body.vault:
        await require_vault_pin(request, viewer)
    tag = await service.get(viewer, tag_id)
    if tag is None or (tag.vault and not viewer.show_hidden):
        # One id, one answer. A concealed tag is not a tag this caller may write to, in either
        # direction, and it gets the 404 an unknown id gets: answering differently is the reveal.
        raise _missing()
    await service.set_vault(viewer, tag_id, vault=body.vault)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/tags/{tag_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_protect)],
)
async def delete_tag(
    tag_id: str,
    service: Annotated[TagService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    reindexer: Annotated[ReindexSeam, Depends(wiring.reindexer)],
    forgets: Annotated[ForgetGoneSeam, Depends(wiring.forget_gone)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> Response:
    """Delete a tag, its assignments, and every grant that named it.

    The assignments go by cascade and the assets do not: `asset_tags` names the asset with
    `ON DELETE CASCADE` pointing the other way, so removing a tag removes the rows joining it to
    files and never the files.

    Resolved through the scoped read first, the rule a Site's delete and a Photo Set's keep: a tag
    this user has hidden behind a locked Hidden section answers the 404 an unknown id does, and
    with Hidden open it deletes like any other. Otherwise a write would reach a row the same user's
    reads say is not there.
    """
    tag = await access.visible_tag(viewer, tag_id)
    if tag is None:
        raise _missing()
    # BEFORE the delete: the cascade takes the assignments with the tag, so asking afterwards which
    # files carried it answers nothing and the index would keep the word for ever.
    carried_by = await service.assets_with(tag_id)
    if not await service.delete(tag_id, actor=Actor.user(viewer.id)):
        raise _missing()
    # The cascade took the assignments, so every file that carried this tag now indexes without it:
    # those files, and not the library.
    await reindexer.touched_many(carried_by)
    await forgets.forget_gone("tag", tag_id, name=tag.name, by=viewer)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/assets/{asset_id}/tags")
async def tags_of_asset(
    asset_id: str,
    access: Annotated[Repository, Depends(wiring.access)],
    service: Annotated[TagService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> list[TagView]:
    """The tags on one asset, for the chips on its tile and in its detail view."""
    resolved = await require_reachable(access, viewer, asset_id, _missing)
    return [_view(tag) for tag in await service.tags_of(viewer, resolved)]


@router.post("/assets/tags", dependencies=[Depends(csrf_protect)])
async def assign_tags(
    body: TagAssignment,
    access: Annotated[Repository, Depends(wiring.access)],
    service: Annotated[TagService, Depends(_service)],
    reindexer: Annotated[ReindexSeam, Depends(wiring.reindexer)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> BulkWriteDone:
    """Attach or detach tags, for one asset or for a selection of them.

    **No file is moved.** This writes rows in the join table and nothing else: every path, every
    byte and every location row is exactly as it was. That is the promise the storage model is
    making, and the test that asserts it is the one worth keeping.

    An asset that cannot be resolved is SKIPPED and counted, and the reply says how many and why.
    A silent partial success would be worse than a refusal, but a reported one is not: failing the
    whole call would tag none of three files because one sits in the viewer's own locked vault.

    The TAGS are still all-or-nothing, and that is a different question: a tag id that resolves to
    nothing is a broken caller rather than a file somebody hid, and there is no useful half of
    "put these two tags on" when one of them does not exist.
    """
    actionable = await access.actionable_of(viewer, body.asset_ids)

    for tag_id in body.tag_ids:
        if await service.get(viewer, tag_id) is None:
            raise _missing()

    changed = (
        await service.assign(
            list(actionable.allowed),
            body.tag_ids,
            add=body.add,
            actor=Actor.user(viewer.id),
        )
        if actionable.allowed
        else 0
    )
    # Known by id, so their rows are rewritten now rather than queued: the next search is
    # right, instead of right after the next rebuild. As one call rather than one per asset: this
    # list runs to five hundred, and each single-asset refresh is its own write transaction.
    #
    # Only what actually changed. Refreshing an asset the write skipped is a write transaction
    # bought for nothing, and on a selection where the vault holds most of them it is most of them.
    if actionable.allowed:
        await reindexer.touched_many(actionable.allowed)
    return BulkWriteDone.after(actionable, changed)


@router.put("/assets/{asset_id}/rating", dependencies=[Depends(csrf_protect)])
async def set_rating(
    asset_id: str,
    body: RatingWrite,
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    user_state: Annotated[UserStateStore, Depends(wiring.user_state)],
) -> AssetOpinion:
    """One to five whole stars, or null to clear it.

    Per user: this writes the row belonging to the person asking and can reach no other. It
    leaves the heart exactly as it was: the two are independent, and a rating is not a quiet way
    to un-favorite something.
    """
    resolved = await require_reachable(access, viewer, asset_id, _missing)
    state = await user_state.set_rating(resolved, viewer.id, body.rating)
    return AssetOpinion(
        asset_id=resolved,
        favorite=state.favorite,
        rating=state.rating,
        views=state.view_count,
        pinned=state.pinned,
        o_count=state.o_count,
    )


@router.put("/assets/{asset_id}/o-count", dependencies=[Depends(csrf_protect)])
async def set_o_count(
    asset_id: str,
    body: OCountWrite,
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    user_state: Annotated[UserStateStore, Depends(wiring.user_state)],
) -> AssetOpinion:
    """The O counter: one more, one fewer, or back to nothing.

    Beside the stars rather than in a slice of its own, because it is the same kind of thing they
    are: one row per user, one number, written by a control and read by every screen already
    drawing the file. A feature of its own would be a second copy of the resolve-then-write shape
    and a second place for the reply to fall out of step with `AssetOpinion`.

    Per user and never anybody else's: two users on one install hold their own hearts, their own
    stars and their own history. A tally is an opinion, so it goes where the rest of them are.

    ONE route for the three acts rather than three routes, and the act is the body. Three addresses
    would be three rows in the authz matrix saying the same policy about the same row, and the
    thing that differs between them is one word.

    `require_reachable` first, and it does more here than refuse a stranger's file: it RESOLVES the
    id, so a state row can never be left hanging off an id that names nothing: the same reasoning
    the pin below gives, and the cascade that would otherwise have nothing to cascade from.

    It answers the whole opinion, as the stars and the pin do, because this file's opinions are one
    row and one shape. See `changes.AssetOpinion`. That is what makes the reply to the tab that
    pressed the control and the message to the tab that did not the same piece of code.
    """
    resolved = await require_reachable(access, viewer, asset_id, _missing)
    if body.change == O_UP:
        state = await user_state.bump_o_count(resolved, viewer.id)
    elif body.change == O_DOWN:
        state = await user_state.lower_o_count(resolved, viewer.id)
    else:
        state = await user_state.reset_o_count(resolved, viewer.id)
    return AssetOpinion(
        asset_id=resolved,
        favorite=state.favorite,
        rating=state.rating,
        views=state.view_count,
        pinned=state.pinned,
        o_count=state.o_count,
    )


@router.post("/assets/rating", dependencies=[Depends(csrf_protect)])
async def set_rating_many(
    body: RatingMany,
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    user_state: Annotated[UserStateStore, Depends(wiring.user_state)],
) -> BulkWriteDone:
    """The same stars, over a selection, in ONE request.

    **One request for the whole selection, which is the whole reason this route exists.** One
    request per file would be a round trip per file, each awaited before the next began, each its
    own write transaction and each telling this user's other screens about one row, and a failure
    part-way would leave a half-rated selection. The work is never the cost.

    A file the caller cannot act on is SKIPPED and counted, exactly as bulk tagging skips one, and
    the reply says how many and why. That is the one behaviour that differs from the single route,
    where there is no partial answer to give and a refusal is the only honest one.

    It answers counts and not opinions. The single route hands back the whole `AssetOpinion`
    because a control is settling onto one file's truth; a selection has no one row to settle, and
    every screen holding any of these is told what they now say over the live channel regardless.
    See `UserStateStore._write_many`.
    """
    actionable = await access.actionable_of(viewer, body.asset_ids)
    changed = (
        await user_state.set_rating_many(actionable.allowed, viewer.id, body.rating)
        if actionable.allowed
        else 0
    )
    return BulkWriteDone.after(actionable, changed)


@router.put("/assets/{asset_id}/pin", dependencies=[Depends(csrf_protect)])
async def set_asset_pinned(
    asset_id: str,
    body: PinWrite,
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    user_state: Annotated[UserStateStore, Depends(wiring.user_state)],
) -> AssetOpinion:
    """Keep this file at the top of whatever wall it is on, for this user.

    The sixth opinion, and the file half of the pin the five named kinds already carry. Everybody's,
    guests included, exactly as the heart below is: a pin changes where a file appears on this
    viewer's wall and never whether it appears at all.

    `_require_visible` first, and it is doing more here than refusing a stranger's file: it also
    resolves the id. A state row hanging off an id that names nothing is one nothing will ever clean
    up, because the cascade has nothing to cascade from: the same reasoning the entity pins give.

    It answers the whole opinion rather than just the pin, unlike the five entity routes, because
    this file's opinions are ONE row and one shape. See `changes.AssetOpinion`. The reply and the
    message this write publishes to the user's other tabs are then the same thing, which is what
    stops the tab that pressed the control and the tab that did not being served by two different
    pieces of code.
    """
    resolved = await require_reachable(access, viewer, asset_id, _missing)
    state = await user_state.set_pinned(resolved, viewer.id, body.pinned)
    return AssetOpinion(
        asset_id=resolved,
        favorite=state.favorite,
        rating=state.rating,
        views=state.view_count,
        pinned=state.pinned,
        o_count=state.o_count,
    )


@router.post("/assets/pin", dependencies=[Depends(csrf_protect)])
async def set_asset_pinned_many(
    body: PinMany,
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    user_state: Annotated[UserStateStore, Depends(wiring.user_state)],
) -> BulkWriteDone:
    """Pin a selection, or take the pins off it, in ONE request.

    One target state for the whole set rather than a toggle each (see `PinMany`), and one
    statement behind it. Everything the list form of the stars above says about skipping, about
    counts instead of opinions and about why the single route stays applies here unchanged.
    """
    actionable = await access.actionable_of(viewer, body.asset_ids)
    changed = (
        await user_state.set_pinned_many(actionable.allowed, viewer.id, body.pinned)
        if actionable.allowed
        else 0
    )
    return BulkWriteDone.after(actionable, changed)


@router.put("/assets/{asset_id}/favorite", dependencies=[Depends(csrf_protect)])
async def set_favorite(
    asset_id: str,
    body: FavoriteWrite,
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    user_state: Annotated[UserStateStore, Depends(wiring.user_state)],
) -> AssetOpinion:
    """The heart, per user, and independent of the stars.

    Favouriting a three-star clip is allowed and is not a contradiction: one is a shortlist and
    the other is a judgement about quality. Clearing it leaves the rating alone.
    """
    resolved = await require_reachable(access, viewer, asset_id, _missing)
    state = await user_state.set_favorite(resolved, viewer.id, body.favorite)
    return AssetOpinion(
        asset_id=resolved,
        favorite=state.favorite,
        rating=state.rating,
        views=state.view_count,
        pinned=state.pinned,
        o_count=state.o_count,
    )


@router.post("/assets/favorite", dependencies=[Depends(csrf_protect)])
async def set_favorite_many(
    body: FavoriteMany,
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    user_state: Annotated[UserStateStore, Depends(wiring.user_state)],
) -> BulkWriteDone:
    """The heart, over a selection, in ONE request.

    One target state for the whole set (see `FavoriteMany`), and one statement behind it. The
    list form of the stars above sets out why the three of these exist, why they answer counts
    rather than opinions, and why a file the caller cannot act on is skipped instead of failing the
    call; none of it is different here.

    It leaves the stars exactly as they were, as the single write does. Two upserts touching one
    column each is what gives that, and it is asserted rather than reasoned about.
    """
    actionable = await access.actionable_of(viewer, body.asset_ids)
    changed = (
        await user_state.set_favorite_many(actionable.allowed, viewer.id, body.favorite)
        if actionable.allowed
        else 0
    )
    return BulkWriteDone.after(actionable, changed)
