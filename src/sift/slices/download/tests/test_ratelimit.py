# SPDX-License-Identifier: AGPL-3.0-or-later
"""The per-host pacer. Time is injected, so a wait is asserted rather than waited out.

The bucket lets a small burst through and then paces; a "too many requests" answer blocks every task
aimed at the host together and widens its interval; a run of clean requests relaxes it back.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from sift.slices.download.sources.ratelimit import (
    LIMITER,
    RATE_LIMIT_CODES,
    HostRateLimiter,
    Pacer,
    note_too_many_requests,
    parse_retry_after,
    wait_out_backoff,
)


class _Clock:
    """A hand-advanced monotonic clock."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def _sleep_into(clock: _Clock, recorded: list[float]) -> Callable[[float], Awaitable[None]]:
    async def sleep(seconds: float) -> None:
        recorded.append(seconds)
        clock.t += seconds  # a slept second is a second of refill

    return sleep


def test_parse_retry_after_honours_only_the_plain_seconds_form() -> None:
    assert parse_retry_after("30") == 30.0
    assert parse_retry_after("  5 ") == 5.0
    assert parse_retry_after(None) is None
    assert parse_retry_after("") is None
    assert parse_retry_after("Wed, 21 Oct 2026 07:28:00 GMT") is None


def test_a_retry_after_the_remote_host_made_up_is_absent_rather_than_fatal() -> None:
    """This header is written by somebody else's server, so it is parsed rather than trusted.

    The superscript is the one that matters: `"\u00b2".isdigit()` is True while `float("\u00b2")`
    raises, so a host answering with one could crash a download job. A negative or infinite wait is
    dropped for the same reason: neither is a delay anybody can serve.
    """
    assert parse_retry_after("\u00b2") is None
    assert parse_retry_after("\u00b9\u00b2\u00b3") is None
    assert parse_retry_after("-5") is None
    assert parse_retry_after("inf") is None
    assert parse_retry_after("nan") is None
    # A digit `float()` genuinely reads is still honoured: this refuses what cannot be converted,
    # not everything unfamiliar.
    assert parse_retry_after("\u0663") == 3.0


async def test_a_burst_goes_through_at_once_then_pacing_takes_over() -> None:
    clock = _Clock()
    waits: list[float] = []
    lim = HostRateLimiter(
        intervals={"h": 2.0}, bursts={"h": 3}, clock=clock, sleep=_sleep_into(clock, waits)
    )
    for _ in range(3):
        await lim.acquire("h")
    assert waits == []  # the whole burst is free

    await lim.acquire("h")
    assert waits[-1] == pytest.approx(2.0)  # bucket empty: pace at the refill interval


async def test_an_unpaced_host_never_waits() -> None:
    clock = _Clock()
    waits: list[float] = []
    lim = HostRateLimiter(clock=clock, sleep=_sleep_into(clock, waits))  # default interval 0
    for _ in range(10):
        await lim.acquire("anything.example")
    assert waits == []


async def test_a_too_many_requests_answer_blocks_every_task_until_the_cooldown() -> None:
    clock = _Clock()
    waits: list[float] = []
    lim = HostRateLimiter(
        intervals={"h": 1.0},
        bursts={"h": 5},
        clock=clock,
        sleep=_sleep_into(clock, waits),
        jitter_frac=0.0,
    )
    lim.note_retry_after("h", 10.0)  # even with tokens to spare, the backoff dominates

    await lim.acquire("h")
    assert waits[-1] == pytest.approx(10.0)


async def test_jitter_moves_the_pacing_wait_around_its_mean() -> None:
    clock = _Clock()
    waits: list[float] = []
    lim = HostRateLimiter(
        intervals={"h": 1.0},
        bursts={"h": 1},
        clock=clock,
        sleep=_sleep_into(clock, waits),
        jitter_frac=0.5,
        rand=lambda: 1.0,  # the top of the jitter range
    )
    await lim.acquire("h")  # full bucket: free
    await lim.acquire("h")  # empty: 1.0 * (1 + 0.5 * (2*1 - 1)) = 1.5
    assert waits[-1] == pytest.approx(1.5)


async def test_jitter_over_one_cannot_produce_a_negative_wait() -> None:
    clock = _Clock()
    waits: list[float] = []
    lim = HostRateLimiter(
        intervals={"h": 1.0},
        bursts={"h": 1},
        clock=clock,
        sleep=_sleep_into(clock, waits),
        jitter_frac=2.0,
        rand=lambda: 0.0,  # 1 + 2*(0-1) = -1 -> a negative wait, clamped to 0
    )
    await lim.acquire("h")  # free
    await lim.acquire("h")  # clamped to 0 -> no sleep recorded
    assert waits == []


def test_widen_grows_a_step_each_complaint_up_to_the_cap() -> None:
    lim = HostRateLimiter(intervals={"h": 1.0}, max_interval_factor=3.0)
    lim.note_retry_after("h", 0.0)
    assert lim._current["h"] == pytest.approx(1.5)
    lim.note_retry_after("h", 0.0)
    assert lim._current["h"] == pytest.approx(2.25)
    lim.note_retry_after("h", 0.0)
    assert lim._current["h"] == pytest.approx(3.0)  # capped at base * factor
    lim.note_retry_after("h", 0.0)
    assert lim._current["h"] == pytest.approx(3.0)


def test_an_unpaced_host_is_never_widened() -> None:
    lim = HostRateLimiter()  # no configured interval for "h" -> base 0
    lim.note_retry_after("h", 0.0)
    assert "h" not in lim._current  # nothing to widen; the shared backoff rode it out


