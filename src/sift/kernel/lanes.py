# SPDX-License-Identifier: AGPL-3.0-or-later
"""How many files may be read from one storage at the same time.

A network share collapses under concurrent seeking readers: a dozen at once deliver about half
what two do, while the processor sits at a quarter. So the cap belongs to the STORAGE. Every read
of a library file takes a place in its storage's lane first, and a network lane has as many places
as that storage measured (two until it is), or the setting's number where one is set. A local
disk has no cap; its measured number only says how many files a reader keeps open.

**The lane is taken around the read, not around the job, and that is the whole design.** A job
that recognises faces reads a handful of frames and then computes for seconds; a job that
describes a picture reads once and then runs a model. Holding a lane for the job's whole life would
leave most of the workers idle for the length of every inference, so the lane is held for exactly
as long as the file is open. What the job count governs is the processor; what this governs is the
wire. The two are different resources, and a NAS install needs them counted apart: one knob
cannot be both.

**A place let go goes to whoever has waited longest**, never back to the reader that let it go.
A reader that reads a file a block at a time (a swap's whole-file digest) takes its place again for
every block, and it comes back for the next one before anybody it woke has run. Were a freed place
free for anyone to take, that reader would take it back every time: two such readers would hold
both places of a two-place share for the whole of their files, a block at a time on paper and a
file at a time in fact, and every other read on the share would wait for a whole file. So a release
hands the place straight to the first waiter, and a reader coming back joins the end of the queue.

**And every fourth place goes to an ordinary reader while both kinds wait.** A reader that asked to
go first (`first()`: a scan taking files in, a swap somebody is watching) is handed a place before
the rest, and a swap asks for places without a break for as long as it runs. Strictly first, the
ordinary reads on the share (a picture being made, a file somebody opened) would wait behind the
whole of it. So of the places handed on while both kinds are waiting, every `ORDINARY_TURN`th goes
to the ordinary reader that has waited longest: the readers that asked to go first still have
most of the share, and nothing else on it stops.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import time
from collections import deque
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path

from sift.kernel.content.mounts import Storage, storage_of
from sift.kernel.log import get_logger

log = get_logger(__name__)

#: How many files are read at once from a network share nobody has measured or set: the number
#: seen to work, rather than a larger one nobody has shown holds.
NETWORK_READS_AT_ONCE = 2

#: The most anybody may set by hand. Above this a share is being asked to do what the measurement
#: says it cannot; a person who wants more than this has a link nobody here has seen.
MAX_READS_AT_ONCE = 64

#: A wait worth reporting, in seconds: the same quarter second the loop and the pools warn at.
WAIT_WARN_SECONDS = 0.25

#: Of the places handed on while readers that asked to go first and ordinary readers both wait,
#: which one in how many goes to the oldest ordinary reader. See the module docstring.
ORDINARY_TURN = 4


@functools.lru_cache(maxsize=4096)
def _storage_of_directory(directory: str) -> Storage:
    """Which storage a directory is on, remembered. Asked per read, so it is not asked of the
    operating system per read: a library of a million files has hundreds of folders, not a
    million, and a folder does not move storages while Sift runs."""
    return storage_of(Path(directory))


def storage_for(path: Path) -> Storage:
    """Which storage a file is on, by its folder."""
    return _storage_of_directory(str(path.parent))


@dataclass
class Lane:
    """One storage's places, and who is waiting for one."""

    storage: Storage
    limit: int
    """How many may read at once; zero means no cap."""
    active: int = 0
    waiting: int = 0
    #: How many of those waiting asked to go first. See `first()`. While any does, an ordinary
    #: waiter waits for its turn (`hand_on`).
    urgent_waiting: int = 0
    waits: int = 0
    """How many reads had to wait at all."""
    worst_wait: float = 0.0
    #: Seconds waited in all, by readers that asked to go first and by the rest.
    urgent_wait: float = 0.0
    ordinary_wait: float = 0.0
    #: Who is waiting, oldest first: those who asked to go first, and everyone else. Each is told
    #: by its future once a place has been handed to it (see the module docstring).
    _urgent: deque[asyncio.Future[None]] = field(default_factory=deque)
    _ordinary: deque[asyncio.Future[None]] = field(default_factory=deque)
    #: The reads holding a place for a whole file start to end (`whole_file`), and who waits to.
    whole: int = 0
    _whole_waiters: deque[asyncio.Future[None]] = field(default_factory=deque)
    #: How many places were handed on while both kinds were waiting. See `hand_on`.
    _contested: int = 0

    @property
    def capped(self) -> bool:
        return self.limit > 0

    def _free_for(self, urgent: bool) -> bool:
        """Whether a reader arriving now may take a place without queueing: one is free and nobody
        it must not pass is waiting for it."""
        if not self.capped:
            return True
        if self.active >= self.limit:
            return False
        return not self._urgent if urgent else not (self._urgent or self._ordinary)

    def hand_on(self) -> None:
        """Give every free place to whoever has waited longest, a reader that asked to go first
        before the rest, except that every `ORDINARY_TURN`th place handed on while both kinds wait
        goes to the oldest ordinary reader. A waiter given up while it waited is passed over."""
        while not self.capped or self.active < self.limit:
            queue = self._urgent or self._ordinary
            if not queue:
                return
            waiter = queue.popleft() if queue is self._ordinary else self._next_while_both_wait()
            if waiter.done():
                continue
            self.active += 1
            waiter.set_result(None)

    def _next_while_both_wait(self) -> asyncio.Future[None]:
        """The next waiter while a reader that asked to go first waits: that reader, or, on the
        ordinary readers' turn and while one of them waits, the oldest of them."""
        if not self._ordinary:
            return self._urgent.popleft()
        self._contested += 1
        if self._contested % ORDINARY_TURN == 0:
            return self._ordinary.popleft()
        return self._urgent.popleft()

    async def take(self, urgent: bool) -> float | None:
        """Take a place, queueing for one when none is free. How long it waited, or None when it
        did not wait at all."""
        if self._free_for(urgent):
            self.active += 1
            return None
        began = time.monotonic()
        waiter: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        queue = self._urgent if urgent else self._ordinary
        queue.append(waiter)
        self.waiting += 1
        self.waits += 1
        self.urgent_waiting += urgent
        try:
            await waiter
        except BaseException:
            if waiter.done() and not waiter.cancelled():
                # Handed a place in the same moment it was given up: the place goes on.
                self.active -= 1
                self.hand_on()
            else:
                with contextlib.suppress(ValueError):
                    queue.remove(waiter)
            raise
        finally:
            self.waiting -= 1
            self.urgent_waiting -= urgent
        return time.monotonic() - began

    def give_back(self) -> None:
        self.active -= 1
        self.hand_on()

    @property
    def whole_limit(self) -> int:
        """How many reads may hold a place for a whole file at once: all of a capped storage's
        places but one, and never none. Zero means no cap."""
        return max(1, self.limit - 1) if self.capped else 0

    def hand_on_whole(self) -> None:
        while self._whole_waiters and (not self.whole_limit or self.whole < self.whole_limit):
            waiter = self._whole_waiters.popleft()
            if waiter.done():
                continue
            self.whole += 1
            waiter.set_result(None)

    async def take_whole(self) -> None:
        if not self._whole_waiters and (not self.whole_limit or self.whole < self.whole_limit):
            self.whole += 1
            return
        waiter: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._whole_waiters.append(waiter)
        try:
            await waiter
        except BaseException:
            if waiter.done() and not waiter.cancelled():
                self.whole -= 1
                self.hand_on_whole()
            else:
                with contextlib.suppress(ValueError):
                    self._whole_waiters.remove(waiter)
            raise

    def give_back_whole(self) -> None:
        self.whole -= 1
        self.hand_on_whole()

    def as_dict(self) -> dict[str, object]:
        return {
            "remote": self.storage.remote,
            "limit": self.limit or None,
            "active": self.active,
            "waiting": self.waiting,
            "waits": self.waits,
            "worst_wait_seconds": round(self.worst_wait, 3),
            "urgent_wait_seconds": round(self.urgent_wait, 3),
            "ordinary_wait_seconds": round(self.ordinary_wait, 3),
        }


