# SPDX-License-Identifier: AGPL-3.0-or-later
"""The playback endpoints: the routes that return the media itself.

Every path comes from the access layer, denied and missing are one 404, and every byte is
`private, no-cache`, so no shared cache can answer without the permission check.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Annotated, Any, Literal, cast, get_args

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import Field
from starlette.background import BackgroundTask

from sift.kernel import places, wiring
from sift.kernel.access import Repository, Viewer
from sift.kernel.attention import PLAYED
from sift.kernel.client import Client, client_of
from sift.kernel.content import Asset, ContentStore, DerivativeKind, UserStateStore, VerdictProduct
from sift.kernel.content.user_state import HEAT_BUCKETS, resume_minimum_ms, resume_point
from sift.kernel.db import Database
from sift.kernel.http import is_local_request
from sift.kernel.jobs import WAITED_ON_PRIORITY, JobQueue, registered_families
from sift.kernel.jobs.families import Family
from sift.kernel.log import get_logger
from sift.kernel.media_sources import CopiesAway, MissingAsset, NoReadableCopy, resolve
from sift.kernel.mp4 import NEEDS_REPAIR_BYTES
from sift.kernel.numbers import as_int
from sift.kernel.reach import refuse_one
from sift.kernel.serving import keeps, serve_file
from sift.kernel.threads import on_serving_thread
from sift.kernel.use_history import keeps_history
from sift.kernel.wire import Wire
from sift.kernel.wiring import HARDWARE, QUEUE, SETTINGS, SETTINGS_HUB, part_of
from sift.slices import media_jobs
from sift.slices.auth import csrf_protect, current_viewer
from sift.slices.player import policy, tuning
from sift.slices.player.about import about_of
from sift.slices.player.plays import (
    MOST_SEEKS,
    MOST_SPEEDS,
    Inside,
    Kind,
    OpenedFrom,
    Place,
    Screen,
    record_play,
    sitting_is_here,
)
from sift.slices.player.qualities import Quality, _qualities
from sift.slices.player.service import (
    SERVICE,
    PlayerService,
    SegmentUnavailable,
    byte_range,
    capped,
    master_playlist,
    plan_query,
    playlist,
    read_range,
)

log = get_logger(__name__)

router = APIRouter(tags=["player"])

_CACHE_PRIVATE = {"Cache-Control": "private, no-cache"}


def _player(request: Request) -> PlayerService:
    return part_of(request, SERVICE)


def _missing() -> HTTPException:
    """The one answer for "no such asset" and "not yours"."""
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")


def _why_the_copy(asset: Asset) -> policy.Copy:
    """Which problem this file's second copy was written for, read off the stored asset."""
    gap = asset.interleave_gap
    if gap is not None and gap >= NEEDS_REPAIR_BYTES:
        return policy.Copy.REPAIRED
    return policy.Copy.REPACKAGED


async def _open(access: Repository, viewer: Viewer, asset_id: str) -> Asset:
    """The asset, if this viewer may actually watch it. 404 otherwise, either way."""
    asset = await access.open_asset(viewer, asset_id)
    if asset is None:
        raise _missing()
    return asset


class Capabilities(Wire):
    """What the browser reported it can decode and read: only the client knows."""

    video_codecs: list[str] = Field(default_factory=list, max_length=32)
    #: The codecs also decoded at ten bits; empty reads as eight bits only, which converts.
    video_codecs_10bit: list[str] = Field(default_factory=list, max_length=32)
    audio_codecs: list[str] = Field(default_factory=list, max_length=32)
    containers: list[str] = Field(default_factory=list, max_length=32)


class PlaybackPlan(Wire):
    """How this file will be played, and what the player should say about it."""

    route: str
    reason: str
    #: Which second copy this is, when a copy is played: the route says `remux` for both.
    copy_kind: str | None = None
    url: str
    scale_height: int | None = None
    projected_realtime: float | None = None
    streamable: bool = True
    duration_ms: int | None = None
    #: Where to start, in milliseconds, or None for the beginning.
    resume_ms: int | None = None
    #: How long the file must be on screen to count as a view, by the rule that judges the report
    #: (`policy.watch_needed`), so a tile can update without waiting for the player to close.
    view_at_ms: int = 0

    #: Every size this file can be watched at, in the menu's order.
    qualities: list[Quality] = Field(default=[])

    #: Why no copy can be read now, asked as a conversion asks: `gone` (its folder answers and the
    #: file is not in it) or `away` (its drive or share is not answering). None when one can.
    unreadable: Literal["gone", "away"] | None = None
    #: Whether a scan of the library folder it was in is waiting or running, which finds it if moved.
    scan_queued: bool = False


