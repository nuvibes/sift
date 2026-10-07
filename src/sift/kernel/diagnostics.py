# SPDX-License-Identifier: AGPL-3.0-or-later
"""Catching the one failure that leaves no evidence: the event loop stopping.

Sift is a single process with a single event loop, shared by the API, the live job feed and every
video anybody is watching. Anything that holds that loop freezes all of it together, and the
symptom is silence. Nothing is logged, because logging happens on the loop; nothing is slow,
because nothing is running; the process sits at almost no processor use and every request waits.

Nor can it be found out afterwards: a stack trace has to be taken while it is happening, and by
the time anybody notices, the process may well have been restarted.

Four things here, and the first is the one that matters:

- **A watchdog that dumps a stack by itself.** A thread outside the loop watches a mark the loop
  keeps refreshing. If the mark stops moving for longer than any real piece of work takes, every
  thread's stack goes to the log. Nobody has to be watching.
- **A dump on request**, so a stack can be taken from outside at any moment without restarting
  anything or granting the container new privileges.
- **A watch on the thread pool**, which is the *other* way this application stops working, and the
  one the two above are blind to. See `ThreadPoolWatch`.
- **A watch on the database read pool**, which is the third, and which the other two are equally
  blind to: both can read healthy and truthful while the application answers nothing. See
  `ReadPoolWatch`.

The stacks go to standard error rather than to a file: the container's filesystem is read-only,
which is correct and was not weakened for this.
"""

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

#: How long the loop may go unresponsive before a stack is taken, in seconds.
#:
#: Well above anything legitimate. The heaviest synchronous work in Sift is handed to a thread
#: precisely so it does not hold the loop, so a gap this long is not a slow operation: it is the
#: loop not running at all. Set much lower and this fires during an ordinary busy moment on a
#: small machine, and a diagnostic that cries wolf gets turned off.
STALL_SECONDS = 15.0

#: How often the loop refreshes its mark. Cheap: one wake-up a second, and nothing else.
BEAT_SECONDS = 1.0

#: How late the heartbeat may be before it is worth a line in the log, in seconds.
#:
#: The watchdog above catches a loop that has stopped. This catches one that is merely being held,
#: which is the same fault before it becomes obvious: a stall tends to start as a stutter that
#: reads as the machine being busy. A quarter of a second is above what
#: scheduling jitter produces on a loaded small machine and below what a person fails to notice
#: while scrubbing a video.
LAG_WARN_SECONDS = 0.25

#: The least time between two lag warnings. A loop held repeatedly would otherwise write a line a
#: second for as long as it lasted, and the first line already said it.
LAG_REPORT_INTERVAL_SECONDS = 30.0

#: How often the watching thread looks at the loop, in seconds. A hold is over in a few tenths of a
#: second, and where the loop is can only be read while it is still there: a look once a beat would
#: arrive after most of them had ended.
LOOK_SECONDS = 0.05

#: How many frames of the loop's stack a held line carries, innermost first.
HELD_FRAMES = 6

#: How many recent garbage collections are kept to set beside a hold. A hold is matched against
#: the collections of the beat it happened in, and a beat never sees more than a few.
COLLECTIONS_KEPT = 16


def _short(filename: str) -> str:
    """A source file as the log names it: inside this package by its path under the package,
    anything else by its last two parts. Never a folder of the machine it runs on."""
    path = filename.replace("\\", "/")
    inside = path.rfind("/sift/")
    if inside != -1:
        return path[inside + len("/sift/") :]
    return "/".join(path.split("/")[-2:])


