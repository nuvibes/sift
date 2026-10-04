# SPDX-License-Identifier: AGPL-3.0-or-later
"""What is still to come, per kind of job, and the two rules that make it safe to draw a bar from.

A progress bar needs a denominator. The work already in the queue is not one: it GROWS while a
pass runs (a scan hands out a read per file as it walks, a catch-up queues a page at a time), so a
total read from it would climb under the reader and no estimate of time left would be worth
anything for exactly as long as somebody was watching it.
"""

from __future__ import annotations

import asyncio

import pytest

from sift.kernel.jobs import work_ahead
from sift.kernel.jobs.work_ahead import Counter, WorkAhead


async def test_it_reports_what_each_kind_registered() -> None:
    ahead = WorkAhead()
    ahead.register("thumbnail", _answering(12))
    ahead.register("probe", _answering(300))

    assert await ahead.waiting() == {"thumbnail": 12, "probe": 300}


async def test_a_counter_that_fails_is_left_out_rather_than_called_nought() -> None:
    """Zero is a real answer meaning "nothing left to do", and this must never be able to say it.

    A feature that is switched off, mid-migration or simply broken would otherwise report that the
    library is finished, which is the one wrong answer that looks like good news. Absent means
    "not known", and a screen draws what it does know.
    """
    ahead = WorkAhead()
    ahead.register("thumbnail", _answering(12))
    ahead.register("face_scan", _raising())

    answer = await ahead.waiting()

    assert answer == {"thumbnail": 12}
    assert "face_scan" not in answer, "a broken counter reported the work as finished"


async def test_a_negative_answer_is_floored_rather_than_trusted() -> None:
    """A count below nought is a bug in a counter, and a bar built on it would run backwards."""
    ahead = WorkAhead()
    ahead.register("preview", _answering(-5))

    assert await ahead.waiting() == {"preview": 0}


async def test_it_is_asked_again_only_after_the_answer_goes_stale() -> None:
    """The dashboard re-reads whenever the queue moves, which during an import is several times a
    second. Counting on every one of those is a whole count of the library spent watching a number
    that moves slowly."""
    asked = _counting()
    # Generous, so the two reads below are certainly inside it however slow the machine is.
    ahead = WorkAhead(fresh_for=3600.0)
    ahead.register("probe", asked.count)

    first = await ahead.waiting()
    again = await ahead.waiting()

    assert first == again == {"probe": 7}
    assert asked.times == 1, "it counted the library twice for two reads in the same instant"


async def test_a_run_that_ends_sends_the_next_read_to_count_again() -> None:
    """The one moment the cached answer is wrong however young it is.

    A run ends and the queue empties at once, while the answer still holds the files that run has
    just finished, so Activity would draw a finished Generate as "Not started, under a minute". A
    kind whose finished tally moved since the count and which has nothing outstanding now is counted
    again; a kind still busy is not, or the cache would be gone during exactly the import it
    exists for.
    """
    asked = _counting()
    ahead = WorkAhead(fresh_for=3600.0)
    ahead.register("preview", asked.count)

    ahead.observe({"preview"}, {"preview": 3})
    await ahead.waiting()
    ahead.observe({"preview"}, {"preview": 5})
    await ahead.waiting()
    assert asked.times == 1, "a run still going was counted again on every read"

    ahead.observe(set(), {"preview": 8})
    await ahead.waiting()
    assert asked.times == 2, "a run that had ended was read from the count taken before it did"

    ahead.observe(set(), {"preview": 8})
    await ahead.waiting()
    assert asked.times == 2, "nothing had ended, and the library was counted again anyway"


async def test_a_run_shorter_than_the_gap_between_two_reads_still_counts_again() -> None:
    """Nothing was ever seen busy: the run began and ended between one read and the next. The
    finished tally moved all the same, and that is the signal."""
    asked = _counting()
    ahead = WorkAhead(fresh_for=3600.0)
    ahead.register("preview", asked.count)

    ahead.observe(set(), {"preview": 3})
    await ahead.waiting()
    ahead.observe(set(), {"preview": 6})
    await ahead.waiting()

    assert asked.times == 2


async def test_a_carrier_that_ends_counts_as_a_run_ending() -> None:
    """A per-file Generate job makes a preview, a strip and a fingerprint without being any of the
    three kinds, and its ending changes all three counts at once."""
    asked = _counting()
    ahead = WorkAhead(fresh_for=3600.0)
    ahead.register("preview", asked.count)

    ahead.observe({"generate_file"}, {"generate_file": 1})
    await ahead.waiting()
    ahead.observe(set(), {"generate_file": 2})
    await ahead.waiting()

    assert asked.times == 2


# --- a read never waits behind a count it does not need -----------------------------------------


