# SPDX-License-Identifier: AGPL-3.0-or-later
"""The watchdog that catches a frozen event loop.

Driven by a clock the test moves rather than by sleeping, so these are instant and say what they
mean: the question is never "did enough time pass", it is "what does this decide at this gap".
"""

from __future__ import annotations

import asyncio
import contextlib
import faulthandler
import gc
import json
import signal
import threading
import time
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor

import pytest

from sift.kernel import diagnostics
from sift.kernel.db import DatabaseError
from sift.kernel.diagnostics import (
    LoopBacklogWatch,
    LoopWatchdog,
    ReadPoolWatch,
    SlowestWork,
    ThreadPoolWatch,
    WidestReads,
    boot_set_aside,
    install_stack_dumper,
    where_is,
)
from sift.kernel.log import configure_logging


class Clock:
    """A clock a test moves by hand."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def a_watchdog(clock: Clock, *, stall: float = 15.0) -> LoopWatchdog:
    return LoopWatchdog(stall_seconds=stall, beat_seconds=0.01, clock=clock)


def test_a_loop_that_keeps_answering_is_never_reported() -> None:
    clock = Clock()
    watchdog = a_watchdog(clock)

    for _ in range(100):
        clock.now += 1.0
        watchdog.beat()
        assert watchdog.check() is False


def test_a_gap_shorter_than_the_threshold_is_not_a_stall() -> None:
    """Ordinary busy moments must not produce a stack. A diagnostic that cries wolf gets ignored."""
    clock = Clock()
    watchdog = a_watchdog(clock)

    clock.now += 14.9

    assert watchdog.check() is False


def test_a_loop_that_stops_answering_is_reported() -> None:
    clock = Clock()
    watchdog = a_watchdog(clock)

    clock.now += 15.1

    assert watchdog.check() is True


def test_one_stall_is_reported_once_however_long_it_lasts() -> None:
    """A loop blocked for seven minutes would otherwise write the same stack hundreds of times,
    and the first one is the only one worth reading."""
    clock = Clock()
    watchdog = a_watchdog(clock)

    clock.now += 20.0
    assert watchdog.check() is True
    for _ in range(50):
        clock.now += 1.0
        assert watchdog.check() is False


def test_the_loop_coming_back_arms_it_again() -> None:
    """A second freeze after a recovery is a second thing to look at, not the same one."""
    clock = Clock()
    watchdog = a_watchdog(clock)

    clock.now += 20.0
    assert watchdog.check() is True

    clock.now += 1.0
    watchdog.beat()
    clock.now += 20.0

    assert watchdog.check() is True


def test_how_long_it_has_been_waiting_is_what_gets_reported() -> None:
    clock = Clock()
    watchdog = a_watchdog(clock)

    clock.now += 42.0

    assert watchdog.overdue() == pytest.approx(42.0)


async def test_the_running_loop_keeps_the_mark_moving() -> None:
    """The half that cannot be tested with a fake clock: something has to actually beat."""
    watchdog = LoopWatchdog(stall_seconds=15.0, beat_seconds=0.001)
    watchdog.start()
    try:
        await asyncio.sleep(0.05)
        assert watchdog.overdue() < 0.05
    finally:
        await watchdog.stop()


async def test_stopping_it_twice_is_harmless() -> None:
    """Shutdown paths get run more than once, and a diagnostic must never be the thing that
    fails one."""
    watchdog = LoopWatchdog(beat_seconds=0.001)
    watchdog.start()

    await watchdog.stop()
    await watchdog.stop()


def test_a_stack_can_be_asked_for_from_outside() -> None:
    """So a freeze can be looked at without restarting anything or granting new privileges."""
    assert install_stack_dumper() is (hasattr(signal, "SIGUSR1"))


@pytest.mark.parametrize(
    ("filename", "said"),
    [
        ("C:\\Users\\someone\\app\\src\\sift\\kernel\\db.py", "kernel/db.py"),
        ("/home/someone/app/src/sift/slices/faces/queue.py", "slices/faces/queue.py"),
        # Outside the package: its last two parts, never the folders above them.
        ("C:\\Users\\someone\\Python\\Lib\\asyncio\\events.py", "asyncio/events.py"),
        ("/usr/lib/python3.13/threading.py", "python3.13/threading.py"),
    ],
)
def test_a_held_frame_names_its_file_without_the_machines_folders(filename: str, said: str) -> None:
    assert diagnostics._short(filename) == said


# --- how hard the loop is being held ---------------------------------------------------------
#
# The watchdog above answers "has the loop stopped". These answer "is it being held", which is the
# same fault while it is still only a stutter, a state a stall can pass through without anything
# noticing.


def test_an_unheld_loop_reports_no_lag() -> None:
    watchdog = LoopWatchdog()

    assert watchdog.record_lag(0.0) is False
    assert watchdog.worst_lag_seconds == 0.0
    assert watchdog.held_count == 0


def test_jitter_below_the_threshold_is_remembered_but_not_reported() -> None:
    """The peak is still worth having: it is what says a change did not put work back on the
    loop. It is only the log line that has a bar to clear."""
    watchdog = LoopWatchdog(lag_warn_seconds=0.25)

    assert watchdog.record_lag(0.1) is False
    assert watchdog.worst_lag_seconds == pytest.approx(0.1)
    assert watchdog.held_count == 0


def test_a_held_loop_is_reported() -> None:
    watchdog = LoopWatchdog(lag_warn_seconds=0.25)

    assert watchdog.record_lag(0.4) is True
    assert watchdog.worst_lag_seconds == pytest.approx(0.4)
    assert watchdog.held_count == 1


def test_the_worst_is_kept_rather_than_the_latest() -> None:
    """A run is judged by its worst moment. Keeping the latest would let one calm second erase
    the half-second freeze that came before it."""
    watchdog = LoopWatchdog(lag_warn_seconds=0.25)

    watchdog.record_lag(0.9)
    watchdog.record_lag(0.05)

    assert watchdog.worst_lag_seconds == pytest.approx(0.9)


def test_repeated_holding_is_counted_but_not_logged_every_time() -> None:
    """A loop held once a second would otherwise write a line a second for as long as it lasted,
    and the first line already said it. The count still rises, so nothing is lost."""
    clock = Clock()
    watchdog = LoopWatchdog(lag_warn_seconds=0.25, lag_report_interval=30.0, clock=clock)

    assert watchdog.record_lag(0.4) is True
    clock.now += 1.0
    assert watchdog.record_lag(0.4) is False
    clock.now += 1.0
    assert watchdog.record_lag(0.4) is False

    assert watchdog.held_count == 3


def test_holding_again_after_the_interval_is_reported_again() -> None:
    """Throttled, not silenced: a fault that is still happening half an hour later has to be
    able to say so."""
    clock = Clock()
    watchdog = LoopWatchdog(lag_warn_seconds=0.25, lag_report_interval=30.0, clock=clock)

    assert watchdog.record_lag(0.4) is True
    clock.now += 31.0
    assert watchdog.record_lag(0.4) is True


def test_the_lag_is_the_overshoot_and_not_the_whole_wait() -> None:
    """The measurement is how much LONGER the wake-up took than it asked for.

    Handing back the elapsed time instead would report a perfectly idle loop as held for the beat
    interval, every beat, for ever, and this number is the only one anybody would act on.
    """
    assert LoopWatchdog.overshoot(elapsed=1.5, asked=1.0) == pytest.approx(0.5)


def test_a_wake_up_that_was_early_is_not_negative_lag() -> None:
    """A sleep can return a hair early. Reported as held for less than no time, it would drag the
    worst-seen figure down and read as an improvement."""
    assert LoopWatchdog.overshoot(elapsed=0.99, asked=1.0) == 0.0


async def test_the_running_heartbeat_measures_its_own_lateness() -> None:
    """End to end on a real loop: nothing holds it, so the lag stays at jitter rather than at
    the beat interval, which is what a subtraction with the wrong sign would produce."""
    watchdog = LoopWatchdog(beat_seconds=0.01, lag_warn_seconds=5.0)
    watchdog.start()
    try:
        await asyncio.sleep(0.1)
    finally:
        await watchdog.stop()

    assert watchdog.worst_lag_seconds < 0.5
    assert watchdog.held_count == 0


# --- how long work waits for a thread ----------------------------------------------------------
#
# The other way this application stops working, and the one everything above is blind to. Work is
# kept off the loop by handing it to a thread; when there is no free thread it waits. The loop stays
# perfectly healthy throughout, which is why a green reading from the watchdog says nothing here.


def test_a_pool_with_room_reports_no_wait() -> None:
    watch = ThreadPoolWatch()

    assert watch.record_wait(0.0) is False
    assert watch.worst_wait_seconds == 0.0
    assert watch.full_count == 0


def test_a_short_wait_is_remembered_but_not_reported() -> None:
    """Every submission queues for a moment. The peak is still worth keeping (it is what says a
    change did or did not put more pressure on the pool), but only a real wait earns a log line."""
    watch = ThreadPoolWatch(wait_warn_seconds=0.25)

    assert watch.record_wait(0.01) is False
    assert watch.worst_wait_seconds == pytest.approx(0.01)
    assert watch.full_count == 0


def test_a_full_pool_is_reported() -> None:
    watch = ThreadPoolWatch(wait_warn_seconds=0.25)

    assert watch.record_wait(0.4) is True
    assert watch.worst_wait_seconds == pytest.approx(0.4)
    assert watch.full_count == 1


def test_the_worst_wait_is_kept_rather_than_the_latest() -> None:
    """One calm second must not erase the moment every thread was busy for half of one."""
    watch = ThreadPoolWatch(wait_warn_seconds=0.25)

    watch.record_wait(0.9)
    watch.record_wait(0.001)

    assert watch.worst_wait_seconds == pytest.approx(0.9)


def test_a_pool_full_repeatedly_is_counted_but_not_logged_every_time() -> None:
    clock = Clock()
    watch = ThreadPoolWatch(wait_warn_seconds=0.25, report_interval=30.0, clock=clock)

    assert watch.record_wait(0.4) is True
    clock.now += 1.0
    assert watch.record_wait(0.4) is False
    clock.now += 31.0
    assert watch.record_wait(0.4) is True

    assert watch.full_count == 3


def test_the_wait_is_the_time_before_it_started_not_the_round_trip() -> None:
    """The measurement has to end when the work BEGINS, not when it finishes.

    Timing the round trip instead would fold in the loop's own lateness resuming afterwards, so a
    held loop would read as a full pool, and the one thing these two measurements exist to do is
    tell those apart.
    """
    assert ThreadPoolWatch.queued_for(submitted=10.0, began=10.75) == pytest.approx(0.75)


def test_a_clock_that_went_backwards_is_not_a_negative_wait() -> None:
    """Reported below zero it would drag the worst-seen figure down and read as an improvement."""
    assert ThreadPoolWatch.queued_for(submitted=10.0, began=9.9) == 0.0


def test_nothing_is_waiting_when_no_probe_is_in_flight() -> None:
    assert ThreadPoolWatch().waiting_seconds == 0.0


async def test_a_pool_with_room_measures_almost_no_wait() -> None:
    """End to end on a real loop and the real default pool, with nothing else running."""
    watch = ThreadPoolWatch(probe_seconds=0.01, wait_warn_seconds=5.0)
    watch.start()
    try:
        await asyncio.sleep(0.1)
    finally:
        await watch.stop()

    assert watch.worst_wait_seconds < 0.5
    assert watch.full_count == 0


async def test_a_full_pool_is_measured_end_to_end() -> None:
    """The fault itself, in miniature: every thread busy, and the wait is seen while it is happening.

    The pool is cut to a single thread rather than every one the machine has being filled, so this
    measures the same thing on any size of box and in a fraction of a second. `asyncio.to_thread`
    goes to whatever the loop's default executor is, which is exactly what makes that substitution
    legitimate: the probe is not told it is being tested.

    The executor is left for the loop's own teardown to reap: shutting it down here, while the loop
    still holds it as its default, would leave anything that stepped off the loop during teardown
    handing work to a closed pool.
    """
    loop = asyncio.get_running_loop()
    occupied = asyncio.Event()
    release = threading.Event()

    only_one = ThreadPoolExecutor(max_workers=1)
    loop.set_default_executor(only_one)

    def hold() -> None:
        # Set from the thread by way of the loop: the flag is read on the loop, and a plain
        # threading event polled from here would be this test busy-waiting on its own subject.
        loop.call_soon_threadsafe(occupied.set)
        release.wait(timeout=5.0)

    holding = loop.run_in_executor(only_one, hold)
    await occupied.wait()

    watch = ThreadPoolWatch(probe_seconds=0.01, wait_warn_seconds=0.05)
    watch.start()
    try:
        await asyncio.sleep(0.2)
        # The live reading, and the half that matters while something is wrong: worst-seen cannot
        # say anything yet, because the probe that would tell it is itself stuck in the queue.
        assert watch.waiting_seconds > 0.0
    finally:
        release.set()
        await holding
        await asyncio.sleep(0.05)
        await watch.stop()

    assert watch.worst_wait_seconds > 0.05
    assert watch.full_count >= 1
    assert watch.waiting_seconds == 0.0


# --- what the time went on, and how much the reads moved ----------------------------------------
#
# The two readings that are not about waiting for anything. They are the only sections on the health
# screen that can say what is wrong when every queue is empty, so a fault in them is a screen that
# reads clean while somebody is telling you the application is unusable.


def test_the_work_report_ranks_by_total_rather_than_by_the_worst_run() -> None:
    """The fault worth catching is a cheap thing done far too often.

    Ranking by the worst single run is exactly what hides it, so the two have to disagree here or
    the test proves nothing: the frequent stage is a tenth of the rare one per run and eight times
    the cost altogether.
    """
    work = SlowestWork()
    for _ in range(80):
        work.record("db.read", 100.0)
    work.record("sprite.render", 1000.0)

    ranked = work.worst()

    assert [row["stage"] for row in ranked] == ["db.read", "sprite.render"]
    assert ranked[0]["total_ms"] == 8000.0
    assert ranked[0]["worst_ms"] == 100.0
    assert ranked[0]["runs"] == 80


def test_a_new_window_forgets_what_was_costing_the_time_an_hour_ago() -> None:
    work = SlowestWork()
    work.record("db.read", 5.0)
    work.reset()

    assert work.worst() == []


def test_the_widest_read_ranks_by_ONE_read_rather_than_by_a_total() -> None:
    """The opposite choice to the report above, and deliberate.

    A wide read is a fault in one statement (one query returning five thousand rows is the thing
    to find), so a total would bury it under a narrow read that runs constantly. Arranged so the
    two orders disagree: the narrow one moves four times as many rows altogether.
    """
    reads = WidestReads()
    for _ in range(400):
        reads.record("db.read", 50, "SELECT id FROM assets WHERE folder_id = ?")
    reads.record("db.read", 5000, "SELECT * FROM face_tracks")

    ranked = reads.widest()

    assert ranked[0]["read"] == "SELECT * FROM face_tracks"
    assert ranked[0]["widest_rows"] == 5000
    assert ranked[1]["total_rows"] == 20000, "the narrow one moved more rows and still ranks below"


def test_the_same_statement_lands_in_the_same_bucket_however_it_is_spaced() -> None:
    """Whitespace is not a different query. Without this one statement reads as several, each with
    a fraction of the count, and a wide read hides as a handful of ordinary ones."""
    reads = WidestReads()
    reads.record("db.read", 10, "SELECT a\n  FROM b")
    reads.record("db.read", 20, "SELECT a FROM b")

    ranked = reads.widest()

    assert len(ranked) == 1
    assert ranked[0]["widest_rows"] == 20
    assert ranked[0]["runs"] == 2


def test_a_read_with_no_statement_is_still_counted_under_its_stage() -> None:
    """Dropping it would make the total quietly wrong, which is worse than a vague name."""
    reads = WidestReads()
    reads.record("db.sweep", 900, None)

    assert reads.widest() == [
        {"read": "db.sweep", "widest_rows": 900, "runs": 1, "total_rows": 900}
    ]


# --- the stack, and the line that goes with it ---------------------------------------------------


def test_a_stall_writes_a_line_and_a_stack(capfd: pytest.CaptureFixture[str]) -> None:
    """The line goes through the ordinary logger even though the loop is not running: it writes to
    a stream from whichever thread calls it, and this call is the one thing that must work while
    the loop does not.

    Captured at the file descriptor rather than at `sys.stderr`: the stack is written by the C-level
    fault handler, which needs a real one.
    """
    configure_logging()
    clock = Clock()
    watchdog = a_watchdog(clock)
    clock.now += 42.0

    watchdog.dump()

    written = capfd.readouterr()
    assert "loop.stalled" in written.out
    assert "Thread" in written.err or "File " in written.err


async def test_the_watching_thread_dumps_when_the_loop_stops_answering(
    capfd: pytest.CaptureFixture[str],
) -> None:
    """The thread body, which is the whole mechanism: nothing on the loop can notice the loop has
    stopped, so the noticing is done from a thread that never touches it.

    The clock is stepped rather than the test waiting: what is under test is that the thread asks,
    not how long a real freeze takes.
    """
    configure_logging()

    class Stepping:
        now = 0.0

        def __call__(self) -> float:
            Stepping.now += 60.0
            return Stepping.now

    # A clock that moves a minute per reading, so the mark is always overdue however often the
    # heartbeat resets it. Stepping it once instead leaves a race with the beat.
    watchdog = LoopWatchdog(stall_seconds=0.5, beat_seconds=0.005, clock=Stepping())
    watchdog.start()
    try:
        await asyncio.sleep(0.1)
    finally:
        await watchdog.stop()

    assert "loop.stalled" in capfd.readouterr().out


async def test_a_held_loop_says_so_from_its_own_heartbeat(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The beat measures its own lateness, so the line comes out of the same call that keeps the
    mark moving rather than out of a second timer that would only be a second thing to be late.

    The clock moves a long way between readings while the real sleep is short, which is a loop that
    was held without the test having to hold one.
    """
    configure_logging()

    class Stepping:
        now = 0.0

        def __call__(self) -> float:
            Stepping.now += 5.0
            return Stepping.now

    # Far above what the stepping clock produces, so the stall dump does not fire as well and the
    # line under test is the only one written.
    watchdog = LoopWatchdog(stall_seconds=10_000.0, beat_seconds=0.005, clock=Stepping())
    watchdog.start()
    try:
        await asyncio.sleep(0.05)
    finally:
        await watchdog.stop()

    assert "loop.held" in capsys.readouterr().out
    assert watchdog.held_count >= 1


