# SPDX-License-Identifier: AGPL-3.0-or-later
"""What every part of the queue shares: the database, the clock, and the one door its writes use."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Iterable, Sequence
from contextlib import AbstractAsyncContextManager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, NamedTuple

from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, telling
from sift.kernel.db import Connection, Database
from sift.kernel.jobs.queue_rows import WorkSummary
from sift.kernel.jobs.quiet_hours import AT_QUIET
from sift.kernel.jobs.switchboard import QuietHold, Switchboard
from sift.kernel.log import get_logger

#: How long a whole-table work summary stays true: `WorkAhead`'s five seconds, for one dashboard.
SUMMARY_FRESH_FOR_SECONDS = 5

#: How long the last claim's types at their cap are believed, when nothing has claimed since.
FULL_FRESH_FOR_SECONDS = 60

#: A read of every live row that cost this much or more is kept for `KEPT_FOR_COST` times what it
#: cost, so however long the queue it takes at most a tenth of the time of the screens that poll
#: it, and never for longer than the longest. A cheaper read is read every time.
KEPT_FROM_SECONDS = 0.1
KEPT_FOR_COST = 10
KEPT_LONGEST_SECONDS = 10.0

log = get_logger(__name__)

#: The urgency of the job running in this task, if any: what a settle it asks for inherits.
ASKED_AT: ContextVar[int | None] = ContextVar("jobs_asked_at", default=None)


class Arrival(NamedTuple):
    """A row just queued, as much of it as says whether a worker could take it now."""

    job_type: str
    family: str
    timing: str | None
    run_after: int | None


@dataclass(slots=True)
class _Kept:
    """One read's last answer: when it was taken and what it cost."""

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    #: The loop the lock belongs to: a lock used on another loop is replaced (the tests' loops).
    loop: asyncio.AbstractEventLoop | None = None
    answer: Any = None
    taken: float = 0.0
    cost: float = 0.0
    refreshing: asyncio.Task[None] | None = None


