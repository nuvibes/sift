# SPDX-License-Identifier: AGPL-3.0-or-later
"""The grid's endpoints: a page of assets, one asset, its thumbnail, its preview, its bytes.

**Denied and missing are the same answer.**

**The permission is re-read on every request.**
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from sift.kernel import heif, places, wiring
from sift.kernel.access import (
    ADMIN_FACETS,
    FACETS,
    FACETS_RENAMED,
    SEEKABLE_SORTS,
    SHUFFLE_MODULUS,
    SIMILARITY,
    SORT_KEYS,
    AllOf,
    AssetFilter,
    AssetPage,
    GrantMark,
    ObjectType,
    Repository,
    Viewer,
    Where,
)
from sift.kernel.access.catalog import refused_for_swaps_among
from sift.kernel.access.history import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    history_of_asset,
)
from sift.kernel.access.history_boxes import unshown_said
from sift.kernel.access.repository.asset_orders import ordering_for
from sift.kernel.access.search_index import INDEXED_RECORD_FIELDS
from sift.kernel.content import (
    Asset,
    ContentStore,
    Derivative,
    DerivativeKind,
    Location,
    LocationStatus,
    Verdict,
    VerdictProduct,
)
from sift.kernel.content.identity import MUSIC_SHARED, music_provenance
from sift.kernel.content.user_state import resume_minimum_ms, resume_point
from sift.kernel.db import Database
from sift.kernel.jobs.failure_words import why_left_out
from sift.kernel.log import get_logger
from sift.kernel.mp4 import NEEDS_REPAIR_BYTES
from sift.kernel.paging import resume_at
from sift.kernel.partial_write import UNCHANGED
from sift.kernel.reach import refuse_one, require_reachable
from sift.kernel.sampling import sprite_frames
from sift.kernel.seams import DisagreementSeam, FilterEngine, Narrowed, ReindexSeam
from sift.kernel.serving import keeps, serve_file
from sift.kernel.wire import FacetCounts, FacetValue, UndoPoint, history_event
from sift.kernel.wiring import FILTER_ENGINE, SETTINGS_HUB, part_of
from sift.kernel.workbench import Workbench
from sift.slices.auth import csrf_protect, current_viewer, require_admin
from sift.slices.browse.models import (
    AssetDetail,
    AssetPageResponse,
    AssetSummary,
    EnrichedBy,
    FileHistoryPage,
    FilterProblem,
    Membership,
    MembershipAsk,
    Memberships,
    MusicFrom,
    NarrowedToUsername,
    RecordWrite,
    SaveLogResponse,
    SaveRecord,
    SpriteSheet,
    summary,
)
from sift.slices.browse.service import SERVICE, BrowseService

log = get_logger(__name__)

# The paths are written out rather than carried on a router prefix: `/assets` names the thing.
router = APIRouter(tags=["browse"])

#: The app setting that decides whether a guest may keep a copy. Read from the database on every
#: request that needs it, never cached, never copied into a session.
SAVE_TO_DEVICE_KEY = "guests.can_save_to_device"

#: Whether Sift keeps corrected copies of files that are hard to seek through.
REPAIR_PLAYBACK_KEY = "performance.repair_playback"

_NOT_FOUND = "not found"

#: Where a copy of a file with its location taken out waits while it is sent to somebody's device:
#: a folder of Sift's own cache, never beside the file in the library.
OUTGOING_DIR_NAME = places.OUTGOING_FOLDER


def _service(request: Request) -> BrowseService:
    return part_of(request, SERVICE)


def _filters(request: Request) -> FilterEngine:
    return part_of(request, FILTER_ENGINE)


async def _repairing(request: Request) -> bool:
    """Whether Sift is currently making repaired copies at all. Read per request, like the rest."""
    return bool(await part_of(request, SETTINGS_HUB).get_app(REPAIR_PLAYBACK_KEY))


async def _guests_may_save(request: Request) -> bool:
    """Read the capability, now, from the database."""
    value = await part_of(request, SETTINGS_HUB).get_app(SAVE_TO_DEVICE_KEY)
    return bool(value)


def _missing() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, _NOT_FOUND)


def _refuse_cross_site(request: Request) -> None:
    """Refuse a request another site sent the browser here to make."""
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "that request did not come from Sift")


async def _narrowed(
    request: Request,
    engine: FilterEngine,
    viewer: Viewer,
    *,
    sort: str | None,
    meaning: bool | None,
    after: str | None,
    pinned_first: bool,
    photo_set: str | None,
    username: str | None,
    need: int,
) -> tuple[Narrowed, AssetFilter, str]:
    """What the wall asks for and in what order: the narrowing, its filter, and the ordering.
    Refuses an unknown sort, and a continuation the order cannot seek."""
    # An unknown sort is refused rather than quietly ignored: a caller who asked for an order and
    # silently got another has a page that looks wrong for no visible reason.
    if sort is not None and sort not in SORT_KEYS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"unknown sort {sort!r}")
    # `seed` says WHICH shuffle, and it is a parameter of its own for the same reason `sort` is: it
    # arranges the set rather than describing a member of it.

    # How to SEARCH, which is not the same question as how to ARRANGE what is found.
    by_meaning = meaning if meaning is not None else sort == SIMILARITY
    # `need` is the last row of the page being asked for.
    narrowed = await engine.narrow(viewer, request.query_params, by_meaning=by_meaning, need=need)

    # The username's narrowing, folded into whatever the query language already said.
    asset_filter = narrowed.asset_filter

    # The order, once it is known whether there are words to be close TO.
    ordering = ordering_for(sort, asset_filter, by_meaning=by_meaning)

    # `after` continues the page from the row named, on the sort's own index: one seek rather than a
    # walk past every row an offset counts.
    if after is not None and (
        ordering not in SEEKABLE_SORTS or pinned_first or photo_set is not None
    ):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "this wall cannot be continued from a row"
        )

    if username:
        asset_filter = replace(
            asset_filter, where=AllOf((asset_filter.where, Where("usernames", (username,))))
        )
    return narrowed, asset_filter, ordering


async def _page_response(
    access: Repository,
    viewer: Viewer,
    username: str | None,
    items: list[AssetSummary],
    result: AssetPage,
    narrowed: Narrowed,
    limit: int,
    offset: int,
) -> AssetPageResponse:
    """The page as the wall reads it, with the chip that names a narrowing to one username."""
    # What the chip for `?username=` says.
    shown = None if not username else await access.visible_username(viewer, username)
    return AssetPageResponse(
        items=items,
        total=result.total,
        total_bytes=result.total_bytes,
        limit=limit,
        offset=offset,
        username=(
            None
            if shown is None
            else NarrowedToUsername(
                id=shown.id,
                username=shown.name,
                site=shown.site_name,
                person_id=shown.person_id,
            )
        ),
        complete=narrowed.complete,
        problems=[
            FilterProblem(field=field, value=value, reason=reason)
            for field, value, reason in narrowed.problems
        ],
    )


async def _anchor_offset(
    access: Repository,
    viewer: Viewer,
    start: str,
    near: int | None,
    *,
    hidden: bool,
    pinned_first: bool,
    asset_filter: AssetFilter,
    photo_set: str | None,
    ordering: str,
    seed: int | None,
) -> int:
    """Where the anchor sits in this screen's results, or where the page was when it is gone."""
    at = await access.position_of(
        viewer,
        start,
        hidden_only=hidden,
        # The same answer the page below gets, or the anchor is a position in a different
        # ordering and a link lands near the right file instead of at it.
        pinned_first=pinned_first,
        # The COMBINED filter, so the anchor is a position in the set this page is a page of.
        asset_filter=asset_filter,
        # And the set's own order where the page is a Photo Set's: an anchor ranked newest
        # first is a position in a different list, and the page lands one file wide of it.
        photo_set_id=photo_set,
        sort=ordering,
        # And the same shuffle.
        seed=seed,
    )
    # Not found is where the page was (`near`), or the top, not a refusal. See `resume_at`.
    return resume_at(at, near)


