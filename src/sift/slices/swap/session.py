# SPDX-License-Identifier: AGPL-3.0-or-later
"""A swap session, from the press to its end, on both sides: the frames, the hello and the code,
the offer and the diff, the streams, the watchdog, and the task that holds it all.

## How a session goes

The HOST presses Start: the tunnel is restarted with a listener (`Hoster.host_on`), a token is made
(`token.mint`) and shown, and the session task is queued. The GUEST pastes the token and presses
Join: its own tunnel carries a dial to the host's VPN address, and the connection is locked with the
token's secret (`lock.py`). Each side then sends a hello (its device id, its public key, a fresh
nonce, and a signature binding the key to THIS session's secret) and both screens show the same
six characters, the code (`code_of`). The host compares them with the guest by whatever channel the
token went through, and presses They match: only then is the offer sent. The guest says what it
wants (the diff), and the host sends it, file by file, across several streams.

    waiting -> connected -> offered -> transferring -> done | ended | failed

Any live step can be CUT OFF by a tunnel, and joined again from the same step (`live.py`).

## What is written down, and what is not

The row holds the state, the counts, the peer's device id, the rate and the reason it ended. The
token, its secret and the peer's address are held in memory by this module and nowhere else, so a
session cannot outlive the process: `SwapSessions.settle_after_restart` ends every session a
restart interrupted. A log line names a session by its short id, its counts and its reason, never
an address.

## What the code is for

Anybody who can read the chat the token was pasted into can try it before the guest does, and the
first to connect wins (the token works once). The code is derived from the connection (the secret,
both device ids and both nonces), so an interloper's code differs from the one the real guest reads
out, and the host's They don't match ends it before a single file is named.

## One use

The host takes ONE hello from a new device. A second one (a copy of the token tried later by
somebody else) is refused, whoever sends it. The one exception is the guest the session is already
with: its hello is signed by the device key the first hello proved, over this session's secret, so
the same device joining again is the same session carrying on (see "Cut off"). Streams are not
hellos: each is its own locked connection with the same secret, naming the session in its first
frame, and is accepted only while the session is transferring.

## The watchdog, and why it listens rather than waits

With the host's tunnel killed mid-transfer, the SENDER's own connection can take many seconds to
notice, while the receiver's acknowledgements stop immediately. So neither side waits for its transport
to fail. Every frame from the other side (a chunk, an acknowledgement, the ping each side sends
every two seconds) is heard, and ten seconds of silence cuts the session off (see "Cut off"). It is armed from the hello onward, not only during the transfer: a session waiting at the
code is just as gone when the tunnel is, and a person looking at the code screen should be told.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from sift.kernel.access import Viewer
from sift.kernel.ids import new_id
from sift.kernel.jobs import JobContext, register_handler
from sift.kernel.jobs.families import OwnEstimate, estimate_itself
from sift.kernel.jobs.tuning import WAITED_ON_PRIORITY
from sift.kernel.log import get_logger
from sift.kernel.secret_store import SecretStore
from sift.kernel.tunnels import TunnelError
from sift.kernel.wiring import Part
from sift.slices.swap import lock
from sift.slices.swap.device import (
    DeviceLocked,
    device_of,
    ensure_device,
)
from sift.slices.swap.frames import Chunk as Chunk
from sift.slices.swap.frames import Conn as Conn
from sift.slices.swap.frames import ProtocolError as ProtocolError
from sift.slices.swap.guest import (
    GuestSession,
    _BackSending,
)
from sift.slices.swap.handshake import (
    _STATE_OF as _STATE_OF,
)
from sift.slices.swap.handshake import (
    CHOOSE_FIRST,
    ENDED_BY_YOU,
    EXIT_WAIT_SECONDS,
    FOLDER_FIELD,
    HELLO_WAIT_SECONDS,
    LOCKED,
    LOST,
    NO_FOLDER,
    NO_TUNNEL,
    PASTED_FIELD,
    PING_SECONDS,
    RATE_WINDOW_SECONDS,
    REFUSED,
    RETRY_SECONDS,
    SAME_SERVER,
    SWAP_SESSION,
    TUNNEL_FIELD,
    WAITING_NOTE,
    WATCHDOG_SECONDS,
    SwapRefused,
)
from sift.slices.swap.handshake import (
    CUT_OFF_NOTE as CUT_OFF_NOTE,
)
from sift.slices.swap.handshake import (
    ENDED_BY_THEM as ENDED_BY_THEM,
)
from sift.slices.swap.handshake import (
    EXPIRED as EXPIRED,
)
from sift.slices.swap.handshake import (
    OLDER as OLDER,
)
from sift.slices.swap.handshake import (
    PACE_WINDOWS as PACE_WINDOWS,
)
from sift.slices.swap.handshake import (
    REASON_DONE as REASON_DONE,
)
from sift.slices.swap.handshake import (
    REASONS as REASONS,
)
from sift.slices.swap.handshake import (
    REDIAL_LIMIT as REDIAL_LIMIT,
)
from sift.slices.swap.handshake import (
    STREAMS_CAP as STREAMS_CAP,
)
from sift.slices.swap.handshake import (
    STREAMS_START as STREAMS_START,
)
from sift.slices.swap.handshake import (
    USED as USED,
)
from sift.slices.swap.handshake import (
    WRONG_DEVICE as WRONG_DEVICE,
)
from sift.slices.swap.handshake import (
    code_of as code_of,
)
from sift.slices.swap.handshake import (
    hello as hello,
)
from sift.slices.swap.handshake import (
    read_hello as read_hello,
)
from sift.slices.swap.host import (
    HostSession,
)
from sift.slices.swap.live import (
    Assess,
    Egress,
    ExitOf,
    FaceModel,
    Hoster,
    Hosting,
    Jobs,
    Land,
    LandFingerprints,
    MakeOffer,
    PathOf,
    Prepare,
    ReadRoute,
    ServerOf,
    Taken,
    Weigh,
    _Live,
    _no_exit,
    _no_fingerprints,
    _no_model,
    _no_server,
    _not_wired,
    _read_within,
    _remove,
)
from sift.slices.swap.live import (
    Ladder as Ladder,
)
from sift.slices.swap.live import (
    Watchdog as Watchdog,
)
from sift.slices.swap.models import Chosen, OfferScreen
from sift.slices.swap.receiving import (
    _Receiving,
)
from sift.slices.swap.store import SessionRow, SessionStore
from sift.slices.swap.token import Token, TokenRefused, mint, parse, server_or_none

__all__ = [
    "CUT_OFF_NOTE",
    "ENDED_BY_THEM",
    "ENDED_BY_YOU",
    "EXPIRED",
    "FOLDER_FIELD",
    "LOCKED",
    "LOST",
    "NO_FOLDER",
    "NO_TUNNEL",
    "OLDER",
    "PACE_WINDOWS",
    "REASONS",
    "REASON_DONE",
    "REDIAL_LIMIT",
    "REFUSED",
    "SAME_SERVER",
    "SESSIONS",
    "STREAMS_CAP",
    "STREAMS_START",
    "SWAP_SESSION",
    "TUNNEL_FIELD",
    "USED",
    "WAITING_NOTE",
    "WRONG_DEVICE",
    "_STATE_OF",
    "Chunk",
    "Conn",
    "GuestSession",
    "HostSession",
    "Ladder",
    "LiveFacts",
    "ProtocolError",
    "Started",
    "SwapRefused",
    "SwapSessions",
    "Taken",
    "Watchdog",
    "code_of",
    "hello",
    "read_hello",
    "register_handlers",
]

log = get_logger(__name__)

# --- the sessions, as a service ----------------------------------------------------------------

#: How long an unfinished file's manifest and its staged chunks are kept for a later swap with
#: the same device to resume.
MANIFEST_KEPT_SECONDS = 7 * 24 * 60 * 60


@dataclass(frozen=True, slots=True)
class Started:
    session_id: str
    token: Token


@dataclass(frozen=True, slots=True)
class LiveFacts:
    """What only memory knows about a running session: for the screen, never for a row."""

    code: str | None
    token: str | None
    wanted_bytes: int
    moved_bytes: int
    #: The session's pace (`_Live.pace_bps`), the rate its estimate reads.
    rate_bps: int | None
    #: The offer screen of what the other side offered, once its offer has arrived and been read:
    #: the guest's, and the host's too in a swap that sends and receives.
    screen: OfferScreen | None
    unwanted: int
    #: Whether files go both ways, and whether this side has answered the other's offer.
    two_way: bool = False
    answered: bool = False
    #: The direction from the guest to the host, in a swap that sends and receives: what was
    #: wanted, what has moved, its pace, and whether its files are moving yet.
    back_wanted_bytes: int = 0
    back_moved_bytes: int = 0
    back_rate_bps: int | None = None
    back_moving: bool = False
    #: Whether the files from the host to the guest are moving yet.
    moving: bool = False
    #: The files RECEIVED so far, each counted when its last piece is in and the whole file's digest
    #: matches (`_Receiving.verified`): on the guest, from the host; with `back_`, on the host of a
    #: swap that sends and receives, from the guest. Landing (the strip, the gate, the import, the
    #: filing) comes after, one file at a time, and the row counts a file only once it is filed, so
    #: the receiver's screen says both and the two meet at the end.
    received_files: int = 0
    back_received_files: int = 0


class SwapSessions:
    """Every live session on this device, the presses that start and steer them, and the task.

    One per application. A route starts or joins; the task (`run`) is what holds a session for its
    life; the registry here is how a route reaches the session its task is running.
    """

    def __init__(
        self,
        store: SessionStore,
        secrets: SecretStore,
        *,
        jobs: Jobs,
        hoster: Hoster,
        egress: Egress,
        read_route: ReadRoute,
        staging: Path,
        prepare: Prepare,
        make_offer: MakeOffer | None = None,
        assess: Assess | None = None,
        land: Land | None = None,
        land_fingerprints: LandFingerprints | None = None,
        face_model: FaceModel | None = None,
        exit_of: ExitOf | None = None,
        server_of: ServerOf | None = None,
        path_of: PathOf | None = None,
        weigh: Weigh | None = None,
        workdir: Callable[[str], Path] | None = None,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], float] = time.time,
        watchdog_seconds: float = WATCHDOG_SECONDS,
        ping_seconds: float = PING_SECONDS,
        rate_window: float = RATE_WINDOW_SECONDS,
        hello_wait: float = HELLO_WAIT_SECONDS,
        retry_seconds: float = RETRY_SECONDS,
    ) -> None:
        self.store = store
        self.secrets = secrets
        self.jobs = jobs
        self.hoster = hoster
        self.egress = egress
        self.read_route = read_route
        self.staging = staging
        self.prepare = prepare
        self.make_offer: MakeOffer = make_offer or _not_wired("the offer")
        self.assess: Assess = assess or _not_wired("the diff")
        self.land: Land = land or _not_wired("the landing")
        self.land_fingerprints: LandFingerprints = land_fingerprints or _no_fingerprints
        self.face_model: FaceModel = face_model or _no_model
        self.exit_of: ExitOf = exit_of or _no_exit
        self.server_of: ServerOf = server_of or _no_server
        self.path_of: PathOf = path_of or _not_wired("the host's files")
        self.weigh: Weigh = weigh or _not_wired("the weighing")
        self._workdir = workdir
        self.clock = clock
        self._now = now
        self.watchdog_seconds = watchdog_seconds
        self.ping_seconds = ping_seconds
        self.rate_window = rate_window
        self.hello_wait = hello_wait
        self.retry_seconds = retry_seconds
        self._live: dict[str, _Live] = {}
        self._by_job: dict[str, str] = {}

    def now(self) -> int:
        return int(self._now())

    def forget(self, live: _Live) -> None:
        self._live.pop(live.id, None)
        if live.job_id is not None:
            self._by_job.pop(live.job_id, None)

    def live(self, session_id: str) -> _Live | None:
        return self._live.get(session_id)

    def any_live(self) -> bool:
        return bool(self._live)

    def workdir_of(self, live: _Live) -> Path:
        """Where the host's stripped copies go: the task's own workspace, swept when it settles."""
        if live.context is not None:
            with contextlib.suppress(RuntimeError):
                return live.context.workspace
        if self._workdir is not None:
            return self._workdir(live.id)
        return self.staging / "outgoing" / live.id

    def staged_is_ours(self, path: str | None) -> bool:
        """Whether a manifest's staged path is under this device's staging folder. A row's path is
        only ever one this module wrote, and this is what keeps it so."""
        if not path:
            return False
        try:
            return Path(path).resolve().is_relative_to(self.staging.resolve())
        except (OSError, ValueError):
            return False

    # --- the presses ---------------------------------------------------------------------------

    async def start(
        self,
        *,
        viewer: Viewer,
        chosen: Sequence[Chosen],
        tunnel_id: str,
        share_boxes: bool,
        master_key: bytes | None,
        two_way: bool = False,
        dest_folder_id: str | None = None,
    ) -> Started:
        """Host: restart the tunnel with a listener, make the token, queue the task. With
        `two_way`, a swap that sends and receives, whose received files go in `dest_folder_id`."""
        if not lock.PSK_AVAILABLE:
            raise SwapRefused(lock.NOT_HERE)
        if master_key is None:
            raise SwapRefused(LOCKED)
        if two_way and not dest_folder_id:
            raise SwapRefused(NO_FOLDER, field=FOLDER_FIELD)
        # With the key in hand the id is there or is made: only a missing key refuses, and that
        # was answered above.
        device = await ensure_device(self.store, self.secrets, master_key)
        session_id = new_id()
        live = HostSession(
            self,
            session_id,
            os.urandom(32),
            tunnel_id=tunnel_id,
            viewer=viewer,
            chosen=chosen,
            share_boxes=share_boxes,
            two_way=two_way,
            dest_folder_id=dest_folder_id,
        )
        # The listener is on loopback only, on a port the system chooses. The tunnel's listener
        # section targets it; nothing else can reach it.
        live.server = await asyncio.start_server(live.on_connection, "127.0.0.1", 0)
        port = live.port = int(live.server.sockets[0].getsockname()[1])
        try:
            hosting = await self.hoster.host_on(
                tunnel_id,
                target_port=port,
                master_key=master_key,
                on_moved=live.hosting_moved,
                on_lost=live.hosting_lost,
            )
        except TunnelError as error:
            # The tunnel manager's words are the screen's ("This tunnel's provider did not give it
            # a port..."), and they name no address.
            await live.teardown()
            raise SwapRefused(str(error)) from None
        except BaseException:
            await live.teardown()
            raise
        live.hosting = True
        try:
            token, job_id = await self._mint_and_queue(
                live,
                device,
                hosting,
                viewer,
                chosen,
                two_way=two_way,
                dest_folder_id=dest_folder_id,
            )
        except BaseException:
            await live.end(LOST, tell=False)
            raise
        live.job_id = job_id
        self._by_job[job_id] = session_id
        # The row's id beside the short one, so a watcher reading the log can ask the session's view
        # for its progress; the short id alone opens nothing. Not named `session`: the redaction
        # blanks that word as a credential.
        log.info("swap.started", swap=live.short_id, swap_row=live.id)
        return Started(session_id, token)

    async def _mint_and_queue(
        self,
        live: HostSession,
        device: str,
        hosting: Hosting,
        viewer: Viewer,
        chosen: Sequence[Chosen],
        *,
        two_way: bool,
        dest_folder_id: str | None,
    ) -> tuple[Token, str]:
        """The host's token, its row and its task, once the tunnel hosts the listener."""
        session_id, tunnel_id = live.id, live.tunnel_id
        # The server the guest compares with its own (`_same_server`). Unread, the token says
        # it does not know, and the guest compares exits alone.
        server = await _read_within(self.server_of, tunnel_id)
        try:
            token = mint(
                hosting.public_ipv4,
                hosting.external_port,
                device,
                self.now(),
                secret=live.secret,
                server=server,
            )
        except (TokenRefused, ValueError) as error:
            raise SwapRefused(
                "This tunnel's provider didn't give it a public address, so a swap can't "
                "reach it. Choose a tunnel made on a P2P VPN server with port forwarding on."
            ) from error
        live.token = token
        await self.store.create(
            session_id,
            role="host",
            started_at=self.now(),
            started_by=viewer.id,
            tunnel_id=tunnel_id,
            token_expires=token.expires,
            chosen=[one.model_dump(mode="json") for one in chosen],
            dest_folder_id=dest_folder_id if two_way else None,
            two_way=two_way,
        )
        self._live[session_id] = live
        job_id = await self.jobs.enqueue(
            SWAP_SESSION,
            {"session_id": session_id},
            priority=WAITED_ON_PRIORITY,
            max_attempts=1,
            requested_by=viewer.id,
        )
        return token, job_id

    async def join(
        self,
        *,
        viewer_id: str,
        token_text: str,
        dest_folder_id: str,
        master_key: bytes | None,
        tunnel_id: str | None = None,
    ) -> str:
        """Guest: read the token, check this side has a tunnel, queue the task.

        The tunnel is the one chosen beside Join (`tunnel_id`), else the one chosen last time
        (`read_route`, the `swap.guest_tunnel` setting): never the downloads' route. A token this
        device is already swapping on is that session joined again, not a second one. A token
        naming the server this device's tunnel connects to, or the address it leaves from, is
        refused before anything dials (`SAME_SERVER`)."""
        if not lock.PSK_AVAILABLE:
            raise SwapRefused(lock.NOT_HERE)
        if master_key is None:
            raise SwapRefused(LOCKED)
        try:
            token = parse(token_text, self.now())
        except TokenRefused as error:
            raise SwapRefused(str(error), field=PASTED_FIELD) from None
        for live in self._live.values():
            if isinstance(live, GuestSession) and hmac.compare_digest(live.secret, token.secret):
                live.join_again(token)
                log.info("swap.joined_again", swap=live.short_id, swap_row=live.id)
                return live.id
        route = tunnel_id or await self.read_route()
        if route is None or route == "direct":
            raise SwapRefused(NO_TUNNEL, field=TUNNEL_FIELD)
        # With the key in hand the id is there or is made: only a missing key refuses, and that
        # was answered above.
        device = await ensure_device(self.store, self.secrets, master_key)
        if token.host_device == device:
            raise SwapRefused(
                "This token was made on this device. Send it to the person you are swapping with.",
                field=PASTED_FIELD,
            )
        if await self._same_server(route, token):
            log.info("swap.same_server")
            raise SwapRefused(SAME_SERVER, field=TUNNEL_FIELD)
        session_id = new_id()
        live = GuestSession(self, session_id, token, route=route, dest_folder_id=dest_folder_id)
        await self.store.create(
            session_id,
            role="guest",
            started_at=self.now(),
            started_by=viewer_id,
            peer_device=token.host_device,
            token_expires=token.expires,
            dest_folder_id=dest_folder_id,
        )
        self._live[session_id] = live
        try:
            job_id = await self.jobs.enqueue(
                SWAP_SESSION,
                {"session_id": session_id},
                priority=WAITED_ON_PRIORITY,
                max_attempts=1,
                requested_by=viewer_id,
            )
        except BaseException:
            await live.end(LOST, tell=False)
            raise
        live.job_id = job_id
        self._by_job[job_id] = session_id
        log.info("swap.joined", swap=live.short_id, swap_row=live.id)
        return session_id

    async def _same_server(self, route: str, token: Token) -> bool:
        """Whether the guest's own tunnel is on the host's VPN server. Unknown (no answer in time,
        or none at all) is not the same: the join goes ahead.

        The server first: two configurations for one server leave from different exits, and a
        dial between them is answered by the server all the same. The exit after it, for a token
        whose server is unknown and for the same configuration on both sides. Both reads share
        one wait (`EXIT_WAIT_SECONDS`), the tunnel's start included."""
        try:
            return await asyncio.wait_for(self._on_their_server(route, token), EXIT_WAIT_SECONDS)
        except (TimeoutError, TunnelError, OSError):
            return False

    async def _on_their_server(self, route: str, token: Token) -> bool:
        if token.server is not None:
            try:
                server = server_or_none(await self.server_of(route))
            except (TunnelError, OSError):
                server = None
            if server is not None and server == token.server:
                return True
        exit_address = await self.exit_of(route)
        return exit_address is not None and exit_address == token.address

    async def answer_code(
        self,
        session_id: str,
        match: bool,
        *,
        viewer: Viewer | None = None,
        chosen: Sequence[Chosen] = (),
        share_boxes: bool = True,
    ) -> None:
        """They match / They don't match. On the host, a match releases the offer. On the guest
        of a swap that sends and receives, it releases the guest's own offer too: what `chosen`
        names, read as `viewer` with Hidden open (`offer.swap_reader`)."""
        live = self._live.get(session_id)
        if live is None or live.code is None:
            raise SwapRefused("There's no code to compare yet.")
        if not match:
            await live.end(REFUSED)
            return
        if isinstance(live, HostSession):
            live.settle(live.code_matched, None)
            return
        if isinstance(live.other_way, _BackSending):
            if not chosen or viewer is None:
                raise SwapRefused(CHOOSE_FIRST)
            live.other_way.choose(viewer, chosen, share_boxes)

    async def take(self, session_id: str, taken: Taken) -> None:
        """The answer to the offer screen (Take / Skip per person, and any unticked file): the
        guest's, and the host's in a swap that sends and receives. Makes the diff and starts the
        transfer."""
        live = self._live.get(session_id)
        receiving = None if live is None else live.receiving_half()
        if receiving is None or receiving.offer is None:
            raise SwapRefused("There's no offer to answer yet.")
        _Live.settle(receiving.taken, taken)

    async def end(self, session_id: str) -> None:
        """End the swap, from either side, at any point."""
        live = self._live.get(session_id)
        if live is not None:
            # The task's own drive returns once the session has ended, so the task settles as
            # finished rather than cancelled; one still waiting for a worker finds the row ended
            # when it runs and returns immediately.
            await live.end(ENDED_BY_YOU)
            return
        await self.store.end(session_id, state="ended", reason=ENDED_BY_YOU, now=self.now())

    def weigh_answer(self, session_id: str, taken: Taken) -> tuple[int, int]:
        """Guest: what this answer to the offer would bring, files and bytes, by the same rule Take
        these applies (skipping a person drops their files unless somebody taken is on them too),
        before it is pressed."""
        live = self._live.get(session_id)
        receiving = None if live is None else live.receiving_half()
        if receiving is None or receiving.offer is None or receiving.answer is None:
            raise SwapRefused("There's no offer to answer yet.")
        sizes = {one.key: one.size for one in receiving.offer.files}
        wanted = [key for key in dict.fromkeys(receiving.answer(taken).wanted) if key in sizes]
        return len(wanted), sum(sizes[key] for key in wanted)

    def facts(self, session_id: str) -> LiveFacts | None:
        live = self._live.get(session_id)
        if live is None:
            return None
        token = live.token.text if isinstance(live, HostSession) and live.token else None
        receiving = live.receiving_half()
        back = live.other_way
        return LiveFacts(
            code=live.code,
            # Before anybody joins, and again while cut off: the token is how they join again, and
            # it may have been remade for a port the provider moved.
            token=token if live.code is None or live.cut_at is not None else None,
            wanted_bytes=live.wanted_bytes,
            moved_bytes=live.moved_bytes,
            rate_bps=live.pace_bps,
            screen=None if receiving is None else receiving.screen,
            unwanted=0 if receiving is None else receiving.unwanted,
            two_way=back is not None,
            answered=receiving is not None and receiving.diff is not None,
            back_wanted_bytes=0 if back is None else back.wanted_bytes,
            back_moved_bytes=0 if back is None else back.moved_bytes,
            back_rate_bps=None if back is None else back.pace_bps,
            back_moving=back is not None and back.transferring,
            moving=live.transferring,
            received_files=len(live.verified) if isinstance(live, _Receiving) else 0,
            back_received_files=len(back.verified) if isinstance(back, _Receiving) else 0,
        )

    async def row(self, session_id: str) -> SessionRow | None:
        return await self.store.get(session_id)

    def time_left(self) -> OwnEstimate:
        """What Activity's row for swaps says of them: waiting while no session is moving files,
        then the longest time left by each moving session's own measured rate, or nothing while
        the first window is still being measured. Never a guess from the swaps before."""
        moving = [one for one in self._live.values() if one.moving and one.cut_at is None]
        if not moving:
            return OwnEstimate(waiting=True)
        # Each direction by its own pace: in a swap that sends and receives the two move at the same
        # time, so the session takes as long as the slower of them.
        lefts = [
            int(max(0, flow.wanted_bytes - flow.moved_bytes) * 8 / flow.pace_bps)
            for one in moving
            for flow in one.flows()
            if flow.transferring and flow.pace_bps
        ]
        return OwnEstimate(seconds=max(lefts) if lefts else None)

    # --- the task ------------------------------------------------------------------------------

    async def run(self, context: JobContext) -> None:
        """The session, held for its life. The device key is opened here and dropped at the end."""
        session_id = context.require_str("session_id", "a swap task needs its session's id")
        row = await self.store.get(session_id)
        if row is None or not row.live:
            return
        live = self._live.get(session_id)
        if live is None:
            # Nothing in memory: this device restarted since the press, and the token's secret
            # went with it. The session cannot be carried on.
            await self.store.end(session_id, state="failed", reason=LOST, now=self.now())
            return
        try:
            device = await device_of(self.store, self.secrets, await context.master_key())
        except DeviceLocked:
            log.info("swap.device_locked", swap=live.short_id)
            await live.end(LOST, tell=False)
            return
        live.attach(device, context)
        await live.say(WAITING_NOTE)
        await self.sweep_manifests()
        try:
            await live.drive()
        except asyncio.CancelledError:
            # Cancel on Activity: ended by this side, and the other side is told.
            await asyncio.shield(live.end(ENDED_BY_YOU))
            raise
        except Exception as error:
            # The type only. An error from a connection can carry the address it was dialling, and
            # neither the log nor the task's row may hold that.
            log.warning("swap.session_failed", swap=live.short_id, error=type(error).__name__)
            await live.end(LOST, tell=False)
        finally:
            live.device = None
        if live.reason is None:
            await live.end(LOST, tell=False)
        await live.finished.wait()

    async def on_settled(self, job_id: str) -> None:
        """A session task settled. One that never ran (cancelled while it waited for a worker),
        still has a listener and a hosting tunnel, and this is what takes them down."""
        session_id = self._by_job.get(job_id)
        live = None if session_id is None else self._live.get(session_id)
        if live is not None and live.reason is None:
            await live.end(ENDED_BY_YOU)

    async def settle_after_restart(self) -> list[str]:
        """End every session a restart interrupted. At boot, before any worker starts."""
        ended = []
        for session_id in await self.store.unfinished():
            if session_id in self._live:
                continue
            if await self.store.end(session_id, state="failed", reason=LOST, now=self.now()):
                ended.append(session_id)
        if ended:
            log.info("swap.ended_by_restart", swaps=len(ended))
        return ended

    async def sweep_manifests(self) -> int:
        """Forget unfinished files nobody has touched for a week, and their staged chunks."""
        swept = 0
        before = self.now() - MANIFEST_KEPT_SECONDS
        for manifest in await self.store.stale_manifests(before):
            if manifest.session_id in self._live:
                continue
            if await asyncio.to_thread(self.staged_is_ours, manifest.staged_path):
                await asyncio.to_thread(_remove, Path(str(manifest.staged_path)))
            await self.store.drop_manifest(manifest.session_id, manifest.file_key)
            swept += 1
        return swept


#: The live sessions behind the routes, on the application.
SESSIONS: Part[SwapSessions] = Part("swap_sessions")


def register_handlers(sessions: SwapSessions) -> None:
    """The session task. Family `OTHER` with a place among Activity's housekeeping (`Swaps`,
    `kernel/jobs/families.py`), like a download: it is not a pass over the library."""
    register_handler(SWAP_SESSION, sessions.run, name="Swapping with another Sift")
    estimate_itself(SWAP_SESSION, sessions.time_left)
