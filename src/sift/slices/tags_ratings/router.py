# SPDX-License-Identifier: AGPL-3.0-or-later
"""Endpoints for tags, the heart and the stars.

Tags are shared, so their writes are admin-only; hearts and stars are one row per person. Every
refusal is the 404 an unknown id gets, and tagging moves nothing on disk.
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
    """A scoped tag row as the screen reads one, so the wall and the by-id read agree."""
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
    """What the tags on the wall are: their category, and whether a stash-box is attached."""

    def __init__(
        self,
        category: Annotated[list[str] | None, Query()] = None,
        linked: Annotated[list[str] | None, Query()] = None,
        # Which boxes wrote to the tag, beside the yes-or-no `linked`.
        enriched: Annotated[list[str] | None, Query()] = None,
        # WHICH BOX MADE the tag, by the box's own word: a different question from `linked`.
        created: Annotated[list[str] | None, Query()] = None,
        # Where a linked stash-box disagrees with this tag; not a column of the table.
        disagrees: Annotated[list[str] | None, Query()] = None,
        # The tags filed directly under this one, by its id: a tag's own Tags tab.
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
        """The picks as the one conjunct the statement takes; see `PeopleNarrowing.of`."""
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
    """The tags this viewer may know about, most-used first, or those on one thing's files.

    `count` is which tally a card prints: `whole`, or `narrowed` to this wall. `from` starts the
    page at a row, resolved in this same list; one that resolves to nothing serves `near`, or the top.
    """
    # Refused rather than ignored: an order silently swapped makes a wall look wrong for no reason.
    if sort not in ENTITY_SORT_KEYS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"unknown sort {sort!r}")
    narrowing = related_filter(
        person=person, site=site, collection=collection, photo_set=photo_set, song=song
    )
    # Read once for both: the position and the page are taken in the same list.
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

    Declared before `/tags/{tag_id}`, which would otherwise read this address as a tag's id.
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
    """One tag, as its own page reads it; 404 alike for a hidden tag and an unknown id."""
    found = await access.visible_tag(viewer, tag_id)
    if found is None:
        raise _missing()
    marks = await access.visible_marks(viewer, ObjectType.TAG, [found.id])
    view = _tag_from_suggestion(found, marks.get(found.id), art=face_version(viewer.cache_stamp))
    # The record after the visibility answer, never part of it; the O tally only here, since it is
    # a sum over the tag's files; and the card's counts by the wall's own call.
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
    """What happened to this tag, oldest first; a tag this viewer may not be shown is a 404."""
    if await access.visible_tag(viewer, tag_id) is None:
        raise _missing()
    # No Undo is offered on what the reversers say can never be taken back.
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
    # A loop is refused before anything is written.
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
    # Only what the caller sent: silence is not "blank it".
    sent = body.model_fields_set
    if {"description", "category", "aliases", "parent"} & sent:
        # The statement replaces all three columns, so what was not sent is put back.
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
    # Every file carrying this tag now indexes different text: a job rewrites exactly those.
    await reindexer.queue_many(await service.assets_with(tag_id))
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
    """Heart a tag, or take the heart off, for this user; guests too: it reaches nobody else."""
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
    """Keep a tag at the top of the Tags wall, for this user; looked up first."""
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
    """The still this tag is drawn as, at whichever moment was chosen."""
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
    """Choose the still a tag is drawn as; both the tag and the file are checked for the viewer."""
    if await access.visible_tag(viewer, tag_id) is None:
        raise _missing()
    if body.asset_id is not None and not await access.can_view(viewer, body.asset_id):
        raise _missing()
    # The write's own "no such tag" is not reported apart: the re-read below answers that case.
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
    # Queued, not rendered here; the client shows the file's own still until it lands.
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
    """Hide a tag, or bring it back, for this user; hiding needs a PIN, restoring an open Hidden."""
    if body.vault:
        await require_vault_pin(request, viewer)
    tag = await service.get(viewer, tag_id)
    if tag is None or (tag.vault and not viewer.show_hidden):
        # One id, one answer: a concealed tag gets the 404 an unknown id gets.
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
    """Delete a tag, its assignments, and every grant that named it; never a file."""
    tag = await access.visible_tag(viewer, tag_id)
    if tag is None:
        raise _missing()
    # Before the delete, which takes the assignments with it.
    carried_by = await service.assets_with(tag_id)
    if not await service.delete(tag_id, actor=Actor.user(viewer.id)):
        raise _missing()
    # Every file that carried this tag now indexes without it.
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
    """Attach or detach tags on files, moving none; an unresolved file is skipped and counted."""
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
    # Rewritten now, in one call, and only the files that changed.
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
    """One to five whole stars, or null to clear it, for this user; the heart is left as it was."""
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
    """The O counter, for this user: one more, one fewer, or back to nothing."""
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
    """The same stars, over a selection, in one request; files not acted on are counted."""
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
    """Keep this file at the top of whatever wall it is on, for this user."""
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
    """Pin a selection, or take the pins off it, in one request."""
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
    """The heart, per user, and independent of the stars."""
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
    """The heart, over a selection, in one request; the stars are left as they were."""
    actionable = await access.actionable_of(viewer, body.asset_ids)
    changed = (
        await user_state.set_favorite_many(actionable.allowed, viewer.id, body.favorite)
        if actionable.allowed
        else 0
    )
    return BulkWriteDone.after(actionable, changed)
