# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for kernel.ids."""

from __future__ import annotations

import contextlib
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

import pytest
from hypothesis import given
from hypothesis import strategies as st

from sift.kernel import ids as ids_module
from sift.kernel.ids import floor_at, is_id, new_id, timestamp_ms

pytestmark = pytest.mark.unit


def test_ids_are_unique() -> None:
    ids = [new_id() for _ in range(10_000)]
    assert len(set(ids)) == len(ids)


def test_ids_sort_by_creation_order() -> None:
    """Ids sort by creation, so paging by primary key needs no secondary index."""
    first = new_id()
    time.sleep(0.002)
    second = new_id()
    assert first < second


def test_ids_minted_in_the_same_millisecond_still_sort() -> None:
    """Ids minted in one millisecond still sort in minting order: the random half counts up."""
    ids = [new_id() for _ in range(1000)]
    assert ids == sorted(ids)


@pytest.fixture
def fresh_clock() -> Iterator[None]:
    """Put the generator's floor back afterwards: module state left wound would make the suite
    order-dependent."""
    before = (ids_module._last_ms, ids_module._last_random)
    ids_module._last_ms, ids_module._last_random = -1, 0
    try:
        yield
    finally:
        ids_module._last_ms, ids_module._last_random = before


def test_a_clock_that_steps_backwards_does_not_reorder_the_ids(
    monkeypatch: pytest.MonkeyPatch, fresh_clock: None
) -> None:
    """A clock stepping backwards while Sift runs never mints an id below the one before it, or
    every "newest first" listing would put the newest row last."""
    ticks = iter([1000.000, 1000.001, 1000.002, 0.999999e3, 0.999998e3, 1000.004])
    last = [1000.004]

    def stepping() -> float:
        with contextlib.suppress(StopIteration):
            last[0] = next(ticks)
        return last[0]

    monkeypatch.setattr(time, "time", stepping)
    made = [new_id() for _ in range(6)]

    assert made == sorted(made), (
        "an ID minted after the clock went back sorts below its predecessor"
    )
    # Without repeating an id.
    assert len(set(made)) == len(made)


def test_the_id_clock_catches_up_rather_than_running_ahead(
    monkeypatch: pytest.MonkeyPatch, fresh_clock: None
) -> None:
    """The floor is held, not advanced: once real time passes it, ids carry that time again."""
    monkeypatch.setattr(time, "time", lambda: 500.0)
    new_id()
    monkeypatch.setattr(time, "time", lambda: 400.0)
    held = new_id()
    monkeypatch.setattr(time, "time", lambda: 900.0)
    later = new_id()

    assert timestamp_ms(held) == 500_000, "the floor should be the last millisecond used"
    assert timestamp_ms(later) == 900_000, "real time should be used again once it passes the floor"


def test_concurrent_minting_does_not_collide() -> None:
    with ThreadPoolExecutor(max_workers=16) as pool:
        ids = list(pool.map(lambda _: new_id(), range(5000)))
    assert len(set(ids)) == len(ids)


def test_ids_are_not_guessable() -> None:
    """Consecutive ids do not differ predictably: they appear in URLs shared with people."""
    a, b = new_id(), new_id()
    random_a, random_b = a[10:], b[10:]
    differing = sum(1 for x, y in zip(random_a, random_b, strict=True) if x != y)
    # One millisecond increments the low bits; the two random halves must still differ.
    assert random_a != random_b
    assert differing >= 1


def test_the_timestamp_round_trips() -> None:
    """The time an id carries is the time it was minted, bracketed by BOTH clock readings: after a
    clock correction the floor legitimately sits above the later reading."""
    before = new_id()
    first = int(time.time() * 1000)
    value = new_id()
    second = int(time.time() * 1000)

    floor = timestamp_ms(before)
    minted = timestamp_ms(value)

    assert minted >= floor, "an ID never carries a time below the one before it"
    # Never beyond the clock unless the floor holds it up.
    assert minted <= max(first, second, floor)
    # With no correction in play it is the clock.
    if floor <= min(first, second):
        assert min(first, second) <= minted <= max(first, second)


def test_the_shape_check_accepts_what_we_mint() -> None:
    assert is_id(new_id())


@pytest.mark.parametrize(
    "value",
    [
        "",
        "too-short",
        "01J5X2T7ABCDEFGHIJKLMNOPQRSTUV",
        "01J5X2T7ABCDEFGHIJKLMNOPQI",  # I is not in the alphabet
        "01J5X2T7ABCDEFGHIJKLMNOPQL",  # nor L
        "../../../etc/passwd",
        "'; DROP TABLE assets;--",
    ],
)
def test_the_shape_check_rejects_junk(value: str) -> None:
    assert not is_id(value)


@given(st.text())
def test_the_shape_check_never_raises(value: str) -> None:
    """It runs on request parameters, so it never raises."""
    is_id(value)


def test_reading_the_time_off_something_that_is_not_an_id_is_refused(
    fresh_clock: None,
) -> None:
    """A malformed id read from a request is refused, not decoded into some number."""
    for junk in ("", "not-an-id", new_id()[:-1], new_id().replace("0", "U", 1)):
        with pytest.raises(ValueError, match="not a valid identifier"):
            timestamp_ms(junk)


def test_a_million_ids_in_one_millisecond_steps_the_clock_rather_than_repeating(
    monkeypatch: pytest.MonkeyPatch, fresh_clock: None
) -> None:
    """The random half overflowing inside one millisecond steps the floor a millisecond into the
    future: wrapping would sort below the last id, fresh randomness could repeat one. Reached by
    starting the counter one short of the ceiling."""
    monkeypatch.setattr(time, "time", lambda: 1_700_000_000.0)
    ids_module._last_ms = 1_700_000_000_000
    ids_module._last_random = (1 << 80) - 1

    first = new_id()
    second = new_id()

    assert first < second, "the ID after an overflow sorted below the one before it"
    assert is_id(second)
    assert timestamp_ms(second) == 1_700_000_000_001
    assert ids_module._last_random < (1 << 80)


def test_the_floor_of_a_moment_sorts_below_every_id_minted_from_it_and_above_those_before() -> None:
    """A moment's floor is at or below every id of that millisecond and later and above every
    earlier one; before the epoch it floors at the lowest id."""
    minted = new_id()
    at = timestamp_ms(minted)

    assert floor_at(at) <= minted < floor_at(at + 1)
    assert floor_at(at - 1) < floor_at(at)
    assert is_id(floor_at(at)) and timestamp_ms(floor_at(at)) == at
    assert floor_at(-5) == floor_at(0) == "0" * 26
