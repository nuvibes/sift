# SPDX-License-Identifier: AGPL-3.0-or-later
"""The receiving half of a direction of a swap.

## Each file read once

A receiver that checks the whole first is told the file's digest in its header, so the sender reads
the file whole before its first piece and again to send it: twice over a network share. A receiver
whose hello says `once` checks the whole after the last piece instead (`pieces`): the header names
the file's version, the sender keeps each piece's digest as it sends it, and the stream that finds
the file whole asks for the digest of those digests in piece order. Its manifest is keyed by that
version (the file, its size and its modification time, signed by the sender), so a cut or a later
swap resumes from the chunks it holds. Both sides say `once` in every hello but a one-way host's;
an older Sift never says it, and is sent and answered the shape it knows.

Every read the host makes for a swap (the look, a digest, a strip, a chunk) goes to the front of its
storage's lane (`lanes.first`): a swap somebody started and is watching is not background work, and
on a network share it is not queued behind the library's own passes. The lane's limit still holds:
two reads together on a share, each let go before its chunk is sent, and a place let go goes to the
read that has waited longest (`sift.kernel.lanes`), so a digest read a block at a time leaves room
for the streams between its blocks.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from sift.kernel import lanes
from sift.kernel.destination import resolve_destination
from sift.kernel.ingress import NoDestination
from sift.kernel.log import get_logger
from sift.slices.swap import pieces, transfer
from sift.slices.swap.frames import Chunk as Chunk
from sift.slices.swap.frames import Conn as Conn
from sift.slices.swap.frames import ProtocolError as ProtocolError
from sift.slices.swap.handshake import LOST
from sift.slices.swap.ingest import (
    DIGEST,
    STRIP,
    UNWANTED,
    Landed,
    LandingSession,
    Refused,
    received_from_offer,
)
from sift.slices.swap.live import (
    _EMPTY,
    Answer,
    Taken,
    _Figures,
    _Incoming,
    _int_list,
    _remove,
    _wanted_indexes,
)
from sift.slices.swap.manifest import Grouping, grouping
from sift.slices.swap.models import Diff, Offer, OfferScreen, RefusedWhy
from sift.slices.swap.transfer import CHUNK_SIZE, MAX_RETRIES, RECORD_EVERY, RECORD_SECONDS

log = get_logger(__name__)

# --- receiving ----------------------------------------------------------------------------------


class _Receiving(_Figures):
    """The receiving half of a direction: the offer read, the answer to it, and the wanted files
    received in shares, checked whole and landed. The guest's in a swap one way; in a swap that
    sends and receives, the host's as well (`_BackReceiving`)."""

    def _start_receiving(self, dest_folder_id: str | None) -> None:
        loop = asyncio.get_running_loop()
        #: Where received files go ("Put received files in").
        self.dest_folder_id = dest_folder_id
        self.offer_in: asyncio.Future[Mapping[str, Any]] = loop.create_future()
        self.taken: asyncio.Future[Taken] = loop.create_future()
        self.all_resolved: asyncio.Future[None] = loop.create_future()
        self.screen: OfferScreen | None = None
        #: The diff for an answer, held from the offer's arrival: what Take these sends, and what
        #: the offer screen weighs before it is pressed.
        self.answer: Answer | None = None
        self.diff: Diff | None = None
        self.landing_session: LandingSession | None = None
        self.skipped: frozenset[int] = frozenset()
        self.wanted_set: set[int] = set()
        self.verified: set[int] = set()
        self.unwanted = 0
        self.landing: asyncio.Queue[tuple[int, Path, str, int]] = asyncio.Queue()
        #: The files being received, by offer index, until each is verified or dropped; and the
        #: lock that lets one stream alone open a file's manifest.
        self.incoming: dict[int, _Incoming] = {}
        self._opening: dict[int, asyncio.Lock] = {}
        #: Whether this side has said `done`: everything it wanted landed or failed.
        self.said_done = False
        #: The offer's groups, read once when it arrives (`manifest.py`).
        self.groups: Grouping | None = None
        #: Why each refused file was refused, by its place in the offer.
        self.refusals: dict[int, RefusedWhy] = {}
        #: What each landed file was filed as, by its place in the offer, for the receipt.
        self.landed: dict[int, Landed] = {}
        #: The offer's indexes in the order they were opened: the last is the file crossing now.
        self.opened: dict[int, None] = {}
        #: Offered people whose first file has landed, and those whose first is landing now.
        self._settled: set[int] = set()
        self._settling: dict[int, asyncio.Event] = {}

    def _receiving_started(self) -> None:
        """What a side does once its answer is sent and the files can move: nothing on the host,
        whose streams are dialled by the guest."""

    async def take_and_receive(self) -> bool:
        """The other side's offer read the moment it arrives, Take these waited for, the answer
        sent, and every wanted file received and landed; then `done` said. True once said;
        False when the session ended first."""
        live = self.live
        # THE RECEIVER WAITS AT THE CODE until the offer arrives: the sender's They match
        # releases it.
        if not await live.until(self.offer_in):
            return False
        try:
            # THE OFFER IS A PEER'S, read through the offer's own strict shapes: a field nobody
            # declared, a list past its cap or a person index pointing nowhere refuses it whole.
            offer = Offer.model_validate(self.offer_in.result())
        except ValidationError:
            log.info("swap.lie", swap=live.short_id, what="offer")
            await live.end(LOST)
            return False
        self.screen, answer = await live.owner.assess(offer)
        self.answer = answer
        self.offer = offer
        self.groups = grouping(offer)
        await live.owner.store.set_offered(live.id, len(offer.files), back=self.back)
        await live.advance("offered")
        if not await live.until(self.taken):
            return False
        taken = self.taken.result()
        diff = answer(taken)
        try:
            self.wanted = _wanted_indexes(offer, diff)
        except ProtocolError:
            log.warning("swap.diff_not_of_the_offer", swap=live.short_id)
            await live.end(LOST)
            return False
        self.diff = diff
        self.skipped = taken.skipped
        self.wanted_set = set(self.wanted)
        self.wanted_bytes = sum(offer.files[n].size for n in self.wanted)
        self.landing_session = LandingSession(
            id=live.id,
            peer_device=str(live.peer),
            dest_folder_id=str(self.dest_folder_id),
            wanted=frozenset(offer.files[n].key for n in self.wanted),
        )
        # The answer as SENT: the wanted keys, and none of this library's people (`Diff`). Through
        # a cut if there is one: joined again, it goes again anyway (`say_again`).
        if not await live.send_control({"diff": diff.for_the_other_side().model_dump(mode="json")}):
            return False
        await live.owner.store.set_wanted(live.id, len(self.wanted), back=self.back)
        await live.advance("transferring")
        # The people offered for their facial fingerprints alone arrive with no file, so nothing
        # below would ever land them: they land here, once, before the files. A failure costs
        # those fingerprints and never the swap.
        try:
            await live.owner.land_fingerprints(
                self.landing_session, offer, matched=diff.people, skipped=taken.skipped
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            log.warning(
                "swap.fingerprints_not_landed", swap=live.short_id, error=type(error).__name__
            )
        self.transferring = True
        live.start_measuring()
        live.spawn(self.land_each(), "land")
        self._receiving_started()
        self.check_resolved()
        if not await live.until(self.all_resolved):
            return False
        await self.landing.join()
        self.said_done = True
        with contextlib.suppress(Exception):
            await asyncio.wait_for(
                live.send_control({"done": {"received": len(self.done_files)}}),
                live.owner.watchdog_seconds,
            )
        return True

    def check_resolved(self) -> None:
        if self.transferring and self.resolved:
            self.live.settle(self.all_resolved, None)

    @property
    def received_all(self) -> bool:
        """Every wanted file verified or failed: what a stream stops on. Landing may still be
        going: `resolved` waits for that too."""
        return len(self.verified | self.failed_files) >= len(self.wanted)

    async def receive(self, conn: Conn, *, delivered: Callable[[], None] | None = None) -> bool:
        """Receive files on one stream. True when the sender says there are none left for it.

        `delivered` is told at each thing this connection moved (a chunk written, a file
        answered): the caller counts connections that failed in a row having moved nothing, and a
        frame alone does not prove a connection worked if its first chunk fails the same way.
        """
        live = self.live
        worked = delivered or _nothing
        while not live.ended.is_set():
            header = await conn.read()
            live.watchdog.heard()
            if isinstance(header, Chunk):
                raise ProtocolError("a chunk before its file")
            if header.get("none"):
                return True
            index = header.get("file")
            if not isinstance(index, int) or isinstance(index, bool):
                raise ProtocolError("a file with no index")
            if index in self.verified:
                # Verified already, and the word was lost with a dropped stream: say it again.
                await conn.send({"file_done": index})
            elif index not in self.wanted_set or index in self.failed_files:
                # REFUSED AND COUNTED: a file nobody asked for is not received, whatever it is.
                self.unwanted += 1
                await conn.send({"skip": index})
            elif header.get("cannot"):
                self.failed_files.add(index)
                self.check_resolved()
            else:
                await self._receive_file(conn, index, header, worked)
            worked()
        return False

    async def _manifest_for(
        self, key: str, size: int, digest: str, once: bool
    ) -> tuple[Path, tuple[int, ...]]:
        live = self.live
        owner = live.owner
        store = owner.store
        now = owner.now()
        # The bytes are named by the whole digest, or under `once` by the sender's version.
        named = (None, digest) if once else (digest, None)
        found = await store.manifest(live.id, key)
        if (
            found is not None
            and (found.size, found.digest, found.version) == (size, *named)
            and found.chunk_size == CHUNK_SIZE
            and await asyncio.to_thread(owner.staged_is_ours, found.staged_path)
        ):
            return Path(str(found.staged_path)), found.done
        earlier = await store.adoptable(
            live.id,
            peer_device=str(live.peer),
            file_key=key,
            size=size,
            chunk_size=CHUNK_SIZE,
            digest=named[0],
            version=named[1],
        )
        if earlier is not None and await asyncio.to_thread(
            owner.staged_is_ours, earlier.staged_path
        ):
            adopted = await store.adopt(earlier, live.id, now)
            return Path(str(adopted.staged_path)), adopted.done
        path = transfer.staged_path(owner.staging, live.id, key)
        await asyncio.to_thread(_remove, path)
        await store.put_manifest(
            live.id,
            key,
            size=size,
            chunk_size=CHUNK_SIZE,
            digest=named[0],
            staged_path=str(path),
            now=now,
            version=named[1],
        )
        return path, ()

    def key_of(self, index: int) -> str:
        if self.offer is None:  # pragma: no cover (files are received only after the offer)
            raise ProtocolError("a file before the offer")
        return self.offer.files[index].key

    async def _drop(self, index: int, state: _Incoming) -> None:
        """A file failed: every stream still carrying a share of it turns it down from here."""
        state.dropped = True
        self.incoming.pop(index, None)
        self.failed_files.add(index)
        await self.live.owner.store.drop_manifest(self.live.id, self.key_of(index))
        await asyncio.to_thread(_remove, state.path)
        self.check_resolved()

    async def _incoming_for(self, index: int, size: int, digest: str, once: bool) -> _Incoming:
        """The file's one map of missing chunks, opened by the first stream to carry a share of
        it: a resumed manifest, one adopted from an earlier swap, or a new one."""
        async with self._opening.setdefault(index, asyncio.Lock()):
            state = self.incoming.get(index)
            if state is not None:
                if (state.size, state.digest, state.once) != (size, digest, once):
                    raise ProtocolError("a file header that disagrees with the one before")
                return state
            path, done = await self._manifest_for(self.key_of(index), size, digest, once)
            self.opened[index] = None
            count = transfer.chunk_count(size)
            missing = set(transfer.missing(done, count))
            state = _Incoming(
                path,
                size,
                digest,
                count,
                missing,
                once=once,
                recorded=count - len(missing),
                recorded_at=self.live.owner.clock(),
            )
            self.incoming[index] = state
            return state

    async def _receive_file(
        self, conn: Conn, index: int, header: Mapping[str, Any], worked: Callable[[], None]
    ) -> None:
        """Receive one share of a file on one stream: the chunks the header names, or the whole
        file when it names none (a sender that sends whole files)."""
        if self.offer is None:  # pragma: no cover (files are received only after the offer)
            raise ProtocolError("a file before the offer")
        live = self.live
        size, digest, once = _checked_header(header, self.offer.files[index].size)
        count = transfer.chunk_count(size)
        named = header.get("chunks")
        share = None if named is None else set(_int_list(named, below=count))
        state = await self._incoming_for(index, size, digest, once)
        key = self.key_of(index)
        # What this stream waits for and what it says it has come from one look at the map, so a
        # chunk another stream writes in between is acknowledged, never waited for twice.
        remaining = set(state.missing) if share is None else state.missing & share
        await conn.send({"have": sorted(set(range(count)) - state.missing)})
        while remaining:
            frame = await conn.read(live.owner.watchdog_seconds)
            if not isinstance(frame, Chunk) or frame.file != index or frame.index >= count:
                raise ProtocolError("a chunk that isn't one of this file's")
            live.watchdog.heard()
            if state.is_dropped():
                await conn.send({"skip": index})
                return
            if frame.index not in state.missing:
                remaining.discard(frame.index)
                await conn.send({"ack": frame.index, "file": index})
                continue
            good = len(frame.data) == transfer.chunk_length(size, frame.index) and (
                await asyncio.to_thread(transfer.chunk_digest, frame.data) == frame.digest
            )
            if not good:
                if await self._refused_again(conn, index, state, frame.index):
                    return
                continue
            await _on_disk(transfer.write_chunk, state.path, frame.index, frame.data)
            if state.is_dropped():
                # Failed on another stream while this chunk was written: nothing of it stays.
                await asyncio.to_thread(_remove, state.path)
                await conn.send({"skip": index})
                return
            if frame.index in state.missing:
                state.missing.discard(frame.index)
                self.moved(len(frame.data))
            worked()
            remaining.discard(frame.index)
            await conn.send({"ack": frame.index, "file": index})
            self._record_beside(state, key)
        await self._conclude(conn, index, state)

    def _record_beside(self, state: _Incoming, key: str) -> None:
        """The record, when due, beside the stream: a sync and a database write never sit ahead of
        the ack the sender's window waits for."""
        if not self._due(state):
            return
        state.pending = state.count - len(state.missing)
        state.recorded_at = self.live.owner.clock()
        state.record = self.live.spawn(self._record(state, key), "record")

    def _due(self, state: _Incoming) -> bool:
        unrecorded = state.count - len(state.missing) - max(state.recorded, state.pending)
        waited = self.live.owner.clock() - state.recorded_at
        return unrecorded >= RECORD_EVERY or (unrecorded > 0 and waited >= RECORD_SECONDS)

    async def _record(self, state: _Incoming, key: str) -> None:
        """Write down whatever chunks the file holds unrecorded, the staged file synced to the disk
        first. When it is due is the caller's (`_record_beside`, `_conclude`, `record_all`)."""
        live = self.live
        async with state.recording:
            done = set(range(state.count)) - state.missing
            if state.is_dropped() or len(done) == state.recorded:
                return
            await _on_disk(transfer.sync_file, state.path)
            if state.is_dropped():
                return
            await live.owner.store.record_done(live.id, key, done, live.owner.now())
            state.recorded = len(done)

    async def record_all(self) -> None:
        """Every file part-way through written down as it stands, for a later swap to resume. A
        failure here costs only the chunks since that file's last record."""
        for index, state in list(self.incoming.items()):
            with contextlib.suppress(Exception):
                await self._record(state, self.key_of(index))

    async def _refused_again(self, conn: Conn, index: int, state: _Incoming, chunk: int) -> bool:
        """Ask for a bad chunk again, or past the retries drop the file; True when dropped."""
        state.attempts[chunk] = state.attempts.get(chunk, 0) + 1
        if state.attempts[chunk] > MAX_RETRIES:
            log.info("swap.chunk_refused", swap=self.live.short_id, file=index)
            self.refusals[index] = "bytes"
            await self._drop(index, state)
            await conn.send({"skip": index})
            return True
        await conn.send({"again": chunk, "file": index})
        return False

    async def _conclude(self, conn: Conn, index: int, state: _Incoming) -> None:
        """A share's chunks are all in: the stream that finds the file whole checks it and says
        `file_done` (or turns it down); any other stream says its share is done."""
        # A record of the file still in flight ends first: a drop it meets is this share's word too.
        if state.record is not None and not state.record.done():
            with contextlib.suppress(Exception):
                await state.record
        if state.dropped:
            await conn.send({"skip": index})
            return
        if index in self.verified or state.missing or state.checking:
            await conn.send({"share_done": index})
            return
        state.checking = True
        try:
            await self._record(state, self.key_of(index))
            whole = await self._whole_of(conn, index, state)
        except BaseException:
            state.checking = False
            raise
        if whole is None:
            log.info("swap.file_refused", swap=self.live.short_id, file=index)
            self.refusals[index] = "bytes"
            await self._drop(index, state)
            await conn.send({"skip": index})
            return
        # Written down before it is said: a word lost with the stream is said again on the next
        # header for the file (`receive`).
        self.verified.add(index)
        self.incoming.pop(index, None)
        await self.landing.put((index, state.path, whole, state.size))
        await conn.send({"file_done": index})

    async def _whole_of(self, conn: Conn, index: int, state: _Incoming) -> str | None:
        """The staged file's whole digest when it is the file sent, else None. Under `once` the
        sender is asked for its pieces' digest, and the staged file is read once for both."""
        if not state.once:
            whole = (
                await asyncio.to_thread(transfer.digest_file, state.path) if state.count else _EMPTY
            )
            return whole if whole == state.digest else None
        await conn.send({"check": index})
        answer = await conn.read()
        if isinstance(answer, Chunk) or answer.get("file") != index:
            raise ProtocolError("an answer about another file")
        if answer.get("cannot"):
            return None
        whole, ours = await asyncio.to_thread(pieces.staged_digests, state.path, state.size)
        return whole if answer.get("pieces") == ours else None

    async def land_each(self) -> None:
        """Hand the verified files to the landing as they finish, as many together as the take-in
        reads into the same library folder (`_landing_at_once`)."""
        gate = asyncio.Semaphore(await self._landing_at_once())
        while True:
            await gate.acquire()
            index, path, digest, _size = await self.landing.get()
            self.live.spawn(self._land_one(gate, index, path, digest), "land")

    async def _landing_at_once(self) -> int:
        """The take-in's number for the destination's storage (`lanes.reads_at_once`); one where
        there is no destination to read it from, and the landing refuses each file anyway."""
        library = getattr(self.live.context, "library", None)
        if library is None:
            return 1
        try:
            destination = await resolve_destination(library, self.dest_folder_id)
        except NoDestination:
            return 1
        return max(1, lanes.reads_at_once(Path(destination.root.abs_path)))

    @contextlib.asynccontextmanager
    async def _first_of_each(self, people: Sequence[int]) -> AsyncIterator[None]:
        """A person's first file lands alone: who they are here and their faces are settled by it
        before another file of theirs is filed. A file waits holding no claim, so none deadlock."""
        while busy := [self._settling[one] for one in people if one in self._settling]:
            await busy[0].wait()
        mine = {one for one in people if one not in self._settled}
        for one in mine:
            self._settling[one] = asyncio.Event()
        landed = False
        try:
            yield
            landed = True
        finally:
            for one in mine:
                if landed:
                    self._settled.add(one)
                self._settling.pop(one).set()

    async def _land_one(self, gate: asyncio.Semaphore, index: int, path: Path, digest: str) -> None:
        live = self.live
        try:
            if self.offer is None or self.diff is None or self.landing_session is None:
                raise ProtocolError("a file before the diff")  # pragma: no cover
            offer = self.offer
            people = [one.model_dump(mode="json") for one in offer.people]
            received = received_from_offer(
                offer.files[index].model_dump(mode="json"),
                people,
                matched=self.diff.people,
                taken=[n for n in range(len(people)) if n not in self.skipped],
                staged=path,
                digest=digest,
                checked=True,
            )
            async with self._first_of_each(offer.files[index].people):
                # The landing counts the file on this row itself, in the transaction that files
                # it (`store.count_landed_on`): a count written here as well would be a second.
                landed = await live.owner.land(self.landing_session, received, ctx=live.context)
            if isinstance(landed, Landed):
                self.landed[index] = landed
            self.done_files.add(index)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.refusals[index] = _refused_why(error)
            # The file is counted as failed and the swap goes on. The type only: a landing's
            # message can carry a path, and this line is about a session.
            log.warning("swap.landing_failed", swap=live.short_id, error=type(error).__name__)
            self.failed_files.add(index)
        finally:
            gate.release()
            await asyncio.to_thread(_remove, path)
            with contextlib.suppress(Exception):
                await live.owner.store.drop_manifest(live.id, self.key_of(index))
            self.landing.task_done()
            self.check_resolved()


def _nothing() -> None:
    """No one counting what a connection moved."""


def _refused_why(error: Exception) -> RefusedWhy:
    """A landing's refusal in the screen's three words."""
    if isinstance(error, Refused) and error.reason in (DIGEST, UNWANTED):
        return "bytes"
    if isinstance(error, Refused) and error.reason == STRIP:
        return "strip"
    return "landing"


async def _on_disk(write: Callable[..., None], *args: Any) -> None:
    """A write to a staged file, off the loop; a full disk raised as `DiskFull`."""
    try:
        await asyncio.to_thread(write, *args)
    except OSError as error:
        if transfer.is_disk_full(error):
            raise transfer.DiskFull from error
        raise


def _checked_header(header: Mapping[str, Any], offered: int) -> tuple[int, str, bool]:
    """A file header's size, digest and whether it is a `once` version, or a refusal."""
    size = header.get("size")
    # A header names the whole digest, or under `once` the sender's version (`pieces`).
    once = "digest" not in header
    digest = header.get("version" if once else "digest")
    # A PEER SENDS NO MORE THAN IT OFFERED. The stripped file is the offered one less its
    # metadata; a size past the offer's (with room for a container rewritten) is a lie.
    if (
        not isinstance(size, int)
        or isinstance(size, bool)
        or not 0 <= size <= offered + offered // 20 + (1 << 20)
        or header.get("chunk_size") != CHUNK_SIZE
        or not isinstance(digest, str)
        or len(digest) != 64
    ):
        raise ProtocolError("a file header past what was offered")
    return size, digest, once