async def test_an_old_answer_is_handed_out_at_once_while_one_count_runs_behind_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A whole count on a large library is over a second, so an answer that has only grown old is
    handed out as it stands rather than waited for, and the reads that follow while the count runs
    start no count of their own."""
    # A clock that stands still: a count then costs nothing, and a window of nought is always over.
    monkeypatch.setattr(work_ahead, "monotonic", _Clock().now)
    counter = _gated(answer=7)
    counter.gate.set()
    ahead = WorkAhead(fresh_for=0.0)
    ahead.register("thumbnail", counter.count)
    assert await ahead.waiting() == {"thumbnail": 7}

    counter.gate.clear()
    counter.answer = 5
    for _ in range(3):
        assert await asyncio.wait_for(ahead.waiting(), 1.0) == {"thumbnail": 7}
        await _turns()
    assert counter.times == 2, "every read of an old answer started a count of its own"

    counter.gate.set()
    await _turns()
    assert await ahead.waiting() == {"thumbnail": 5}, "the count behind the read never landed"


async def test_reads_that_arrive_while_a_count_runs_wait_for_that_one_count() -> None:
    """Each read that finds no answer starting a count of its own would run a dozen at once over
    the same readers, each slowing the others, and the screen would wait most of a minute."""
    counter = _gated(answer=9)
    ahead = WorkAhead()
    ahead.register("probe", counter.count)

    reads = [asyncio.create_task(ahead.waiting()) for _ in range(5)]
    await _turns()
    counter.gate.set()

    assert await asyncio.gather(*reads) == [{"probe": 9}] * 5
    assert counter.times == 1, "every read that arrived during the count started one of its own"


async def test_a_read_during_the_count_after_a_run_ended_waits_for_it_rather_than_another() -> None:
    """A run ends and the next read waits for a fresh count. A read arriving while that count runs,
    with nothing new ended, waits for the same count: had it started another, a screen asking once
    a second would make every count wrong before it landed and never be handed an answer."""
    counter = _gated(answer=4)
    counter.gate.set()
    ahead = WorkAhead(fresh_for=3600.0)
    ahead.register("preview", counter.count)
    ahead.observe(set(), {"preview": 1})
    await ahead.waiting()

    counter.gate.clear()
    counter.answer = 0
    ahead.observe(set(), {"preview": 2})
    first = asyncio.create_task(ahead.waiting())
    await _turns()
    ahead.observe(set(), {"preview": 2})
    second = asyncio.create_task(ahead.waiting())
    await _turns()
    counter.gate.set()

    assert list(await asyncio.gather(first, second)) == [{"preview": 0}] * 2
    assert counter.times == 2, "a read with nothing new ended started a count of its own"


async def test_a_run_that_ends_during_the_count_is_counted_again_before_anyone_is_told() -> None:
    """The count began before the run ended, so it holds the files the run has since finished. A
    read that saw the ending waits for a count begun after it."""
    counter = _gated(answer=4)
    counter.gate.set()
    ahead = WorkAhead(fresh_for=3600.0)
    ahead.register("preview", counter.count)
    ahead.observe(set(), {"preview": 1})
    await ahead.waiting()

    counter.gate.clear()
    ahead.observe(set(), {"preview": 2})
    first = asyncio.create_task(ahead.waiting())
    await _turns()
    counter.answer = 0
    ahead.observe(set(), {"preview": 3})
    second = asyncio.create_task(ahead.waiting())
    await _turns()
    counter.gate.set()

    assert (await second) == {"preview": 0}, "a read was handed a count taken before the run ended"
    await first
    assert counter.times == 3


async def test_a_count_that_costs_more_is_reused_for_longer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The counting takes at most a tenth of the time while a screen watches it, so the passes the
    screen reports on keep their readers: a count of two seconds is reused for twenty, not five."""
    clock = _Clock()
    monkeypatch.setattr(work_ahead, "monotonic", clock.now)
    times = 0

    async def costly() -> int:
        nonlocal times
        times += 1
        clock.at += 2.0
        return 1

    ahead = WorkAhead(fresh_for=5.0)
    ahead.register("face_scan", costly)
    await ahead.waiting()

    clock.at += 12.0
    await ahead.waiting()
    await _turns()
    assert times == 1, "a count costing two seconds was taken again twelve seconds later"

    clock.at += 7.0
    await ahead.waiting()
    await _turns()
    assert times == 2


class _gated:
    """A counter that answers only once let go, so a test can hold a count open."""

    def __init__(self, *, answer: int) -> None:
        self.answer = answer
        self.times = 0
        self.gate = asyncio.Event()

    async def count(self) -> int:
        # What the library held when the count began, which is what a count reports.
        answer = self.answer
        self.times += 1
        await self.gate.wait()
        return answer


class _Clock:
    def __init__(self) -> None:
        self.at = 1000.0

    def now(self) -> float:
        return self.at


async def _turns() -> None:
    """Let every task that can move, move: a count started behind a read runs on later turns."""
    for _ in range(20):
        await asyncio.sleep(0)


def _answering(total: int) -> Counter:
    async def counter() -> int:
        return total

    return counter


def _raising() -> Counter:
    async def counter() -> int:
        raise RuntimeError("the model is not loaded")

    return counter


class _counting:
    def __init__(self) -> None:
        self.times = 0

    async def count(self) -> int:
        self.times += 1
        return 7


