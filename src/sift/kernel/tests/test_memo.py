# SPDX-License-Identifier: AGPL-3.0-or-later
"""The answer memo: kept under the mark it was computed at, bounded by count."""

from __future__ import annotations

import asyncio

import pytest

from sift.kernel.memo import MarkedMemo, PacedAnswer

pytestmark = pytest.mark.unit


class _Counting:
    """Something to compute, which remembers how often it was asked."""

    def __init__(self) -> None:
        self.asked = 0

    async def __call__(self) -> int:
        self.asked += 1
        return self.asked


async def test_the_same_key_under_the_same_mark_is_answered_from_memory() -> None:
    memo: MarkedMemo[int] = MarkedMemo()
    compute = _Counting()

    first = await memo.get("facets", "m1", compute)
    second = await memo.get("facets", "m1", compute)

    assert (first, second) == (1, 1)
    assert compute.asked == 1


async def test_a_moved_mark_is_a_different_library_and_is_computed_again() -> None:
    memo: MarkedMemo[int] = MarkedMemo()
    compute = _Counting()

    await memo.get("facets", "m1", compute)
    again = await memo.get("facets", "m2", compute)

    assert again == 2


async def test_with_no_mark_nothing_is_kept() -> None:
    """Nothing is announcing, so nothing could ever say the answer went stale."""
    memo: MarkedMemo[int] = MarkedMemo()
    compute = _Counting()

    await memo.get("facets", None, compute)
    await memo.get("facets", None, compute)

    assert compute.asked == 2


async def test_the_oldest_answer_goes_when_more_are_kept_than_the_bound() -> None:
    """Bounded by count, and the count is what was least recently asked for."""
    memo: MarkedMemo[int] = MarkedMemo(kept=2)
    compute = _Counting()

    await memo.get("a", "m1", compute)
    await memo.get("b", "m1", compute)
    await memo.get("a", "m1", compute)  # asked again, so `b` is now the oldest
    await memo.get("c", "m1", compute)

    assert compute.asked == 3
    assert await memo.get("a", "m1", compute) == 1, "the answer asked for again was kept"
    assert await memo.get("b", "m1", compute) == 4, "the one nobody asked for again was not"


async def test_forgetting_throws_every_answer_away() -> None:
    memo: MarkedMemo[int] = MarkedMemo()
    compute = _Counting()
    await memo.get("facets", "m1", compute)

    memo.forget()

    assert await memo.get("facets", "m1", compute) == 2


class _Clock:
    """A clock a test moves by hand."""

    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


async def test_a_paced_answer_is_kept_for_its_time_and_then_counted_again() -> None:
    clock, compute = _Clock(), _Counting()
    paced = PacedAnswer(compute, fresh_for=5.0, clock=clock)

    assert await paced.get() == 1
    clock.now += 4.9
    assert await paced.get() == 1, "kept within its time"
    clock.now += 0.2
    assert await paced.get() == 2, "counted again after it"


async def test_a_paced_answer_that_cost_more_is_kept_ten_times_as_long() -> None:
    clock = _Clock()
    asked = 0

    async def slow() -> int:
        nonlocal asked
        asked += 1
        clock.now += 2.0
        return asked

    paced = PacedAnswer(slow, fresh_for=5.0, clock=clock)
    await paced.get()
    clock.now += 19.0

    assert await paced.get() == 1
    clock.now += 2.0
    assert await paced.get() == 2


async def test_askers_at_the_same_moment_share_one_count() -> None:
    asked = 0

    async def compute() -> int:
        nonlocal asked
        asked += 1
        await asyncio.sleep(0)
        return asked

    paced = PacedAnswer(compute)

    answers = await asyncio.gather(paced.get(), paced.get(), paced.get())

    assert list(answers) == [1, 1, 1]
    assert asked == 1