async def _summaries(
    request: Request,
    service: BrowseService,
    access: Repository,
    content: ContentStore,
    database: Database,
    viewer: Viewer,
    result: AssetPage,
    left_out: Sequence[str],
) -> list[AssetSummary]:
    """One page's tiles: each file with this viewer's marks, resume point and why it is left out."""
    ids = [view.asset.id for view in result.items]
    states = await service.state_for(viewer, ids)
    marks = await _marks(access, viewer, ids)
    # WHICH WILL NOT GO IN A SWAP, for swap mode's mark on a tile: an admin's alone (a swap is),
    # one read for the page, and never about a placeholder, whose file it would describe.
    refused = (
        await refused_for_swaps_among(
            database, [view.asset.id for view in result.items if not view.concealed]
        )
        if viewer.is_admin
        else set()
    )
    # WHY EACH FILE WAS LEFT OUT, on a wall asked for the files a product gave up on. One read for
    # the page, and only on such a wall: every other wall asks nothing here.
    why = await _left_out_words(
        content,
        [view.asset.id for view in result.items if not view.concealed or viewer.show_hidden],
        left_out,
    )
    # Read ONCE for the page, not once per tile: it is two settings reads, and a grid of fifty
    # tiles asking fifty times is the shape that makes a fast page feel slow.
    resume_minimum = await _resume_minimum(request, viewer)
    items: list[AssetSummary] = []
    for view in result.items:
        state = states.get(view.asset.id)
        items.append(
            summary(
                view,
                revealed=viewer.show_hidden,
                favorite=state.favorite if state else False,
                rating=state.rating if state else None,
                views=state.view_count if state else 0,
                o_count=state.o_count if state else 0,
                resume_ms=resume_point(
                    view.asset.duration_ms,
                    state.resume_ms if state else None,
                    minimum_ms=resume_minimum,
                ),
                mark=marks.get(view.asset.id),
                left_out=why.get(view.asset.id),
                swap_refused=view.asset.id in refused,
            )
        )
    return items


