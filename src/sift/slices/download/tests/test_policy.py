# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading the download preferences into the object a tool run is given.

The interesting cases are all the same case: a stored value that is not what it should be. A row can
outlive the code that wrote it (a restored backup, a setting whose bounds narrowed in an update),
and on this path the wrong answer is not a crash, it is a timeout of zero seconds or a bandwidth cap
of nothing, both of which the tools would honour exactly as asked.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from sift.slices.download.sources import policy
from sift.slices.download.sources.tuning import PACING, QUALITY_BEST, QUALITY_COMPATIBLE


def _reader(stored: dict[str, Any]) -> Callable[[str], Awaitable[Any]]:
    async def get_app(key: str) -> Any:
        return stored.get(key, policy.DEFAULTS[key])

    return get_app


@pytest.mark.anyio
async def test_the_defaults_read_back_as_the_chosen_values() -> None:
    read = await policy.read_policy(_reader({}))
    assert read.pacing.seconds_between_requests == PACING.seconds_between_requests
    assert read.pacing.retries == PACING.retries
    assert read.pacing.timeout_seconds == PACING.timeout_seconds
    assert read.pacing.bytes_per_second is None
    assert read.filters.at_least_bytes is None
    assert read.quality == QUALITY_COMPATIBLE
    assert read.verbose is False


@pytest.mark.anyio
async def test_a_stored_value_reaches_the_run() -> None:
    read = await policy.read_policy(
        _reader(
            {
                policy.PACE_MS_KEY: 2500,
                policy.RETRIES_KEY: 1,
                policy.TIMEOUT_KEY: 90,
                policy.BANDWIDTH_KEY: 500,
                policy.SKIP_LARGER_KEY: 2048,
                policy.SKIP_SMALLER_KEY: 3,
                policy.BACKOFF_KEY: 240,
                policy.QUALITY_KEY: QUALITY_BEST,
                policy.VERBOSE_KEY: True,
            }
        )
    )
    assert read.pacing.seconds_between_requests == 2.5
    assert read.pacing.retries == 1
    assert read.pacing.timeout_seconds == 90.0
    assert read.pacing.bytes_per_second == 500 * 1024
    assert read.filters.at_most_bytes == 2048 * 1024 * 1024
    assert read.filters.at_least_bytes == 3 * 1024 * 1024
    assert read.pacing.wait_after_too_many_requests == 240.0
    assert read.quality == QUALITY_BEST
    assert read.verbose is True


@pytest.mark.anyio
async def test_zero_means_no_limit_rather_than_a_limit_of_nothing() -> None:
    """The difference matters: a cap of zero is a number the tools would try to honour."""
    read = await policy.read_policy(
        _reader({policy.BANDWIDTH_KEY: 0, policy.SKIP_SMALLER_KEY: 0, policy.SKIP_LARGER_KEY: 0})
    )
    assert read.pacing.bytes_per_second is None
    assert read.filters.at_least_bytes is None
    assert read.filters.at_most_bytes is None


@pytest.mark.anyio
async def test_a_chosen_zero_for_the_wait_and_the_retries_is_honoured_not_replaced() -> None:
    """0 is a choice for these two (no pause, no retries), and the registry offers it, so it is
    not replaced by the default (`--sleep-requests 0.5 --retries 3`). Below zero is not a choice
    anybody could make on the screen, so that still falls back."""
    read = await policy.read_policy(_reader({policy.PACE_MS_KEY: 0, policy.RETRIES_KEY: 0}))
    assert read.pacing.seconds_between_requests == 0
    assert read.pacing.retries == 0
    below = await policy.read_policy(_reader({policy.PACE_MS_KEY: -5, policy.RETRIES_KEY: -1}))
    assert below.pacing.seconds_between_requests == PACING.seconds_between_requests
    assert below.pacing.retries == PACING.retries
    # And the two where zero cannot be meant still refuse it: a timeout of nothing.
    zero_timeout = await policy.read_policy(_reader({policy.TIMEOUT_KEY: 0}))
    assert zero_timeout.pacing.timeout_seconds == PACING.timeout_seconds


@pytest.mark.anyio
async def test_a_stored_value_that_is_not_a_number_falls_back_rather_than_becoming_zero() -> None:
    """The failure this prevents: a timeout of zero seconds, which fails every download instantly."""
    read = await policy.read_policy(
        _reader(
            {
                policy.TIMEOUT_KEY: "from an older version",
                policy.RETRIES_KEY: None,
                policy.PACE_MS_KEY: "",
            }
        )
    )
    assert read.pacing.timeout_seconds == PACING.timeout_seconds
    assert read.pacing.retries == PACING.retries
    assert read.pacing.seconds_between_requests == PACING.seconds_between_requests


@pytest.mark.anyio
async def test_a_quality_word_this_version_does_not_know_falls_back_to_todays_behaviour() -> None:
    read = await policy.read_policy(_reader({policy.QUALITY_KEY: "av1-only"}))
    assert read.quality == QUALITY_COMPATIBLE


@pytest.mark.anyio
async def test_verbose_is_only_true_for_a_real_true() -> None:
    """Not merely truthy: a stored string would otherwise turn logging on for every download."""
    for stored in ("yes", 1, "true", None):
        read = await policy.read_policy(_reader({policy.VERBOSE_KEY: stored}))
        assert read.verbose is False


#: The seven settings that reach every Site.
_EVERY_SITE = (
    policy.PACE_MS_KEY,
    policy.RETRIES_KEY,
    policy.TIMEOUT_KEY,
    policy.BACKOFF_KEY,
    policy.BANDWIDTH_KEY,
    policy.SKIP_SMALLER_KEY,
    policy.SKIP_LARGER_KEY,
)


def test_each_of_the_seven_says_on_its_row_that_it_applies_to_every_site() -> None:
    """The row is where somebody learns what a setting reaches. "Every Site" is the promise the
    gate in test_fetcher holds these seven to, so the words and the behavior cannot drift apart
    unseen."""
    import sift.slices.download  # noqa: F401 (registers the settings)
    from sift.kernel.settings_registry import get_registered

    for key in _EVERY_SITE:
        registered = get_registered(key)
        assert registered is not None, key
        assert "every Site" in registered.help, key
    pace = get_registered(policy.PACE_MS_KEY)
    assert pace is not None
    assert pace.maximum == policy.PACE_MS_MAX
