# SPDX-License-Identifier: AGPL-3.0-or-later
"""The live feed: one connection per browser, told which kinds of thing changed, never what."""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, status
from fastapi.websockets import WebSocketDisconnect
from pydantic import Field

from sift.kernel.access import Viewer
from sift.kernel.changes import About, AssetOpinion, ChangeBus, RemoteCommand, Subscription
from sift.kernel.http import origin_is_allowed
from sift.kernel.log import get_logger
from sift.kernel.wire import Wire
from sift.kernel.wiring import CHANGES, SETTINGS, part_of
from sift.slices.auth import current_viewer, viewer_on
from sift.slices.live import tuning

log = get_logger(__name__)

router = APIRouter(prefix="/live", tags=["live"])

#: A refused handshake has no status code, so the 403 travels as this close code.
_POLICY_VIOLATION = status.WS_1008_POLICY_VIOLATION


class LiveState(Wire):
    """Where a user's view stands; one shape for both addresses, so its type is generated."""

    about: list[About]
    #: Compare for equality only: it is a mark, not a measurement.
    marker: str
    #: Safe as an answer because none of it is a permission, and it reaches only this user.
    opinions: list[AssetOpinion] = Field(default_factory=list)
    #: More than a message carries: the screen re-reads its page instead of applying rows.
    more_opinions: bool = False
    #: Never on the plain read: a command is delivered once, and a read is not a delivery.
    commands: list[RemoteCommand] = Field(default_factory=list)


#: What a browser that was away re-reads. Opinions carry their rows, so an empty one repairs
#: nothing; a command must never be replayed to a tab that connects later.
REPAIRED = tuple(about for about in About if about not in {About.OPINIONS, About.REMOTE})


def _marker(bus: ChangeBus) -> str:
    """The bus's mark, not `cache_stamp`, which a setting or a job never moves."""
    return bus.mark


async def _close(websocket: WebSocket, code: int) -> None:
    """End this connection; a client already gone is the ordinary end, not a fault."""
    try:
        await websocket.close(code=code)
    except (WebSocketDisconnect, RuntimeError):
        return


@router.get("")
async def live_state(
    request: Request,
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> LiveState:
    """Where this user's view stands right now; 429 when at the live connection limit."""
    # A refused handshake carries no reason, so the client asks here which refusal it was.
    bus = part_of(request, CHANGES)
    if bus.open_for(viewer.id) >= tuning.MAX_CONNECTIONS_PER_USER:
        log.warning("live.state_refused_too_many", open_for_user=bus.open_for(viewer.id))
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "too many live connections are open for this user",
            headers={"Retry-After": str(tuning.FULL_AGAIN_SECONDS)},
        )
    return LiveState(
        about=[],
        marker=_marker(bus),
        opinions=[],
        more_opinions=False,
    )


@router.websocket("/stream")
async def stream_changes(websocket: WebSocket) -> None:
    """Tell this browser when what it is drawing has changed, re-checking the user on every beat."""
    settings = part_of(websocket, SETTINGS)
    if not origin_is_allowed(
        websocket.headers.get("origin"),
        websocket.headers.get("host"),
        tuple(settings.cors_origins),
    ):
        # Before `accept`, so the page never holds a live connection, however briefly.
        log.warning("live.stream_rejected_origin")
        await _close(websocket, _POLICY_VIOLATION)
        return

    found = await viewer_on(websocket)
    if found.locked or found.viewer is None:
        await _close(websocket, _POLICY_VIOLATION)
        return
    viewer = found.viewer

    bus = part_of(websocket, CHANGES)
    if bus.open_for(viewer.id) >= tuning.MAX_CONNECTIONS_PER_USER:
        log.warning("live.stream_refused_too_many", open_for_user=bus.open_for(viewer.id))
        await _close(websocket, _POLICY_VIOLATION)
        return

    since = websocket.query_params.get("since")
    await websocket.accept()
    subscription = bus.subscribe(viewer.id)
    try:
        await _feed(websocket, subscription, bus, since=since)
    finally:
        bus.release(subscription)


async def _feed(
    websocket: WebSocket, subscription: Subscription, bus: ChangeBus, *, since: str | None
) -> None:
    """The beat: send what has gathered and re-read who this is, until one of them says stop."""
    while True:
        found = await viewer_on(websocket)
        if found.locked or found.viewer is None:
            log.info("live.stream_closed_no_longer_allowed")
            await _close(websocket, _POLICY_VIOLATION)
            return

        marker = _marker(bus)
        # Filtered here, not at publish, so a user demoted a second ago stops hearing the next beat.
        pending = subscription.take(as_admin=found.viewer.is_admin)
        waiting = list(pending.about)
        # A message is a question, never an answer, so the repair needs no role filter.
        if since is not None and since != marker:
            waiting = sorted({*waiting, *REPAIRED})
        # Only the first pass compares: a mark moves on every announcement, so a repeat would poll.
        since = None

        if waiting:
            message = LiveState(
                about=waiting,
                marker=marker,
                opinions=list(pending.opinions),
                more_opinions=pending.more_opinions,
                commands=list(pending.commands),
            )
            if not await _send(websocket, message):
                return
        if not await _rest(websocket, subscription.wake):
            return


async def _rest(websocket: WebSocket, wake: asyncio.Event) -> bool:
    """Wait out one beat, or until `wake`; False when the client sent any frame or has gone."""
    # Reading is the only way to see a quiet tab close; our client never sends a frame.
    received = asyncio.ensure_future(websocket.receive())
    woken = asyncio.ensure_future(wake.wait())
    try:
        done, _ = await asyncio.wait(
            {received, woken}, timeout=tuning.BEAT_SECONDS, return_when=asyncio.FIRST_COMPLETED
        )
    finally:
        for waiting in (received, woken):
            if not waiting.done():
                waiting.cancel()
    if received in done:
        # Read so a failed receive raises here rather than being left unretrieved.
        received.result()
        return False
    return True


async def _send(websocket: WebSocket, state: LiveState) -> bool:
    """Push one message; False when the client is gone or too slow to take it inside a beat."""
    try:
        async with asyncio.timeout(tuning.SEND_DEADLINE_SECONDS):
            await websocket.send_json(state.model_dump(mode="json"))
    except TimeoutError:
        log.info("live.stream_closed_too_slow")
        await _close(websocket, status.WS_1013_TRY_AGAIN_LATER)
        return False
    except (WebSocketDisconnect, RuntimeError):
        # A transport torn down mid-send raises RuntimeError rather than a disconnect.
        return False
    return True