def _plan_url(base: str, plan: policy.Plan) -> str:
    """Where the browser asks for the plan's bytes."""
    # The cheap routes are the same request; `stream` picks the file.
    if plan.route in (policy.Route.DIRECT, policy.Route.REMUX, policy.Route.UNREAD):
        return f"{base}/stream"
    # The decision travels in the address, or later requests fall back to a full transcode.
    return f"{base}/hls/index.m3u8{plan_query(plan)}"


@router.post("/assets/{asset_id}/playback", dependencies=[Depends(csrf_protect)])
async def plan_playback(
    asset_id: str,
    capabilities: Capabilities,
    request: Request,
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    user_state: Annotated[UserStateStore, Depends(wiring.user_state)],
    player: Annotated[PlayerService, Depends(_player)],
    content: Annotated[ContentStore, Depends(wiring.content)],
    queue: Annotated[JobQueue, Depends(wiring.queue)],
) -> PlaybackPlan:
    """Decide how this browser should play this file: a POST, as the answer is the client's own."""
    asset = await _open(access, viewer, asset_id)
    # Decided from the file that will actually be sent (`policy.as_served`).
    repaired = await access.locate_derivative(viewer, asset.id, DerivativeKind.REMUX)
    # Every copy unreachable: the 404 `stream` would give, given first.
    if repaired is None and await access.locate(viewer, asset.id) is None:
        raise _missing()
    # A file recorded where it no longer is fails like one the browser cannot decode, so it is said.
    unreadable = None if repaired is not None else await _unreadable(content, asset.id)
    scan_queued = unreadable is not None and await _scan_queued(content, queue, asset.id)

    client = _client_of(capabilities)
    settings = part_of(request, SETTINGS)
    hardware = part_of(request, HARDWARE)
    max_height = await part_of(request, SETTINGS_HUB).get_app(MAX_TRANSCODE_HEIGHT_KEY)

    served = policy.as_served(asset, repaired=repaired is not None)

    # The remux tier, asked of the stored file: a copy is built in the background.
    if repaired is None and policy.needs_repackaging(asset, client):
        await _repackage(request, asset.id)

    copy = _why_the_copy(asset) if repaired is not None else None
    plan = policy.decide(
        served,
        client,
        max_height=int(max_height),
        cpu_count=hardware.cpu_count,
        encoder_rate=player.encoder_rate,
        copy=copy,
        stored_container=asset.container,
    )
    del settings  # the plan needs none of it; kept explicit so the dependency is not re-added

    plan = await _unread(request, content, asset, plan)

    base = f"/api/assets/{asset_id}"
    url = _plan_url(base, plan)

    # Through the same rule that stored it, so a raised minimum applies now.
    state = await user_state.state_of(asset_id, viewer.id)
    resume_ms = await _resume_where(request, viewer.id, asset, state.resume_ms)

    return PlaybackPlan(
        route=plan.route.value,
        reason=plan.reason,
        copy_kind=copy.value if copy is not None else None,
        url=url,
        scale_height=plan.scale_height,
        projected_realtime=plan.projected_realtime,
        streamable=plan.streamable,
        duration_ms=asset.duration_ms,
        resume_ms=resume_ms,
        view_at_ms=policy.watch_needed(asset.media_type, asset.duration_ms),
        qualities=_qualities(
            asset,
            plan,
            base=base,
            default_url=url,
            cpu_count=hardware.cpu_count,
            encoder_rate=player.encoder_rate,
        ),
        unreadable=unreadable,
        scan_queued=scan_queued,
    )


#: How long a plan waits to hear whether a copy can be opened; past it, the copy is away.
_REACH_SECONDS = 5.0


async def _unreadable(content: ContentStore, asset_id: str) -> Literal["gone", "away"] | None:
    """Whether a conversion could open this file now, by the lookup it makes, and if not, why."""
    try:
        async with asyncio.timeout(_REACH_SECONDS):
            await resolve(content, asset_id)
    except MissingAsset:
        raise _missing() from None
    except (CopiesAway, TimeoutError):
        return "away"
    except NoReadableCopy:
        return "gone"
    return None


