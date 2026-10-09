# SPDX-License-Identifier: AGPL-3.0-or-later
"""Endpoints for loops: making one is not admin-only, every read is scoped by the join."""

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
    """Which of the files behind these rows are shared or restricted, by asset id, in one call."""
    if not loops:
        return {}
    return await access.visible_marks(
        viewer, ObjectType.ITEM, list(dict.fromkeys(loop.asset_id for loop in loops))
    )


async def _one(access: Repository, viewer: Viewer, loop: LoopView) -> LoopSummary:
    """One loop, answered with the same fields the wall sends."""
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
        # The mark's length, which the tile's badge describes, not the video's.
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
    """The same read, refused as missing unless the loop is this user's or they are an admin."""
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
    """Which files this wall's marks may be cut from: the related filter and the viewer's filter."""
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
    """One page of the loops this viewer may see; `asset_id`, `person`, `tag`, `site`, `collection`
    narrow it, `from` starts the page at a mark, `called` searches mark and file names."""
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
        # The tag stays out of the filter: the statement answers it per mark (see `loops_query`).
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
    """Save the stretch somebody marked in the player; a concealed file is a 404."""
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
    """The mark's own picture, at the moment the mark begins; the moment comes from the row only."""
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
    """Forget a selection of marks in one request; marks not yours are skipped and counted.
    Must stay declared before any `POST /loops/{loop_id}`, or that route would take `forget`."""
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
        # Never the vault: a locked mark was never shown to this user.
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
    """Tag the moment; admin-only, as a tag is shared vocabulary."""
    await _require_loop(access, viewer, loop_id)
    await service.set_tag(loop_id, body.tag_id, on=body.add)
    return [TagOnLoop(id=row["id"], name=row["name"]) for row in await service.tags_on(loop_id)]
