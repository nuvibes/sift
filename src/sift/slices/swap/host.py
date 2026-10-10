# SPDX-License-Identifier: AGPL-3.0-or-later
"""The host's side of a swap, and the guest's files arriving at it in a swap both ways.

## Both ways

A session can carry files both ways: the host presses Start under Send and receive (`two_way`),
and each side then offers, answers and receives. One token, one code, and each side's own They match
releases its own offer, as the host's does in a swap that only sends. The guest's offer is chosen
with the same pickers and built by the same read as the host's (`make_offer`: Hidden open, Do not
swap and kept local honoured), its files made ready by the same look and strip (`prepare`), and the
host answers it on the same offer screen (`assess`, the dedup rule) and lands what it takes through
the same landing a guest's files go through (`land`).

The guest still dials every connection: the host cannot dial a device behind its VPN. A stream says
which way it carries in its first frame. `{"stream", "session"}` carries files to the guest, as it
always has; `{"stream", "session", "send": 1}` carries them to the host, the guest the sender on it
and the host the receiver. The two directions move together, each with its own streams, its own
ladder and its own pace, because they go up two different uploads (the host's and the guest's): a
sum would judge one side's line by the other's, and one direction after the other would leave the
first sender's line idle while the second waited. The estimate is the longer of the two.

On the control connection the words are the same both ways and mean the same from whoever says
them: `offer` is what the sender offers, `diff` is the answer to the other side's offer, and `done`
says everything wanted from the other side has landed. A side ends as done once it has said done
and heard it; a side that only sends, once it has heard it.

Both devices must know the shape. A guest's hello says it can send back (`both`); the host's hello
says the session goes both ways only when it does. A host that started a swap both ways turns away
a hello that does not say `both`, an older Sift's, with `refused`, and ends as `older`, which its
screen says in words: the older guest reads that refusal as a token already used and ends too, so
nothing is half swapped. An older host never says `both`, so a newer guest swaps one way with it, as
before. A swap that only sends is unchanged on the wire but for the guest's hello, which says `both`
as it says `striped`, and which an older host passes over as it passes over any field it does not
read.

A file's song travels the same way. Each side's hello says it takes one (`songs`: a guest's always,
a host's in a swap both ways, where it receives too), and a sender leaves every song off the offer
for a side that did not say so (`Offer.as_sent`): an older Sift refuses an offer holding a
field it does not declare, so it is sent the offer it always was, and swaps as before.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, cast

from sift.kernel.access import Viewer
from sift.kernel.log import get_logger
from sift.kernel.tunnels import TunnelError
from sift.slices.swap import lock
from sift.slices.swap.frames import Chunk as Chunk
from sift.slices.swap.frames import Conn as Conn
from sift.slices.swap.frames import ProtocolError as ProtocolError
from sift.slices.swap.handshake import (
    DISK_FULL,
    EXPIRED,
    LOST,
    OLDER,
    REASON_DONE,
    code_of,
    hello,
    read_hello,
)
from sift.slices.swap.live import Hosting, _Live
from sift.slices.swap.models import Chosen
from sift.slices.swap.receiving import _Receiving
from sift.slices.swap.sending import _Sending
from sift.slices.swap.token import Token, mint
from sift.slices.swap.transfer import DiskFull

if TYPE_CHECKING:
    from sift.slices.swap.session import SwapSessions

log = get_logger(__name__)

# --- the sessions, side by side ------------------------------------------------------------------


class HostSession(_Sending, _Live):
    """The host's side: the listener, the one hello, the code's release, the offer, the sending,
    and in a swap that sends and receives, the guest's files received (`other_way`)."""

    role = "host"

    def __init__(
        self,
        owner: SwapSessions,
        session_id: str,
        secret: bytes,
        *,
        tunnel_id: str,
        viewer: Viewer,
        chosen: Sequence[Chosen],
        share_boxes: bool,
        two_way: bool = False,
        dest_folder_id: str | None = None,
    ) -> None:
        super().__init__(owner, session_id, secret)
        loop = asyncio.get_running_loop()
        self._start_sending(viewer=viewer, chosen=chosen, share_boxes=share_boxes)
        self.tunnel_id = tunnel_id
        self.token: Token | None = None
        self.server: asyncio.Server | None = None
        #: The listener's port on loopback: what the tunnel's listener targets, again after a cut.
        self.port = 0
        self.hosting = False
        self.hello_used = False
        #: Settled by They match. They don't match ends the session there and then.
        self.code_matched: asyncio.Future[None] = loop.create_future()
        #: The streams carrying the guest's files to this side, in a swap that sends and receives.
        self.streams_in: set[asyncio.Task[Any]] = set()
        if two_way:
            self.other_way = _BackReceiving(self, dest_folder_id)

    @property
    def two_way(self) -> bool:
        return self.other_way is not None

    def sending_half(self) -> _Sending:
        return self

    def receiving_half(self) -> _Receiving | None:
        return self.other_way if isinstance(self.other_way, _BackReceiving) else None

    # --- connections ---------------------------------------------------------------------------

    async def on_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Every connection the listener takes: locked, then a hello or a stream, or dropped."""
        # `asyncio.start_server` runs each connection as a task of its own, so there is one here.
        task = cast("asyncio.Task[Any]", asyncio.current_task())
        self.tasks.add(task)
        conn: Conn | None = None
        try:
            if self.ended.is_set():
                writer.close()
                return
            try:
                locked = await lock.accept(reader, writer, self.secret)
            except lock.LockFailed:
                log.info("swap.connection_refused", swap=self.short_id)
                return
            conn = Conn(*locked)
            self.conns.add(conn)
            first = await conn.read(lock.HANDSHAKE_SECONDS)
            if isinstance(first, Chunk):
                return
            if "device" in first:
                await self._hello(conn, first)
            elif "stream" in first and first.get("send") == 1:
                await self._stream_in(conn, first, task)
            elif "stream" in first:
                self.stream_tasks.add(task)
                await self._stream(conn, first)
        except (asyncio.IncompleteReadError, ConnectionError, OSError, ProtocolError, TimeoutError):
            pass
        finally:
            if conn is not None and conn is not self.control:
                conn.close()
                self.conns.discard(conn)
            self.tasks.discard(task)
            self.stream_tasks.discard(task)
            self.streams_in.discard(task)

    @property
    def expires(self) -> int:
        return self.token.expires if self.token is not None else 0

    async def _says_both(self) -> dict[str, str | int]:
        """What this side's hello adds in a swap that sends and receives: that it does, that it
        takes files in shares, and its face model, for the pictures beside the guest's offer.
        Nothing in a swap one way, whose hello is as it always was."""
        if not self.two_way:
            return {}
        model = await self.owner.face_model()
        return {
            "both": 1,
            "striped": 1,
            "songs": 1,
            "once": 1,
            "counts": 1,
            **({"model": model} if model else {}),
        }

    async def _hello(self, conn: Conn, first: Mapping[str, Any]) -> None:
        """The one hello this token is good for, and the same guest's again after a cut."""
        if self.hello_used and self.peer is not None:
            again = read_hello(first, "guest", self.secret)
            if again.device == self.peer:
                await self._rejoin(conn)
                return
        if self.hello_used:
            # ONE USE. A second hello is refused whoever sends it: the same guest trying again
            # included: a session does not restart on its token.
            with contextlib.suppress(Exception):
                await conn.send({"refused": "used"})
            log.info("swap.hello_refused", swap=self.short_id, why="used")
            return
        peer = read_hello(first, "guest", self.secret)
        self.hello_used = True
        if self.two_way and not peer.both:
            # AN OLDER SIFT cannot send back, and a swap both ways with it would be half a swap.
            # It reads the refusal as a token already used and ends; this side ends as `older`,
            # which the screen says in words. Nothing about either library has been named.
            with contextlib.suppress(Exception):
                await conn.send({"refused": OLDER})
            log.info("swap.hello_refused", swap=self.short_id, why=OLDER)
            await self.end(OLDER, tell=False)
            return
        self.peer_model = peer.model
        self.peer_striped = peer.striped
        self.peer_songs = peer.songs
        self.peer_once = peer.once
        self.peer_counts = peer.counts
        try:
            await asyncio.wait_for(self.attached.wait(), self.owner.hello_wait)
        except TimeoutError:
            await self.end(LOST, tell=False)
            return
        device = self.device
        # An end cancels this task (it is one of `tasks`) before it lets go of the device, and a
        # connection taken after the end is closed unread above.
        if device is None:  # pragma: no cover (attached above)
            return
        nonce = os.urandom(32)
        both = await self._says_both()
        await conn.send(hello(device, "host", self.secret, nonce, session=self.id, **both))
        self.control = conn
        self.conns.discard(conn)
        await self.became_connected(peer.device, conn)
        # The code is shown once the row says connected, so a screen never draws a code beside a
        # session that still reads as waiting.
        self.code = code_of(self.secret, device.id, peer.device, nonce, peer.nonce)
        await self.listen(conn)

    async def _rejoin(self, conn: Conn) -> None:
        """The guest this session is with, joining again: the same device, proven by its key over
        this session's secret. The code it compared stands; what the guest may not have heard is
        said again (`say_again`)."""
        device = self.device
        if device is None:  # pragma: no cover (held until the session ends)
            return
        both = await self._says_both()
        await conn.send(hello(device, "host", self.secret, os.urandom(32), session=self.id, **both))
        old = self.control
        # Closed only once the new one is the session's: its reader, waking to the close while
        # it was still the control, would take it for a cut and close the new one with it.
        await self.resume(conn)
        if old is not None and old is not conn:
            old.close()
        await self.say_again(conn)
        await self.listen(conn)

    # --- the life of it ------------------------------------------------------------------------

    async def drive(self) -> None:
        token = self.token
        wait = max(0.0, (token.expires if token else 0) - self.owner.now())
        try:
            await asyncio.wait_for(self.until(self.connected), wait)
        except TimeoutError:
            log.info("swap.token_ran_out", swap=self.short_id)
            await self.end(EXPIRED, tell=False)
            return
        if self.ended.is_set():
            return
        # THE CODE RELEASES THE OFFER. Nothing about the library is named until the person who
        # pressed Start says the two codes are the same.
        if not await self.until(self.code_matched):
            return
        receiving = self.receiving_half()
        if receiving is None:
            if await self.offer_and_send():
                await self.end(REASON_DONE)
            return
        if await self.both_ways(self.offer_and_send(), receiving.take_and_receive()):
            await self.end(REASON_DONE)

    async def _stream(self, conn: Conn, first: Mapping[str, Any]) -> None:
        """A stream carrying this side's files to the guest, while the transfer is on."""
        if (
            not self.transferring
            or self.control is None
            or first.get("session") != self.id
            or self.ended.is_set()
        ):
            return
        self.watchdog.heard()
        await self.serve(conn)

    async def _stream_in(
        self, conn: Conn, first: Mapping[str, Any], task: asyncio.Task[Any]
    ) -> None:
        """A stream carrying the guest's files to this side, in a swap that sends and receives,
        once this side's answer has gone."""
        # Taken from the moment the answer is made, before it is sent: the guest dials as soon
        # as it hears it, and a stream turned away here would count as a failed dial.
        receiving = self.receiving_half()
        if (
            receiving is None
            or receiving.diff is None
            or self.control is None
            or first.get("session") != self.id
            or self.ended.is_set()
        ):
            return
        self.streams_in.add(task)
        self.watchdog.heard()
        try:
            await receiving.receive(conn)
        except DiskFull:
            log.info("swap.disk_full", swap=self.short_id)
            await self.end(DISK_FULL)

    async def hosting_moved(self, hosting: Hosting) -> None:
        """The provider moved the public port at a renewal. Before anybody has joined, the token is
        made again for the new port (same secret, same expiry) and the screen shows it; after, the
        guest is told where to dial its next stream."""
        if self.token is None or self.ended.is_set():
            return
        if self.control is None:
            try:
                moved = mint(
                    hosting.public_ipv4,
                    hosting.external_port,
                    self.token.host_device,
                    self.owner.now(),
                    secret=self.secret,
                )
            except ValueError:
                await self.end(LOST, tell=False)
                return
            # The same server: a renewal moves the port, never the tunnel.
            self.token = Token(
                moved.address,
                moved.port,
                self.secret,
                self.token.expires,
                self.token.host_device,
                self.token.server,
            )
            log.info("swap.token_remade", swap=self.short_id)
            return
        with contextlib.suppress(Exception):
            await self.control.send(
                {"moved": {"address": hosting.public_ipv4, "port": hosting.external_port}}
            )

    async def hosting_lost(self, error: TunnelError) -> None:
        """The hosting ended on its own: a renewal the provider refused. Nobody can reach this
        side until it hosts again, so the session is cut off, not ended (see "Cut off"), and the
        hosting is tried again while the token lasts."""
        log.info("swap.hosting_lost", swap=self.short_id)
        # The hoster has already ended the hosting: asking it to stop again would wait on the
        # lock it is telling this session under.
        self.hosting = False
        if self.cut_at is None:
            await self.cut()
        else:
            await self.on_cut()

    def streaming(self) -> list[asyncio.Task[Any]]:
        return [*self.stream_tasks, *self.streams_in]

    async def on_cut(self) -> None:
        if not self.hosting and not self.ended.is_set():
            self.spawn(self.host_again(), "rehost")

    async def host_again(self) -> None:
        """Host on the same tunnel again, every `RETRY_SECONDS` until it takes or the token runs
        out. The listener never closed; a port the provider moved remakes the token. It waits
        first: the provider has just refused, and the tunnel is being put back as it was. A loss
        heard while cut off starts another of these; whichever hosts first, the other then stops
        rather than ask the tunnel for a second hosting."""
        while True:
            await asyncio.sleep(self.owner.retry_seconds)
            if self.hosting or self.ended.is_set():
                return
            key = None
            if self.context is not None:
                with contextlib.suppress(Exception):
                    key = await self.context.master_key()
            if key is not None:
                try:
                    hosting = await self.owner.hoster.host_on(
                        self.tunnel_id,
                        target_port=self.port,
                        master_key=key,
                        on_moved=self.hosting_moved,
                        on_lost=self.hosting_lost,
                    )
                except TunnelError:
                    pass
                else:
                    self.hosting = True
                    log.info("swap.hosting_again", swap=self.short_id)
                    await self.hosting_moved(hosting)
                    return

    async def teardown(self) -> None:
        await super().teardown()
        server, self.server = self.server, None
        if server is not None:  # pragma: no branch (`start` listens before a session can end)
            server.close()
        await self.let_go_of_everything()
        if self.hosting:
            self.hosting = False
            try:
                await self.owner.hoster.stop_hosting(self.tunnel_id)
            except Exception as error:  # the session still ends; the tunnel says why it did not
                log.warning(
                    "swap.stop_hosting_failed", swap=self.short_id, error=type(error).__name__
                )


class _BackReceiving(_Receiving):
    """The guest's files arriving at the host, in a swap that sends and receives: its offer
    answered on this side's offer screen, received on the streams the guest dials, and landed
    through the one landing a guest's files go through."""

    back = True

    def __init__(self, live: HostSession, dest_folder_id: str | None) -> None:
        self.live = live
        self._start_figures()
        self._start_receiving(dest_folder_id)