class StorageLanes:
    """Every storage Sift has read from since it started, and its places."""

    def __init__(self, *, network_reads_at_once: int = 0) -> None:
        #: The setting's number for every share, or 0 for each share as measured.
        self._network = network_reads_at_once
        self._measured: dict[str, int] = {}
        self._lanes: dict[str, Lane] = {}

    async def configure(
        self, *, network_reads_at_once: int, measured: Mapping[str, int] | None = None
    ) -> bool:
        """Set the number for every share (0: each as measured) and, when given, each storage's
        measured number. True when either changed. A lane that grew hands its new places on."""
        wanted = max(0, min(MAX_READS_AT_ONCE, network_reads_at_once))
        known = self._measured
        if measured is not None:
            known = {key: max(1, min(MAX_READS_AT_ONCE, n)) for key, n in measured.items()}
        if wanted == self._network and known == self._measured:
            return False
        self._network, self._measured = wanted, known
        for lane in self._lanes.values():
            if lane.storage.remote:
                lane.limit = self._limit(lane.storage)
                lane.hand_on()
                lane.hand_on_whole()
        return True

    def _limit(self, storage: Storage) -> int:
        if not storage.remote:
            return 0
        return self._network or self._measured.get(storage.key, NETWORK_READS_AT_ONCE)

    @property
    def network_reads_at_once(self) -> int:
        return self._network

    def lane_for(self, path: Path) -> Lane:
        storage = storage_for(path)
        lane = self._lanes.get(storage.key)
        if lane is None:
            lane = Lane(storage=storage, limit=self._limit(storage))
            self._lanes[storage.key] = lane
            log.info("lanes.storage_seen", storage=storage.key, remote=storage.remote)
        return lane

    def reads_at_once(self, path: Path) -> int:
        """How many files a reader of `path`'s storage keeps open: the cap on a share, the
        measured number or `LOCAL_READS_AT_ONCE` on a disk."""
        lane = self.lane_for(path)
        if lane.capped:
            return lane.limit
        return self._measured.get(lane.storage.key, LOCAL_READS_AT_ONCE)

    @asynccontextmanager
    async def reading(self, path: Path) -> AsyncIterator[None]:
        """Hold a place in the storage's lane while the file is read. Uncapped storages cost a
        dictionary lookup and nothing else."""
        lane = self.lane_for(path)
        if not lane.capped:
            yield
            return
        # An ordinary read has every fourth place handed on while a first reader waits (`hand_on`).
        urgent = _FIRST.get()
        waited = await lane.take(urgent)
        if waited is not None:
            if urgent:
                lane.urgent_wait += waited
            else:
                lane.ordinary_wait += waited
            if waited > lane.worst_wait:
                lane.worst_wait = waited
            if waited >= WAIT_WARN_SECONDS:
                log.info(
                    "lanes.waited", storage=lane.storage.key, seconds=round(waited, 3), first=urgent
                )
        try:
            yield
        finally:
            lane.give_back()

    def readings(self) -> dict[str, dict[str, object]]:
        """Every lane's figures, for /health and the Performance screen."""
        return {key: lane.as_dict() for key, lane in self._lanes.items()}

    @property
    def worst_wait_seconds(self) -> float:
        return max((lane.worst_wait for lane in self._lanes.values()), default=0.0)


