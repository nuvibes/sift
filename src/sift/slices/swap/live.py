# SPDX-License-Identifier: AGPL-3.0-or-later
"""A live session, either side: the watchdog, the stream ladder, what a session is handed, and
the connection both sides keep.

## Cut off, and joined again

A tunnel can drop under a swap: the host's hosting lost at a renewal, the guest's streams failing
past `REDIAL_LIMIT`, or ten seconds of silence either way. None of those is anybody's End, so the
session is not ended: it is CUT OFF. The row says so beside its step (`cut_off_at`), the
connections go, and the task stays, waiting. The host keeps its listener, hosts again on the same
tunnel as soon as the tunnel lets it (the token is remade if the port moved), and takes the same
guest's hello again; the guest dials again every `RETRY_SECONDS`, and a Join with the same token
dials at once. Joined again, each side carries on from its step, resending what the other may not
have heard (the offer, the answer), and a file part-way through resumes from the chunks the guest
already verified. The wait is bounded by the token's own life: a session still cut off when the
token runs out ends as `lost`. A person's End is never a cut: it ends the session on both sides.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections import deque
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from blake3 import blake3

from sift.kernel.access import Viewer
from sift.kernel.jobs import JobContext
from sift.kernel.log import get_logger
from sift.kernel.tunnels import TunnelError
from sift.slices.swap import transfer
from sift.slices.swap.device import (
    Device,
)
from sift.slices.swap.frames import Chunk as Chunk
from sift.slices.swap.frames import Conn as Conn
from sift.slices.swap.frames import ProtocolError as ProtocolError
from sift.slices.swap.handshake import (
    _HEARD,
    _STATE_OF,
    _TELL,
    CUT_OFF_NOTE,
    ENDED_BY_THEM,
    EXIT_WAIT_SECONDS,
    LOST,
    PACE_WINDOWS,
    STREAMS_CAP,
    STREAMS_START,
    STREAMS_WITHOUT_A_RISE,
    TREND_HOLDS,
    TREND_RESUMES,
    TREND_RISES,
    TREND_WINDOWS,
    WATCHDOG_SECONDS,
)
from sift.slices.swap.models import Chosen, Diff, Offer, OfferScreen
from sift.slices.swap.transfer import Prepared
from sift.slices.swap.weight import Weight

if TYPE_CHECKING:
    from sift.slices.swap.guest import _BackSending
    from sift.slices.swap.host import _BackReceiving
    from sift.slices.swap.receiving import _Receiving
    from sift.slices.swap.sending import _Sending
    from sift.slices.swap.session import SwapSessions

log = get_logger(__name__)


class Watchdog:
    """Silence from the other side, measured. `heard` on every frame; `silent` past the limit."""

    def __init__(
        self, seconds: float = WATCHDOG_SECONDS, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.seconds = seconds
        self._clock = clock
        self._last = clock()

    def heard(self) -> None:
        self._last = self._clock()

    def silent(self) -> bool:
        return self._clock() - self._last >= self.seconds


@dataclass
class Ladder:
    """How many streams a transfer uses: eight, then one more each window while the TREND holds,
    to thirty-two. The climb ends when more streams add nothing, and starts again when the line
    gives more.

    A tunnel's upload is bound per stream (a send window across the tunnel's round trip), so the
    total grows with the count until the line or the tunnel is the limit, and a count past that
    point adds nothing. A window's rate wobbles by half from one to the next on the same count, and
    a session of mixed file sizes dips for a window whenever the host is between files, so no one
    window is a verdict. Judged window by window, a climb that ended at the first window under nine
    tenths of the best would end at the first wobble, a few windows in, and stay there for the rest
    of the swap.

    So the trend is the middle rate of the last `TREND_WINDOWS` windows, which one dip cannot move.
    A trend within `TREND_HOLDS` of the best adds a stream; a lower one holds the count and waits.
    Each stream added without a rise of `TREND_RISES` over the best counts toward
    `STREAMS_WITHOUT_A_RISE`, and reaching it ends the climb: that many more streams carried nothing
    more. A trend `TREND_RESUMES` over the best after that starts the climb again, since the line
    (or the files) changed under it.
    """

    streams: int = STREAMS_START
    climbing: bool = True
    #: The best trend seen, in bits a second; the next one is judged against it.
    best: float | None = None
    #: The last windows' rates, newest last, at most `TREND_WINDOWS`.
    rates: list[float] = field(default_factory=list)
    #: Streams added since the trend last rose.
    flat: int = 0

    def window(self, rate: float) -> int:
        """The rate of the window just ended, in bits a second; the streams to use now."""
        self.rates = [*self.rates, rate][-TREND_WINDOWS:]
        trend = sorted(self.rates)[len(self.rates) // 2]
        if self.best is None:
            self.best = trend
            return self.streams
        rose = trend > 0 and trend >= self.best * (TREND_RISES if self.climbing else TREND_RESUMES)
        if rose:
            self.flat = 0
            self.climbing = True
        if self.climbing and trend >= self.best * TREND_HOLDS:
            if self.streams < STREAMS_CAP:
                self.streams += 1
                self.flat += 0 if rose else 1
            if self.flat >= STREAMS_WITHOUT_A_RISE or self.streams >= STREAMS_CAP:
                self.climbing = False
        self.best = max(self.best, trend)
        return self.streams


# --- what a session is handed ------------------------------------------------------------------


class Hosting(Protocol):
    """What the tunnel manager answers a host with (`kernel.tunnels.Hosting`)."""

    @property
    def public_ipv4(self) -> str: ...

    @property
    def external_port(self) -> int: ...


class Hoster(Protocol):
    """The tunnel manager's two calls a host makes (`kernel.tunnels.TunnelStore`)."""

    async def host_on(
        self,
        tunnel_id: str,
        *,
        target_port: int,
        master_key: bytes,
        on_moved: Callable[[Any], Awaitable[None]] | None = None,
        on_lost: Callable[[TunnelError], Awaitable[None]] | None = None,
    ) -> Hosting: ...

    async def stop_hosting(self, tunnel_id: str) -> None: ...