def where_is(thread_id: int, *, frames: int = HELD_FRAMES) -> str | None:
    """What a thread is running this moment, innermost frame first, or None when it is idle.

    Read from another thread, which is the only place it can be read from while the thread in
    question is busy. The event loop's own machinery is left out: a loop found waiting for its next
    event is not being held, and naming the wait would point at nothing.
    """
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
        """The loop saying it is still running. Called from the loop and nowhere else."""
        self._last = self._clock()
        self._reported = False

    def overdue(self) -> float:
        """How long since the loop last said anything."""
        return self._clock() - self._last

    def check(self) -> bool:
        """One look. True when this look is the one that reported a stall.

        Reported once per stall rather than once per look, because a loop blocked for seven
        minutes would otherwise fill the log with the same stack four hundred times, and the
        first one is the one worth reading anyway.
        """
        if self._reported or self.overdue() < self._stall:
            return False
        self._reported = True
        return True

    def look(self) -> None:
        """One look for a loop that is being held, taken from the watching thread: note where.

        The heartbeat can say a hold happened only once it is over, and by then the loop is
        somewhere else. So the loop's own stack is read from here while whatever holds it is still
        running, and the heartbeat puts it on the line it writes when it wakes. The look comes at
        half the warning, not at the warning itself: a hold just over the warning is over a few
        looks after the warning is reached, and one that ends between two looks would be logged
        with nowhere. A hold that stays under the warning writes no line, and its look is dropped
        with it. Once per hold: the first look is where it began.
        """
        if self._held_where is not None or self._loop_thread is None:
            return
        if self.overdue() < self._beat + self._lag_warn / 2:
            return
        self._held_where = self._where(self._loop_thread)

    def held_where(self) -> str | None:
        """Where the loop was when the last look found it late, handed over once."""
        where, self._held_where = self._held_where, None
        return where

    def collected(self, phase: str, info: dict[str, int]) -> None:
        """The interpreter starting or ending a garbage collection, on whichever thread ran it.

        A collection holds the interpreter lock for as long as it runs, so the watching thread
        cannot look while it does, and a hold it caused comes back with nowhere. Kept so that line
        can say a collection was the hold. No lock here: a collection can start inside any
        allocation, including one made while such a lock was held, and would then wait on itself.
        """
        now = self._clock()
        if phase == "start":
            self._collecting = now
        elif phase == "stop" and self._collecting is not None:
            self._collections.append((self._collecting, now, info.get("generation", 0)))
            self._collecting = None

    def collection_during(self, since: float, until: float) -> tuple[float, int] | None:
        """How long garbage collection overlapped the moments from `since` to `until`, with the
        oldest generation collected, or None when no collection did."""
        seconds = 0.0
        generation = -1
        for began, ended, gen in list(self._collections):
            overlap = min(ended, until) - max(began, since)
            if overlap > 0:
                seconds += overlap
                generation = max(generation, gen)
        return (seconds, generation) if generation >= 0 else None

    def dump(self) -> None:
        """Every thread's stack, to standard error, plus a line saying why it is there.

        The log line goes through the ordinary logger even though the loop is not running: the
        logger writes to a stream from whichever thread calls it, and this call is the one thing
        that must work while the loop does not.
        """
        log.error("loop.stalled", seconds=round(self.overdue(), 1))
        faulthandler.dump_traceback(file=sys.stderr, all_threads=True)

    @property
    def worst_lag_seconds(self) -> float:
        """The longest the loop has been held since the process started.

        Read rather than reset, so whatever asks for it sees the same number as everything else
        that asks. A run that never rises above scheduling jitter is the evidence that a change
        did not put work back on the loop, which is otherwise only ever noticed by somebody
        watching a video.
        """
        return self._worst_lag

    @property
    def held_count(self) -> int:
        """How many heartbeats came back later than the warning threshold."""
        return self._held_count

    @staticmethod
    def overshoot(elapsed: float, asked: float) -> float:
        """How much longer a wake-up took than it asked for, never below zero.

        The subtraction is the whole measurement and it is easy to get backwards: handing back the
        elapsed time instead would report a perfectly idle loop as held for the beat interval,
        every beat, for ever. Its own function so that can be pinned without a clock.
        """
        return max(0.0, elapsed - asked)

    def record_lag(self, lag: float) -> bool:
        """Take one measurement. True when this one is worth a line in the log.

        The measurement is the heartbeat's own lateness: it asks to be woken in a fixed time, and
        anything beyond that is time the loop was not free to wake it. Nothing else has to be
        instrumented for this to be true of the whole application.
        """
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
            # Whatever this slept beyond what it asked for is time the loop was held by something
            # else. Measured here because the heartbeat already exists and already wakes on a
            # timer; a second timer would only be a second thing to be late.
            woke = self._clock()
            lag = self.overshoot(woke - started, self._beat)
            # The mark moves before the note is taken: a look arriving between the two would find
            # the loop still late and read this very line being written as a second hold.
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
        """Begin watching. The thread is a daemon, so it never holds up a shutdown."""
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


#: How often the pool is asked whether it has a thread to spare, in seconds.
#:
#: Matched to the heartbeat above rather than chosen separately. One submission a second is nothing
#: next to what the pool is already doing, and a probe far apart would step over the short, frequent
#: waits that are what a stutter actually is.
POOL_PROBE_SECONDS = 1.0

#: How long a piece of work may wait for a free thread before it is worth a line in the log.
#:
#: The same quarter second the loop uses, on purpose: to somebody watching a video the two failures
#: feel identical, so the number at which each becomes a complaint is the same number. What differs
#: is which of them is happening, and that is what these two measurements are for.
POOL_WAIT_WARN_SECONDS = 0.25


