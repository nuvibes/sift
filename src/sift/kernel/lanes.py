# SPDX-License-Identifier: AGPL-3.0-or-later
"""How many files may be read from one storage at the same time.

A network share collapses under concurrent seeking readers: a dozen together deliver about half
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

**A place let go goes to whoever has waited longest**, never back to the reader that let it go. A
reader taking its place again for every block (a swap's whole-file digest) would otherwise take it
back each time before anybody it woke has run, and two of them would hold both places of a
two-place share for the whole of their files.

**Three kinds of reader, and every fourth place to the kinds below.** A reader that asked to go
first (`first()`: a scan's walk, a swap somebody is watching) is handed a place before a file's
read (`the_read()`: a probe), and that before everything else (a picture, a fingerprint). Strictly
so, the rest would wait out a whole import; so of the places handed on while a lower kind also
waits, every `ORDINARY_TURN`th goes to the lower kinds, by the same rule.
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

#: How many files are read together from a network share nobody has measured or set: the number
#: seen to work, rather than a larger one nobody has shown holds.
NETWORK_READS_AT_A_TIME = 2

#: The most anybody may set by hand. Above this a share is being asked to do what the measurement
#: says it cannot; a person who wants more than this has a link nobody here has seen.
MAX_READS_AT_ONCE = 64

#: A wait worth reporting, in seconds: the same quarter second the loop and the pools warn at.
WAIT_WARN_SECONDS = 0.25

#: Of the places handed on while a lower kind of reader also waits, which one in how many goes to
#: the lower kinds. See the module docstring.
ORDINARY_TURN = 4

#: Off, a file's read (`the_read()`) waits in line with the work made from files already read.
READS_FIRST = False

#: The kinds of reader, lowest first.
ORDINARY, READ, FIRST = 0, 1, 2


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
    """How many may read together; zero means no cap."""
    active: int = 0
    waiting: int = 0
    #: How many of those waiting are of a kind above the ordinary.
    urgent_waiting: int = 0
    waits: int = 0
    """How many reads had to wait at all."""
    worst_wait: float = 0.0
    #: Seconds waited in all, by readers of a kind above the ordinary and by the rest.
    urgent_wait: float = 0.0
    ordinary_wait: float = 0.0
    #: When a file's read last took a place here (`time.monotonic`), or None.
    read_at: float | None = None
    #: Who is waiting, oldest first, by kind (`ORDINARY`, `READ`, `FIRST`).
    _waiters: tuple[deque[asyncio.Future[None]], ...] = field(
        default_factory=lambda: (deque(), deque(), deque())
    )
    #: The reads holding a place for a whole file start to end (`whole_file`), and who waits to.
    whole: int = 0
    _whole_waiters: deque[asyncio.Future[None]] = field(default_factory=deque)
    #: How many places were handed on while a lower kind also waited, by kind. See `_next`.
    _contested: list[int] = field(default_factory=lambda: [0, 0, 0])

    @property
    def capped(self) -> bool:
        return self.limit > 0

    def _free_for(self, rank: int) -> bool:
        """Whether a reader arriving now may take a place without queueing: one is free and nobody
        of its kind or above is waiting for it."""
        if not self.capped:
            return True
        return self.active < self.limit and not any(self._waiters[rank:])

    def hand_on(self) -> None:
        """Give every free place to the next waiter (`_next`), passing over one given up."""
        while not self.capped or self.active < self.limit:
            waiter = self._next()
            if waiter is None:
                return
            if waiter.done():
                continue
            self.active += 1
            waiter.set_result(None)

    def _next(self) -> asyncio.Future[None] | None:
        """The oldest waiter of the highest kind waiting, except every `ORDINARY_TURN`th place
        handed on while a lower kind also waits, which goes to the lower kinds by the same rule."""
        ranks = [rank for rank in (FIRST, READ, ORDINARY) if self._waiters[rank]]
        for at, rank in enumerate(ranks):
            if at + 1 < len(ranks):
                self._contested[rank] += 1
                if self._contested[rank] % ORDINARY_TURN == 0:
                    continue
            return self._waiters[rank].popleft()
        return None

    async def take(self, rank: int) -> float | None:
        """Take a place, queueing for one when none is free. How long it waited, or None when it
        did not wait at all."""
        if self._free_for(rank):
            self.active += 1
            return None
        began = time.monotonic()
        waiter: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        queue = self._waiters[rank]
        queue.append(waiter)
        self.waiting += 1
        self.waits += 1
        urgent = rank > ORDINARY
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
        """How many reads may hold a place for a whole file together: all of a capped storage's
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
        return self._network or self._measured.get(storage.key, NETWORK_READS_AT_A_TIME)

    @property
    def network_reads_at_once(self) -> int:
        return self._network

    def reading_first(self, within: float) -> set[str]:
        """The storages where a file's read took a place in the last `within` seconds."""
        now = time.monotonic()
        return {
            key
            for key, lane in self._lanes.items()
            if lane.read_at is not None and now - lane.read_at < within
        }

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
        measured number or `LOCAL_READS_AT_A_TIME` on a disk."""
        lane = self.lane_for(path)
        if lane.capped:
            return lane.limit
        return self._measured.get(lane.storage.key, LOCAL_READS_AT_A_TIME)

    @asynccontextmanager
    async def reading(self, path: Path) -> AsyncIterator[None]:
        """Hold a place in the storage's lane while the file is read. Uncapped storages cost a
        dictionary lookup and nothing else."""
        lane = self.lane_for(path)
        if not lane.capped:
            yield
            return
        rank = _RANK.get()
        waited = await lane.take(rank)
        if rank == READ:
            lane.read_at = time.monotonic()
        urgent = rank > ORDINARY
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

#: The kind of reader the reads under way in this task are. Set by `first()` and `the_read()`, read
#: by `reading()`; a context variable so it follows a job through every helper it calls.
_RANK: ContextVar[int] = ContextVar("lanes_rank", default=ORDINARY)

#: How many files one reader keeps open on a local disk nobody has measured: enough to keep an
#: NVMe busy without turning a spinning disk into a seek storm.
LOCAL_READS_AT_A_TIME = 4


@asynccontextmanager
async def first() -> AsyncIterator[None]:
    """Every read made under this goes to the front of its storage's lane: a scan's walk, so its
    files are taken in ahead of their probes, and a swap somebody is watching."""
    token = _RANK.set(FIRST)
    try:
        yield
    finally:
        _RANK.reset(token)


@asynccontextmanager
async def the_read() -> AsyncIterator[None]:
    """Every read made under this, a file's read, goes ahead of the work made from files already
    read, behind `first()`: on a share an import's files are all read first, to the last."""
    if not READS_FIRST or _RANK.get() >= READ:
        yield
        return
    token = _RANK.set(READ)
    try:
        yield
    finally:
        _RANK.reset(token)


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
    makes of it. On a capped storage at most all its places but one are held that way together, so
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