class Egress(Protocol):
    """The guest's way out (`kernel.tunnels.EgressRouter.through`): its tunnel's proxy, held."""

    def through(self, route: str | None) -> AbstractAsyncContextManager[str | None]: ...


class Jobs(Protocol):
    """The one call on the job queue a session makes."""

    async def enqueue(
        self,
        job_type: str,
        payload: Mapping[str, Any] | None = None,
        *,
        priority: int = ...,
        max_attempts: int = ...,
        requested_by: str | None = ...,
    ) -> str: ...


@dataclass(frozen=True, slots=True)
class Taken:
    """The guest's answer to the offer screen: the offered people it skipped and the files it
    unticked. Everything else offered and not already held is wanted."""

    skipped: frozenset[int] = frozenset()
    unticked: frozenset[str] = frozenset()


#: The diff for the guest's answer: `diff.answer` over the assessment made when the offer
#: arrived.
Answer = Callable[[Taken], Diff]

#: The host's offer for what was chosen, read as the host's admin with the vault shut
#: (`offer.offer_for`), and the face model the guest's hello named, or None.
MakeOffer = Callable[[Viewer, Sequence[Chosen], bool, str | None], Awaitable[Offer]]
#: The guest's reading of an offer the moment it arrives: what the offer screen draws, and how the
#: diff is made from the answer (`diff.wanted`, `match_people`, `screen`, `answer`).
Assess = Callable[[Offer], Awaitable[tuple[OfferScreen, Answer]]]
#: The guest's landing of one received file (`ingest.land`, bound at boot to its
#: import, database, reindexer, faces and settings).
Land = Callable[..., Awaitable[object]]
#: Where the host's file with this key is on disk, as the host's own admin may read it with the
#: vault shut: the scoped `Repository.locate` over `load_viewer` (a fresh, vault-shut viewer), so
#: the path comes through the one check that decides whether bytes may be served at all.
PathOf = Callable[[Viewer, str], Awaitable[Path | None]]
#: The sender's look, strip and measure (`transfer.prepare`, bound to the settings).
Prepare = Callable[[Path, Path], Awaitable[Prepared]]
#: How many files the picks would offer and their bytes, and what they leave out and why, read as
#: the viewer with the vault shut (`weight.weigh`, bound at boot to the access layer, the database
#: and the saved filters).
Weigh = Callable[[Viewer, Sequence[Chosen]], Awaitable[Weight]]
#: The tunnel chosen last for joining a swap (the `swap.guest_tunnel` setting), or None.
ReadRoute = Callable[[], Awaitable[str | None]]
#: The guest's landing of the people offered for their facial fingerprints alone
#: (`ingest.land_fingerprints`, bound at boot to its database and the face feature).
LandFingerprints = Callable[..., Awaitable[object]]
#: The face model this install uses, for the guest's hello, or None when faces are off.
FaceModel = Callable[[], Awaitable[str | None]]
#: The public IPv4 address a tunnel leaves from, or None when it cannot be read
#: (`kernel.tunnels.TunnelStore.exit_address`).
ExitOf = Callable[[str], Awaitable[str | None]]
#: The address of the VPN server a tunnel connects to, or None when it cannot be read
#: (`kernel.tunnels.TunnelStore.server_address`).
ServerOf = Callable[[str], Awaitable[str | None]]