class ThreadPoolWatch:
    """Watches how long work waits for a thread, which is the failure the loop watch cannot see.

    Everything that waits on the disk or on an external tool is handed to a thread precisely so it
    does not hold the loop, and they all go to the one pool the language provides by default. That
    pool is finite. When it is full, the next piece of work does not fail and is not slow: it sits
    in a queue until a thread is free.

    **The two failures look opposite from the outside, and that is why both have to be measured:**

    - a *held loop* stops everything together, the interface included
    - a *full pool* leaves the interface perfectly responsive while video stutters and file work
      crawls, because the interface does almost nothing on a thread and streaming does nothing else

    So the health of the loop says nothing at all about this. A request that only reads a constant
    is answered instantly by a process whose every thread is busy decoding, which is exactly the
    reassuring answer nobody should trust.

    The measurement is the plainest one available: hand the pool a piece of work that does nothing
    but note the moment it began, and subtract. That gap is time the work spent queued, and queued
    is the only thing it can be: there is nothing in it to be slow at.

    **Deliberately not sampled on the heartbeat**, though the heartbeat already wakes on the right
    timer. Waiting for the probe there would delay the next beat, and a full pool would then be
    reported as a held loop, the two readings collapsing into one at the exact moment telling them
    apart is the point.
    """

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
        """How long the probe in flight has been waiting right now. Zero when none is.

        The live half of the reading, and the one worth looking at while something is wrong: the
        worst-seen figure says the pool filled at some point, this one says it is full at this
        moment. A pool held full for minutes would otherwise show nothing at all until it cleared,
        because the probe measuring it is itself stuck in the queue.
        """
        if self._submitted_at is None:
            return 0.0
        return max(0.0, self._clock() - self._submitted_at)

    @staticmethod
    def queued_for(submitted: float, began: float) -> float:
        """How long a submission waited before it started running, never below zero.

        Its own function, and pure, for the same reason `LoopWatchdog.overshoot` is: it is a
        subtraction that is easy to write backwards, and backwards it reports a healthy pool as a
        full one for ever. Pinned without a clock.
        """
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
        """Hand the pool a piece of work that does nothing, and time how long until it starts."""
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


#: How long a request may wait for a database connection before it is worth a line in the log.
#:
#: The same quarter second the other two use, and for the same reason: to whoever is looking at a
#: screen the three failures are indistinguishable, so the point at which each becomes a complaint
#: is the same point. Which of the three is happening is what these measurements are for.
READ_WAIT_WARN_SECONDS = 0.25


