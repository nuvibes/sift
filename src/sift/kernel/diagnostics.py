# SPDX-License-Identifier: AGPL-3.0-or-later
"""Catching a stopped event loop, which logs nothing because logging needs the loop.

A watchdog thread dumps stacks by itself; pool, read-pool and backlog watches see the rest."""

from __future__ import annotations

import asyncio
import contextlib
import faulthandler
import gc
import signal
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import AbstractAsyncContextManager

from sift.kernel.db import DatabaseError
from sift.kernel.log import get_logger

log = get_logger(__name__)

#: How long the loop may stop before a stack is taken: well past any real work.
STALL_SECONDS = 15.0

#: How often the loop refreshes its mark.
BEAT_SECONDS = 1.0

#: How late a heartbeat may be before a log line: past jitter, short of a visible stutter.
LAG_WARN_SECONDS = 0.25

#: The least time between two lag warnings.
LAG_REPORT_INTERVAL_SECONDS = 30.0

#: How often the watching thread looks: a hold is over in tenths of a second.
LOOK_SECONDS = 0.05

#: How many frames of the loop's stack a held line carries, innermost first.
HELD_FRAMES = 6

#: Recent collections kept to set beside a hold in the same beat.
COLLECTIONS_KEPT = 16


def _short(filename: str) -> str:
    """A source file as the log names it, never a folder of the machine it runs on."""
    path = filename.replace("\\", "/")
    inside = path.rfind("/sift/")
    if inside != -1:
        return path[inside + len("/sift/") :]
    return "/".join(path.split("/")[-2:])


def where_is(thread_id: int, *, frames: int = HELD_FRAMES) -> str | None:
    """What a thread runs this moment, innermost first, or None when idle; asyncio's own skipped."""
    frame = sys._current_frames().get(thread_id)
    names: list[str] = []
    while frame is not None and len(names) < frames:
        code = frame.f_code
        path = code.co_filename.replace("\\", "/")
        if "/asyncio/" not in path and not path.endswith(("/selectors.py", "/threading.py")):
            names.append(f"{_short(path)}:{frame.f_lineno} {code.co_name}")
        frame = frame.f_back
    return " < ".join(names) or None