async def _no_fingerprints(*_args: object, **_kwargs: object) -> None:
    """Nothing to land: an install wired without the face feature."""


async def _no_model() -> None:
    """No face model to name: an install wired without the face feature."""


async def _no_exit(_route: str) -> None:
    """No exit address to read: an install wired without a tunnel manager that reads one."""


async def _no_server(_route: str) -> None:
    """No server address to read: an install wired without a tunnel manager that reads one."""


async def _read_within(read: Callable[[str], Awaitable[str | None]], route: str) -> str | None:
    """One of a tunnel's addresses, or None when it has no answer within `EXIT_WAIT_SECONDS`."""
    try:
        return await asyncio.wait_for(read(route), EXIT_WAIT_SECONDS)
    except (TimeoutError, TunnelError, OSError):
        return None


def _not_wired(what: str) -> Callable[..., Awaitable[Any]]:
    async def missing(*_args: object, **_kwargs: object) -> Any:
        raise RuntimeError(f"{what} is not wired")

    return missing


def _int_list(value: object, *, below: int) -> list[int]:
    if not isinstance(value, list):
        raise ProtocolError("a list that isn't one")
    out = []
    for one in value:
        if not isinstance(one, int) or isinstance(one, bool) or not 0 <= one < below:
            raise ProtocolError("an index out of range")
        out.append(one)
    return out


def _wanted_indexes(offer: Offer, diff: Diff) -> list[int]:
    """The diff's wanted keys as indexes into the offer, in the diff's order, each once. A key the
    offer never named is a lie, and the whole diff is refused for it."""
    by_key = {entry.key: n for n, entry in enumerate(offer.files)}
    out: list[int] = []
    seen: set[int] = set()
    for key in diff.wanted:
        if key not in by_key:
            raise ProtocolError("a diff asking for a file that wasn't offered")
        index = by_key[key]
        if index not in seen:
            seen.add(index)
            out.append(index)
    return out


@dataclass(eq=False)
class _Outgoing:
    """One file the host has opened for sending, shared by every stream carrying a share of it."""

    index: int
    ready: Prepared
    count: int
    #: The chunks no stream holds and the guest has not confirmed: what the next share is cut from.
    pool: set[int]
    #: The chunks the guest confirmed, by an acknowledgement or its `have`.
    landed: set[int] = field(default_factory=set)
    #: The shares on a stream now. The stripped copy stays until the last of them is over.
    holders: int = 0
    #: How often each chunk was asked for again, across every stream.
    retries: dict[int, int] = field(default_factory=dict)
    #: Each piece's digest as it was read to be sent, for the check at the end (`pieces`).
    pieces: dict[int, bytes] = field(default_factory=dict)


@dataclass(eq=False)
class _Incoming:
    """One file the guest is receiving, shared by every stream carrying a share of it."""

    path: Path
    size: int
    #: The whole file's digest, or the sender's version of it where the whole is checked at the end.
    digest: str
    count: int
    #: The chunks not yet written and verified.
    missing: set[int]
    #: How often each chunk arrived bad, across every stream.
    attempts: dict[int, int] = field(default_factory=dict)
    #: A stream has taken the whole file's check: every other one says its share is done.
    checking: bool = False
    #: The file failed: a stream still carrying a share of it turns it down.
    dropped: bool = False
    #: The sender checks the whole after the last piece (`pieces`).
    once: bool = False

    def is_dropped(self) -> bool:
        """Read afresh after every wait: another stream can drop the file meanwhile."""
        return self.dropped


#: The live steps in the order a session passes through them: a row is only ever moved on.
_STEP_ORDER = {"waiting": 0, "connected": 1, "offered": 2, "transferring": 3}


