# SPDX-License-Identifier: AGPL-3.0-or-later
"""The live feed: one connection per browser, told when what it is drawing has changed.

Two addresses, and they answer the same question in two ways. The socket says "something changed"
as it happens; the plain read says "here is where you stand" and exists for three
reasons together: it is where the shape the socket sends is declared, so the client's types are
generated rather than hand-written; it is what a reconnecting client compares against; and it is
how a client tells a refusal from a dropped connection, because a handshake refused before it
completes has no frame to carry a reason.

**Nothing here says what changed.** A message names a kind of thing and nothing else, and the
client asks the ordinary endpoint again, which goes through the permission layer like every other
read in the application. See `sift.kernel.changes` for why that is the load-bearing decision.

**Nothing here decides who may see anything, either.** An audience is resolved by the write that
moved it, inside that write, and arrives here already decided. This matches it against who is
connected and sends a word.

The socket is the part worth reading carefully, and for the reasons the job feed's header gives:
every control that makes "who may call this" un-forgettable in this app is built around HTTP
requests, and none of them applies to a WebSocket. It is invisible to the route table's
authorization check, the browser will open one across origins with the session attached, and it
stays open long enough that authorizing it once is a promise about the future. Each is handled
below and named where it is handled.
"""

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

#: The close code for "you may not do this". The WebSocket equivalent of a 403: a handshake that
#: is refused has no status code to carry, so the reason travels here.
_POLICY_VIOLATION = status.WS_1008_POLICY_VIOLATION


class LiveState(Wire):
    """Where a user's view stands, and what has just moved it.

    One shape for both addresses on purpose. A shape that only ever travelled down a socket would
    have no generated type (the route table does not describe sockets) so the client would
    hand-write an interface and the drift between the two would be silent.
    """

    #: What has changed since this connection was last spoken to. Empty on the plain read, and
    #: empty is also what an idle beat sends: nothing at all.
    about: list[About]
    #: Where this user's view stands now.
    #:
    #: **Compare it for equality and nothing else.** It is a mark, not a measurement: a client that
    #: subtracted two of them would be reasoning about a mechanism it does not own. What it is for
    #: is one question (did anything move while I was away) and the answer is yes when it
    #: differs from the one you were holding.
    marker: str
    #: This user's own opinions of particular files, written somewhere else and carried here so
    #: that the screens already drawing those files can follow without asking for anything.
    #:
    #: The one thing on this connection that is an answer rather than a question, and it is safe to
    #: be one for a narrow reason: none of it is a permission. It is what this user thinks of a
    #: file, written by this user a moment ago, and it reaches nobody else's connection.
    opinions: list[AssetOpinion] = Field(default_factory=list)
    #: Whether more opinions were written in that second than a message will carry. Setting a rating
    #: across a large selection is one request per file, so this is what a screen reads to know it
    #: should re-read its page instead of applying rows.
    more_opinions: bool = False
    #: Commands from this user's phone for one of their screens, under `REMOTE`. The second thing
    #: on this connection that is an answer rather than a question, for the reason the kernel's
    #: `RemoteCommand` gives. Never on the plain read: a command is delivered once, to the
    #: connections open when it was accepted, and a read is not a delivery.
    commands: list[RemoteCommand] = Field(default_factory=list)


#: What a browser that was away is told to re-read: everything the connection can speak about,
#: except the two subjects that are answers rather than questions.
#:
#: `OPINIONS` is left out because it does not work the way the others do. It carries the rows
#: themselves, so announcing it with nothing attached asks for nothing and repairs nothing, and
#: the repair for a missed opinion is already in the list, because a bulk rating past what a message
#: will hold says so by ringing `ARRIVALS` and the screens re-read their page.
#:
#: `REMOTE` is left out for a stronger reason: announcing it to a browser that was away would be
#: telling it that a command arrived while it was gone, and a command must never be replayed to a
#: tab that connects later. There is nothing to repair; the phone sees the screen did not answer.
#:
#: Built from the enum rather than written out, so a subject added to `About` is repaired without
#: anybody remembering to come here. A list typed out by hand is a list that goes one short exactly
#: once, and the symptom is a screen that is stale only for somebody who was disconnected.
REPAIRED = tuple(about for about in About if about not in {About.OPINIONS, About.REMOTE})


def _marker(bus: ChangeBus) -> str:
    """Where the stream of announcements stands, as something to compare rather than to read.

    Not the user's `cache_stamp`: that counts changes to what a user may SEE and is the cache key
    every thumbnail address carries, so a setting, a job, a download or a saved search never moves
    it. The bus's own count answers "has anything been announced" for every subject in one go. See
    `ChangeBus.mark`.
    """
    return bus.mark