def test_the_interval_relaxes_only_after_the_clean_streak_and_only_above_base() -> None:
    lim = HostRateLimiter(intervals={"h": 1.0}, decay_after=2)
    lim._current["h"] = 2.0  # as if widened earlier
    lim._note_clean("h")  # streak 1 < 2: no change
    assert lim._current["h"] == pytest.approx(2.0)
    lim._note_clean("h")  # streak hits 2: relax one step, max(1.0, 2.0*0.8) = 1.6
    assert lim._current["h"] == pytest.approx(1.6)

    at_base = HostRateLimiter(intervals={"h": 1.0}, decay_after=1)
    at_base._current["h"] = 1.0  # already at base: a clean streak does not push it below
    at_base._note_clean("h")
    assert at_base._current["h"] == pytest.approx(1.0)

    unseen = HostRateLimiter(intervals={"h": 1.0}, decay_after=1)
    unseen._note_clean("h")  # never widened (cur is None): nothing to relax
    assert "h" not in unseen._current


def test_set_jitter_clamps_a_negative_to_zero() -> None:
    lim = HostRateLimiter(jitter_frac=0.3)
    lim.set_jitter(-1.0)
    assert lim._jitter_frac == 0.0


def test_the_process_limiter_paces_the_free_middleman_apis() -> None:
    assert LIMITER._base_interval("www.tikwm.com") == pytest.approx(1.0)
    assert LIMITER._base_interval("instasave.website") == pytest.approx(4.5)
    assert LIMITER._burst("instasave.website") == 3
    assert LIMITER._base_interval("some-unpaced-host.example") == 0.0


def test_a_declared_pace_is_recorded_and_a_wider_one_replaces_it() -> None:
    """For a caller whose pace is configuration rather than a constant. A stash-box carries its own
    requests-per-minute, so the number is not known when the limiter is built."""
    lim = HostRateLimiter()

    lim.pace("box.example", 2.0)
    assert lim._base_interval("box.example") == pytest.approx(2.0)

    lim.pace("box.example", 5.0)
    assert lim._base_interval("box.example") == pytest.approx(5.0)


def test_re_declaring_a_pace_does_not_undo_a_backoff_a_refusal_asked_for() -> None:
    """The half that is easy to get wrong. A box that has just asked Sift to slow down would
    otherwise be back at full speed on the next request that happened to mention its pace."""
    lim = HostRateLimiter()
    lim.pace("box.example", 1.0)
    lim._widen("box.example")
    backed_off = lim._current["box.example"]
    assert backed_off > 1.0

    lim.pace("box.example", 1.0)

    assert lim._current["box.example"] == pytest.approx(backed_off)


def test_a_pace_wider_than_the_backoff_takes_over_from_it() -> None:
    """The other direction, and it is the reason this is a comparison rather than a skip: a base
    slower than the backoff makes the backoff meaningless, so it is dropped rather than kept."""
    lim = HostRateLimiter()
    lim.pace("box.example", 1.0)
    lim._widen("box.example")

    lim.pace("box.example", 60.0)

    assert "box.example" not in lim._current
    assert lim._base_interval("box.example") == pytest.approx(60.0)


# --- the Downloads settings' waits --------------------------------------------------------------


async def test_the_pacer_spaces_each_request_from_the_start_of_the_one_before() -> None:
    clock = _Clock()
    slept: list[float] = []
    pacer = Pacer(0.5, clock=clock, sleep=_sleep_into(clock, slept))

    await pacer.wait()  # the first goes at once
    clock.t += 0.2  # the request took a fifth of a second
    await pacer.wait()
    await pacer.wait()

    assert slept == pytest.approx([0.3, 0.5])


async def test_a_pace_of_zero_never_waits() -> None:
    clock = _Clock()
    slept: list[float] = []
    pacer = Pacer(0.0, clock=clock, sleep=_sleep_into(clock, slept))
    for _ in range(5):
        await pacer.wait()
    assert slept == []


async def test_a_refusal_holds_its_host_for_every_downloader_and_only_that_host() -> None:
    clock = _Clock()
    slept: list[float] = []
    limiter = HostRateLimiter(clock=clock, sleep=_sleep_into(clock, slept))
    from sift.slices.download.sources.tuning import POLICY

    held = note_too_many_requests("https://Bunkr.example/a/b", POLICY, limiter=limiter)
    await wait_out_backoff("https://other.example/x", limiter=limiter)
    assert slept == []  # another host is not held
    await wait_out_backoff("https://bunkr.example/next", limiter=limiter)
    assert held == POLICY.pacing.wait_after_too_many_requests
    assert slept == [held]


def test_the_rate_limit_codes_are_the_ones_the_failure_reader_writes() -> None:
    """Both spellings a refusal can be read as, taken from the reader rather than retyped."""
    from sift.slices.download.sources import failures

    status = failures.explain_status("https://cdn.example/x", 429).code
    worded = failures.classify("https://example.com/x", "ERROR: rate limit reached, slow down")
    assert worded is not None
    assert {status, worded.code} == RATE_LIMIT_CODES


async def test_a_request_slower_than_the_pace_is_followed_at_once() -> None:
    clock = _Clock()
    slept: list[float] = []
    pacer = Pacer(0.5, clock=clock, sleep=_sleep_into(clock, slept))
    await pacer.wait()
    clock.t += 0.7  # the request took longer than the pace asks between starts
    await pacer.wait()
    assert slept == []
