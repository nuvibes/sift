# SPDX-License-Identifier: AGPL-3.0-or-later
"""Endpoints for loops.

**Making one is not admin-only, and that is deliberate.** Every other write to shared vocabulary is,
because a tag or a person changes what everybody's searches return. A loop does not: it points at
one file, it is visible only to users who can already see that file, and marking a moment is
much closer to a rating than to renaming a tag. Naming it after somebody else's file changes nothing
about that file.

**Every read is scoped by the join, not by a rule.** A loop of a file this viewer may not see is not
a row (see `_VISIBLE_LOOPS`), so there is nothing here that has to remember to check.

**Denied and missing are the same answer**, as everywhere else.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from sift.kernel import wiring
from sift.kernel.access import (
    ENTITY_SORT_KEYS,
    ENTITY_SORT_SEEN,
    NO_FILTER,
    AssetFilter,
    GrantMark,
    LoopView,
    ObjectType,
    Repository,
    Viewer,
    related_filter,
)
from sift.kernel.content import DerivativeKind
from sift.kernel.paging import resume_at
from sift.kernel.reach import OUT_OF_REACH, OUT_OF_REACH_MANY, BulkWriteDone
from sift.kernel.seams import FilterEngine
from sift.kernel.serving import keeps, serve_file
from sift.kernel.wiring import part_of
from sift.slices.auth import csrf_protect, current_viewer, require_admin
from sift.slices.loops.models import (
    MAX_LOOP_NAME,
    ForgetLoops,
    LoopList,
    LoopRename,
    LoopSummary,
    LoopTagWrite,
    LoopWrite,
    TagOnLoop,
)
from sift.slices.loops.service import SERVICE, LoopService, Refused

router = APIRouter(tags=["loops"])


def _service(request: Request) -> LoopService:
    return part_of(request, SERVICE)


def _missing() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "not found")


async def _marks(access: Repository, viewer: Viewer, loops: list[LoopView]) -> dict[str, GrantMark]:
    """Which of the FILES behind these rows have been shared or restricted.

    One call for the page rather than one per row, which is the same arrangement the media grid
    makes, and through `visible_marks`, which is the only way to ask: the "an admin and nobody
    else" rule is written there once rather than on every screen that draws a badge.

    Keyed by ASSET id, because that is what a grant names. Two marks of one video therefore share
    one answer, exactly as they share its heart.
    """
    if not loops:
        return {}
    return await access.visible_marks(
        viewer, ObjectType.ITEM, list(dict.fromkeys(loop.asset_id for loop in loops))
    )


async def _one(access: Repository, viewer: Viewer, loop: LoopView) -> LoopSummary:
    """One loop, answered with the SAME fields the wall sends.

    Its own helper rather than `_view(loop)` at three call sites, because the default there is
    "no marks", and a route quietly answering a thinner version of a published shape is a fault
    nothing would notice. One read for one row; a guest costs nothing at all, since
    `visible_marks` tells them nothing by design.
    """
    return _view(loop, (await _marks(access, viewer, [loop])).get(loop.asset_id))


def _view(loop: LoopView, mark: GrantMark | None = None) -> LoopSummary:
    return LoopSummary(
        id=loop.id,
        asset_id=loop.asset_id,
        name=loop.name,
        start_ms=loop.start_ms,
        end_ms=loop.end_ms,
        created_at=loop.created_at,
        media_type=loop.media_type,
        width=loop.width,
        height=loop.height,
        # The MARK's length. See the model: this is the field a tile draws its badge from, and what
        # that badge is describing is the row, not the video the row points into.
        duration_ms=loop.length_ms,
        favorite=loop.favorite,
        rating=loop.rating,
        pinned=loop.pinned,
        views=loop.views,
        o_count=loop.o_count,
        hidden=loop.hidden,
        hidden_here=loop.hidden_here,
        unreachable=loop.unreachable,
        shared=mark.shared if mark else False,
        restricted=mark.restricted if mark else False,
        shared_here=mark.shared_here if mark else False,
        restricted_here=mark.restricted_here if mark else False,
        concealed=loop.concealed,
        thumb=loop.has_thumb,
        preview=loop.has_preview,
        still=loop.has_still,
        art=loop.art_version,
        original_filename=loop.original_filename,
        whole=loop.whole,
        tags=[TagOnLoop(id=tag.id, name=tag.name) for tag in loop.own_tags],
    )


async def _require_loop(access: Repository, viewer: Viewer, loop_id: str) -> LoopView:
    loop = await access.visible_loop(viewer, loop_id)
    if loop is None:
        raise _missing()
    return loop


async def _require_mine(access: Repository, viewer: Viewer, loop_id: str) -> LoopView:
    """The same read, and then: is this yours to change?

    Making a mark is not admin-only, and moving or removing somebody else's must not follow from
    that. A loop is visible to every user who can see the video it points at, so without this a
    guest could rename or delete a mark an admin made: reachable from the API whether or not any
    screen offers it.

    Refused as MISSING rather than as forbidden, like everything else here: "not yours" and "no such
    loop" are the same answer from outside, or the refusal itself would say the mark exists.
    """
    loop = await _require_loop(access, viewer, loop_id)
    if not viewer.is_admin and loop.created_by != viewer.id:
        raise _missing()
    return loop


async def _narrowing(
    request: Request,
    engine: FilterEngine,
    viewer: Viewer,
    *,
    person: str | None,
    site: str | None,
    collection: str | None,
) -> AssetFilter:
    """Which FILES this wall's marks may be cut from: the related filter and the viewer's filter.

    **The viewer's filter is the query language, read off the raw address by the one engine**:
    the same reading `/assets` makes, so a saved filter, a chip and a ticked column narrow this wall
    by its files' facets exactly as they filter the library. A mark is a piece of a file, so "the
    loops whose file is tagged beach and rated four or more" is the whole of what a file filter
    can mean here; the marks themselves have no facets.

    It cannot mistake one of this route's own parameters for a filter: `person`, `tag`, `site`,
    `collection`, `asset_id`, `sort`, `from`, `limit` and `offset` are none of them a word the
    language answers to (the query-parameter gate holds that from the parser's side).

    The TAG is deliberately absent. It is handed to the listing on its own and answered per mark
    by the statement; in the filter it would lose the marks tagged in their own right. See
    `loops_query` for why it is not a widening of the filter.

    Grants nothing, like every filter: it is one conjunct over the set this viewer may already
    see, so a filter naming files nobody shared filters to none of their marks.
    """
    asked = await engine.constrain(viewer, request.query_params)
    related = related_filter(person=person, site=site, collection=collection)
    return asked if related is NO_FILTER else asked.also(related.where)


@router.get("/loops")
async def list_loops(
    request: Request,
    access: Annotated[Repository, Depends(wiring.access)],
    engine: Annotated[FilterEngine, Depends(wiring.filter_engine)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort: Annotated[str, Query()] = ENTITY_SORT_SEEN,
    start: Annotated[str | None, Query(alias="from")] = None,
    near: Annotated[int | None, Query(ge=0)] = None,
    asset_id: Annotated[str | None, Query()] = None,
    person: Annotated[str | None, Query()] = None,
    tag: Annotated[str | None, Query()] = None,
    site: Annotated[str | None, Query()] = None,
    collection: Annotated[str | None, Query()] = None,
    called: Annotated[str | None, Query(max_length=MAX_LOOP_NAME)] = None,
) -> LoopList:
    """One page of the loops this viewer may see.

    `asset_id` narrows to one file, which is what the player asks for when it draws the marks on a
    timeline. `person`, `tag`, `site` and `collection` narrow to the loops cut from the files
    that thing reaches, which is what makes this a related list as well as a wall.

    `collection` is there for a collection's Loops tab; the kernel's `_LEAF` understands the word.
    See `TABS_FOR` in the related slice for why the tab exists at all.

    `from` names a mark to start the page at, instead of an offset. This wall is drawn by the media
    grid, which pages by whole rows, so how many marks a page holds depends on the size of the
    screen and a page NUMBER is not a durable thing to put in an address. It is resolved against
    this same question, because a position only means anything in the list it was taken from.

    A `from` that resolves to nothing serves the page it was on (`near`), or the TOP,
    rather than refusing: a mark that has since
    been moved, deleted or concealed is a stale link and not an error. It is also why a caller
    cannot learn anything by trying ids: a mark being kept back and one that never existed give
    the same answer, and both are the first page.

    **And by the query language**, which is what puts the bar's filters on this wall: every file
    filter the library takes narrows the marks to those cut from matching files. See `_narrowing`.

    `called` is the wall's search box: the marks whose own name, or whose file's name, holds the
    words, in any case. A mark with no name of its own is drawn under its file's name, so the box
    has to find it by that. Not `name`, which the query language already reads as a FILE's title
    off this same address and which would narrow the named marks away with the rest.
    """
    if sort not in ENTITY_SORT_KEYS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"unknown sort {sort!r}")
    narrowing = await _narrowing(
        request, engine, viewer, person=person, site=site, collection=collection
    )
    typed = (called or "").strip() or None
    if start is not None:
        at = await access.position_of_loop(
            viewer,
            start,
            sort=sort,
            asset_id=asset_id,
            asset_filter=narrowing,
            tag=tag,
            called=typed,
        )
        offset = resume_at(at, near)
    page = await access.list_loops(
        viewer,
        limit=limit,
        offset=offset,
        sort=sort,
        asset_id=asset_id,
        asset_filter=narrowing,
        # The tag, on its own and never in the filter: the statement answers it per mark, both the
        # marks carrying it and the marks of videos carrying it. See `loops_query`.
        tag=tag,
        called=typed,
    )
    marks = await _marks(access, viewer, page.items)
    return LoopList(
        items=[_view(loop, marks.get(loop.asset_id)) for loop in page.items],
        total=page.total,
        limit=limit,
        offset=offset,
    )


@router.post("/loops", status_code=status.HTTP_201_CREATED, dependencies=[Depends(csrf_protect)])
async def create_loop(
    body: LoopWrite,
    service: Annotated[LoopService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> LoopSummary:
    """Save the stretch somebody marked in the player.

    The file is resolved through the access layer first (`open_asset`, so a concealed one is a 404
    rather than a placeholder), which is what stops a loop being written against a file this user
    may not be shown. Its duration comes from that same read, so the service can refuse an end past
    the end of the file without a second query.
    """
    asset = await access.open_asset(viewer, body.asset_id)
    if asset is None:
        raise _missing()
    try:
        loop = await service.create(
            asset_id=asset.id,
            start_ms=body.start_ms,
            end_ms=body.end_ms,
            name=body.name,
            created_by=viewer.id,
            duration_ms=asset.duration_ms,
        )
    except Refused as refused:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(refused)) from refused
    visible = await access.visible_loop(viewer, loop.id)
    if visible is None:
        raise _missing()  # pragma: no cover (the file resolved a moment ago)
    return await _one(access, viewer, visible)


@router.get("/loops/{loop_id}")
async def get_loop(
    loop_id: str,
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> LoopSummary:
    return await _one(access, viewer, await _require_loop(access, viewer, loop_id))


@router.get("/loops/{loop_id}/thumb")
async def loop_thumb(
    loop_id: str,
    request: Request,
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> Response:
    """The mark's own picture: a frame of its video, at the moment the mark begins.

    **The moment comes from the row, never from the caller.** A still is filed under `(asset_id,
    kind, params)`, so a moment named in a query string would let anybody ask for an unbounded
    number of distinct pictures of one file and fill the cache with them. Asked this way, the set of
    stills a library can be made to hold is bounded by the number of marks somebody actually saved.

    There is no permission rule here and there does not need to be one. `_require_loop` resolves the
    mark through the join to the visible set, so a mark of a video this user may not see is a 404
    before a picture is looked for, and the picture itself is then served by the same scoped read
    the video's own still goes through, which checks the video again.

    A 404 covers "not allowed", "no such mark" and "not built yet" alike, exactly as the asset
    still's does. The last of those is ordinary rather than exceptional: the client falls back to
    the video's own picture until the sweep has been round.
    """
    loop = await _require_loop(access, viewer, loop_id)
    try:
        served = await access.serve_derivative(
            viewer, loop.asset_id, DerivativeKind.THUMB, params={"at_ms": loop.start_ms}
        )
    except ValueError:
        raise _missing() from None
    if served is None:
        raise _missing()
    return await serve_file(
        request,
        served.path,
        media_type="image/jpeg",
        headers=keeps(request, version=served.version, concealed=served.concealed),
    )


@router.put("/loops/{loop_id}", dependencies=[Depends(csrf_protect)])
async def rename_loop(
    loop_id: str,
    body: LoopRename,
    service: Annotated[LoopService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> LoopSummary:
    """Rename the mark. Yours, or an admin's. See `_require_mine`."""
    await _require_mine(access, viewer, loop_id)
    await service.rename(loop_id, body.name)
    return await _one(access, viewer, await _require_loop(access, viewer, loop_id))


@router.post("/loops/forget", dependencies=[Depends(csrf_protect)])
async def forget_loops(
    body: ForgetLoops,
    service: Annotated[LoopService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> BulkWriteDone:
    """Forget a selection of marks, in ONE request. No file is touched and no byte moves.

    **This is the only way to forget a mark.** There is no per-row DELETE route: the wall forgets
    in bulk, and a route nothing calls is one the reachability gate refuses. The shared Delete verb
    is hidden on the Loops wall deliberately, because it removes the FILE; and a screen's extra menu
    row is drawn per tile. Between them a selection of thirty marks had nothing at all to press,
    which is what this route is for.

    Answered as `BulkWriteDone`, the shape every other bulk write in Sift answers, so the screen
    says what it says everywhere else: what went, what did not, and why. A mark that is not this
    user's to remove is SKIPPED and counted rather than refusing the whole call: one row
    somebody else made should not stop the twenty-nine they did.

    "Not yours" and "no such loop" are one answer here, as they are on the single route: telling
    them apart would say the mark exists.

    !! `forget` is a literal segment where `{loop_id}` would match, and FastAPI resolves in
    DECLARATION order, so this is only safe because no `POST /loops/{loop_id}` exists. If one is
    ever added it must be declared after this, or a mark whose id was `forget` is the least of it:
    every forget request would be read as a write to one loop.
    """
    wanted = list(dict.fromkeys(body.loop_ids))
    mine: list[str] = []
    for loop_id in wanted:
        loop = await access.visible_loop(viewer, loop_id)
        if loop is not None and (viewer.is_admin or loop.created_by == viewer.id):
            mine.append(loop_id)
    skipped = len(wanted) - len(mine)
    changed = await service.forget_many(mine)
    return BulkWriteDone(
        changed=changed,
        skipped=skipped,
        reason=OUT_OF_REACH if skipped else None,
        reason_many=OUT_OF_REACH_MANY if skipped else None,
        # Never the vault: a mark reaches a user only through the file it points at, so one in
        # a locked vault is not a row this user was shown in the first place.
        vault_locked=False,
    )


@router.get("/loops/{loop_id}/tags")
async def loop_tags(
    loop_id: str,
    service: Annotated[LoopService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> list[TagOnLoop]:
    await _require_loop(access, viewer, loop_id)
    return [TagOnLoop(id=row["id"], name=row["name"]) for row in await service.tags_on(loop_id)]


@router.post("/loops/{loop_id}/tags", dependencies=[Depends(csrf_protect)])
async def set_loop_tag(
    loop_id: str,
    body: LoopTagWrite,
    service: Annotated[LoopService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> list[TagOnLoop]:
    """Tag the moment.

    An admin's, like every other tag write in the application. Making a mark is not (it is closer
    to a rating), but a TAG is shared vocabulary: it changes what everybody's searches return, and
    it does that whoever put the mark there.
    """
    await _require_loop(access, viewer, loop_id)
    await service.set_tag(loop_id, body.tag_id, on=body.add)
    return [TagOnLoop(id=row["id"], name=row["name"]) for row in await service.tags_on(loop_id)]