@router.get("/assets")
async def list_assets(
    request: Request,
    service: Annotated[BrowseService, Depends(_service)],
    engine: Annotated[FilterEngine, Depends(_filters)],
    access: Annotated[Repository, Depends(wiring.access)],
    content: Annotated[ContentStore, Depends(wiring.content)],
    database: Annotated[Database, Depends(wiring.database)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    start: Annotated[str | None, Query(alias="from")] = None,
    near: Annotated[int | None, Query(ge=0)] = None,
    after: Annotated[str | None, Query()] = None,
    hidden: Annotated[bool, Query()] = False,
    photo_set: Annotated[str | None, Query()] = None,
    # Read by `parse_modal`; declared so the declaration gate can see a test send it.
    depth: Annotated[str | None, Query()] = None,
    # One History line's files, `<id>~<source>~<day>`, read by `parse_modal`.
    filed: Annotated[list[str] | None, Query()] = None,
    tagged: Annotated[list[str] | None, Query()] = None,
    named: Annotated[list[str] | None, Query()] = None,
    #: Whether the wall asking honours the pin: floating what this user has pinned to the top.
    pinned_first: Annotated[bool, Query()] = False,
    username: Annotated[str | None, Query()] = None,
    sort: Annotated[str | None, Query()] = None,
    #: WHICH shuffle, for the one order that has more than one of them.
    seed: Annotated[int | None, Query(ge=0, le=SHUFFLE_MODULUS - 1)] = None,
    #: How the words are answered.
    meaning: Annotated[bool | None, Query()] = None,
) -> AssetPageResponse:
    """A page of what this viewer may see, newest first.

    **WARNING: one order cannot always resolve a deep anchor, and that order is asking by MEANING.**
    """
    narrowed, asset_filter, ordering = await _narrowed(
        request,
        engine,
        viewer,
        sort=sort,
        meaning=meaning,
        after=after,
        pinned_first=pinned_first,
        photo_set=photo_set,
        username=username,
        need=offset + limit,
    )

    # Where the anchor sits in THIS screen's results, read as the page below is.
    if start is not None:
        offset = await _anchor_offset(
            access,
            viewer,
            start,
            near,
            hidden=hidden,
            pinned_first=pinned_first,
            asset_filter=asset_filter,
            photo_set=photo_set,
            ordering=ordering,
            seed=seed,
        )

    result = await service.page(
        viewer,
        limit=limit,
        offset=offset,
        hidden_only=hidden,
        photo_set_id=photo_set,
        pinned_first=pinned_first,
        asset_filter=asset_filter,
        sort=ordering,
        seed=seed,
        after=after,
    )

    items = await _summaries(
        request, service, access, content, database, viewer, result, narrowed.left_out
    )

    return await _page_response(access, viewer, username, items, result, narrowed, limit, offset)


@router.get("/assets/facets")
async def asset_facets(
    request: Request,
    engine: Annotated[FilterEngine, Depends(_filters)],
    service: Annotated[BrowseService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    facet: Annotated[str, Query()],
    hidden: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 24,
) -> FacetCounts:
    """What the files this query reaches are made of, along one dimension, with counts."""
    wanted = FACETS_RENAMED.get(facet, facet)
    # A dimension a guest may not ask about is refused in the same words an invented one is, and
    # deliberately so.
    if wanted not in FACETS or (wanted in ADMIN_FACETS and not viewer.is_admin):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"unknown facet {facet!r}")
    facet = wanted

    asset_filter = await engine.constrain(viewer, request.query_params)
    counted = await service.facets(
        viewer, facet, asset_filter=asset_filter, hidden_only=hidden, limit=limit
    )
    return FacetCounts(
        facet=facet,
        values=[FacetValue(value=row.value, count=row.count, label=row.label) for row in counted],
    )


@router.get("/assets/random")
async def random_asset(
    request: Request,
    service: Annotated[BrowseService, Depends(_service)],
    engine: Annotated[FilterEngine, Depends(_filters)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    avoiding: Annotated[str | None, Query()] = None,
) -> AssetSummary:
    """Something else to look at, chosen at random out of the set the address describes.

    **The query in the address narrows the draw, exactly as it narrows a page.**
    """
    # `constrain` rather than `narrow`: the words are answered by the index, not by a model.
    asset_filter = await engine.constrain(viewer, request.query_params)
    view = await service.something_else(viewer, avoiding=avoiding, asset_filter=asset_filter)
    if view is None:
        raise _missing()
    state = (await service.state_for(viewer, [view.asset.id])).get(view.asset.id)
    mark = (await _marks(access, viewer, [view.asset.id])).get(view.asset.id)
    return summary(
        view,
        revealed=viewer.show_hidden,
        favorite=state.favorite if state else False,
        rating=state.rating if state else None,
        mark=mark,
    )


@router.post("/assets/memberships", dependencies=[Depends(csrf_protect)])
async def memberships_of_assets(
    body: MembershipAsk,
    access: Annotated[Repository, Depends(wiring.access)],
    service: Annotated[BrowseService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> Memberships:
    """What these files are already on, so a picker can draw a tick beside each row.

    **This writes nothing.**
    """
    actionable = await access.actionable_of(viewer, body.asset_ids)
    asked = list(actionable.allowed)
    if not asked:
        return Memberships()

    counted = await access.memberships_of(asked)
    states = await service.state_for(viewer, asked)
    hearted = sum(1 for state in states.values() if state.favorite)
    return Memberships(
        people=_membership(counted.people, len(asked)),
        sites=_membership(counted.sites, len(asked)),
        collections=_membership(counted.collections, len(asked)),
        photo_sets=_membership(counted.photo_sets, len(asked)),
        tags=_membership(counted.tags, len(asked)),
        songs=_membership(counted.songs, len(asked)),
        favorite=_how_many(hearted, len(asked)),
    )


def _membership(counted: Mapping[str, int], asked: int) -> Membership:
    """One kind's tally turned into the two lists a picker draws from."""
    return Membership(
        all=sorted(one for one, carried in counted.items() if carried >= asked),
        some=sorted(one for one, carried in counted.items() if 0 < carried < asked),
    )


def _how_many(carried: int, asked: int) -> str:
    """All of them, some of them, or none. The one word the heart answers with."""
    if carried == 0:
        return "none"
    return "all" if carried >= asked else "some"


def _disagreement_seam(request: Request) -> DisagreementSeam:
    """Whoever can say how many fields a stash-box disagrees with about one file. A shape, because
    the disagreements are the stash-box slice's and a slice may not import another."""
    return wiring.part_of(request, wiring.DISAGREEMENTS)


@router.get("/assets/{asset_id}")
async def get_asset(
    request: Request,
    asset_id: str,
    access: Annotated[Repository, Depends(wiring.access)],
    database: Annotated[Database, Depends(wiring.database)],
    service: Annotated[BrowseService, Depends(_service)],
    content: Annotated[ContentStore, Depends(wiring.content)],
    waiting: Annotated[DisagreementSeam, Depends(_disagreement_seam)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> AssetDetail:
    """One asset; a concealed one is the placeholder unless this viewer's vault is open."""
    record = await access.file_record(viewer, asset_id)
    if record is None:
        raise _missing()
    view = record.view
    if view.concealed and not viewer.show_hidden:
        return AssetDetail(id=view.asset.id, media_type="", concealed=True, added_at=0)

    states = await service.state_for(viewer, [view.asset.id])
    state = states.get(view.asset.id)
    mark = (await _marks(access, viewer, [view.asset.id])).get(view.asset.id)
    music_source, music_from, music_undo, song_id = await _music_named(
        database, access, viewer, view.asset.id, view.asset.music
    )
    return AssetDetail(
        id=view.asset.id,
        media_type=view.asset.media_type,
        title=view.asset.title,
        download_url=view.asset.download_url,
        release_date=view.asset.release_date,
        unreachable=view.unreachable,
        disagreements=(marked := await waiting.disagreement_mark(viewer, "asset", asset_id))[0],
        disagreement_boxes=marked[1],
        details=view.asset.details,
        production_date=view.asset.production_date,
        site_code=view.asset.site_code,
        music=view.asset.music,
        music_source=music_source,
        music_from=music_from,
        music_undo=music_undo,
        song_id=song_id,
        links=await service.links_of(view.asset.id),
        enriched_by=[
            EnrichedBy(via=one.via, name=one.name, box=one.box)
            for one in await access.enriched_by(view.asset.id)
        ],
        width=view.asset.width,
        height=view.asset.height,
        duration_ms=view.asset.duration_ms,
        size_bytes=view.asset.size_bytes,
        browser_may_not_draw=(view.asset.mime or "") in heif.BROWSER_MAY_NOT_DRAW,
        container=view.asset.container,
        vcodec=view.asset.vcodec,
        acodec=view.asset.acodec,
        fps=view.asset.fps,
        bit_depth=view.asset.bit_depth,
        original_filename=view.asset.original_filename,
        filename=record.name_on_disk,
        where=await _where_on_disk(request, viewer, record.locations),
        added_at=view.asset.added_at,
        art=view.art_version,
        thumb=view.has_thumb,
        preview=view.has_preview,
        favorite=state.favorite if state else False,
        rating=state.rating if state else None,
        views=state.view_count if state else 0,
        o_count=state.o_count if state else 0,
        last_viewed_at=state.last_viewed_at if state else None,
        sprite=_sheet_layout(await service.sprite_of(viewer, record), view.asset.duration_ms),
        playback_repair=await _repair_state(request, access, viewer, view.asset),
        fingerprint_verdict=_standing(
            await content.verdict_of(view.asset.id, VerdictProduct.FINGERPRINTS)
        ),
        concealed=False,
        hidden=view.concealed,
        shared=mark.shared if mark else False,
        restricted=mark.restricted if mark else False,
        shared_here=mark.shared_here if mark else False,
        restricted_here=mark.restricted_here if mark else False,
    )


async def _left_out_words(
    content: ContentStore, ids: list[str], products: Sequence[str]
) -> dict[str, str]:
    """Why each of these files was left out by these products, in plain words, by file."""
    if not products or not ids:
        return {}
    found = await content.standing_verdicts_among(ids, products)
    order = {product: at for at, product in enumerate(products)}
    return {
        asset_id: " ".join(
            dict.fromkeys(
                why_left_out(one.code, one.reason)
                for one in sorted(verdicts, key=lambda one: order.get(one.product, len(order)))
            )
        )
        for asset_id, verdicts in found.items()
    }


def _standing(verdict: Verdict | None) -> str | None:
    """A verdict's words, where it is the permanent kind. None otherwise."""
    if verdict is None or verdict.transient:
        return None
    # The code's own words, never the tool's text: the same rule the left-out wall reads by.
    return why_left_out(verdict.code, verdict.reason)


#: Files whose audio sits at least this far from their video are repaired before being served.
_REPAIR_AT = NEEDS_REPAIR_BYTES


async def _repair_state(
    request: Request, access: Repository, viewer: Viewer, asset: Asset
) -> str | None:
    """Whether this file needed repairing, and what is happening about it. The setting is read
    only for a file that needs it."""
    gap = asset.interleave_gap
    if gap is None or gap < _REPAIR_AT:
        return None
    repaired = await access.locate_derivative(viewer, asset.id, DerivativeKind.REMUX)
    if repaired:
        return "repaired"
    return "pending" if await _repairing(request) else "off"


async def _where_on_disk(
    request: Request, viewer: Viewer, locations: Sequence[Location]
) -> str | None:
    """Where the file is, said the way this viewer may be told it (`kernel.where`): the full path
    for an admin, the library folder's name and the folders they may see for anyone else."""
    for location in locations:
        if location.status is not LocationStatus.PRESENT:
            continue
        where = await wiring.whereabouts(request, viewer, root_id=location.root_id)
        return where.of(location.root_id, location.rel_path)
    return None


async def _serve_derivative(
    request: Request,
    access: Repository,
    viewer: Viewer,
    asset_id: str,
    kind: DerivativeKind,
    media_type: str,
    newest: bool = False,
) -> Response:
    """A generated file, if this viewer may have the asset it was generated from."""
    try:
        served = (
            await access.serve_newest_derivative(viewer, asset_id, kind)
            if newest
            else await access.serve_derivative(viewer, asset_id, kind)
        )
    except ValueError:
        # A row naming a path that is not inside the cache any more: a restored backup, or one
        # written before the check that now refuses it.
        log.warning("browse.derivative_unusable", asset_id=asset_id, kind=kind.value)
        raise _missing() from None
    if served is None:
        raise _missing()
    return await serve_file(
        request,
        served.path,
        media_type=media_type,
        headers=keeps(request, version=served.version, concealed=served.concealed),
    )


@router.put("/assets/{asset_id}", dependencies=[Depends(csrf_protect)])
async def set_asset_record(
    asset_id: str,
    body: RecordWrite,
    service: Annotated[BrowseService, Depends(_service)],
    reindexer: Annotated[ReindexSeam, Depends(wiring.reindexer)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> Response:
    """Write the editable half of a file's record: its title, where it came from, when it came out.

    **IMPORTANT: Only the fields the caller actually sent are written.**
    """
    sent = body.model_fields_set
    if not sent:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    if not await service.set_record(
        viewer,
        asset_id,
        title=body.title if "title" in sent else UNCHANGED,
        download_url=body.download_url if "download_url" in sent else UNCHANGED,
        release_date=body.release_date if "release_date" in sent else UNCHANGED,
        details=body.details if "details" in sent else UNCHANGED,
        production_date=body.production_date if "production_date" in sent else UNCHANGED,
        site_code=body.site_code if "site_code" in sent else UNCHANGED,
        music=body.music if "music" in sent else UNCHANGED,
        links=body.links if "links" in sent else UNCHANGED,
    ):
        raise _missing()
    # Any field the index reads, not the title alone: the index carries the music too (it has a
    # token of its own and is free text besides), and a write that changed only that must not
    # leave the old words findable, whatever fields a client happens to send.
    if any(field in sent for field in INDEXED_RECORD_FIELDS):
        await reindexer.touched(asset_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/assets/{asset_id}/history")
async def get_history(
    asset_id: str,
    access: Annotated[Repository, Depends(wiring.access)],
    database: Annotated[Database, Depends(wiring.database)],
    workbench: Annotated[Workbench, Depends(wiring.workbench)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> FileHistoryPage:
    """What happened to this file, oldest first: the newest `limit` lines and the total."""
    resolved = await require_reachable(access, viewer, asset_id, _missing)
    final = [one.name for one in workbench.reversers if not one.reversible]
    # Assembled once and the newest `limit` kept, so the total and the lines cannot disagree.
    every = await history_of_asset(
        database, access, viewer, resolved, limit=MAX_LIMIT, final_queues=final, bench=workbench
    )
    page = await unshown_said(database, access, viewer, every[-limit:])
    return FileHistoryPage(items=[history_event(event) for event in page], total=len(every))


@router.get("/assets/{asset_id}/thumb")
async def get_thumb(
    asset_id: str,
    request: Request,
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> Response:
    """The still. A 404 covers both "not allowed" and "not generated yet"."""
    return await _serve_derivative(
        request, access, viewer, asset_id, DerivativeKind.THUMB, "image/jpeg"
    )


@router.get("/assets/{asset_id}/preview")
async def get_preview(
    asset_id: str,
    request: Request,
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> Response:
    """The short hover clip, generated at import so the grid never transcodes while scrolling."""
    return await _serve_derivative(
        request,
        access,
        viewer,
        asset_id,
        DerivativeKind.PREVIEW,
        "video/mp4",
        newest=True,
    )


def _sheet_layout(sheet: Derivative | None, duration_ms: int | None) -> SpriteSheet | None:
    """A sheet's recorded layout, or None when there is not one to report."""
    if sheet is None:
        return None
    try:
        recorded = json.loads(sheet.params)
    except json.JSONDecodeError:
        return None
    if not isinstance(recorded, dict):
        return None
    try:
        columns = int(recorded["columns"])
        rows = int(recorded["rows"])
        # How many cells actually hold a frame. The sampler is asked rather than the row, because
        # the count is not part of what a sheet is filed under.
        frames = min(len(sprite_frames(duration_ms or 0)), max(columns * rows, 1))
        return SpriteSheet(
            columns=columns,
            rows=rows,
            tile_width=int(recorded["tile_width"]),
            frames=frames,
        )
    except (KeyError, TypeError, ValueError):
        # A pydantic refusal is a ValueError, so the bounds on the model land here too.
        return None


@router.get("/assets/{asset_id}/sprite")
async def get_sprite(
    asset_id: str,
    request: Request,
    service: Annotated[BrowseService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> Response:
    """The scrub strip: every frame of the timeline preview, as one image."""
    served = await service.sprite_served(viewer, asset_id)
    if served is None:
        raise _missing()
    return await serve_file(
        request,
        served.path,
        media_type="image/jpeg",
        headers=keeps(request, version=served.version, concealed=served.concealed),
    )


async def _resume_minimum(request: Request, viewer: Viewer) -> int | None:
    """The shortest video this user keeps a place in, or None when it keeps none at all."""
    return await resume_minimum_ms(part_of(request, SETTINGS_HUB).get_user, viewer.id)


async def _marks(access: Repository, viewer: Viewer, asset_ids: list[str]) -> dict[str, GrantMark]:
    """Which of these have been shared or restricted, for an admin, and for nobody else."""
    # Through the access layer's own gate, so the "an admin and nobody else" rule is written in one
    # place rather than repeated on every screen that draws a badge.
    return await access.visible_marks(viewer, ObjectType.ITEM, asset_ids)


@router.get("/assets/{asset_id}/save-to-device")
async def save_to_device(
    asset_id: str,
    request: Request,
    access: Annotated[Repository, Depends(wiring.access)],
    service: Annotated[BrowseService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> Response:
    """Hand over a file this viewer can already see, with no location in it."""
    _refuse_cross_site(request)

    path = await access.locate(viewer, asset_id)
    if path is None:
        raise await refuse_one(access, viewer, asset_id, _missing)

    if not viewer.is_admin and not await _guests_may_save(request):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "saving to your device is turned off")

    # The bytes are asked for BEFORE the save is recorded, and that ordering is load-bearing rather
    # than a nicety.
    if not await asyncio.to_thread(path.is_file):
        raise _missing()

    # A HEAD is the same question WITHOUT the bytes, which is what makes it the right thing for a
    # client to ask before it claims a download happened.
    if request.method == "HEAD":
        try:
            await asyncio.to_thread(places.places_in, path)
        except places.CannotRemovePlaces as refusal:
            raise _cannot_take_the_place_out() from refusal
        return FileResponse(path, media_type="application/octet-stream")

    # THE COPY THAT LEAVES CARRIES NO LOCATION: where a picture was taken never leaves in anything
    # Sift hands out.
    try:
        unplaced = await asyncio.to_thread(
            places.remove_places, path, wiring.settings(request).cache_dir / OUTGOING_DIR_NAME
        )
    except places.CannotRemovePlaces as refusal:
        raise _cannot_take_the_place_out() from refusal

    await service.record_save(viewer, asset_id)
    log.info("browse.saved_to_device", viewer_id=viewer.id, asset_id=asset_id)
    # The name the file was imported under, not the name it happens to have on disk.
    asset = await access.open_asset(viewer, asset_id)
    filename = (asset.original_filename if asset else None) or path.name
    if unplaced is None:
        return FileResponse(path, filename=filename, media_type="application/octet-stream")
    return FileResponse(
        unplaced,
        filename=filename,
        media_type="application/octet-stream",
        background=BackgroundTask(places.discard, unplaced),
    )


def _cannot_take_the_place_out() -> HTTPException:
    """The refusal of a file whose location cannot be taken out, one sentence for the GET and the
    HEAD that asks before it."""
    return HTTPException(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        "Sift couldn't take the location out of this file, so it wasn't saved.",
    )


@router.get("/save-log")
async def save_log(
    service: Annotated[BrowseService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SaveLogResponse:
    """Who saved what, and when. Admin-only."""
    rows, total = await service.saves(limit=limit, offset=offset)
    return SaveLogResponse(items=[SaveRecord(**_record(row)) for row in rows], total=total)


@router.head("/assets/{asset_id}/save-to-device", include_in_schema=False)
async def save_to_device_head(
    asset_id: str,
    request: Request,
    access: Annotated[Repository, Depends(wiring.access)],
    service: Annotated[BrowseService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> Response:
    """The same question without the bytes: may I have this file, and is it still there?"""
    return await save_to_device(asset_id, request, access, service, viewer)


def _record(row: Mapping[str, Any]) -> dict[str, Any]:
    return dict(row)


async def _music_named(
    database: Database, access: Repository, viewer: Viewer, asset_id: str, music: str | None
) -> tuple[str | None, MusicFrom | None, UndoPoint | None, str | None]:
    """Where a file's song came from, the file it was shared from where this viewer may see it,
    what takes a shared name back, and the song itself. A file the viewer may not see is not
    named: the source is still said, because it is a fact about THIS file's field, and a name it
    may not see would be a disclosure. The undo door is an admin's, as every workbench reversal
    is. The song is the viewer's to open whenever the file is: a song is seen through its files."""
    provenance = await music_provenance(database, asset_id, music)
    if provenance is None:
        return None, None, None, None
    shared: MusicFrom | None = None
    if provenance.source == MUSIC_SHARED and provenance.from_asset_id:
        seen = await access.get_asset(viewer, provenance.from_asset_id)
        if seen is not None:
            # Named in the file page's order: title, the name on disk now, the imported name.
            on_disk = (await access.names_on_disk(viewer, [seen.asset.id])).get(seen.asset.id)
            shared = MusicFrom(
                id=seen.asset.id,
                name=seen.asset.title or on_disk or seen.asset.original_filename or seen.asset.id,
            )
    undo: UndoPoint | None = None
    if provenance.source == MUSIC_SHARED and provenance.act_id and viewer.is_admin:
        undo = UndoPoint(kind="decision", id=provenance.act_id)
    return provenance.source, shared, undo, provenance.song_id