class LoopWatchdog:
    """Watches the event loop from a plain thread, and dumps every stack if it stops."""

    def __init__(
        self,
        *,
        stall_seconds: float = STALL_SECONDS,
        beat_seconds: float = BEAT_SECONDS,
        lag_warn_seconds: float = LAG_WARN_SECONDS,
        lag_report_interval: float = LAG_REPORT_INTERVAL_SECONDS,
        look_seconds: float = LOOK_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        where: Callable[[int], str | None] = where_is,
    ) -> None:
        self._stall = stall_seconds
        self._beat = beat_seconds
        self._lag_warn = lag_warn_seconds
        self._lag_report_interval = lag_report_interval
        self._clock = clock
        self._last = clock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._task: asyncio.Task[None] | None = None
        self._reported = False
        self._worst_lag = 0.0
        self._held_count = 0
        self._last_lag_report: float | None = None
        self._look = look_seconds
        self._where = where
        self._loop_thread: int | None = None
        self._held_where: str | None = None
        self._collections: deque[tuple[float, float, int]] = deque(maxlen=COLLECTIONS_KEPT)
        self._collecting: float | None = None

    def beat(self) -> None:
        """The loop saying it is still running; called only from the loop."""
        self._last = self._clock()
        self._reported = False

    def overdue(self) -> float:
        """How long since the loop last said anything."""
        return self._clock() - self._last

    def check(self) -> bool:
        """One look; True only for the look that first reports a stall."""
        if self._reported or self.overdue() < self._stall:
            return False
        self._reported = True
        return True

    def look(self) -> None:
        """Note where a held loop is, read from this thread while the hold lasts, once per hold."""
        if self._held_where is not None or self._loop_thread is None:
            return
        if self.overdue() < self._beat + self._lag_warn / 2:
            return
        self._held_where = self._where(self._loop_thread)

    def held_where(self) -> str | None:
        """Where the loop was when last found late, handed over once."""
        where, self._held_where = self._held_where, None
        return where

    def collected(self, phase: str, info: dict[str, int]) -> None:
        """Note a garbage collection, which blocks the look; no lock, or it could wait on itself."""
        now = self._clock()
        if phase == "start":
            self._collecting = now
        elif phase == "stop" and self._collecting is not None:
            self._collections.append((self._collecting, now, info.get("generation", 0)))
            self._collecting = None

    def collection_during(self, since: float, until: float) -> tuple[float, int] | None:
        """Collection time within `since` to `until`, with the oldest generation, or None."""
        seconds = 0.0
        generation = -1
        for began, ended, gen in list(self._collections):
            overlap = min(ended, until) - max(began, since)
            if overlap > 0:
                seconds += overlap
                generation = max(generation, gen)
        return (seconds, generation) if generation >= 0 else None

    def dump(self) -> None:
        """Every thread's stack to standard error, with a log line that works without the loop."""
        log.error("loop.stalled", seconds=round(self.overdue(), 1))
        faulthandler.dump_traceback(file=sys.stderr, all_threads=True)

    @property
    def worst_lag_seconds(self) -> float:
        """The longest the loop has been held since the process started."""
        return self._worst_lag

    @property
    def held_count(self) -> int:
        """How many heartbeats came back later than the warning threshold."""
        return self._held_count

    @staticmethod
    def overshoot(elapsed: float, asked: float) -> float:
        """How much longer a wake-up took than it asked for, never below zero; easy to invert."""
        return max(0.0, elapsed - asked)

    def record_lag(self, lag: float) -> bool:
        """Take one measurement of the heartbeat's lateness; True when it is worth a log line."""
        self._worst_lag = max(self._worst_lag, lag)
        if lag < self._lag_warn:
            return False
        self._held_count += 1
        now = self._clock()
        if (
            self._last_lag_report is not None
            and now - self._last_lag_report < self._lag_report_interval
        ):
            return False
        self._last_lag_report = now
        return True

    async def _heartbeat(self) -> None:
        while True:
            self.beat()
            started = self._clock()
            await asyncio.sleep(self._beat)
            # Sleep beyond what was asked is time the loop was held.
            woke = self._clock()
            lag = self.overshoot(woke - started, self._beat)
            # The mark moves first, so a look meanwhile does not read this as a second hold.
            self.beat()
            where = self.held_where()
            if self.record_lag(lag):
                collection = self.collection_during(started + self._beat, woke)
                log.warning(
                    "loop.held",
                    seconds=round(lag, 3),
                    worst_seconds=round(self._worst_lag, 3),
                    occurrences=self._held_count,
                    at=where,
                    gc_seconds=round(collection[0], 3) if collection else None,
                    gc_generation=collection[1] if collection else None,
                )

    def _watch(self) -> None:
        while not self._stop.wait(min(self._beat, self._look)):
            self.look()
            if self.check():
                self.dump()

    def start(self) -> None:
        """Begin watching on a daemon thread, which never holds up a shutdown."""
        self.beat()
        self._loop_thread = threading.get_ident()
        gc.callbacks.append(self.collected)
        self._task = asyncio.get_running_loop().create_task(self._heartbeat())
        self._thread = threading.Thread(target=self._watch, name="loop-watchdog", daemon=True)
        self._thread.start()

    async def stop(self) -> None:
        self._stop.set()
        with contextlib.suppress(ValueError):
            gc.callbacks.remove(self.collected)
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._thread is not None:
            self._thread.join(timeout=self._beat * 2)
            self._thread = None


#: How often the pool is probed, matched to the heartbeat.
POOL_PROBE_SECONDS = 1.0

#: The same quarter second as the loop: to a viewer the two failures feel identical.
POOL_WAIT_WARN_SECONDS = 0.25


class ThreadPoolWatch:
    """Watches how long work waits for a thread: a full pool stutters video, not the interface.

    Probed off the heartbeat, or a full pool would read as a held loop."""

    def __init__(
        self,
        *,
        probe_seconds: float = POOL_PROBE_SECONDS,
        wait_warn_seconds: float = POOL_WAIT_WARN_SECONDS,
        report_interval: float = LAG_REPORT_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._probe_seconds = probe_seconds
        self._wait_warn = wait_warn_seconds
        self._report_interval = report_interval
        self._clock = clock
        self._task: asyncio.Task[None] | None = None
        self._worst_wait = 0.0
        self._full_count = 0
        self._last_report: float | None = None
        self._submitted_at: float | None = None

    @property
    def worst_wait_seconds(self) -> float:
        """The longest anything has waited for a thread since the process started."""
        return self._worst_wait

    @property
    def full_count(self) -> int:
        """How many probes waited longer than the warning threshold."""
        return self._full_count

    @property
    def waiting_seconds(self) -> float:
        """How long the probe in flight has waited so far, as a stuck probe reports nothing."""
        if self._submitted_at is None:
            return 0.0
        return max(0.0, self._clock() - self._submitted_at)

    @staticmethod
    def queued_for(submitted: float, began: float) -> float:
        """How long a submission waited before it ran, never below zero; easy to invert."""
        return max(0.0, began - submitted)

    def record_wait(self, wait: float) -> bool:
        """Take one measurement. True when this one is worth a line in the log."""
        self._worst_wait = max(self._worst_wait, wait)
        if wait < self._wait_warn:
            return False
        self._full_count += 1
        now = self._clock()
        if self._last_report is not None and now - self._last_report < self._report_interval:
            return False
        self._last_report = now
        return True

    async def _probe_once(self) -> float:
        """Hand the pool a no-op and time how long until it starts."""
        submitted = self._clock()
        self._submitted_at = submitted
        try:
            began = await asyncio.to_thread(self._clock)
        finally:
            self._submitted_at = None
        return self.queued_for(submitted, began)

    async def _watch(self) -> None:
        while True:
            await asyncio.sleep(self._probe_seconds)
            wait = await self._probe_once()
            if self.record_wait(wait):
                log.warning(
                    "threads.pool.full",
                    seconds=round(wait, 3),
                    worst_seconds=round(self._worst_wait, 3),
                    occurrences=self._full_count,
                )

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._watch())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None