class _Figures:
    """One direction's files and figures: the offer, what was wanted, what has moved, its pace.

    A session holds the figures of the direction from the host to the guest itself. A swap that
    sends and receives holds the other direction's on `other_way` (see "Both ways"), and `back`
    says which of the row's columns a direction writes (`schema.py`)."""

    #: The direction from the guest to the host: the row's `back_` columns.
    back = False
    #: The session this direction belongs to.
    live: _Live

    def _start_figures(self) -> None:
        self.offer: Offer | None = None
        self.wanted: list[int] = []
        self.wanted_bytes = 0
        self.moved_bytes = 0
        self.unsaved_bytes = 0
        self.window_bytes = 0
        self.rate_bps: int | None = None
        #: What the estimate reads: the mean rate of the last `PACE_WINDOWS` windows, kept through
        #: a minute that moved nothing, so a session that had a pace never goes back to having
        #: none. A window that moves nothing comes near every swap's end (the last files checked
        #: and landed while nothing is in flight), and a pace read from that one window would say
        #: "Not enough to say yet" over the last of the swap.
        self.pace_bps: int | None = None
        self._recent: deque[int] = deque(maxlen=PACE_WINDOWS)
        #: The last write of the bytes moved, while it may still be under way (see `flush`).
        self._writing: asyncio.Future[Any] | None = None
        self.done_files: set[int] = set()
        self.failed_files: set[int] = set()
        #: Whether this direction's files are moving: what the Activity row's estimate waits for.
        self.transferring = False

    @property
    def resolved(self) -> bool:
        return len(self.done_files | self.failed_files) >= len(self.wanted)

    def moved(self, count: int) -> None:
        self.moved_bytes += count
        self.unsaved_bytes += count
        self.window_bytes += count

    def roll(self, seconds: float) -> None:
        """The window just ended: its rate, and the pace the estimate reads."""
        moved, self.window_bytes = self.window_bytes, 0
        self.rate_bps = int(moved * 8 / seconds)
        self._recent.append(self.rate_bps)
        pace = sum(self._recent) // len(self._recent)
        if pace > 0:
            self.pace_bps = pace

    async def flush(self) -> None:
        """Write the bytes moved since the last window, so the row is exact at the end.

        The write is a task of its own, shielded: the end stops the window's loop wherever it is,
        and a write stopped part-way loses a whole window's bytes from the row (a guest's row
        reading two thirds of a file it landed whole). `end` waits for it before its own last
        flush, so the bytes are written once."""
        if self.unsaved_bytes:
            moved, self.unsaved_bytes = self.unsaved_bytes, 0
            write = asyncio.ensure_future(
                self.live.owner.store.add_sent(self.live.id, sent_bytes=moved, back=self.back)
            )
            self._writing = write
            await asyncio.shield(write)


