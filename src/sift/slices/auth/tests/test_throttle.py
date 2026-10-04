# SPDX-License-Identifier: AGPL-3.0-or-later
"""The lockout that turns online guessing into a wait, driven by a controllable clock."""

from __future__ import annotations

import pytest

from sift.slices.auth.throttle import Tarpit, Throttle

pytestmark = pytest.mark.unit


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_a_key_locks_after_the_limit_and_releases_after_the_window() -> None:
    clock = Clock()
    throttle = Throttle(max_failures=3, lockout_seconds=60, clock=clock)

    assert not throttle.locked("kate")
    throttle.record_failure("kate")
    throttle.record_failure("kate")
    assert not throttle.locked("kate")  # under the limit
    throttle.record_failure("kate")
    assert throttle.locked("kate")  # the third failure locks it

    clock.now += 59
    assert throttle.locked("kate")
    clock.now += 2
    assert not throttle.locked("kate")  # the window has passed


def test_a_lapsed_lockout_starts_the_count_over() -> None:
    """A lapsed lockout starts the count over."""
    clock = Clock()
    throttle = Throttle(max_failures=3, lockout_seconds=60, clock=clock)

    for _ in range(3):
        throttle.record_failure("kate")
    assert throttle.locked("kate")

    clock.now += 61  # the window passes
    assert not throttle.locked("kate")

    throttle.record_failure("kate")
    assert not throttle.locked("kate")  # one mistake after the lapse does not re-lock
    throttle.record_failure("kate")
    assert not throttle.locked("kate")
    throttle.record_failure("kate")
    assert throttle.locked("kate")  # three again, counted fresh, re-locks


def test_a_success_clears_the_count() -> None:
    clock = Clock()
    throttle = Throttle(max_failures=3, lockout_seconds=60, clock=clock)
    throttle.record_failure("kate")
    throttle.record_failure("kate")
    throttle.record_success("kate")
    throttle.record_failure("kate")
    throttle.record_failure("kate")
    assert not throttle.locked("kate")  # the earlier two no longer count


def test_keys_are_independent() -> None:
    throttle = Throttle(max_failures=1, lockout_seconds=60)
    throttle.record_failure("kate")
    assert throttle.locked("kate")
    assert not throttle.locked("sam")


# --- the tarpit: an escalating delay that never refuses -------------------------------------


def _tarpit(
    clock: Clock, *, grace: int = 2, cap: float = 8.0, forget: float = 900, max_keys: int = 4096
) -> Tarpit:
    return Tarpit(
        grace=grace,
        base_delay_seconds=1.0,
        max_delay_seconds=cap,
        forget_after_seconds=forget,
        max_keys=max_keys,
        clock=clock,
    )


def test_the_tarpit_is_silent_within_the_grace() -> None:
    """A few free attempts, so ordinary mistyping costs nothing."""
    tarpit = _tarpit(Clock(), grace=3)
    for _ in range(3):
        assert tarpit.delay("kate") == 0.0
        tarpit.record_failure("kate")
    assert tarpit.delay("kate") == 0.0  # three failures, three of grace: still nothing


def test_the_tarpit_doubles_past_the_grace_and_caps() -> None:
    """Past the grace the delay doubles with each failure, up to a ceiling it never exceeds."""
    tarpit = _tarpit(Clock(), grace=2, cap=8.0)
    for _ in range(2):  # spend the grace
        tarpit.record_failure("kate")
    expected = [1.0, 2.0, 4.0, 8.0, 8.0]
    seen = []
    for _ in expected:
        tarpit.record_failure("kate")
        seen.append(tarpit.delay("kate"))
    assert seen == expected  # doubles, then holds at the cap


def test_reserve_counts_and_escalates_on_every_call() -> None:
    """`reserve` counts as it hands back the delay, so concurrent guesses still escalate."""
    tarpit = _tarpit(Clock(), grace=2, cap=8.0)
    seen = [tarpit.reserve("kate") for _ in range(7)]
    assert seen == [0.0, 0.0, 1.0, 2.0, 4.0, 8.0, 8.0]  # grace spent, then doubling to the cap
    # A read never moves the count: delay reports the standing wait without raising it again.
    assert tarpit.delay("kate") == 8.0
    # A success still clears the whole run, however it was counted.
    tarpit.record_success("kate")
    assert tarpit.delay("kate") == 0.0


def test_a_success_clears_the_tarpit() -> None:
    """A correct answer ends the run, so the next attempt waits for nothing."""
    tarpit = _tarpit(Clock())
    for _ in range(5):
        tarpit.record_failure("kate")
    assert tarpit.delay("kate") > 0
    tarpit.record_success("kate")
    assert tarpit.delay("kate") == 0.0


def test_a_quiet_period_forgets_the_tarpit() -> None:
    """After a long enough silence the run is forgotten, so an old fumble does not slow a later,
    honest login."""
    clock = Clock()
    tarpit = _tarpit(clock, grace=1, forget=60)
    for _ in range(4):
        tarpit.record_failure("kate")
    assert tarpit.delay("kate") > 0

    clock.now += 61
    assert tarpit.delay("kate") == 0.0  # the silence forgot it
    tarpit.record_failure("kate")  # and a new failure starts the climb over, from the grace
    assert tarpit.delay("kate") == 0.0


def test_tarpit_keys_are_independent() -> None:
    tarpit = _tarpit(Clock(), grace=0)
    tarpit.record_failure("kate")
    assert tarpit.delay("kate") > 0
    assert tarpit.delay("sam") == 0.0


def test_the_map_is_capped_against_a_flood_of_distinct_names() -> None:
    """The tarpit is keyed by the submitted name, so a spray of never-seen names would grow it
    without bound. The cap holds it: past the ceiling the oldest are evicted."""
    tarpit = _tarpit(Clock(), grace=0, max_keys=8)
    for i in range(200):
        tarpit.record_failure(f"user{i}")
    assert len(tarpit._by_key) <= 8  # bounded, not one entry per name tried


def test_forgotten_runs_are_evicted_first_when_the_cap_is_reached() -> None:
    """Runs a quiet period has forgotten impose no delay, so they are dropped before any live one:
    a name under active guessing survives a flood of stale entries."""
    clock = Clock()
    tarpit = _tarpit(clock, grace=0, forget=60, max_keys=4)
    for i in range(4):
        tarpit.record_failure(f"old{i}")
    clock.now += 61  # every run so far is now forgotten
    tarpit.record_failure("fresh")  # over the cap: the forgotten ones are reaped first
    assert "fresh" in tarpit._by_key
    assert len(tarpit._by_key) <= 4


def test_a_run_the_tarpit_has_forgotten_starts_again_from_nothing() -> None:
    """A quiet period wipes the count rather than pausing it."""
    clock = Clock()
    tarpit = _tarpit(clock, grace=1, forget=60)
    for _ in range(4):
        tarpit.reserve("kate")
    assert tarpit.reserve("kate") > 0

    clock.now += 61

    assert tarpit.reserve("kate") == 0
