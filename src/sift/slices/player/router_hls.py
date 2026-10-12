# SPDX-License-Identifier: AGPL-3.0-or-later
"""The HLS routes: the master and media playlists and the segments, split out of the player's router."""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse

from sift.kernel import wiring
from sift.kernel.access import Repository, Viewer
from sift.kernel.attention import PLAYED
from sift.kernel.content import Asset
from sift.kernel.numbers import as_int
from sift.slices.auth import current_viewer
from sift.slices.player import policy, tuning
from sift.slices.player.router_common import _CACHE_PRIVATE, _missing, _open, _player, log
from sift.slices.player.service import (
    PlayerService,
    SegmentUnavailable,
    byte_range,
    master_playlist,
    plan_query,
    playlist,
    read_range,
)

router = APIRouter()


@router.get("/assets/{asset_id}/hls/master.m3u8")
async def hls_master(
    asset_id: str,
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> Response:
    """The ladder for the player to choose between.

    Declared before `hls/{segment}`, which would otherwise match "master.m3u8".
    """
    asset = await _open(access, viewer, asset_id)
    body = master_playlist(asset, base_url=f"/api/assets/{asset_id}/hls")
    return Response(
        content=body,
        media_type="application/vnd.apple.mpegurl",
        headers=_CACHE_PRIVATE,
    )


@router.head("/assets/{asset_id}/hls/master.m3u8", include_in_schema=False)
async def hls_master_head(
    asset_id: str,
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> Response:
    return await hls_master(asset_id, access, viewer)


@router.get("/assets/{asset_id}/hls/index.m3u8")
async def hls_playlist(
    asset_id: str,
    request: Request,
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> Response:
    """The list of segments, made as they are asked for; the plan rides on every URL."""
    asset = await _open(access, viewer, asset_id)
    plan = _plan_from_query(request, asset)
    body = playlist(asset, base_url=f"/api/assets/{asset_id}/hls", query=plan_query(plan))
    return Response(
        content=body,
        media_type="application/vnd.apple.mpegurl",
        headers=_CACHE_PRIVATE,
    )


@router.head("/assets/{asset_id}/hls/index.m3u8", include_in_schema=False)
async def hls_playlist_head(
    asset_id: str,
    request: Request,
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> Response:
    return await hls_playlist(asset_id, request, access, viewer)


@router.get("/assets/{asset_id}/hls/{segment}")
async def hls_segment(
    asset_id: str,
    segment: str,
    request: Request,
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    player: Annotated[PlayerService, Depends(_player)],
) -> Response:
    """One segment, transcoded on demand if it is not already cached; scoped as `stream` is."""
    asset = await _open(access, viewer, asset_id)
    plan = _plan_from_query(request, asset)
    index = _segment_index(segment, asset)

    try:
        produced = await player.segment(asset, index=index, plan=plan)
    except SegmentUnavailable as error:
        log.warning("player.segment_unavailable", asset_id=asset_id, index=index, error=str(error))
        # 503, not 404: a failed build must not look like a refusal.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="That part of the video couldn't be prepared. Try again.",
        ) from None

    size = (await asyncio.to_thread(produced.path.stat)).st_size
    span = byte_range(None, size) or _whole(size)

    async def body() -> Any:
        try:
            async for chunk in read_range(produced.path, span, played=PLAYED.now):
                yield chunk
        finally:
            if produced.ephemeral:
                await asyncio.to_thread(produced.path.unlink, missing_ok=True)

    return StreamingResponse(
        body(),
        media_type=tuning.SEGMENT_MIME,
        headers={"Content-Length": str(size), **_CACHE_PRIVATE},
    )


def _whole(size: int) -> Any:
    span = byte_range(f"bytes=0-{max(0, size - 1)}", max(1, size))
    assert span is not None  # noqa: S101 - a constructed range is always parseable
    return span


def _segment_index(segment: str, asset: Asset) -> int:
    """Which segment `0.m4s` or `init.mp4` names; 404 for anything else, before the filesystem."""
    if segment == tuning.INIT_SEGMENT_NAME:
        return -1
    if not segment.endswith(tuning.SEGMENT_SUFFIX):
        raise _missing()
    raw = segment[: -len(tuning.SEGMENT_SUFFIX)]
    index = as_int(raw)
    # Only the init segment is under zero.
    if index is None or index < 0 or index >= policy.segment_count(asset.duration_ms):
        raise _missing()
    return index


def _plan_from_query(request: Request, asset: Asset) -> policy.Plan:
    """The plan this segment is asked under, validated: a height must be one of the ladder's."""
    height: int | None = None
    candidate = as_int(request.query_params.get("height"))
    if candidate is not None:
        offered = {rung.height for rung in policy.rungs(asset.width, asset.height)}
        if candidate in offered:
            height = candidate

    return policy.Plan(route=policy.Route.TRANSCODE, reason="", scale_height=height)


@router.head("/assets/{asset_id}/hls/{segment}", include_in_schema=False)
async def hls_segment_head(
    asset_id: str,
    segment: str,
    request: Request,
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    player: Annotated[PlayerService, Depends(_player)],
) -> Response:
    return await hls_segment(asset_id, segment, request, access, viewer, player)
