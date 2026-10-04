# SPDX-License-Identifier: AGPL-3.0-or-later
"""The saved-login killswitch: repeated auth failures stop a site's cookie, a success resets."""

from __future__ import annotations

import pytest

from sift.slices.download.sources import cookie_health
from sift.slices.download.sources.cookie_health import _Health


@pytest.fixture(autouse=True)
def _clean_switches() -> None:
    cookie_health.reset()  # the state is module-global; do not let a trip leak between tests


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("www.reddit.com", "reddit"),
        ("old.reddit.com", "reddit"),
        ("v.redd.it", "reddit"),  # an alias folds into the same site
        ("redd.it", "reddit"),
        (".reddit.com", "reddit"),  # a leading dot is tolerated
        ("REDDIT.COM", "reddit"),  # case-insensitive
        ("example.com", None),  # a site Sift has no record of
        ("notreddit.com", None),  # a suffix that is not on a dot boundary
        (None, None),
        ("", None),
    ],
)
def test_the_site_a_hostname_belongs_to(host: str | None, expected: str | None) -> None:
    """A hostname's site is read from the catalog."""
    assert cookie_health.site_key(host) == expected


def test_a_fresh_health_starts_clean() -> None:
    # A new switch starts untripped with no streak, as a fresh process does.
    health = _Health()
    assert health.fails == 0
    assert health.tripped is False


def test_an_unwatched_host_may_always_send_cookies() -> None:
    assert cookie_health.should_use_cookies("example.com") is True
    # A flood of failures for an unwatched host trips nothing.
    for _ in range(10):
        cookie_health.record_auth_failure("example.com")
    assert cookie_health.should_use_cookies("example.com") is True


def test_three_consecutive_failures_trip_the_switch() -> None:
    assert cookie_health.should_use_cookies("www.reddit.com") is True
    cookie_health.record_auth_failure("www.reddit.com")
    cookie_health.record_auth_failure("old.reddit.com")  # an alias counts toward the same site
    assert cookie_health.should_use_cookies("www.reddit.com") is True  # not yet
    cookie_health.record_auth_failure("v.redd.it")
    assert cookie_health.should_use_cookies("www.reddit.com") is False  # tripped, all aliases


def test_a_success_before_the_third_failure_clears_the_streak() -> None:
    cookie_health.record_auth_failure("reddit.com")
    cookie_health.record_auth_failure("reddit.com")
    cookie_health.record_ok("reddit.com")  # the cookie still works, so the streak resets
    cookie_health.record_auth_failure("reddit.com")
    cookie_health.record_auth_failure("reddit.com")
    assert cookie_health.should_use_cookies("reddit.com") is True  # never reached three in a row


def test_a_tripped_switch_stays_tripped_and_a_late_success_does_not_lift_it() -> None:
    for _ in range(3):
        cookie_health.record_auth_failure("reddit.com")
    assert cookie_health.should_use_cookies("reddit.com") is False
    cookie_health.record_ok("reddit.com")  # a lone success does not un-trip a dead-cookie switch
    assert cookie_health.should_use_cookies("reddit.com") is False


def test_record_ok_and_failure_ignore_an_unwatched_host() -> None:
    cookie_health.record_ok("example.com")  # no state to touch: must not raise
    cookie_health.record_auth_failure("example.com")
    assert cookie_health.should_use_cookies("example.com") is True


def test_reset_clears_a_tripped_switch() -> None:
    for _ in range(3):
        cookie_health.record_auth_failure("reddit.com")
    assert cookie_health.should_use_cookies("reddit.com") is False
    cookie_health.reset()
    assert cookie_health.should_use_cookies("reddit.com") is True


def test_a_site_sift_does_not_recognize_has_no_health_to_report() -> None:
    """A site Sift does not recognize reports no health rather than claiming "fine"."""
    assert cookie_health.status_for("example.com") is None
    assert cookie_health.status_for(None) is None


def test_a_recognized_site_reports_its_health_both_ways() -> None:
    assert cookie_health.status_for("www.reddit.com") == cookie_health.SAVED
    for _ in range(3):
        cookie_health.record_auth_failure("www.reddit.com")
    assert cookie_health.status_for("v.redd.it") == cookie_health.NEEDS_COOKIES


def test_a_site_sift_does_not_recognize_clears_nothing_and_does_not_raise() -> None:
    cookie_health.clear_for_site("Something Made Up")


def test_a_jar_whose_last_date_is_near_is_shown_as_ending_soon() -> None:
    """Soon enough to act on, and not yet expired: the one state that asks somebody to do
    something before anything has failed."""
    now = 1_700_000_000
    assert cookie_health.state_of(None, now + 60, now) == cookie_health.STATE_ENDING_SOON
    assert cookie_health.state_of(None, now, now) == cookie_health.STATE_EXPIRED
    far = now + cookie_health.ENDING_SOON_SECONDS + 1
    assert cookie_health.state_of(None, far, now) == cookie_health.STATE_SAVED
