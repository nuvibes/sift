# SPDX-License-Identifier: AGPL-3.0-or-later
"""The sending half of a direction of a swap.

## Making files ready, ahead of the streams

The host LOOKS at each wanted file before it is sent (`transfer.prepare`): one with no location in
it goes as it is, its chunks read from the original; one that carries a place is stripped into a
copy first. Either way the reading is done AHEAD of the streams, a few files at a time
(`READY_AHEAD`, `READYING_AT_ONCE`), so a stream that finishes a file finds the next one ready
rather than waiting for it. A stream with nothing to carry opens the first queued file that IS
ready, and waits for any to become ready, never for one in particular, so a ready file is never
left to a single stream at one connection's pace.

## A file over several streams (shares)

A stream carries a SHARE of a file: the chunks the host hands it (`chunks` in the file's header),
cut from what no stream holds and the guest has not confirmed. The shares shrink as the file's
last chunks are cut, so a large file ends on every stream at once rather than on one alone at one
stream's pace. The guest keeps one map of each file's missing chunks across its streams, answers
each chunk on the stream it came on, and checks the whole file once: the stream that lands the
last chunk says `file_done`, and every other stream says `share_done`. A share dropped with its
stream goes back to be cut again, and after a cut each share is cut afresh from the guest's
`have`, the chunks it already verified.

A guest says it takes shares in its hello (`striped`). A host hands a guest that did not say so
whole files, as a Sift before shares did; a header with no `chunks` is the whole file to a guest.
The hello's version stays one: a Sift refuses a hello of another version outright.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from pydantic import ValidationError

from sift.kernel import lanes
from sift.kernel.access import Viewer
from sift.kernel.log import get_logger
from sift.slices.swap import pieces, transfer
from sift.slices.swap.frames import Chunk as Chunk
from sift.slices.swap.frames import Conn as Conn
from sift.slices.swap.frames import ProtocolError as ProtocolError
from sift.slices.swap.handshake import (
    LOST,
    READY_AHEAD,
    READYING_AT_ONCE,
    SHARE_MOST,
    STREAMS_START,
    WINDOW,
)
from sift.slices.swap.live import (
    _Figures,
    _int_list,
    _looked_at,
    _Outgoing,
    _remove,
    _wanted_indexes,
)
from sift.slices.swap.models import Chosen, Diff
from sift.slices.swap.transfer import CHUNK_SIZE, MAX_RETRIES, CannotStrip, Prepared

log = get_logger(__name__)

# --- sending ------------------------------------------------------------------------------------


class _Sending(_Figures):
    """The sending half of a direction: the offer made and sent, the answer taken, and the wanted
    files made ready and carried in shares over every stream that asks. The host's in a swap one
    way; in a swap that sends and receives, the guest's as well (`_BackSending`)."""

    def _start_sending(
        self,
        *,
        viewer: Viewer | None,
        chosen: Sequence[Chosen],
        share_boxes: bool,
    ) -> None:
        loop = asyncio.get_running_loop()
        #: Whose files are offered: read as this admin with the vault shut.
        self.viewer = viewer
        self.chosen = list(chosen)
        self.share_boxes = share_boxes
        #: The face model the other side's hello named (`Peer.model`).
        self.peer_model: str | None = None
        #: Whether the other side's hello said it takes files in shares (`Peer.striped`).
        self.peer_striped = False
        #: Whether the other side's hello said it takes a file's song (`Peer.songs`).
        self.peer_songs = False
        #: Whether the other side's hello said it checks the whole at the end (`Peer.once`).
        self.peer_once = False
        #: Whether the other side's hello said it takes a person's confirmed count (`Peer.counts`).
        self.peer_counts = False
        #: The other side's answer to the offer, and its word that everything it wanted landed.
        self.diff_in: asyncio.Future[Mapping[str, Any]] = loop.create_future()
        self.done_in: asyncio.Future[None] = loop.create_future()
        self.queue: deque[int] = deque()
        self.prepared: dict[int, Prepared] = {}
        #: The files being made ready, or ready and not yet opened, by index (see "Making files
        #: ready, ahead of the streams").
        self.readying: dict[int, asyncio.Task[Prepared]] = {}
        self._readying_slots = asyncio.Semaphore(READYING_AT_ONCE)
        #: Files that failed part-way through a share (an original changed under the read), not
        #: yet said to the receiver: the next stream to ask hears it before anything else.
        self.untold: set[int] = set()
        #: The files open for sending, oldest first, until each is done or failed.
        self.outgoing: dict[int, _Outgoing] = {}
        #: Files being stripped by a stream: a stream with nothing to carry waits for them.
        self.preparing = 0
        #: Set whenever there may be something new to carry, or less to wait for.
        self._stirred = asyncio.Event()
        #: The connections serving a stream, so the end can let them finish what is in flight.
        self.stream_tasks: set[asyncio.Task[Any]] = set()
        #: Per file, the chunks this session put on a connection, and the ones counted as moved.
        #: A chunk whose acknowledgement was lost with a dropped stream is counted when the
        #: receiver's `have` names it; one it had from an earlier session was not moved by this one.
        self.put: dict[int, dict[int, int]] = {}
        self.counted: dict[int, set[int]] = {}

    def _sending_started(self) -> None:
        """What a side does once the answer has come and the files can move: nothing on the host,
        whose streams are dialled by the guest."""

    async def offer_and_send(self) -> bool:
        """THE CODE HAS RELEASED THE OFFER: make it, send it, take the answer, carry the wanted
        files, and wait for the other side's word that everything it wanted landed. True once
        that word has come; False when the session ended first."""
        live = self.live
        if self.viewer is None:  # pragma: no cover (a sender is always given whose files)
            raise RuntimeError("a sending side with nobody to read as")
        offer = await live.owner.make_offer(
            self.viewer, self.chosen, self.share_boxes, self.peer_model
        )
        self.offer = offer
        await live.owner.store.set_offered(live.id, len(offer.files), back=self.back)
        await live.advance("offered")
        # Through a cut if there is one: joined again, the offer goes on the new connection.
        if not await live.send_control(
            {"offer": offer.as_sent(songs=self.peer_songs, counts=self.peer_counts)}
        ):
            return False
        if not await live.until(self.diff_in):
            return False
        try:
            self.wanted = _wanted_indexes(offer, Diff.model_validate(self.diff_in.result()))
        except (ProtocolError, ValidationError):
            log.info("swap.lie", swap=live.short_id, what="diff")
            await live.end(LOST)
            return False
        self.wanted_bytes = sum(offer.files[n].size for n in self.wanted)
        self.queue = deque(self.wanted)
        await live.owner.store.set_wanted(live.id, len(self.wanted), back=self.back)
        await live.advance("transferring")
        self.transferring = True
        self._ready_ahead()
        live.start_measuring()
        self._sending_started()
        if not await live.until(self.done_in):
            return False
        # The receiver's word arrives on the control connection, and the last acknowledgements on
        # the streams may still be in flight beside it: let the streams finish reading them, so
        # the row counts every file and byte that arrived.
        serving = [task for task in self.stream_tasks if not task.done()]
        if serving:
            await asyncio.wait(serving, timeout=live.owner.watchdog_seconds)
        return True

    async def serve(self, conn: Conn, *, delivered: Callable[[], None] | None = None) -> bool:
        """One stream: a share of an open file, else the first ready file opened, until every
        file is settled. A stream with nothing to carry while others still carry waits: a share
        dropped with its stream comes back to be cut again. True once it has said there is
        nothing left; `delivered` is told after each share the receiver answered."""
        # Every read this stream makes goes to the front of its storage's lane: see "Making files
        # ready, ahead of the streams".
        async with lanes.first():
            while True:
                if self.untold:
                    await conn.send({"file": self.untold.pop(), "cannot": True})
                    continue
                taken = self._cut()
                if taken is not None:
                    await self._carry(conn, *taken)
                    if delivered is not None:
                        delivered()
                    continue
                if self.queue:
                    # The first queued file that is READY, never simply the next one: a stream
                    # that took the next file and waited for it to be read would sit idle while a
                    # file already read went out on a single stream (see "Making files ready").
                    index = self._next_ready()
                    if index is not None:
                        await self._open(conn, index)
                    else:
                        await self._wait_for_work()
                    continue
                if self.preparing or self.outgoing:
                    await self._wait_for_work()
                    continue
                await conn.send({"none": True})
                return True

    def _stir(self) -> None:
        """Wake every stream waiting for something to carry."""
        stirred, self._stirred = self._stirred, asyncio.Event()
        stirred.set()

    async def _wait_for_work(self) -> None:
        # The event is taken with no wait between the look that found nothing and this, so a stir
        # in between is never missed; the bound is only a second look for safety.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._stirred.wait(), self.live.owner.watchdog_seconds)

    def _cut(self) -> tuple[_Outgoing, list[int]] | None:
        """The next share: from the oldest open file with chunks nobody holds. A file nobody holds
        with nothing left to cut is asked about with a share of nothing: its last word may have
        been lost with a dropped stream, and the receiver says it again."""
        for out in self.outgoing.values():
            if out.pool:
                chunks = sorted(out.pool)
                if self.peer_striped:
                    # Divided among the streams there are (never fewer than a transfer starts
                    # with), so the shares shrink as the file's end nears.
                    streams = max(len(self.stream_tasks), STREAMS_START)
                    chunks = chunks[: max(1, min(SHARE_MOST, -(-len(chunks) // streams)))]
                out.pool.difference_update(chunks)
                return out, chunks
            if not out.holders:
                return out, []
        return None

    async def _open(self, conn: Conn, index: int) -> None:
        """Open a wanted file for sending, made ready ahead (`_next_ready`). A file that cannot be
        sent without its place is said so on this stream and failed."""
        self.preparing += 1
        self._ready_ahead()
        try:
            ready = await self._readied(index)
        except CannotStrip:
            self.preparing -= 1
            self._stir()
            await conn.send({"file": index, "cannot": True})
            await self._fail(index)
            return
        except BaseException:
            # Stopped part-way: the file goes back to the front for the next stream.
            self.preparing -= 1
            self.queue.appendleft(index)
            self._stir()
            raise
        count = transfer.chunk_count(ready.size)
        self.outgoing[index] = _Outgoing(index, ready, count, set(range(count)))
        self.preparing -= 1
        self._stir()

    def _ready_ahead(self) -> None:
        """Start making ready the first `READY_AHEAD` files of the queue that are not already."""
        for index in list(self.queue)[:READY_AHEAD]:
            if index not in self.readying:
                self._start_readying(index)

    def _start_readying(self, index: int) -> asyncio.Task[Prepared]:
        task = self.live.spawn(self._ready(index), "ready")
        # Read here when nobody comes to open the file (the session ended first), so a failure
        # is not reported as one nobody looked at.
        task.add_done_callback(_looked_at)
        # A file read is something to carry: every stream waiting for one looks again.
        task.add_done_callback(lambda _task: self._stir())
        self.readying[index] = task
        return task

    def _next_ready(self) -> int | None:
        """Take from the queue the first file whose making ready has ended (ready, or failed for
        the stream that opens it to say so), else start the readying ahead and None."""
        self._ready_ahead()
        for index in self.queue:
            task = self.readying.get(index)
            if task is not None and task.done():
                self.queue.remove(index)
                return index
        return None

    async def _ready(self, index: int) -> Prepared:
        async with self._readying_slots, lanes.first():
            return await self._prepared(index)

    async def _readied(self, index: int) -> Prepared:
        """The file made ready ahead, whose readying has ended (`_next_ready` opens no other). One
        that failed is forgotten, so the next try reads the file afresh."""
        try:
            return await self.readying[index]
        except BaseException:
            del self.readying[index]
            raise

    async def _prepared(self, index: int) -> Prepared:
        ready = self.prepared.get(index)
        if ready is None:
            if self.offer is None or self.viewer is None:  # pragma: no cover (only once offered)
                raise CannotStrip("there is no offer")
            owner = self.live.owner
            source = await owner.path_of(self.viewer, self.offer.files[index].key)
            if source is None:
                raise CannotStrip("the file is not on this device any more")
            ready = await owner.prepare(source, owner.workdir_of(self.live))
            self.prepared[index] = ready
        if ready.digest is None and not self.peer_once:
            # A receiver that checks the whole first is told its digest in the header. A file that
            # cannot be read whole is made ready afresh on the next try.
            try:
                ready = self.prepared[index] = await transfer.with_digest(ready)
            except BaseException:
                del self.prepared[index]
                if ready.copy:
                    await asyncio.to_thread(_remove, ready.path)
                raise
        return ready

    def _count(self, index: int, chunk: int) -> None:
        """Count one chunk as moved by this session, once, if this session put it on the wire."""
        size = self.put.get(index, {}).get(chunk)
        counted = self.counted.setdefault(index, set())
        if size is not None and chunk not in counted:
            counted.add(chunk)
            self.moved(size)

    async def _let_go(self, index: int) -> None:
        """The file is settled: its stripped copy goes. An original sent as it is is somebody's
        file, and is only forgotten."""
        self.readying.pop(index, None)
        ready = self.prepared.pop(index, None)
        if ready is not None and ready.copy:
            await asyncio.to_thread(_remove, ready.path)

    async def let_go_of_everything(self) -> None:
        """At the end: every stripped copy still held goes."""
        for index in list(self.prepared):
            await self._let_go(index)

    async def _fail(self, index: int) -> None:
        self.failed_files.add(index)
        await self._let_go(index)

    async def _settle(self, out: _Outgoing, *, done: bool) -> None:
        """A file done or failed, once, whichever stream hears it first."""
        if self.outgoing.get(out.index) is not out:
            return
        del self.outgoing[out.index]
        if done:
            self.done_files.add(out.index)
            await self.live.owner.store.add_sent(self.live.id, files=1, back=self.back)
        else:
            self.failed_files.add(out.index)
        await self._release(out)
        self._stir()

    async def _release(self, out: _Outgoing) -> None:
        """The stripped copy goes once its file is settled and no share of it is on a stream."""
        if out.index not in self.outgoing and not out.holders:
            await self._let_go(out.index)

    async def _carry(self, conn: Conn, out: _Outgoing, share: list[int]) -> None:
        """Carry one share on one stream. Whatever of it the receiver did not confirm goes back
        to be cut again when the stream drops."""
        out.holders += 1
        worded = False
        try:
            worded = await self._send_share(conn, out, share)
        finally:
            out.holders -= 1
            if self.outgoing.get(out.index) is out:
                out.pool.update(chunk for chunk in share if chunk not in out.landed)
            await self._release(out)
            self._stir()
        if worded and not share and self.outgoing.get(out.index) is out:
            # Asked about a file with nothing left to cut, and another of the receiver's streams
            # is still checking it: a pause before asking again, rather than asking in a loop.
            await asyncio.sleep(min(0.5, self.live.owner.watchdog_seconds / 4))

    async def _send_share(self, conn: Conn, out: _Outgoing, share: list[int]) -> bool:
        """Send one share of a file on one stream. True once the receiver has said how it ended:
        done, turned down, or (for a share) its part landed."""
        live = self.live
        index, ready = out.index, out.ready
        await conn.send(self._header(out, share))
        reply = await conn.read(live.owner.watchdog_seconds)
        if isinstance(reply, Chunk):
            raise ProtocolError("a chunk from the receiver")
        if reply.get("skip") == index:
            await self._settle(out, done=False)
            return True
        if reply.get("file_done") == index:
            await self._settle(out, done=True)
            return True
        have = _int_list(reply.get("have"), below=out.count)
        for chunk in have:
            self._count(index, chunk)
            out.landed.add(chunk)
            out.pool.discard(chunk)
        todo = deque(chunk for chunk in share if chunk not in out.landed)
        in_flight: dict[int, int] = {}
        while todo or in_flight:
            while todo and len(in_flight) < WINDOW:
                chunk = todo.popleft()
                try:
                    data = await transfer.read_ready_chunk(ready, chunk)
                except transfer.Changed:
                    # An original changed since it was measured: the file fails, the receiver
                    # hears so on the next stream that asks, and this one drops what it held.
                    log.info("swap.file_changed", swap=live.short_id, file=index)
                    await self._settle(out, done=False)
                    self.untold.add(index)
                    raise
                self.put.setdefault(index, {})[chunk] = len(data)
                out.pieces[chunk] = await asyncio.to_thread(transfer.chunk_digest, data)
                await conn.send_chunk(index, chunk, data)
                in_flight[chunk] = len(data)
            # A STREAM SILENT THIS LONG WITH A CHUNK IN FLIGHT IS DROPPED, and its share goes back
            # to be cut again: one stuck connection must not hold a file for the rest of the
            # session.
            frame = await conn.read(live.owner.watchdog_seconds)
            live.watchdog.heard()
            if isinstance(frame, Chunk):
                raise ProtocolError("a chunk from the receiver")
            if frame.get("skip") == index:
                await self._settle(out, done=False)
                return True
            chunk_index = frame.get("ack", frame.get("again"))
            if frame.get("file") != index or chunk_index not in in_flight:
                raise ProtocolError("an answer about a chunk not in flight")
            if "ack" in frame:
                in_flight.pop(chunk_index)
                out.landed.add(chunk_index)
                self._count(index, chunk_index)
                continue
            out.retries[chunk_index] = out.retries.get(chunk_index, 0) + 1
            in_flight.pop(chunk_index)
            if out.retries[chunk_index] > MAX_RETRIES:
                # The receiver says so too, and skips; this side stops offering the chunk
                # regardless, so a receiver asking for it forever cannot keep this side sending
                # it forever.
                await self._settle(out, done=False)
                return True
            todo.appendleft(chunk_index)
        return await self._hear_the_end(conn, out)

    def _header(self, out: _Outgoing, share: list[int]) -> dict[str, Any]:
        """A share's header: the file's size and its whole digest, or for a receiver that checks
        the whole at the end, its version (`pieces`)."""
        ready, device, offer = out.ready, self.live.device, self.offer
        header: dict[str, Any] = {"file": out.index, "size": ready.size, "chunk_size": CHUNK_SIZE}
        if self.peer_once and device is not None and offer is not None:
            header["version"] = pieces.version_of(device, offer.files[out.index].key, ready.stamp)
        else:
            header["digest"] = ready.digest
        if self.peer_striped:
            header["chunks"] = share
        return header

    async def _hear_the_end(self, conn: Conn, out: _Outgoing) -> bool:
        """Every chunk of the share acknowledged: the receiver says whether it was the file's last
        (and checks the whole file, asking for the pieces' digest first under `once`) or only this
        share's."""
        index = out.index
        while True:
            frame = await conn.read()
            self.live.watchdog.heard()
            if isinstance(frame, Chunk):
                raise ProtocolError("a chunk from the receiver")
            if frame.get("file_done") == index:
                await self._settle(out, done=True)
                return True
            if frame.get("skip") == index:
                await self._settle(out, done=False)
                return True
            if self.peer_striped and frame.get("share_done") == index:
                return True
            if self.peer_once and frame.get("check") == index:
                answer: dict[str, Any]
                try:
                    answer = {"pieces": await pieces.pieces_of(out.ready, out.pieces)}
                except transfer.Changed:
                    log.info("swap.file_changed", swap=self.live.short_id, file=index)
                    answer = {"cannot": True}
                await conn.send({"file": index, **answer})
                continue
            if "ack" in frame and frame.get("file") == index:
                continue
            raise ProtocolError("an answer about another file")