#: The lanes this process reads through, or None where nothing has installed any: a console
#: tool, a migration, most of the test suite. None means every read goes straight through, with
#: no cap at all.
_LANES: StorageLanes | None = None

#: Whether the reads under way in this task asked to go first. Set by `first()`, read by
#: `reading()`; a context variable so it follows the scan through every helper it calls without a
#: parameter on each.
_FIRST: ContextVar[bool] = ContextVar("lanes_first", default=False)

#: How many files one reader keeps open on a local disk nobody has measured: enough to keep an
#: NVMe busy without turning a spinning disk into a seek storm.
LOCAL_READS_AT_ONCE = 4


@asynccontextmanager
async def first() -> AsyncIterator[None]:
    """Every read made under this goes to the front of its storage's lane.

    For the scan taking files in: on one share the scan's reads and the probes of the files it
    just took in compete for the same two slots, and in arrival order the files appear at the
    pace the probes leave room for. The answer to which should come first is
    files, and this is the whole of that answer: nothing is reserved, and a probe still reads
    whenever no scan read is waiting.
    """
    token = _FIRST.set(True)
    try:
        yield
    finally:
        _FIRST.reset(token)


def reads_at_once(path: Path) -> int:
    """How many files a reader of `path`'s storage keeps open; one where no lanes are installed."""
    lanes = _LANES
    return 1 if lanes is None else lanes.reads_at_once(path)


def install(lanes: StorageLanes | None) -> None:
    """Set the lanes every read in this process goes through, or clear them."""
    global _LANES
    _LANES = lanes


def installed() -> StorageLanes | None:
    return _LANES


@asynccontextmanager
async def reading(path: Path) -> AsyncIterator[None]:
    """Hold a place in `path`'s storage lane while it is read. The one call every reader makes.

    A module-level door rather than something passed down, for the reason the change bus has one:
    the reads are spread across the kernel's hashing, the media helpers and three slices' frame
    readers, and threading a lanes object through all of them would put a storage parameter on
    every function in Sift that opens a library file.
    """
    lanes = _LANES
    if lanes is None:
        yield
        return
    async with lanes.reading(path):
        yield


@asynccontextmanager
async def whole_file(path: Path) -> AsyncIterator[None]:
    """Around a read that keeps its place for a whole file, start to end: a stream copy a tool
    makes of it. On a capped storage at most all its places but one are held that way at once, so
    the reads that let go block by block (a swap's streams, a scan) always have one, rather than
    every one of them waiting out two copies of two whole files. Not a place itself: the read
    inside still takes its place as every read does."""
    lanes = _LANES
    if lanes is None:
        yield
        return
    lane = lanes.lane_for(path)
    await lane.take_whole()
    try:
        yield
    finally:
        lane.give_back_whole()


@asynccontextmanager
async def reading_if(path: Path | None) -> AsyncIterator[None]:
    """`reading`, for a caller that may or may not be opening a library file at all."""
    if path is None:
        yield
        return
    async with reading(path):
        yield