# --- the run, which must only ever go forwards ---------------------------------------------------


def test_the_finished_count_climbs_as_the_work_lands() -> None:
    """The number never goes BACKWARDS while the machine is working.

    Counting finished jobs in a window that begins at the oldest unfinished one would let finished
    work fall out of the far end, since that window moves forward as the oldest finish: the x of
    the total would keep resetting.

    Nothing here reads a timestamp. `left` is counted from the library and only falls, and `done` is
    the run's size minus it, so it cannot go backwards.
    """
    ahead = WorkAhead()

    first = ahead.run_of("probe", left=1000, done_already=19, busy=True)
    assert (first.total, first.done) == (1019, 19)

    later = ahead.run_of("probe", left=400, done_already=3, busy=True)

    assert later.total == 1019, "the size of the run moved under the reader"
    assert later.done == 619, "finished work was forgotten as the queue drained"


def test_the_run_grows_rather_than_reporting_more_done_than_there_was() -> None:
    """Files arriving mid-run raise what is left. A fixed total below it would report a number of
    finished files that never existed; a longer run is what actually happened."""
    ahead = WorkAhead()
    ahead.run_of("probe", left=100, done_already=0, busy=True)

    more = ahead.run_of("probe", left=250, done_already=0, busy=True)

    assert more.total == 250
    assert more.done == 0, "arriving work was reported as finished work"


def test_a_kind_that_is_not_busy_has_no_run_and_is_measured_fresh_next_time() -> None:
    """Otherwise the next run is measured against a batch that finished hours ago."""
    ahead = WorkAhead()
    ahead.run_of("probe", left=1000, done_already=0, busy=True)

    idle = ahead.run_of("probe", left=40, done_already=0, busy=False)
    assert (idle.total, idle.done, idle.left) == (40, 0, 40)

    started = ahead.run_of("probe", left=40, done_already=0, busy=True)
    assert started.total == 40, "a new run was measured against the last one"


# --- the denominator the bar never had ---------------------------------------------------------


def test_a_finished_library_draws_a_full_bar_and_not_an_empty_one() -> None:
    """A BAR AT 0 PERCENT WHENEVER NOTHING IS OUTSTANDING would be every moment somebody reads the
    Activity screen on a library with nothing to do. With the size of the RUN as the denominator, a
    kind that is not busy has no run, so a library that was entirely finished would divide nought
    by nought. Counted from the library, done over total is defined at rest."""
    ahead = WorkAhead()

    at_rest = ahead.run_of("thumbnail", left=0, done_already=0, busy=False, wanted=100_000)

    assert (at_rest.total, at_rest.done, at_rest.left) == (100_000, 100_000, 0)


def test_the_library_total_beats_the_run_even_while_work_is_moving() -> None:
    """The run's size is the size of a batch; the library's is what the pass is measured against.
    A screen opened halfway through says how much of the LIBRARY has the work, which is the
    question, rather than how much of whatever batch happened to be in flight."""
    ahead = WorkAhead()
    ahead.run_of("face_scan", left=500, done_already=0, busy=True, wanted=100_000)

    moving = ahead.run_of("face_scan", left=91_000, done_already=4, busy=True, wanted=100_000)

    assert (moving.total, moving.done) == (100_000, 9_000)


def test_a_total_that_has_fallen_behind_never_reports_more_done_than_there_was() -> None:
    """A total read a moment before a folder arrived would otherwise be smaller than what is left,
    and the bar would run past its end."""
    ahead = WorkAhead()

    behind = ahead.run_of("thumbnail", left=200, done_already=0, busy=True, wanted=100)

    assert (behind.total, behind.done) == (200, 0)


async def test_the_total_and_the_remainder_are_asked_on_one_cycle() -> None:
    """Two answers read a second apart describe two moments, and a total smaller than its own
    remainder draws a bar past its end. They share the cache the counters already had."""
    asked: list[str] = []

    async def left() -> int:
        asked.append("left")
        return 7

    async def wanted() -> int:
        asked.append("wanted")
        return 70

    ahead = WorkAhead()
    ahead.register("thumbnail", left)
    ahead.register_total("thumbnail", wanted)

    assert await ahead.waiting() == {"thumbnail": 7}
    assert await ahead.wanted() == {"thumbnail": 70}

    assert asked == ["left", "wanted"], "the second question asked again instead of sharing"


async def test_what_is_waiting_is_split_by_media_kind_where_a_counter_can_say() -> None:
    """What the estimate prices apart. A kind with nothing waiting is dropped, and a counter that
    fails is left out rather than read as a library of nothing but the other kinds."""

    async def split() -> dict[str, int]:
        return {"video": 40, "image": 0, "gif": 3}

    async def broken() -> dict[str, int]:
        raise RuntimeError("the table is not there")

    ahead = WorkAhead()
    ahead.register_kinds("preview", split)
    ahead.register_kinds("sprite", broken)

    assert await ahead.waiting_by_kind() == {"preview": {"video": 40, "gif": 3}}