#: The same quarter second as the other watches.
READ_WAIT_WARN_SECONDS = 0.25


class ReadPoolWatch:
    """Watches how long a read waits for a database connection, and names the pass holding it."""

    def __init__(
        self,
        borrow: Callable[[], AbstractAsyncContextManager[object]],
        *,
        holding: Callable[[], str | None] = lambda: None,
        probe_seconds: float = POOL_PROBE_SECONDS,
        wait_warn_seconds: float = READ_WAIT_WARN_SECONDS,
        report_interval: float = LAG_REPORT_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._borrow = borrow
        self._holding = holding
        self._probe_seconds = probe_seconds
        self._wait_warn = wait_warn_seconds
        self._report_interval = report_interval
        self._clock = clock
        self._task: asyncio.Task[None] | None = None
        self._worst_wait = 0.0
        self._full_count = 0
        self._last_report: float | None = None
        self._asked_at: float | None = None

    @property
    def worst_wait_seconds(self) -> float:
        """The longest anything has waited for a connection since the process started."""
        return self._worst_wait

    @property
    def full_count(self) -> int:
        """How many probes waited longer than the warning threshold."""
        return self._full_count

    @property
    def waiting_seconds(self) -> float:
        """How long the probe in flight has waited so far, as a stuck probe reports nothing."""
        if self._asked_at is None:
            return 0.0
        return max(0.0, self._clock() - self._asked_at)

    @property
    def holding(self) -> str | None:
        """Which whole-library pass has the sweep lane right now, or None."""
        return self._holding()

    def record_wait(self, wait: float) -> bool:
        """Take one measurement. True when this one is worth a line in the log."""
        self._worst_wait = max(self._worst_wait, wait)
        if wait < self._wait_warn:
            return False
        self._full_count += 1
        now = self._clock()
        if self._last_report is not None and now - self._last_report < self._report_interval:
            return False
        self._last_report = now
        return True

    async def _probe_once(self) -> float:
        """Borrow a connection, give it straight back, and time the asking."""
        asked = self._clock()
        self._asked_at = asked
        try:
            async with self._borrow():
                got_one = self._clock()
        finally:
            self._asked_at = None
        return ThreadPoolWatch.queued_for(asked, got_one)

    async def _watch(self) -> None:
        while True:
            await asyncio.sleep(self._probe_seconds)
            try:
                wait = await self._probe_once()
            except DatabaseError:
                # Closed for a restore: skip the tick, not the watch, or the task dies silently.
                continue
            if self.record_wait(wait):
                log.warning(
                    "db.readers.queued",
                    seconds=round(wait, 3),
                    worst_seconds=round(self._worst_wait, 3),
                    occurrences=self._full_count,
                    # The pass holding the lane, the line's whole value.
                    sweeping=self._holding(),
                )

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._watch())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None


#: How long the ready queue may take to drain: a backlog of prompt callbacks, which no other
#: watch can see.
BACKLOG_WARN_SECONDS = 0.25