def test_where_a_thread_is_names_the_function_it_is_in() -> None:
    """Read from outside the thread, innermost first, each file by its path under the package: a
    line in the log that names a folder of the machine it ran on would be a line nobody can share."""
    parked = threading.Event()
    release = threading.Event()

    def parked_here() -> None:
        parked.set()
        release.wait(5)

    thread = threading.Thread(target=parked_here, daemon=True)
    thread.start()
    assert parked.wait(5)
    try:
        where = where_is(thread.ident or 0)
    finally:
        release.set()
        thread.join(5)

    assert where is not None and where.startswith("kernel/tests/test_diagnostics.py:"), where
    assert "parked_here" in where, where
    assert ":/" not in where and "\\" not in where, where


def test_a_thread_that_is_not_there_is_nowhere() -> None:
    assert where_is(-1) is None


async def test_a_held_loop_s_line_says_where_it_was_held(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The heartbeat learns of a hold when it is over, and the loop is somewhere else by then. The
    watching thread reads the loop's stack while the hold is still going, and the line carries it:
    without that a held loop says how long and never what."""
    configure_logging()
    watchdog = LoopWatchdog(
        stall_seconds=10_000.0, beat_seconds=0.02, lag_warn_seconds=0.05, look_seconds=0.005
    )

    def holds_the_loop() -> None:
        time.sleep(0.4)

    watchdog.start()
    try:
        await asyncio.sleep(0.05)
        holds_the_loop()
        await asyncio.sleep(0.1)
    finally:
        await watchdog.stop()

    held = [line for line in capsys.readouterr().out.splitlines() if "loop.held" in line]
    assert any("holds_the_loop" in line for line in held), held


async def test_a_hold_is_looked_up_once_and_a_loop_on_time_never(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One look per hold: the first is where it began, and a look every twentieth of a second for
    as long as it lasted would be a stack read twenty times a second from a thread that exists to
    cost nothing. A loop that answers on time is never read at all.

    The line itself is made slow to write here. The heartbeat writes it after the hold, and a look
    arriving while it does must find the loop's mark already moved, or the writing of one hold's
    line is read as a second hold.

    Run on the real clock, with a warning of a whole second: a machine busy with other tests keeps
    the loop waiting a tenth of one on its own, and that is not the hold this is about."""
    looked: list[int] = []

    class SlowToWrite:
        def warning(self, *_args: object, **_said: object) -> None:
            time.sleep(0.5)

    monkeypatch.setattr("sift.kernel.diagnostics.log", SlowToWrite())

    def where(thread_id: int) -> str | None:
        looked.append(thread_id)
        return "somewhere"

    def holds_the_loop() -> None:
        time.sleep(2.5)

    watchdog = LoopWatchdog(
        stall_seconds=10_000.0,
        beat_seconds=0.02,
        lag_warn_seconds=1.0,
        look_seconds=0.005,
        where=where,
    )
    watchdog.start()
    try:
        await asyncio.sleep(0.15)
        assert looked == [], "a loop that kept its beat was read"
        holds_the_loop()
        await asyncio.sleep(1.0)
        assert looked == [threading.get_ident()], looked
        holds_the_loop()
        await asyncio.sleep(1.0)
        assert len(looked) == 2, looked
    finally:
        await watchdog.stop()


def test_a_hold_just_over_the_warning_is_named_though_it_ends_between_two_looks() -> None:
    """A hold of 0.286 s against a quarter second warning and a look every twentieth of a second.

    The looks fall where they fall: here one comes 10 ms before the warning is reached and the next
    4 ms after the hold has ended. A look that waited for the warning itself would see neither and
    the line would say nowhere, so the look is taken at half the warning instead.
    """
    clock = Clock()
    looked: list[float] = []

    def where(_thread_id: int) -> str | None:
        looked.append(clock.now)
        return "library/scan.py:12 walk"

    watchdog = LoopWatchdog(beat_seconds=1.0, lag_warn_seconds=0.25, clock=clock, where=where)
    # The thread the loop runs on, which start() records; this test plays the watching thread.
    watchdog._loop_thread = threading.get_ident()
    watchdog.beat()
    for tick in range(6):
        clock.now = 1.04 + tick * 0.05
        watchdog.look()

    assert looked == [pytest.approx(1.14)], looked
    assert watchdog.record_lag(0.286) is True
    assert watchdog.held_where() == "library/scan.py:12 walk"


async def test_a_hold_under_the_warning_writes_nothing_and_its_look_is_dropped(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The look at half the warning reads a hold that may never reach it. That hold writes no line,
    and where it was is not carried over to name the next hold that does."""
    configure_logging()
    watchdog = LoopWatchdog(
        stall_seconds=10_000.0, beat_seconds=0.02, lag_warn_seconds=0.3, look_seconds=0.005
    )

    def under_the_warning() -> None:
        time.sleep(0.2)

    def over_the_warning() -> None:
        time.sleep(0.5)

    watchdog.start()
    try:
        await asyncio.sleep(0.05)
        under_the_warning()
        await asyncio.sleep(0.05)
        assert "loop.held" not in capsys.readouterr().out
        over_the_warning()
        await asyncio.sleep(0.05)
    finally:
        await watchdog.stop()

    held = [line for line in capsys.readouterr().out.splitlines() if "loop.held" in line]
    assert len(held) == 1, held
    assert "over_the_warning" in held[0], held
    assert "under_the_warning" not in held[0], held


def test_a_collection_is_set_beside_the_moments_it_overlapped() -> None:
    clock = Clock()
    watchdog = a_watchdog(clock)

    clock.now = 10.0
    watchdog.collected("start", {"generation": 2})
    clock.now = 10.4
    watchdog.collected("stop", {"generation": 2, "collected": 0, "uncollectable": 0})
    clock.now = 10.5
    watchdog.collected("start", {"generation": 0})
    clock.now = 10.52
    watchdog.collected("stop", {"generation": 0, "collected": 0, "uncollectable": 0})

    seconds, generation = watchdog.collection_during(10.1, 11.0) or (0.0, -1)
    assert seconds == pytest.approx(0.32)
    assert generation == 2
    assert watchdog.collection_during(9.0, 10.0) is None
    assert watchdog.collection_during(10.6, 12.0) is None
    # An end with no start before it, and a phase that is neither, record nothing.
    watchdog.collected("stop", {"generation": 1})
    watchdog.collected("other", {})
    assert watchdog.collection_during(10.6, 12.0) is None


async def test_a_collection_that_overlaps_a_hold_is_said_on_its_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A collection holds the interpreter lock, so the watching thread cannot look while it runs and
    the line comes back with nowhere. The line says the collection instead, so an empty place has
    its reason beside it. The interpreter's own collections reach the watchdog while it runs, and
    stop reaching it once it has stopped."""
    configure_logging()
    watchdog = LoopWatchdog(
        stall_seconds=10_000.0, beat_seconds=0.02, lag_warn_seconds=0.1, look_seconds=0.005
    )

    def a_long_collection() -> None:
        watchdog.collected("start", {"generation": 2})
        time.sleep(0.3)
        watchdog.collected("stop", {"generation": 2, "collected": 0, "uncollectable": 0})

    watchdog.start()
    try:
        await asyncio.sleep(0.05)
        a_long_collection()
        await asyncio.sleep(0.05)
        assert watchdog.collected in gc.callbacks
        gc.collect(0)
        assert watchdog.collection_during(0.0, time.monotonic()) is not None
    finally:
        await watchdog.stop()
    assert watchdog.collected not in gc.callbacks

    held = [line for line in capsys.readouterr().out.splitlines() if "loop.held" in line]
    assert len(held) == 1, held
    line = json.loads(held[0])
    assert line["gc_generation"] == 2, line
    assert line["gc_seconds"] > 0.1, line


async def test_a_thread_pool_watch_that_never_started_stops_without_complaint() -> None:
    """Shutdown paths get run more than once and out of order, and a diagnostic must never be the
    thing that fails one."""
    await ThreadPoolWatch().stop()


def test_the_stack_dumper_asks_for_every_thread_and_takes_the_signal_outright(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What it registers, on a site that has no such signal to register with.

    The point of the dumper is that a freeze can be looked at from outside, and the two arguments
    that make it useful are the ones nothing else asserts: EVERY thread, because a stall is a thread
    holding something rather than the main one being busy, and no chaining, because there is no
    previous handler for this to pass the signal on to. Both are unreachable on a site without
    SIGUSR1, so the signal and the registrar are both stood in for, and what is checked is the
    call the code makes.
    """
    asked: dict[str, object] = {}

    monkeypatch.setattr(signal, "SIGUSR1", 10, raising=False)
    monkeypatch.setattr(
        faulthandler,
        "register",
        lambda number, **kwargs: asked.update({"number": number, **kwargs}),
        raising=False,
    )

    assert install_stack_dumper() is True
    assert asked["number"] == 10
    assert asked["all_threads"] is True
    assert asked["chain"] is False


def test_the_stack_dumper_answers_for_the_site_it_is_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """False rather than a raise where the signal does not exist: the caller writes it into the
    boot log, and a diagnostic that is unavailable is not a boot failure."""
    monkeypatch.delattr(signal, "SIGUSR1", raising=False)

    assert install_stack_dumper() is False


# --- how long a read waits for a connection ------------------------------------------------------
#
# The third finite resource with exactly the same queueing failure as the two above. The loop can
# be healthy and no thread pool full while every request is queued behind job workers holding every
# connection.


class _Pool:
    """A stand-in read pool that hands a connection over when it is let go."""

    def __init__(self, clock: Clock, *, costs: float = 0.0) -> None:
        self._clock = clock
        self._costs = costs
        self.borrowed = 0

    def borrow(self) -> contextlib.AbstractAsyncContextManager[object]:
        return self._held()

    @contextlib.asynccontextmanager
    async def _held(self) -> AsyncIterator[object]:
        self.borrowed += 1
        self._clock.now += self._costs
        yield object()


def test_a_pool_with_a_connection_free_reports_no_wait() -> None:
    watch = ReadPoolWatch(_Pool(Clock()).borrow)

    assert watch.record_wait(0.0) is False
    assert watch.worst_wait_seconds == 0.0
    assert watch.full_count == 0


def test_a_short_queue_is_remembered_but_not_reported() -> None:
    """Every borrow queues for a moment. The peak is worth keeping and only a real wait earns a
    line, exactly as the thread pool's does."""
    watch = ReadPoolWatch(_Pool(Clock()).borrow, wait_warn_seconds=0.25)

    assert watch.record_wait(0.05) is False
    assert watch.worst_wait_seconds == pytest.approx(0.05)
    assert watch.full_count == 0


def test_a_pool_with_nothing_free_is_reported() -> None:
    watch = ReadPoolWatch(_Pool(Clock()).borrow, wait_warn_seconds=0.25)

    assert watch.record_wait(1.5) is True
    assert watch.worst_wait_seconds == pytest.approx(1.5)
    assert watch.full_count == 1


def test_the_worst_queue_is_kept_rather_than_the_latest() -> None:
    """A screen showing the latest reading says "fine" between two twenty-second waits."""
    watch = ReadPoolWatch(_Pool(Clock()).borrow, wait_warn_seconds=0.25)
    watch.record_wait(4.0)

    watch.record_wait(0.01)

    assert watch.worst_wait_seconds == pytest.approx(4.0)


def test_a_read_pool_full_repeatedly_is_counted_but_not_logged_every_time() -> None:
    """A pool held full for twenty minutes would otherwise write a line per probe, and the count
    is what says how bad it was."""
    clock = Clock()
    watch = ReadPoolWatch(
        _Pool(clock).borrow, wait_warn_seconds=0.25, report_interval=60.0, clock=clock
    )

    assert watch.record_wait(1.0) is True
    for _ in range(10):
        clock.now += 1.0
        assert watch.record_wait(1.0) is False
    assert watch.full_count == 11

    clock.now += 120.0
    assert watch.record_wait(1.0) is True


def test_nothing_is_queued_when_no_probe_is_in_flight() -> None:
    assert ReadPoolWatch(_Pool(Clock()).borrow).waiting_seconds == 0.0


def test_which_pass_is_holding_the_lane_is_asked_rather_than_remembered() -> None:
    """The name of the pass is the whole value of the log line this appears in: without it the log
    says a queue happened and leaves whoever reads it exactly where they started."""
    watch = ReadPoolWatch(_Pool(Clock()).borrow, holding=lambda: "folder sweep")

    assert watch.holding == "folder sweep"


async def test_a_borrow_that_waits_is_measured_while_it_is_still_waiting() -> None:
    """The live half, and the one that matters while something is wrong. Without it a pool held
    full for twenty minutes shows nothing at all until it clears, because the probe measuring it
    is itself stuck in the queue."""
    let_go = asyncio.Event()
    watching: list[float] = []

    @contextlib.asynccontextmanager
    async def slow() -> AsyncIterator[object]:
        await let_go.wait()
        yield object()

    watch = ReadPoolWatch(slow, probe_seconds=0.01, wait_warn_seconds=0.02)
    watch.start()
    try:
        await asyncio.sleep(0.1)
        watching.append(watch.waiting_seconds)
        let_go.set()
        await asyncio.sleep(0.1)
    finally:
        await watch.stop()

    assert watching[0] > 0.0, "a probe stuck in the queue reported nothing"
    assert watch.worst_wait_seconds > 0.0
    assert watch.full_count >= 1
    assert watch.waiting_seconds == 0.0


async def test_a_read_pool_watch_that_never_started_stops_without_complaint() -> None:
    await ReadPoolWatch(_Pool(Clock()).borrow).stop()


async def test_a_closed_database_skips_a_probe_and_the_watch_keeps_watching() -> None:
    """A restore closes the database and opens the restored copy. A probe that lands in that
    window finds no pool at all, and must not end the watch for the rest of the process and hand
    the error to the shutdown that stopped it."""
    closed = True
    borrowed = 0

    @contextlib.asynccontextmanager
    async def borrow() -> AsyncIterator[object]:
        nonlocal borrowed
        if closed:
            raise DatabaseError("the database is not open: call connect() first")
        borrowed += 1
        yield object()

    watch = ReadPoolWatch(borrow, probe_seconds=0.01)
    watch.start()
    try:
        await asyncio.sleep(0.1)
        closed = False
        await asyncio.sleep(0.1)
    finally:
        await watch.stop()

    assert borrowed > 0, "the watch stopped at the first probe that found the database closed"


# --- a loop that is not held and is still late ---------------------------------------------------
#
# Held and backlogged feel identical to whoever is looking at a screen and are fixed by opposite
# things. Everything above can read healthy while the application is unusable: nothing held for
# long, no thread pool full, no connection queued, and requests taking seconds.


def test_a_queue_that_drains_at_once_is_not_reported() -> None:
    watch = LoopBacklogWatch()

    assert watch.record(0.0) is False
    assert watch.worst_seconds == 0.0
    assert watch.latest_seconds == 0.0
    assert watch.over_count == 0


def test_a_backlogged_queue_is_reported() -> None:
    watch = LoopBacklogWatch(warn_seconds=0.25)

    assert watch.record(1.5) is True
    assert watch.worst_seconds == pytest.approx(1.5)
    assert watch.latest_seconds == pytest.approx(1.5)
    assert watch.over_count == 1


def test_the_screen_shows_the_latest_and_the_record_keeps_the_worst() -> None:
    """Two readings and not one: the latest is the state right now, which is what somebody watching
    a screen wait needs, and the worst is what says whether it has ever been bad."""
    watch = LoopBacklogWatch(warn_seconds=0.25)
    watch.record(4.0)

    watch.record(0.01)

    assert watch.worst_seconds == pytest.approx(4.0)
    assert watch.latest_seconds == pytest.approx(0.01)


def test_a_queue_that_stays_backlogged_is_counted_but_not_logged_every_time() -> None:
    clock = Clock()
    watch = LoopBacklogWatch(warn_seconds=0.25, report_interval=60.0, clock=clock)

    assert watch.record(1.0) is True
    for _ in range(10):
        clock.now += 1.0
        assert watch.record(1.0) is False
    assert watch.over_count == 11

    clock.now += 120.0
    assert watch.record(1.0) is True


async def test_a_running_backlog_watch_measures_a_real_ready_queue() -> None:
    """The measurement is the plainest one there is: yield to the loop and subtract. There is
    nothing in that gap to be slow at, only work queued ahead of it."""
    watch = LoopBacklogWatch(probe_seconds=0.005, warn_seconds=5.0)
    watch.start()
    # A second start must not put a second probe on the loop, both reporting into one reading.
    watch.start()
    try:
        await asyncio.sleep(0.05)
    finally:
        await watch.stop()

    assert watch.worst_seconds < 1.0
    assert watch.over_count == 0


async def test_a_backlogged_loop_says_so(capfd: pytest.CaptureFixture[str]) -> None:
    """The fault in miniature: a pile of callbacks that each return promptly, so nothing is ever
    held and everything still waits.

    The bar is set at nothing at all rather than the queue being made genuinely slow, because how
    long fifty callbacks take is a fact about this machine and not about the code.
    """
    configure_logging()
    watch = LoopBacklogWatch(probe_seconds=0.005, warn_seconds=0.0)
    watch.start()
    try:
        for _ in range(50):
            await asyncio.sleep(0)
        await asyncio.sleep(0.05)
    finally:
        await watch.stop()

    assert "loop.backlogged" in capfd.readouterr().out
    assert watch.over_count >= 1


async def test_a_backlog_watch_that_never_started_stops_without_complaint() -> None:
    await LoopBacklogWatch().stop()


def test_a_new_window_forgets_how_wide_the_reads_were() -> None:
    """The window is what makes the reading about now rather than about the whole life of the
    process, and a read that has not run since is not a reading."""
    reads = WidestReads()
    reads.record("db.read", 4267, "SELECT id FROM assets")
    assert reads.widest() != []

    reads.reset()

    assert reads.widest() == []


def test_what_start_up_built_is_set_aside_and_given_back() -> None:
    """Inside, a full collection has nothing of the boot's to walk; outside, nothing is kept."""
    built = [[n] for n in range(1000)]
    with boot_set_aside():
        aside = gc.get_freeze_count()
        assert aside >= len(built)
        assert not any(one is built for one in gc.get_objects())
    assert gc.get_freeze_count() == 0
    assert any(one is built for one in gc.get_objects())
