# SPDX-License-Identifier: AGPL-3.0-or-later
"""The guest's side of a swap, and its own files going to the host in a swap both ways.

## The streams

Throughput through a tunnel is bound per CONNECTION: each stream by its own send window across the
tunnel's round trip, and the line only by the sum. So the guest opens eight, and every window adds
one more while the trend of the last few windows holds up, to thirty-two (`Ladder`). The rate of
each window is written to the row; the screen's estimate reads the session's pace over its last
minute (`pace_bps`), never an assumption about the line.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from sift.kernel.access import Viewer
from sift.kernel.log import get_logger
from sift.slices.swap import lock
from sift.slices.swap.frames import Chunk as Chunk
from sift.slices.swap.frames import Conn as Conn
from sift.slices.swap.frames import ProtocolError as ProtocolError
from sift.slices.swap.handshake import (
    LOST,
    REASON_DONE,
    REDIAL_LIMIT,
    USED,
    WRONG_DEVICE,
    code_of,
    hello,
    read_hello,
)
from sift.slices.swap.live import Ladder, _Live
from sift.slices.swap.models import Chosen
from sift.slices.swap.receiving import _Receiving
from sift.slices.swap.sending import _Sending
from sift.slices.swap.token import Token, mint

if TYPE_CHECKING:
    from sift.slices.swap.session import SwapSessions

log = get_logger(__name__)


class GuestSession(_Receiving, _Live):
    """The guest's side: the dial through its own tunnel, the hello, the diff, the receiving, and
    in a swap that sends and receives, this side's files sent (`other_way`)."""

    role = "guest"

    def __init__(
        self,
        owner: SwapSessions,
        session_id: str,
        token: Token,
        *,
        route: str,
        dest_folder_id: str | None,
    ) -> None:
        super().__init__(owner, session_id, token.secret)
        self._start_receiving(dest_folder_id)
        self.token = token
        #: Where the next connection dials: the token's, until the host says its port moved.
        self.address = token.address
        self.port = token.port
        self.route = route
        self.proxy: str | None = None
        self.host_session: str | None = None
        self.ladder = Ladder()
        self.streams: dict[int, asyncio.Task[Any]] = {}
        #: In a swap that sends and receives, the streams carrying this side's files to the host,
        #: and their own ladder: they go up this side's upload, not down its download.
        self.back_ladder = Ladder()
        self.back_streams: dict[int, asyncio.Task[Any]] = {}
        #: Set by a Join with this session's token while it is cut off: dial again now.
        self.nudged = asyncio.Event()

    def sending_half(self) -> _BackSending | None:
        return self.other_way if isinstance(self.other_way, _BackSending) else None

    def receiving_half(self) -> _Receiving:
        return self

    async def on_message(self, frame: Mapping[str, Any]) -> None:
        if "moved" in frame and isinstance(frame["moved"], dict):
            # The host's provider moved its public port. Checked as a token's address is: a peer
            # cannot use this to send the guest's dials anywhere a token could not.
            moved = frame["moved"]
            try:
                checked = mint(
                    str(moved.get("address")),
                    int(moved.get("port", 0)),
                    self.token.host_device,
                    self.owner.now(),
                    secret=self.secret,
                )
            except (TypeError, ValueError):
                log.info("swap.lie", swap=self.short_id, what="moved")
                return
            self.address, self.port = checked.address, checked.port
            return
        await super().on_message(frame)

    @property
    def expires(self) -> int:
        return self.token.expires

    def join_again(self, token: Token) -> None:
        """A Join with this session's own token: where the host is now (a token remade after
        its port moved names the new one), and a dial immediately if the session is cut off."""
        self.token = token
        self.address, self.port = token.address, token.port
        self.nudged.set()

    def streaming(self) -> list[asyncio.Task[Any]]:
        return [*self.streams.values(), *self.back_streams.values()]

    async def on_cut(self) -> None:
        self.spawn(self.dial_again(), "redial")

    async def dial_again(self) -> None:
        """Dial the host again every `RETRY_SECONDS`, or immediately on a Join, while cut off."""
        while self.cut_at is not None and not self.ended.is_set():
            self.nudged.clear()
            try:
                await self._rejoin()
                return
            except (
                asyncio.IncompleteReadError,
                ConnectionError,
                OSError,
                ProtocolError,
                TimeoutError,
                lock.LockFailed,
            ):
                pass
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.nudged.wait(), self.owner.retry_seconds)

    async def _rejoin(self) -> None:
        """One dial back into the same session, returning once it is settled either way: joined
        again, or the host no longer has it. A dial that fails raises, to be tried again."""
        device = self.device
        if device is None:  # pragma: no cover (held until the session ends)
            return
        conn = await self._dial()
        try:
            await conn.send(
                hello(
                    device,
                    "guest",
                    self.secret,
                    os.urandom(32),
                    striped=1,
                    both=1,
                    songs=1,
                    once=1,
                    counts=1,
                )
            )
            frame = await conn.read(self.owner.hello_wait)
        except BaseException:
            conn.close()
            raise
        if isinstance(frame, Chunk):
            conn.close()
            raise ProtocolError("a chunk for a hello")
        if "refused" in frame:
            # The host has let the session go (it ended there, or restarted): nothing to join.
            conn.close()
            log.info("swap.rejoin_refused", swap=self.short_id)
            await self.end(LOST, tell=False)
            return
        peer = read_hello(frame, "host", self.secret)
        if peer.device != self.token.host_device:
            conn.close()
            log.info("swap.lie", swap=self.short_id, what="host device")
            await self.end(WRONG_DEVICE)
            return
        self.host_session = peer.session
        await self.resume(conn)
        self.spawn(self.listen(conn), "listen")
        # The answer may never have reached the host; it takes the first it hears. So with this
        # side's offer and its done, in a swap that sends and receives.
        await self.say_again(conn)
        self._reopen_streams()

    def _reopen_streams(self) -> None:
        """Dial again every stream a cut stopped, in each direction still carrying files."""
        if self.transferring and not self.received_all:
            for number in range(self.ladder.streams):
                running = self.streams.get(number)
                if running is None or running.done():  # pragma: no branch (a cut stopped them all)
                    self._open_stream(number)
        sending = self.sending_half()
        if sending is not None and sending.transferring and not sending.resolved:
            for number in range(self.back_ladder.streams):
                running = self.back_streams.get(number)
                if running is None or running.done():  # pragma: no branch (a cut stopped them all)
                    self._open_stream(number, back=True)

    async def drive(self) -> None:
        async with self.owner.egress.through(self.route) as proxy:
            if proxy is None:
                # Direct is refused for a swap: the guest's own address would be the one dialling.
                log.info("swap.no_tunnel", swap=self.short_id)
                await self.end(LOST, tell=False)
                return
            self.proxy = proxy
            await self._drive()
            await self.ended.wait()

    async def _dial(self) -> Conn:
        reader, writer = await lock.dial(self.address, self.port, self.proxy, self.secret)
        return Conn(reader, writer)

    async def _drive(self) -> None:
        device = self.device
        if device is None:  # pragma: no cover (attached before drive)
            return
        try:
            conn = await self._dial()
        except lock.LockFailed as error:
            # The reason is the whole of what a person can act on: a port nobody could reach, a
            # tunnel that would not carry the connection, or a lock that failed on the way in.
            log.info("swap.could_not_reach", swap=self.short_id, why=str(error))
            await self.end(LOST, tell=False)
            return
        self.control = conn
        nonce = os.urandom(32)
        try:
            model = await self.owner.face_model()
            # `both`: this side can send back, in a swap the host started both ways.
            await conn.send(
                hello(
                    device,
                    "guest",
                    self.secret,
                    nonce,
                    striped=1,
                    both=1,
                    songs=1,
                    once=1,
                    counts=1,
                    **({"model": model} if model else {}),
                )
            )
            frame = await conn.read(self.owner.hello_wait)
            if isinstance(frame, Chunk):
                raise ProtocolError("a chunk for a hello")
            if "refused" in frame:
                log.info("swap.token_used", swap=self.short_id)
                await self.end(USED, tell=False)
                return
            peer = read_hello(frame, "host", self.secret)
        except (asyncio.IncompleteReadError, ConnectionError, OSError, ProtocolError, TimeoutError):
            await self.end(LOST, tell=False)
            return
        if peer.device != self.token.host_device:
            # The token named one device and another answered with its key. Nothing is offered.
            log.info("swap.lie", swap=self.short_id, what="host device")
            await self.end(WRONG_DEVICE)
            return
        self.host_session = peer.session
        if peer.both:
            # The host started a swap that sends and receives: this side offers too, once its
            # own They match says what (`SwapSessions.answer_code`).
            back = _BackSending(self)
            back.peer_striped = peer.striped
            back.peer_model = peer.model
            back.peer_songs = peer.songs
            back.peer_once = peer.once
            back.peer_counts = peer.counts
            self.other_way = back
            await self.owner.store.set_two_way(self.id)
        self.spawn(self.listen(conn), "listen")
        await self.became_connected(peer.device, conn)
        self.code = code_of(self.secret, peer.device, device.id, peer.nonce, nonce)
        await self._carry_files()

    async def _carry_files(self) -> None:
        """Receive, and send too in a swap both ways, ending the session once all is done."""
        sending = self.sending_half()
        if sending is None:
            if await self.take_and_receive():
                await self.end(REASON_DONE)
            return
        if await self.both_ways(sending.match_then_send(), self.take_and_receive()):
            await self.end(REASON_DONE)

    def _receiving_started(self) -> None:
        for number in range(self.ladder.streams):
            self._open_stream(number)

    def _open_stream(self, number: int, *, back: bool = False) -> None:
        if back:
            self.back_streams[number] = self.spawn(self.stream(number, back=True), f"back{number}")
        else:
            self.streams[number] = self.spawn(self.stream(number), f"stream{number}")

    async def on_window(self) -> None:
        # Each direction's ladder is judged on its own direction's rate, and only while its files
        # move: the two go up two different uploads (see "Both ways").
        if self.transferring:
            wanted = self.ladder.window(self.rate_bps or 0)
            for number in range(len(self.streams), wanted):
                if not self.received_all:
                    self._open_stream(number)
        sending = self.sending_half()
        if sending is not None and sending.transferring:
            wanted = self.back_ladder.window(sending.rate_bps or 0)
            for number in range(len(self.back_streams), wanted):
                if not sending.resolved:
                    self._open_stream(number, back=True)

    # --- the streams ---------------------------------------------------------------------------

    async def stream(self, number: int, *, back: bool = False) -> None:
        """One stream: dial, name the session, and receive files until the host has none left;
        or, with `back`, say so in the first frame and send this side's files until none are
        left to send."""
        sending = self.sending_half() if back else None
        failures = 0
        while not self.ended.is_set() and self._carrying(sending) and self.cut_at is None:
            try:
                conn = await self._dial()
            except lock.LockFailed:
                failures += 1
                if failures >= REDIAL_LIMIT:
                    log.info("swap.streams_lost", swap=self.short_id)
                    await self.cut()
                    return
                await asyncio.sleep(1.0)
                continue
            self.conns.add(conn)
            task = asyncio.current_task()
            if sending is not None and task is not None:
                sending.stream_tasks.add(task)

            def delivered() -> None:
                # A connection that carried a frame is a connection that worked: the count is of
                # dials that failed IN A ROW, so it starts again here and not only when a stream
                # ends cleanly, which the end's cancel means it never does. Counted only at the
                # first frame, a stream dropping once per file over a long swap would keep adding
                # up and the fifth drop would end the whole swap as lost, files received or not.
                nonlocal failures
                failures = 0

            try:
                if sending is not None:
                    await conn.send({"stream": number, "session": self.host_session, "send": 1})
                    # It returns only once it has told the host there is nothing left to send.
                    await sending.serve(conn, delivered=delivered)
                    return
                else:
                    await conn.send({"stream": number, "session": self.host_session})
                    if await self.receive(conn, delivered=delivered):
                        return
            except (
                asyncio.IncompleteReadError,
                ConnectionError,
                OSError,
                ProtocolError,
                TimeoutError,
            ):
                failures += 1
                if failures >= REDIAL_LIMIT:
                    await self.cut()
                    return
            finally:
                self._let_go(conn, sending, task)

    def _carrying(self, sending: _BackSending | None) -> bool:
        if sending is not None:
            return not sending.resolved
        return not self.received_all

    def _let_go(
        self, conn: Conn, sending: _BackSending | None, task: asyncio.Task[Any] | None
    ) -> None:
        conn.close()
        self.conns.discard(conn)
        if sending is not None and task is not None:
            sending.stream_tasks.discard(task)

    async def teardown(self) -> None:
        await super().teardown()
        sending = self.sending_half()
        if sending is not None:
            await sending.let_go_of_everything()


class _BackSending(_Sending):
    """The guest's files going to the host, in a swap that sends and receives: offered once this
    side's own They match has said what (`choose`), carried up streams this side dials."""

    back = True

    def __init__(self, live: GuestSession) -> None:
        self.live = live
        self.guest = live
        self._start_figures()
        self._start_sending(viewer=None, chosen=(), share_boxes=True)
        #: Settled by this side's They match, with what it offers.
        self.code_matched: asyncio.Future[None] = asyncio.get_running_loop().create_future()

    def choose(self, viewer: Viewer, chosen: Sequence[Chosen], share_boxes: bool) -> None:
        """What this side offers, read as `viewer` with Hidden open, and its release."""
        if self.code_matched.done():
            return
        self.viewer = viewer
        self.chosen = list(chosen)
        self.share_boxes = share_boxes
        self.live.settle(self.code_matched, None)

    async def match_then_send(self) -> bool:
        """THIS SIDE'S CODE RELEASES ITS OFFER, as the host's does."""
        if not await self.live.until(self.code_matched):
            return False
        return await self.offer_and_send()

    def _sending_started(self) -> None:
        if not self.wanted:
            return
        for number in range(self.guest.back_ladder.streams):
            self.guest._open_stream(number, back=True)