async def _close(websocket: WebSocket, code: int) -> None:
    """End this connection, and treat a client that has already gone as the ordinary end.

    Closing races the tab being shut, and this route closes on a decision the client did not ask
    for: a user disabled, a lock put on, one connection too many. So the client is sometimes
    gone before the close reaches it, and then closing is what raises: `WebSocketDisconnect` when
    the transport reads a close frame, and the server's own low-level `RuntimeError` when it was
    torn down under the call. Neither is a fault; both mean the thing this was about to do is
    already done. Left to propagate they are an error logged for every tab shut at the wrong
    moment, on a connection every screen in the application holds open.
    """
    try:
        await websocket.close(code=code)
    except (WebSocketDisconnect, RuntimeError):
        return


@router.get("")
async def live_state(
    request: Request,
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> LiveState:
    """Where this user's view stands right now.

    Read once when a screen loads, so the socket that opens a moment later can say where it was
    starting from, without which a change landing between the two would be missed by both. Read
    again when a socket closes for a reason it could not carry, because this route sits behind the
    same session check the socket does: an answer means the connection was dropped, and a refusal
    means it was refused.

    The viewer is resolved even though the mark does not come from it, and that is the point of
    the parameter: this route has to sit behind the same session check the socket does, or it
    cannot tell a refusal from a dropped connection.

    Answers **429** when this user already holds as many live connections as it may, with a
    `Retry-After`. That is the same limit the socket is refused on, said where a client can read it.
    """
    # The connection cap is answered here as 429. A handshake refused before `accept` has no frame
    # to carry a reason (the browser reports 1006), so the client asks this route which refusal it
    # was: 401/403 for the session, 429 for a user already at MAX_CONNECTIONS_PER_USER. A screen
    # reads this before opening a socket, so a client over the cap finds out without opening one.
    # The cap does not get its own close code: accepting a connection in order to refuse it would
    # defeat a control whose job is to stop a client opening hundreds of them. (Kept out of the
    # docstring, which is the route's published API description.)
    bus = part_of(request, CHANGES)
    if bus.open_for(viewer.id) >= tuning.MAX_CONNECTIONS_PER_USER:
        # `Retry-After` is written for anything that is not Sift's own client: ours carries its
        # own wait, because a header the client ignores is a second declaration of one number.
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
    """Tell this browser when what it is drawing has changed, for as long as it is open.

    **Re-checked on every beat, not once at the handshake.** Everywhere else in Sift a permission is
    resolved from the database on every single request, so that revoking something denies the very
    next one and disabling a user ends its session immediately. A socket authorized only when it
    opened would be the one place that promise is false: it would go on telling a user about
    a library it was removed from an hour ago, for as long as the tab stayed open.

    `since` is the mark the client last held, and it is the only thing this route accepts. It
    chooses nothing and selects nothing: whatever it says, the reply is the same word or no word at
    all. That is what keeps this from being a second copy of a list endpoint the authorization gate
    cannot see, which is the reason the job feed takes no parameters at all.
    """
    settings = part_of(websocket, SETTINGS)
    if not origin_is_allowed(
        websocket.headers.get("origin"),
        websocket.headers.get("host"),
        tuple(settings.cors_origins),
    ):
        # Refused before `accept`, so no socket is ever established. Closing after accepting would
        # mean the page on the other end had a live connection, however briefly.
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
        # Refused for the same reason and with the same code as any other refusal: the client asks
        # the plain read what its standing is and is told, rather than retrying forever.
        log.warning("live.stream_refused_too_many", open_for_user=bus.open_for(viewer.id))
        await _close(websocket, _POLICY_VIOLATION)
        return

    since = websocket.query_params.get("since")
    await websocket.accept()
    subscription = bus.subscribe(viewer.id)
    try:
        # The ordinary end (the tab closed) reaches this as a send that could not be made, and
        # the beat stops on it. Nothing in the loop raises a disconnect of its own: the send catches
        # both shapes it can arrive as, and so does every close.
        await _feed(websocket, subscription, bus, since=since)
    finally:
        # Given back whatever ended it. A connection left in the bus is one the process goes on
        # collecting announcements for, one per closed tab, for as long as it runs.
        bus.release(subscription)


async def _feed(
    websocket: WebSocket, subscription: Subscription, bus: ChangeBus, *, since: str | None
) -> None:
    """The beat. Send what has gathered, and re-read who this is, until one of them says stop.

    The FIRST pass repairs whatever was missed while this browser was away, and does it by
    comparison rather than by replay: a mark that has not moved means nothing happened, and a
    connection that has just loaded its page holds no mark at all and is already current. That is
    what stops a flapping connection re-reading its screens every few seconds, and it is what closes
    the window between a change being stored and its announcement being published: a process that
    stopped in between loses the message and not the change, and the next handshake finds a mark
    that has moved.

    **ONLY the first pass, and that is load-bearing rather than tidy.** A mark that moves whenever
    anything is announced makes "it differs from a beat ago" the
    ordinary state during any activity at all, so a repeated comparison would repair on every
    beat, for every connection, and every screen in the application would re-read once a second for
    as long as anything was happening. That is a poll, which is the thing this whole slice exists
    instead of. After the handshake has been reconciled there is nothing left for a comparison to
    find: from then on a change reaches this connection because it was published to it.
    """
    while True:
        found = await viewer_on(websocket)
        if found.locked or found.viewer is None:
            # Disabled, signed out, locked, or the session expired while this was open.
            log.info("live.stream_closed_no_longer_allowed")
            await _close(websocket, _POLICY_VIOLATION)
            return

        marker = _marker(bus)
        # The user is re-read on every beat regardless, so this is the freshest answer there is
        # to "may this connection hear that", which is why the subjects only an admin may hear are
        # dropped here rather than when they were published. Somebody demoted a second ago stops
        # hearing about the work queue on the very next beat.
        pending = subscription.take(as_admin=found.viewer.is_admin)
        waiting = list(pending.about)
        # Something moved while this browser was away, or between its page loading and this socket
        # opening. Which of the two it was does not matter and cannot be known, so everything this
        # connection can be told about is re-asked, not one subject.
        #
        # No filtering by role here, deliberately. A message is a question and never an answer: each
        # bell makes the screens that are drawing that kind of thing re-read through the ordinary
        # door, where the permission check lives. A guest handed the work-queue bell has no work
        # queue on screen to re-read it, and would be refused at that door if they did.
        if since is not None and since != marker:
            waiting = sorted({*waiting, *REPAIRED})
        # Cleared rather than carried forward: this is a handshake reconciliation, not a running
        # comparison. See the note above about what a repeated one would cost.
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
    """Wait out one beat, and say whether there is still anybody on the other end.

    A plain sleep cannot tell a browser that is there from one that has gone: this loop only sends,
    and only when something is announced, so on a quiet connection a closed tab would hold its
    place (and its share of the per-user cap) indefinitely. Reading is the only way to know: a
    server task is not cancelled when its client disconnects; the disconnect arrives as a message.
    The test client cancels the task on exit, which is why only a real server shows the difference.

    **Any frame at all ends the connection, not only a disconnect.** This route accepts one thing,
    `since`, and it arrives in the address, so a client that sends frames is not this application's
    and the connection is worth no more than an ordinary close, which the client already treats as
    "come back and ask where things stand". Reading in a loop and dropping non-disconnect frames
    would not work: an already-queued frame returns without awaiting, so a client sending steadily
    would keep the loop spinning inside its own timeout and the beat would never end.

    **`wake` ends the beat early**, and it is how a command from the phone reaches its screen
    without waiting up to a second for the next look: the bus sets it when a command arrives. The
    rest of the beat is unchanged by it. The user is re-read before anything is sent, so an early
    beat is still an authorized one, and what it costs is bounded by the rate limit on the route
    that accepts commands.
    """
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
        # A frame, or the disconnect itself: either way this connection is finished. Read so a
        # failed receive raises here rather than being left unretrieved.
        received.result()
        return False
    return True


async def _send(websocket: WebSocket, state: LiveState) -> bool:
    """Push one message, or say that this connection is finished.

    A send that cannot complete inside one beat closes the connection, and that is a rule rather
    than a timeout that happens to be convenient: a reader that cannot take one message before the
    next is due is not going to catch up, and the loop waiting on it is one task per connection,
    so without this a frozen tab holds its own beat open for as long as the operating system keeps
    it frozen. The client treats a close as "come back and ask where things stand", so the recovery
    path is the one that is already built.
    """
    try:
        async with asyncio.timeout(tuning.SEND_DEADLINE_SECONDS):
            await websocket.send_json(state.model_dump(mode="json"))
    except TimeoutError:
        log.info("live.stream_closed_too_slow")
        await _close(websocket, status.WS_1013_TRY_AGAIN_LATER)
        return False
    except (WebSocketDisconnect, RuntimeError):
        # The client vanished mid-push: a closed tab, a dropped network. Starlette raises
        # WebSocketDisconnect when it reads the close frame, but a transport torn down under an
        # in-flight send surfaces the server's own low-level RuntimeError instead ("operation on a
        # closed transport"). Both mean there is no one left to send to.
        return False
    return True