class _Live(_Figures):
    """What one session holds in memory while it runs. Never written anywhere as it is."""

    role = ""

    def __init__(self, owner: SwapSessions, session_id: str, secret: bytes) -> None:
        loop = asyncio.get_running_loop()
        self.live = self
        self._start_figures()
        self.owner = owner
        self.id = session_id
        self.short_id = session_id[-8:]
        self.secret = secret
        self.job_id: str | None = None
        self.device: Device | None = None
        self.context: JobContext | None = None
        self.attached = asyncio.Event()
        self.code: str | None = None
        self.peer: str | None = None
        self.control: Conn | None = None
        self.ended = asyncio.Event()
        #: Set once the end is written down: the row, the log line, the listener gone. The task
        #: returns only after this, so a settled task never sits beside a row that is still live.
        self.finished = asyncio.Event()
        self.reason: str | None = None
        self.watchdog = Watchdog(owner.watchdog_seconds, owner.clock)
        self.armed = False
        #: The other direction of a swap that sends and receives, or None for a swap one way.
        self.other_way: _BackSending | _BackReceiving | None = None
        #: The furthest step the row has been moved to (`advance`).
        self.step = "waiting"
        self._measuring = False
        self.tasks: set[asyncio.Task[Any]] = set()
        self.conns: set[Conn] = set()
        self._ending = asyncio.Lock()
        self.connected: asyncio.Future[None] = loop.create_future()
        #: When a tunnel cut the session off, or None while it is not (see "Cut off").
        self.cut_at: int | None = None
        #: Set whenever the session is joined again, for a send waiting out a cut.
        self.resumed = asyncio.Event()
        self._ping: asyncio.Task[Any] | None = None
        self._waiting_out = False

    # --- plumbing ------------------------------------------------------------------------------

    def spawn(self, work: Awaitable[Any], name: str) -> asyncio.Task[Any]:
        task: asyncio.Task[Any] = asyncio.ensure_future(work)
        task.set_name(f"swap.{name}.{self.short_id}")
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    async def until(self, future: asyncio.Future[Any]) -> bool:
        """Wait for `future` or the end, whichever is first. Whether it was the future."""
        ender = asyncio.ensure_future(self.ended.wait())
        try:
            await asyncio.wait({future, ender}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            ender.cancel()
        return future.done() and not future.cancelled() and future.exception() is None

    @staticmethod
    def settle(future: asyncio.Future[Any], value: Any) -> None:
        if not future.done():
            future.set_result(value)

    def attach(self, device: Device, context: JobContext | None) -> None:
        self.device = device
        self.context = context
        self.attached.set()

    @property
    def expires(self) -> int:
        """When the token runs out: how long a cut-off session waits to be joined again."""
        raise NotImplementedError  # pragma: no cover (each side has its own)

    def flows(self) -> list[_Figures]:
        """Every direction this session carries: one, or two in a swap that sends and receives."""
        return [self] if self.other_way is None else [self, self.other_way]

    @property
    def moving(self) -> bool:
        """Whether files are moving either way."""
        return any(flow.transferring for flow in self.flows())

    def sending_half(self) -> _Sending | None:
        """The direction this side sends, if it sends."""
        return None  # pragma: no cover (each side has its own)

    def receiving_half(self) -> _Receiving | None:
        """The direction this side receives, if it receives."""
        return None  # pragma: no cover (each side has its own)

    async def say(self, note: str) -> None:
        """What the task's row on Activity says about where the session stands."""
        if self.context is not None:
            with contextlib.suppress(Exception):
                await self.context.set_note(note)

    async def advance(self, step: str) -> None:
        """Move the row on to `step`, and never back: in a swap that sends and receives each
        direction reaches each step on its own, and the row says the furthest."""
        if _STEP_ORDER[step] <= _STEP_ORDER[self.step]:
            return
        self.step = step
        await self.owner.store.move(self.id, step)

    def start_measuring(self) -> None:
        """The windows' loop, once, from the first direction whose files move."""
        if not self._measuring:
            self._measuring = True
            self.spawn(self.measure(), "measure")

    async def both_ways(self, sending: Awaitable[bool], receiving: Awaitable[bool]) -> bool:
        """The two directions at once. True once both are complete; a failure in either is the
        session's, as it is in a swap one way."""
        tasks = [self.spawn(sending, "send"), self.spawn(receiving, "receive")]
        await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        for task in tasks:
            if task.done() and not task.cancelled():
                error = task.exception()
                if error is not None:
                    raise error
        return all(task.done() and not task.cancelled() and task.result() for task in tasks)

    # --- cut off, and joined again ----------------------------------------------------------------

    async def cut(self) -> None:
        """A tunnel cut the session off. It is NOT ended: the step is kept, the connections go,
        and the session waits for the same token to join again until the token runs out."""
        if self.ended.is_set() or self.cut_at is not None:
            return
        self.cut_at = self.owner.now()
        self.armed = False
        self.resumed.clear()
        control, self.control = self.control, None
        if control is not None:
            control.close()
        for conn in list(self.conns):
            conn.close()
        # A read on a connection the tunnel took can wait out a TLS shutdown nobody answers: the
        # streams are stopped rather than left holding a file until then. Joined again, new ones
        # take up what they held, from the chunks already verified.
        mine = asyncio.current_task()
        for task in self.streaming():
            if task is not mine and not task.done():
                task.cancel()
        await self.owner.store.cut_off(self.id, self.cut_at)
        await self.say(CUT_OFF_NOTE)
        log.info("swap.cut_off", swap=self.short_id, role=self.role)
        if not self._waiting_out:
            self._waiting_out = True
            self.spawn(self.wait_out_the_token(), "expiry")
        await self.on_cut()

    async def on_cut(self) -> None:
        """What a side does to be reachable again: the host hosts, the guest dials."""

    def streaming(self) -> list[asyncio.Task[Any]]:
        """The tasks carrying files, stopped by a cut."""
        return []  # pragma: no cover (each side has its own)

    async def wait_out_the_token(self) -> None:
        """A session still cut off when its token runs out ends as lost: nobody can join it now."""
        while True:
            await asyncio.sleep(
                max(0.0, min(self.owner.retry_seconds, self.expires - self.owner.now()))
            )
            if self.cut_at is not None and self.owner.now() >= self.expires:
                log.info("swap.token_ran_out", swap=self.short_id)
                await self.end(LOST, tell=False)
                return

    async def resume(self, control: Conn) -> None:
        """The same session, joined again on a new control connection: armed, pinged, carrying on
        from the step the row kept. The row first, so nothing reads the session as joined again
        while its row still says cut off."""
        await self.owner.store.rejoined(self.id)
        self.cut_at = None
        self.control = control
        self.conns.discard(control)
        self.watchdog.heard()
        self.armed = True
        if self._ping is not None:  # pragma: no branch (pinging since the first hello was answered)
            self._ping.cancel()
        self._ping = self.spawn(self.ping(control), "ping")
        await self.say(f"Swap with device {self.peer}")
        self.resumed.set()
        log.info("swap.rejoined", swap=self.short_id, role=self.role)

    async def say_again(self, conn: Conn) -> None:
        """Joined again: what the other side may never have heard, said once more. This side's
        offer while no answer to it has come, this side's answer to theirs, and its done. The
        other side takes the first of each it hears."""
        sending, receiving = self.sending_half(), self.receiving_half()
        if sending is not None and sending.offer is not None and not sending.diff_in.done():
            await conn.send(
                {
                    "offer": sending.offer.as_sent(
                        songs=sending.peer_songs, counts=sending.peer_counts
                    )
                }
            )
        if receiving is not None and receiving.diff is not None:
            await conn.send({"diff": receiving.diff.for_the_other_side().model_dump(mode="json")})
        if receiving is not None and receiving.said_done:
            await conn.send({"done": {"received": len(receiving.done_files)}})

    async def send_control(self, message: Mapping[str, Any]) -> bool:
        """Send on the control connection, waiting out a cut. False once the session has ended."""
        while not self.ended.is_set():
            control = self.control
            if control is None:
                waiter = asyncio.ensure_future(self.resumed.wait())
                try:
                    if not await self.until(waiter):
                        return False
                finally:
                    waiter.cancel()
                continue
            try:
                await control.send(message)
                return True
            except (ConnectionError, OSError):
                if control is self.control:
                    await self.cut()
        return False

    # --- the end -------------------------------------------------------------------------------

    async def end(self, reason: str, *, tell: bool = True) -> None:
        """End the session once, whoever asks first. Tells the other side where it can, tears the
        connections and the listener down, and writes the row."""
        if reason not in _STATE_OF:
            raise ValueError(f"{reason!r} is not a reason a session ends for")
        async with self._ending:
            if self.reason is not None:
                return
            self.reason = reason
        control = self.control
        if tell and control is not None and reason in _TELL:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(control.send({"end": _TELL[reason]}), 2.0)
        self.ended.set()
        await self.teardown()
        for flow in self.flows():
            if flow._writing is not None:
                with contextlib.suppress(Exception):
                    await flow._writing
            await flow.flush()
        await self.owner.store.end(
            self.id, state=_STATE_OF[reason], reason=reason, now=self.owner.now()
        )
        self.owner.forget(self)
        self.device = None
        self.finished.set()
        other = self.other_way
        if other is None:
            log.info(
                "swap.ended",
                swap=self.short_id,
                role=self.role,
                files=len(self.done_files),
                failed=len(self.failed_files),
                bytes=self.moved_bytes,
                reason=reason,
            )
            return
        log.info(
            "swap.ended",
            swap=self.short_id,
            role=self.role,
            files=len(self.done_files),
            failed=len(self.failed_files),
            bytes=self.moved_bytes,
            back_files=len(other.done_files),
            back_failed=len(other.failed_files),
            back_bytes=other.moved_bytes,
            reason=reason,
        )

    async def teardown(self) -> None:
        for conn in list(self.conns):
            conn.close()
        if self.control is not None:
            self.control.close()
        mine = asyncio.current_task()
        others = [task for task in list(self.tasks) if task is not mine and not task.done()]
        for task in others:
            task.cancel()
        if others:
            await asyncio.wait(others, timeout=5.0)

    # --- the common loops ----------------------------------------------------------------------
    # Each loop runs in a task of `tasks` and runs until the end cancels it (`teardown`); a loop that
    # calls the end itself returns right after. So none of them checks for the end as it goes round.

    async def watch(self) -> None:
        """Ten seconds of silence from the other side cuts the session off."""
        while True:
            await asyncio.sleep(min(0.5, self.watchdog.seconds / 4))
            if self.armed and self.watchdog.silent():
                log.info("swap.silent", swap=self.short_id)
                await self.cut()

    async def ping(self, control: Conn) -> None:
        while True:
            await asyncio.sleep(self.owner.ping_seconds)
            with contextlib.suppress(Exception):
                await control.send({"ping": 1})

    async def measure(self) -> None:
        """Each window: the rates to the row, the progress to the task, and the guest's ladders.
        A direction's window is read only while its files move, so a pace is never the mean of
        windows from before it started."""
        while True:
            await asyncio.sleep(self.owner.rate_window)
            flows = self.flows()
            for flow in flows:
                if flow.transferring:
                    flow.roll(self.owner.rate_window)
                await flow.flush()
            if self.rate_bps is not None:
                await self.owner.store.set_rate(self.id, self.rate_bps)
            wanted = sum(flow.wanted_bytes for flow in flows)
            if self.context is not None and wanted:
                with contextlib.suppress(Exception):
                    await self.context.report_progress(
                        min(0.99, sum(flow.moved_bytes for flow in flows) / wanted)
                    )
            await self.on_window()

    async def on_window(self) -> None:
        """What a side does at the end of each window, beyond writing it down."""

    async def listen(self, conn: Conn) -> None:
        """The control connection's reader: every frame is heard; an end, a lie or a drop ends."""
        try:
            while True:
                frame = await conn.read()
                self.watchdog.heard()
                if isinstance(frame, Chunk):
                    raise ProtocolError("a chunk on the control connection")
                if "ping" in frame:
                    continue
                if "end" in frame:
                    heard = _HEARD.get(str(frame.get("end")), ENDED_BY_THEM)
                    await self.end(heard, tell=False)
                    return
                await self.on_message(frame)
        except ProtocolError:
            # A lie is not a tunnel dropping: the peer said what a Sift does not, and is not waited
            # for. An end already under way keeps its own reason (`end` takes the first).
            await self.end(LOST, tell=False)
        except (asyncio.IncompleteReadError, ConnectionError, OSError, TimeoutError):
            # Only the connection the session is on now: one a cut or a rejoin has already put
            # aside says nothing about the session when it closes.
            if not self.ended.is_set() and not self.closing_is_expected() and conn is self.control:
                await self.cut()

    async def on_message(self, frame: Mapping[str, Any]) -> None:
        """A control message. The words mean the same whichever side sends them: `offer` is what
        the sender offers, `diff` its answer to this side's offer, and `done` that everything it
        wanted from this side has landed. A word for a direction this side does not have is
        passed over, as an unknown word is."""
        sending, receiving = self.sending_half(), self.receiving_half()
        if "offer" in frame and isinstance(frame["offer"], dict):
            if receiving is not None:
                self.settle(receiving.offer_in, frame["offer"])
        elif "diff" in frame and isinstance(frame["diff"], dict):
            if sending is not None:
                self.settle(sending.diff_in, frame["diff"])
        elif "done" in frame and sending is not None:
            self.settle(sending.done_in, None)

    def closing_is_expected(self) -> bool:
        """Whether the other side closing the control connection now is the end it said it was:
        it said done, and this side has nothing left it waits for."""
        sending, receiving = self.sending_half(), self.receiving_half()
        if sending is None:
            return False
        return sending.done_in.done() and (receiving is None or receiving.said_done)

    async def became_connected(self, peer: str, control: Conn) -> None:
        self.peer = peer
        self.step = "connected"
        await self.owner.store.move(self.id, "connected", peer_device=peer)
        if self.context is not None:  # pragma: no branch (attached before any hello is taken)
            with contextlib.suppress(Exception):
                await self.context.set_note(f"Swap with device {peer}")
        self.watchdog.heard()
        self.armed = True
        self.spawn(self.watch(), "watch")
        self._ping = self.spawn(self.ping(control), "ping")
        self.settle(self.connected, None)

    async def drive(self) -> None:  # pragma: no cover (each side has its own)
        raise NotImplementedError


_EMPTY = blake3(b"").hexdigest()


def _looked_at(task: asyncio.Task[Any]) -> None:
    if not task.cancelled():
        task.exception()


#: Removing a staged file or a stripped copy: the slice's one remover, in `transfer.py`.
_remove = transfer.remove