async def _scan_queued(content: ContentStore, queue: JobQueue, asset_id: str) -> bool:
    """Whether a walk of a library folder holding a copy of this file is waiting or running."""
    roots = {location.root_id for location in await content.locations(asset_id)}
    walks = [kind for kind, family in registered_families().items() if family is Family.SCAN]
    for kind in walks:
        if any(payload.get("root_id") in roots for payload in await queue.live_payloads(kind)):
            return True
    return False


def _client_of(capabilities: Capabilities) -> policy.ClientCapabilities:
    return policy.ClientCapabilities(
        video_codecs=frozenset(c.lower() for c in capabilities.video_codecs),
        video_codecs_10bit=frozenset(c.lower() for c in capabilities.video_codecs_10bit),
        audio_codecs=frozenset(c.lower() for c in capabilities.audio_codecs),
        containers=frozenset(c.lower() for c in capabilities.containers),
    )


async def _unread(
    request: Request, content: ContentStore, asset: Asset, plan: policy.Plan
) -> policy.Plan:
    # An unread file is read now, ahead of the queue, unless the read already failed.
    if plan.route is policy.Route.UNREAD:
        given_up = await content.verdict_of(asset.id, VerdictProduct.PROBE)
        if given_up is not None and not given_up.transient:
            plan = replace(plan, reason=f"Sift could not read this file: {given_up.reason}")
        else:
            await _read(request, asset.id)
    return plan


async def _read(request: Request, asset_id: str) -> None:
    """Ask for this file to be read, at the priority of somebody waiting for it; fire and forget.

    Asked even when a read is coming: `dedupe` raises a waiting read to this priority.
    """
    queue = part_of(request, QUEUE)
    try:
        await queue.enqueue(
            media_jobs.PROBE, {"asset_id": asset_id}, priority=WAITED_ON_PRIORITY, dedupe=True
        )
    except Exception as error:
        log.info("player.read_not_queued", asset_id=asset_id, detail=str(error))


async def _repackage(request: Request, asset_id: str) -> None:
    """Ask for a repackaged copy of this file, unless one is already being made; fire and forget."""
    queue = part_of(request, QUEUE)
    payload = {"asset_id": asset_id, "because": media_jobs.BECAUSE_CONTAINER}
    try:
        # `is_live` covers a copy already running, which `dedupe` does not.
        if await queue.is_live(media_jobs.REMUX, payload):
            return
        await queue.enqueue(media_jobs.REMUX, payload, dedupe=True)
    except Exception as error:
        log.info("player.repackage_not_queued", asset_id=asset_id, detail=str(error))


class LocalFile(Wire):
    """Where this asset's file is, for a caller that could actually open it."""

    path: str | None = None
    """The absolute path, or None when the caller is not on this machine or is a guest. See the
    route."""

    shared_path: str | None = None
    r"""The same file named in a way that means the same thing on ANY computer, or None.

    THIS IS WHAT MAKES A DRAG ON A SECOND COMPUTER COST NOTHING. A library on a network share is
    reachable from every machine that can see the share, so there is no reason for a second Sift to
    copy a video across the network in order to hand it to something running beside it: it can
    hand over the share path and let the receiving application read it exactly as the first machine
    would.

    ONLY A UNC PATH QUALIFIES, and the distinction is the whole safety of it. `\\nas\Media\a.mp4`
    names a server and a share, so it is the same file wherever it is opened. `D:\Media\a.mp4` is a
    different file on every computer that happens to have a D: drive, and handing one of those to
    another machine is how a drag quietly produces somebody else's video.

    It is answered to remote callers, which the plain `path` deliberately is not. What it discloses
    is the name of a share on the local network, to somebody the library has already agreed may
    watch and download the file itself, and the caller still has to be able to reach that share.
    """

    filename: str
    """What the file is called. Answered in both cases: it is what a fetched copy gets named."""

    size_bytes: int | None = None
    """So a download can show progress. None when the row has never been probed. For a file that
    holds a place, the size of the copy without it, which is what arrives."""

    holds_a_place: bool = False
    """The file says where it was made, so it never leaves as it is: `path` and `shared_path` are
    None, whoever asks, and the shell fetches the copy without the place from `/outgoing`
    instead of the original from `/stream`."""


def _machine_independent(path: Path) -> str | None:
    r"""The path, when it names the same file on any computer (UNC); otherwise None.

    `\\?\` and `\\.\` name a local device, so they are excluded.
    """
    text = str(path)
    if not text.startswith("\\\\"):
        return None
    if text.startswith("\\\\?\\") or text.startswith("\\\\.\\"):
        return None
    return text