class ReadPoolWatch:
    """Watches how long a read waits for a database connection. The third way this stops working.

    There are three finite things every request needs and any one of them can be the whole problem:
    the event loop, a thread, and a connection to the database. The first two are watched above,
    and this is the third.

    **The two watches above can both read healthy, and both tell the truth, while requests take
    a minute.** Nothing holds the loop and nothing is short of threads: every connection is held
    by a background pass reading a whole table, and an ordinary lookup is simply queued behind
    them.

    `ThreadPoolWatch`'s own description already contains the argument for this one, one level down:
    the health of the loop says nothing at all about the pool. The same sentence is true of the
    pool it does not watch.

    The measurement is the same plain one: borrow a connection, do nothing with it, and subtract.
    That gap is time spent queued, because there is nothing in it to be slow at.

    **And it says what was holding the lane**, which is the difference between a reading and an
    answer. "The wait is eight seconds" is another question. "The wait is eight seconds and the
    duplicate scan is holding the lane" is somewhere to go.
    """

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
        """How long the probe in flight has been waiting right now. Zero when none is.

        The live half, and the one that matters while something is wrong. Without it a pool held
        full for minutes shows nothing at all until it clears, because the probe measuring it is
        itself stuck in the queue.
        """
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
        """Ask for a connection, give it straight back, and time how long the asking took."""
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
                # The database is closed (a restore closes it and opens the restored copy, which
                # takes a second or two), so there is no pool to measure and nothing waited. The
                # tick is skipped, not the watch: letting this through would end the task for the
                # rest of the process with no line anywhere, and hand the error to whoever stops
                # the watch, which is the shutdown after every restore.
                continue
            if self.record_wait(wait):
                log.warning(
                    "db.readers.queued",
                    seconds=round(wait, 3),
                    worst_seconds=round(self._worst_wait, 3),
                    occurrences=self._full_count,
                    # What is holding the lane, when something is. The name of the pass is the
                    # whole value of this line: without it the log says a queue happened and
                    # leaves whoever reads it exactly where they started.
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


#: How long the loop's ready queue may take to drain before it is worth a line, in seconds.
#:
#: The same quarter second the other three use. What is different is what it catches. The watchdog
#: above finds a loop HELD: one callback that will not give it back. This finds a loop BACKLOGGED:
#: thousands of callbacks that each return promptly, so nothing is ever held and every request
#: still waits, because it is behind all of them.
#:
#: That distinction is not academic. A backlog reads as perfectly healthy while the application is
#: unusable: nothing held for long, no thread pool full, no connection queued, and requests taking
#: seconds. A backlog is what a hundred round trips per request produces, and none of the other
#: three can see it.
BACKLOG_WARN_SECONDS = 0.25


class LoopBacklogWatch:
    """Watches how long the loop takes to come back, which is not the same as it being held.

    The measurement is the plainest one there is: yield to the loop and subtract. `sleep(0)` puts
    this coroutine at the back of the ready queue, so what comes back is exactly how long the queue
    in front of it took to drain: there is nothing in that gap to be slow at, only work queued
    ahead of it.

    Held and backlogged feel identical to whoever is looking at a screen and are fixed by opposite
    things. A held loop means one piece of work does not belong on it. A backlogged loop means
    there is too much work, usually one request making hundreds of small round trips where it could
    make one.
    """

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
        """The most recent reading. What the screen shows as the state right now."""
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


#: How many kinds of work the slowest-work record keeps. Enough to name what is costing the time
#: without becoming a log of its own.
SLOWEST_KEPT = 8


class SlowestWork:
    """What actually took the time, by kind. The fourth thing the other watches cannot show.

    The three watches above all measure **waiting for a resource**: the loop, a thread, a
    connection. A request that waits for none of them and simply does a great deal of work appears
    on none of them, and that is the ordinary case: one endpoint asking a small question several
    thousand times is slow without ever queueing for anything.

    So this records the other half: for each kind of work, how many times it ran in this window,
    the worst one, and the total. The total is the useful column and the one a log line cannot
    give: a thing that takes four milliseconds and runs two thousand times is eight seconds, and
    it looks entirely innocent one line at a time.
    """

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
        """Start a new window. What was costing the time an hour ago is not an answer now."""
        self._runs.clear()
        self._worst.clear()
        self._total.clear()

    def worst(self) -> list[dict[str, float | int | str]]:
        """The kinds of work costing the most time, dearest first.

        Ordered by TOTAL rather than by the worst single run, deliberately. The fault this exists
        to make visible is a cheap thing done far too often, and ordering by the worst single run
        is exactly what hides it.
        """
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


#: How many distinct wide reads to keep. Same reasoning as `SLOWEST_KEPT`: enough to see a pattern,
#: few enough to read at a glance.
WIDEST_KEPT = 8


class WidestReads:
    """Which reads hand back the most rows. The one reading that means the same thing loaded or idle.

    Every other watch in this file is a duration, and a duration is a property of the machine as
    much as of the query: the same statement can measure milliseconds on a quiet box and over a
    minute beside two busy threads, because a row leaving SQLite for Python costs a turn of the
    interpreter and a scan returning thousands of rows queues for it once per row. So a slow
    reading says "something else is busy" at least as loudly as it says "this is expensive", and
    every duration can read healthy through an application that is unusable.

    **The row count does not move.** It is the same number on an idle machine as on a loaded one,
    it is knowable before anything is slow, and it is what actually decides whether a screen
    survives an import. That is what makes it worth a section of its own rather than a column on
    the one beside it.

    Ranked by the WIDEST single read rather than by a total, which is the opposite choice to
    `SlowestWork` and deliberate: a wide read is a fault in one statement, and one statement
    returning five thousand rows is the thing to find. Averaging it into a total would hide it
    behind a cheap read that runs often.
    """

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
    """A short, stable name for a statement, so the same read lands in the same bucket.

    The statement's own text, collapsed to one line and cut short. It carries no values: every
    value in this application is bound, never interpolated, so there is nothing here to redact.
    """
    if not sql:
        return stage
    flat = " ".join(sql.split())
    return flat[:110] if len(flat) <= 110 else flat[:107] + "..."


@contextlib.contextmanager
def boot_set_aside() -> Iterator[None]:
    """Keep what start-up built out of every later collection, for as long as the server runs.

    A full collection walks every tracked object while every request waits, and nearly all of
    them are start-up's, which live until the process ends: seconds on a slow or busy machine.
    Given back on the way out, so an application built again in one process keeps no earlier one.
    """
    gc.collect()
    gc.freeze()
    try:
        yield
    finally:
        gc.unfreeze()


def install_stack_dumper() -> bool:
    """Make a signal dump every thread's stack. False where the site has no such signal.

    `SIGUSR1` because nothing else uses it and it is not one anybody sends by accident. Sending it
    costs nothing and changes nothing: send that signal to the process and every thread's stack
    goes to standard error, where the container's log picks it up.

    `chain=False`: there is no previous handler for this signal to pass it on to.
    """
    handler = getattr(signal, "SIGUSR1", None)
    if handler is None:
        return False
    # `faulthandler.register` exists only where signals do; the SIGUSR1 guard above already
    # returns early on Windows, so this line is unreachable there and simply absent from the
    # module's API. Both codes so neither host complains about the other.
    faulthandler.register(  # type: ignore[attr-defined, unused-ignore]
        handler, file=sys.stderr, all_threads=True, chain=False
    )
    return True