class LoopBacklogWatch:
    """Watches how long `sleep(0)` takes to come back: a backlogged loop, not a held one."""

    def __init__(
        self,
        *,
        probe_seconds: float = POOL_PROBE_SECONDS,
        warn_seconds: float = BACKLOG_WARN_SECONDS,
        report_interval: float = LAG_REPORT_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._probe_seconds = probe_seconds
        self._warn = warn_seconds
        self._report_interval = report_interval
        self._clock = clock
        self._task: asyncio.Task[None] | None = None
        self._worst = 0.0
        self._over_count = 0
        self._latest = 0.0
        self._last_report: float | None = None

    @property
    def worst_seconds(self) -> float:
        """The longest the ready queue has taken to drain since the process started."""
        return self._worst

    @property
    def latest_seconds(self) -> float:
        """The most recent reading, the state right now."""
        return self._latest

    @property
    def over_count(self) -> int:
        """How many probes found the queue slower than the warning threshold."""
        return self._over_count

    def record(self, drained: float) -> bool:
        """Take one measurement. True when this one is worth a line in the log."""
        self._latest = drained
        self._worst = max(self._worst, drained)
        if drained < self._warn:
            return False
        self._over_count += 1
        now = self._clock()
        if self._last_report is not None and now - self._last_report < self._report_interval:
            return False
        self._last_report = now
        return True

    async def _probe_once(self) -> float:
        began = self._clock()
        await asyncio.sleep(0)
        return max(0.0, self._clock() - began)

    async def _watch(self) -> None:
        while True:
            await asyncio.sleep(self._probe_seconds)
            drained = await self._probe_once()
            if self.record(drained):
                log.warning(
                    "loop.backlogged",
                    seconds=round(drained, 3),
                    worst_seconds=round(self._worst, 3),
                    occurrences=self._over_count,
                )

    def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.get_running_loop().create_task(self._watch())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None


#: Kinds of work the slowest-work record keeps.
SLOWEST_KEPT = 8


class SlowestWork:
    """What took the time, by kind, ranked by total: a cheap thing done too often is the fault."""

    def __init__(self, *, keep: int = SLOWEST_KEPT) -> None:
        self._keep = keep
        self._runs: dict[str, int] = {}
        self._worst: dict[str, float] = {}
        self._total: dict[str, float] = {}

    def record(self, stage: str, milliseconds: float) -> None:
        """Note one piece of work of this kind."""
        self._runs[stage] = self._runs.get(stage, 0) + 1
        self._worst[stage] = max(self._worst.get(stage, 0.0), milliseconds)
        self._total[stage] = self._total.get(stage, 0.0) + milliseconds

    def reset(self) -> None:
        """Start a new window."""
        self._runs.clear()
        self._worst.clear()
        self._total.clear()

    def worst(self) -> list[dict[str, float | int | str]]:
        """The kinds of work costing the most total time, dearest first."""
        ranked = sorted(self._total.items(), key=lambda pair: -pair[1])[: self._keep]
        return [
            {
                "stage": stage,
                "runs": self._runs[stage],
                "total_ms": round(total, 1),
                "worst_ms": round(self._worst[stage], 1),
            }
            for stage, total in ranked
        ]


#: Distinct wide reads kept.
WIDEST_KEPT = 8


class WidestReads:
    """Which reads hand back the most rows, a figure the same loaded or idle, ranked by widest."""

    def __init__(self, *, keep: int = WIDEST_KEPT) -> None:
        self._keep = keep
        self._widest: dict[str, int] = {}
        self._runs: dict[str, int] = {}
        self._total: dict[str, int] = {}

    def record(self, stage: str, rows: int, sql: str | None) -> None:
        """Note one read, and how much it moved."""
        key = _read_shape(stage, sql)
        self._widest[key] = max(self._widest.get(key, 0), rows)
        self._runs[key] = self._runs.get(key, 0) + 1
        self._total[key] = self._total.get(key, 0) + rows

    def reset(self) -> None:
        """Start a new window."""
        self._widest.clear()
        self._runs.clear()
        self._total.clear()

    def widest(self) -> list[dict[str, int | str]]:
        """The reads handing back the most rows, widest first."""
        ranked = sorted(self._widest.items(), key=lambda pair: -pair[1])[: self._keep]
        return [
            {
                "read": key,
                "widest_rows": rows,
                "runs": self._runs[key],
                "total_rows": self._total[key],
            }
            for key, rows in ranked
        ]


def _read_shape(stage: str, sql: str | None) -> str:
    """A short, stable name for a statement; values are always bound, so nothing to redact."""
    if not sql:
        return stage
    flat = " ".join(sql.split())
    return flat[:110] if len(flat) <= 110 else flat[:107] + "..."


@contextlib.contextmanager
def boot_set_aside() -> Iterator[None]:
    """Freeze start-up's objects out of every later collection while the server runs."""
    gc.collect()
    gc.freeze()
    try:
        yield
    finally:
        gc.unfreeze()


def install_stack_dumper() -> bool:
    """Make SIGUSR1 dump every thread's stack; False where the site has no such signal."""
    handler = getattr(signal, "SIGUSR1", None)
    if handler is None:
        return False
    # Absent on Windows, where the guard above returns first; both codes for both hosts.
    faulthandler.register(  # type: ignore[attr-defined, unused-ignore]
        handler, file=sys.stderr, all_threads=True, chain=False
    )
    return True