@router.get("/assets/{asset_id}/local-file")
async def local_file(
    asset_id: str,
    request: Request,
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> LocalFile:
    """Where the file is, so the desktop shell can drag it into another application.

    The path comes from `Repository.locate`; `path` is None off this machine, and a guest is given
    neither path, so their drag is always a copy.
    """
    # Not through `_open`: the vault's owner is told what stopped a deliberate drag.
    asset = await access.open_asset(viewer, asset_id)
    if asset is None:
        raise await refuse_one(access, viewer, asset_id, _missing)
    located = await access.locate(viewer, asset.id)
    if located is None:
        raise _missing()

    # A file that says where it was made is never dragged as it is: the shell fetches `outgoing`.
    try:
        unplaced = await on_serving_thread(places.size_without_places, located)
    except places.CannotRemovePlaces as refusal:
        raise _cannot_take_the_place_out() from refusal
    except OSError:
        raise _missing() from None
    if unplaced is not None:
        return LocalFile(
            filename=located.name,
            size_bytes=unplaced,
            holds_a_place=True,
        )

    local = is_local_request(
        request.client.host if request.client else None,
        request.headers.get("x-forwarded-for", ""),
    )
    return LocalFile(
        path=str(located) if local and viewer.is_admin else None,
        # A network share reachable from both machines saves copying the file across.
        shared_path=_machine_independent(located) if viewer.is_admin else None,
        # The name on disk, as the person would find it in their folders.
        filename=located.name,
        size_bytes=asset.size_bytes,
    )


def _cannot_take_the_place_out() -> HTTPException:
    """The refusal of a file whose location cannot be taken out: it does not leave with it."""
    return HTTPException(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        "Sift couldn't take the location out of this file, so it can't be copied out of Sift.",
    )


@router.get("/assets/{asset_id}/outgoing")
async def outgoing(
    asset_id: str,
    request: Request,
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> Response:
    """The file as it may leave Sift: its own bytes, or a copy with every location taken out.

    What a drag out and Copy image fetch; same authority as `stream`.
    """
    asset = await _open(access, viewer, asset_id)
    path = await access.locate(viewer, asset.id)
    if path is None or not await on_serving_thread(path.is_file):
        raise _missing()
    media_type = asset.mime or "application/octet-stream"
    if request.method == "HEAD":
        try:
            await on_serving_thread(places.places_in, path)
        except places.CannotRemovePlaces as refusal:
            raise _cannot_take_the_place_out() from refusal
        return FileResponse(path, media_type=media_type, headers=dict(_CACHE_PRIVATE))
    try:
        unplaced = await on_serving_thread(
            places.remove_places, path, wiring.settings(request).cache_dir / places.OUTGOING_FOLDER
        )
    except places.CannotRemovePlaces as refusal:
        raise _cannot_take_the_place_out() from refusal
    if unplaced is None:
        return FileResponse(path, media_type=media_type, headers=dict(_CACHE_PRIVATE))
    return FileResponse(
        unplaced,
        media_type=media_type,
        headers=dict(_CACHE_PRIVATE),
        background=BackgroundTask(places.discard, unplaced),
    )


@router.head("/assets/{asset_id}/outgoing", include_in_schema=False)
async def outgoing_head(
    asset_id: str,
    request: Request,
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> Response:
    """The same question as `outgoing` without the bytes; 422 when the location cannot go."""
    return await outgoing(asset_id, request, access, viewer)


@router.get("/assets/{asset_id}/stream")
async def stream(
    asset_id: str,
    request: Request,
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> Response:
    """The file itself, over HTTP range requests. No ffmpeg runs."""
    asset = await _open(access, viewer, asset_id)
    # A file whose audio sits far from its video is served from its repaired copy.
    repaired = await access.locate_derivative(viewer, asset.id, DerivativeKind.REMUX)
    path = repaired or await access.locate(viewer, asset.id)
    if path is None:
        raise _missing()

    try:
        stat = await on_serving_thread(path.stat)
    except OSError:
        raise _missing() from None

    size = stat.st_size

    raw_range = request.headers.get("range")
    try:
        span = byte_range(raw_range, size)
    except ValueError:
        return Response(
            status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
            headers={"Content-Range": f"bytes */{size}", **_CACHE_PRIVATE},
        )

    # A repaired copy is always MP4, so the type is what is sent.
    media_type = policy.REPAIRED_MIME if repaired else (asset.mime or "application/octet-stream")
    # `Accept-Ranges` lets the player seek.
    headers = {"Accept-Ranges": "bytes", **_CACHE_PRIVATE}
    # A picture opened isn't a clip playing.
    played = PLAYED.now if asset.media_type == "video" else None

    if span is None:
        headers["Content-Length"] = str(size)
        whole = byte_range(f"bytes=0-{size - 1}", size) if size else None
        if whole is None:
            return Response(status_code=status.HTTP_200_OK, headers=headers, media_type=media_type)
        return StreamingResponse(
            read_range(path, whole, gone=request.is_disconnected, played=played),
            media_type=media_type,
            headers=headers,
        )

    span = capped(span, raw_range, tuning.MAX_OPEN_RANGE_BYTES)
    headers["Content-Range"] = span.content_range
    headers["Content-Length"] = str(span.length)
    return StreamingResponse(
        read_range(path, span, gone=request.is_disconnected, played=played),
        status_code=status.HTTP_206_PARTIAL_CONTENT,
        media_type=media_type,
        headers=headers,
    )


# HEAD on every route that answers with bytes, out of the schema and delegating to the GET.
@router.head("/assets/{asset_id}/stream", include_in_schema=False)
async def stream_head(
    asset_id: str,
    request: Request,
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> Response:
    return await stream(asset_id, request, access, viewer)


@router.get("/assets/{asset_id}/rendition")
async def rendition(
    asset_id: str,
    request: Request,
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> Response:
    """A photograph as a JPEG, for a browser that cannot draw the original. No decoder runs.

    Only a still's copy is answered; a 404 covers every kind of miss.
    """
    try:
        served = await access.serve_derivative(viewer, asset_id, DerivativeKind.RENDITION)
    except ValueError:
        # A row naming a path outside the cache: nothing to serve.
        raise _missing() from None
    if served is None or served.path.suffix.lower() != ".jpg":
        raise _missing()
    return await serve_file(
        request,
        served.path,
        media_type="image/jpeg",
        headers=keeps(request, version=served.version, concealed=served.concealed),
    )


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
            detail="That part of the video could not be prepared. Try again.",
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


#: The longest one sitting may claim to have been, and so one slice of it: a day.
_MAX_SITTING_MS = 24 * 60 * 60 * 1000

_AN_ID = r"^[A-Za-z0-9_-]{1,64}$"

_MOST_SEARCHED = 1000

#: The slowest and fastest speeds a sitting keeps: a bound on a browser.
_SLOWEST = 0.0625
_FASTEST = 16.0


class SeekReport(Wire):
    """One move of the playhead by hand: where it was and where it went, in milliseconds."""

    from_ms: int = Field(ge=0, le=_MAX_SITTING_MS)
    to_ms: int = Field(ge=0, le=_MAX_SITTING_MS)


class ViewReport(Wire):
    """What one sitting with one file consisted of: facts only; the server draws conclusions."""

    watch_ms: int = Field(default=0, ge=0, le=_MAX_SITTING_MS)
    #: How much of this sitting was already reported, or None for the whole of it; zero is a
    #: reported piece with no time. The view is counted on the piece that crosses the line.
    already_reported_ms: int | None = Field(default=None, ge=0, le=_MAX_SITTING_MS)
    #: Where the playhead was when the sitting ended; the server decides what is stored.
    position_ms: int | None = Field(default=None, ge=0, le=_MAX_SITTING_MS)
    #: The file played to its end, which a repeating clip's position cannot show.
    ended: bool = False
    #: Which sitting this is a piece of, minted by the player and repeated, so pieces are one row.
    sitting: str | None = Field(default=None, max_length=64)
    #: How many times the playhead was moved by hand during this piece.
    seeks: int = Field(default=0, ge=0, le=_MAX_SITTING_MS)
    #: How long each slice was on screen, sparse, keyed by slice index; bounded by the slice count.
    heat: dict[str, int] = Field(default_factory=dict, max_length=HEAT_BUCKETS)
    #: Which screen (`plays.Screen`); None is stored as NULL, never guessed.
    screen: Screen | None = None
    opened_from: OpenedFrom | None = None
    loop: str | None = Field(default=None, max_length=64)
    kept_filter: str | None = Field(default=None, max_length=64)
    theater_session: str | None = Field(default=None, max_length=64)
    #: The id of the one thing the screen behind the panel was about.
    opened_from_id: str | None = Field(default=None, max_length=64, pattern=_AN_ID)
    #: The search's words, used to find its record (`search_events`) and not kept.
    searched: str | None = Field(default=None, max_length=_MOST_SEARCHED)
    start_ms: int | None = Field(default=None, ge=0, le=_MAX_SITTING_MS)
    seek_log: list[SeekReport] | None = Field(default=None, max_length=MOST_SEEKS)
    #: Time at each speed, keyed by rate ("1.5"); unreadable keys are dropped.
    speeds: dict[str, int] | None = Field(default=None, max_length=MOST_SPEEDS)
    fullscreen_ms: int | None = Field(default=None, ge=0, le=_MAX_SITTING_MS)
    #: Every pass played to the end, where `ended` says only that one did.
    completions: int | None = Field(default=None, ge=0, le=_MAX_SITTING_MS)
    magnified: bool | None = None


#: The kinds the column takes; another becomes NULL rather than refusing the sitting.
_KINDS: frozenset[str] = frozenset(get_args(Kind))


def _place_of(
    report: ViewReport, asset: Asset, client: Client, opened_from_id: str | None
) -> Place:
    """Where this sitting happened; the kind and the client are read by the server."""
    kind = cast(Kind, asset.media_type) if asset.media_type in _KINDS else None
    return Place(
        screen=report.screen,
        opened_from=report.opened_from,
        kind=kind,
        loop_id=report.loop,
        kept_filter_id=report.kept_filter,
        theater_session=report.theater_session,
        opened_from_id=opened_from_id,
        device_id=client.device,
        client_kind=client.kind,
    )


#: The latest record of one typed search by one User.
_LATEST_SEARCH = (
    "SELECT id FROM search_events WHERE user_id = ? AND kind = 'query' AND subject = ?"
    " ORDER BY id DESC LIMIT 1"
)


async def _opened_from_id(database: Database, viewer: Viewer, report: ViewReport) -> str | None:
    """The one thing the screen behind the file was about; a search is kept by its record."""
    if report.opened_from != "search":
        return report.opened_from_id
    words = " ".join((report.searched or "").split())
    if not words:
        return None
    row = await database.fetch_one(_LATEST_SEARCH, (viewer.id, words))
    return None if row is None else str(row["id"])


def _inside_of(report: ViewReport) -> Inside:
    """What happened inside this piece, with impossible speeds dropped rather than refused."""
    speeds: dict[float, int] | None = None
    if report.speeds is not None:
        speeds = {}
        for key, spent in report.speeds.items():
            try:
                rate = float(key)
            except ValueError:
                continue
            if _SLOWEST <= rate <= _FASTEST and spent > 0:
                speeds[rate] = speeds.get(rate, 0) + min(int(spent), _MAX_SITTING_MS)
    return Inside(
        start_ms=report.start_ms,
        seek_log=None
        if report.seek_log is None
        else tuple((seek.from_ms, seek.to_ms) for seek in report.seek_log),
        speeds=speeds,
        fullscreen_ms=report.fullscreen_ms,
        completions=report.completions,
        magnified=report.magnified,
    )


@router.post(
    "/assets/{asset_id}/view",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_protect)],
)
async def record_view(
    asset_id: str,
    report: ViewReport,
    request: Request,
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    user_state: Annotated[UserStateStore, Depends(wiring.user_state)],
    database: Annotated[Database, Depends(wiring.database)],
) -> Response:
    """Record one sitting: the time, the place, whether it was a view, and which parts were played.

    The client reports facts; this draws every conclusion. Time, position and replays are kept
    whatever the view; a view counts on the piece that crosses `policy.counts_as_a_view`.
    """
    asset = await _open(access, viewer, asset_id)
    resume_ms = await _resume_where(request, viewer.id, asset, report.position_ms)
    # A view is counted on the crossing, so a sitting in two pieces is one view.
    before = report.already_reported_ms
    earned_already = before is not None and policy.counts_as_a_view(
        asset.media_type, asset.duration_ms, before
    )
    earned_now = policy.counts_as_a_view(
        asset.media_type, asset.duration_ms, (before or 0) + report.watch_ms
    )
    if earned_now and not earned_already:
        await user_state.record_view(
            asset.id,
            viewer.id,
            watch_ms=report.watch_ms,
            resume_ms=resume_ms,
            # Either way of finishing: a repeating clip only says `ended`.
            completed=report.ended
            or policy.watched_to_the_end(asset.duration_ms, report.position_ms),
        )
    else:
        await user_state.record_watch_time(
            asset.id,
            viewer.id,
            watch_ms=report.watch_ms,
            resume_ms=resume_ms,
            # The same two ways of finishing: the end often lands after the view was counted.
            completed=report.ended
            or policy.watched_to_the_end(asset.duration_ms, report.position_ms),
        )

    heat = _heat_from(report.heat)
    if heat:
        await user_state.add_replay_heat(asset.id, viewer.id, heat)

    # The sitting itself, kept whole and last; not while this User has paused their history.
    if not await keeps_history(part_of(request, SETTINGS_HUB), viewer.id):
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    # What the file carried is read only for a sitting's first piece, which alone writes it.
    first = report.sitting is None or not await sitting_is_here(
        database, viewer.id, asset.id, report.sitting
    )
    await record_play(
        database,
        user_id=viewer.id,
        asset_id=asset.id,
        sitting=report.sitting,
        watch_ms=report.watch_ms,
        already_reported_ms=report.already_reported_ms,
        position_ms=report.position_ms,
        seeks=report.seeks,
        heat=heat,
        place=_place_of(
            report,
            asset,
            client_of(request),
            await _opened_from_id(database, viewer, report) if first else None,
        ),
        length_ms=asset.duration_ms,
        inside=_inside_of(report),
        about=await about_of(database, access, viewer, asset.id) if first else None,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _heat_from(reported: dict[str, int]) -> dict[int, int]:
    """The replay map as slice indexes, unreadable entries dropped and values clamped to a day.

    Dropped, not refused: a 422 to a closing page would lose the whole sitting.
    """
    heat: dict[int, int] = {}
    for key, milliseconds in reported.items():
        try:
            bucket = int(key)
        except (TypeError, ValueError):
            continue
        if 0 <= bucket < HEAT_BUCKETS and milliseconds > 0:
            heat[bucket] = min(int(milliseconds), _MAX_SITTING_MS)
    return heat


class ReplayCurve(Wire):
    """How much of each slice of a file this user has watched, as a normalised curve."""

    #: One value per slice, 0 to 1; the tallest is 1.
    heat: list[float]
    buckets: int
    #: Whether the curve says anything: a flat one invites a pattern that is not there.
    worth_drawing: bool


#: How far above the average of played slices a peak must stand to be more than noise.
_HEAT_WORTH_DRAWING = 1.25


@router.get("/assets/{asset_id}/replays")
async def replay_curve(
    asset_id: str,
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    user_state: Annotated[UserStateStore, Depends(wiring.user_state)],
) -> ReplayCurve:
    """The parts of this file this user keeps coming back to, per user."""
    asset = await _open(access, viewer, asset_id)
    raw = await user_state.replay_heat(asset.id, viewer.id)
    return _curve_from(raw)


def _curve_from(raw: list[int]) -> ReplayCurve:
    """Milliseconds per slice as a normalised curve, and whether it says anything."""
    peak = max(raw, default=0)
    if peak <= 0:
        return ReplayCurve(heat=[0.0] * len(raw), buckets=len(raw), worth_drawing=False)

    # Averaged over the played slices, so ten minutes of a film does not inflate every peak.
    played = [one for one in raw if one > 0]
    average = sum(played) / len(played)
    return ReplayCurve(
        heat=[one / peak for one in raw],
        buckets=len(raw),
        worth_drawing=peak >= average * _HEAT_WORTH_DRAWING,
    )


async def _resume_where(
    request: Request, user_id: str, asset: Asset, position_ms: int | None
) -> int | None:
    """Where this user would resume this asset under their two settings; off keeps none."""
    hub = part_of(request, SETTINGS_HUB)
    minimum_ms = await resume_minimum_ms(hub.get_user, user_id)
    return resume_point(asset.duration_ms, position_ms, minimum_ms=minimum_ms)


MAX_TRANSCODE_HEIGHT_KEY = "playback.max_transcode_height"

#: How much disk the converted copies may take up.
CACHE_MAX_GB_KEY = "playback.cache_max_gb"
LOOP_MODE_KEY = "playback.loop_mode"
VOLUME_KEY = "playback.volume"
MUTED_KEY = "playback.muted"
DWELL_PICTURES_KEY = "playback.dwell_pictures"