class QueueCore:
    """The state every part of the queue shares, one per database."""

    def __init__(
        self,
        database: Database,
        *,
        clock: Callable[[], float] = time.time,
        summary_fresh_for: int = SUMMARY_FRESH_FOR_SECONDS,
        switchboard: Switchboard | None = None,
    ) -> None:
        self._db = database
        self._clock = clock
        self._summary_fresh_for = summary_fresh_for
        #: Whether each kind of work is switched off or can run here; a bare board allows all.
        self._switchboard = switchboard or Switchboard()
        self._work_summary: tuple[int, WorkSummary] | None = None
        self._kept: dict[tuple[object, ...], _Kept] = {}
        #: The types whose family cannot run here, and when that was asked (`_not_ready_types`).
        self._not_ready: tuple[int, frozenset[str]] | None = None
        #: Who is told that running jobs were asked to stop (`listen_for_stops`).
        self._stop_listeners: list[Callable[[Sequence[str]], None]] = []
        self._work_listeners: list[Callable[[], None]] = []
        #: Who is told rows may have become claimable with no arrival to name (`listen_for_anything`).
        self._anything_listeners: list[Callable[[], None]] = []
        #: Who is told a row was put off to a moment of its own (`listen_for_retime`).
        self._retime_listeners: list[Callable[[], None]] = []
        self._quiet_seen: tuple[int, QuietHold] | None = None
        #: The types at their cap as the last claim found them, and when: work of one wakes nobody,
        #: since the worker that ends the running one claims next.
        self._full: tuple[int, frozenset[str]] | None = None
        #: The types whose every run writes a history line, and their task (`record_runs_of`).
        self._runs_recorded: dict[str, tuple[str, str]] = {}
        #: Who is told that a job of a type has settled (`listen_for_settled`).
        self._settled_listeners: dict[str, list[Callable[[str], Awaitable[None]]]] = {}

    def _now(self) -> int:
        return int(self._clock())

    @property
    def switchboard(self) -> Switchboard:
        """The board this queue consults, read by the worker pool and by the passes screen."""
        return self._switchboard

    def _writing(self) -> AbstractAsyncContextManager[Connection]:
        """A write to the queue, every admin's dashboard told once it lands. Every write but the
        heartbeat (nothing on screen, every few seconds per job) goes through this."""
        return telling(self._db, EVERY_ADMIN, About.JOBS)

    async def _kept_read(self, key: tuple[object, ...], read: Callable[[], Awaitable[Any]]) -> Any:
        """`read`'s answer, kept by what it cost (`KEPT_FROM_SECONDS`): a cheap read is exact; a
        read of a long queue is answered from what was kept and read again behind the answer once
        it is older than `KEPT_FOR_COST` times its cost, so no request waits for it but the first.
        Callers asking together share one read."""
        kept = self._kept.setdefault(key, _Kept())
        if kept.cost >= KEPT_FROM_SECONDS:
            fresh = min(KEPT_LONGEST_SECONDS, kept.cost * KEPT_FOR_COST)
            if time.monotonic() - kept.taken >= fresh and (
                kept.refreshing is None or kept.refreshing.done()
            ):
                kept.refreshing = asyncio.create_task(_read_again(kept, read))
            return kept.answer
        running = asyncio.get_running_loop()
        if kept.loop is not running:
            kept.lock, kept.loop = asyncio.Lock(), running
        async with kept.lock:
            if kept.cost < KEPT_FROM_SECONDS:
                await _read_into(kept, read)
            return kept.answer

    def _claimable_now(self, arrival: Arrival) -> bool:
        """Whether the claim could take this row now, from what the queue already knows without a
        read: its moment, and quiet hours and readiness as last asked. A wrong no
        costs one idle poll, so what is not known counts as claimable."""
        if arrival.run_after is not None and arrival.run_after > self._now():
            return False
        if self._quiet_seen is not None and not self._quiet_seen[1].open:
            quiet = self._quiet_seen[1]
            if arrival.timing == AT_QUIET or (
                arrival.timing is None and arrival.job_type in quiet.types
            ):
                return False
        if (
            self._full is not None
            and self._now() - self._full[0] < FULL_FRESH_FOR_SECONDS
            and arrival.job_type in self._full[1]
        ):
            return False
        return self._not_ready is None or arrival.job_type not in self._not_ready[1]

    def _work_arrived(self, arrivals: Iterable[Arrival]) -> None:
        """Tell every listener once per row queued that a worker could take now: one idle worker
        claims each, and a row that must wait wakes nobody. A listener that raises is logged."""
        arrived = list(arrivals)
        for _ in [arrival for arrival in arrived if self._claimable_now(arrival)]:
            _tell(self._work_listeners)
        if any(
            arrival.run_after is not None and arrival.run_after > self._now() for arrival in arrived
        ):
            _tell(self._retime_listeners)

    def _work_arrived_unknown(self) -> None:
        """Rows may have become claimable that no arrival names (a retry, a resume, a reclaim):
        every idle worker claims."""
        _tell(self._anything_listeners)

    def listen_for_anything(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Hear `_work_arrived_unknown`. Returns the call that stops it."""
        return _listen(self._anything_listeners, listener)

    def listen_for_retime(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Hear of a row queued to wait for a moment of its own, which an idle worker's wait for
        the next due row must now count. Returns the call that stops it."""
        return _listen(self._retime_listeners, listener)


def _listen(
    listeners: list[Callable[[], None]], listener: Callable[[], None]
) -> Callable[[], None]:
    listeners.append(listener)

    def unlisten() -> None:
        if listener in listeners:
            listeners.remove(listener)

    return unlisten


async def _read_into(kept: _Kept, read: Callable[[], Awaitable[Any]]) -> None:
    began = time.monotonic()
    kept.answer = await read()
    kept.taken = time.monotonic()
    kept.cost = kept.taken - began


async def _read_again(kept: _Kept, read: Callable[[], Awaitable[Any]]) -> None:
    """A kept read taken again behind its answer; a failure keeps the old answer, logged."""
    try:
        async with kept.lock:
            await _read_into(kept, read)
    except Exception:
        log.exception("job.kept_read_failed")


def _tell(listeners: list[Callable[[], None]]) -> None:
    for listener in list(listeners):
        try:
            listener()
        except Exception:
            log.exception("job.work_listener_failed")
