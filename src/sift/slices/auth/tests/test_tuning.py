# SPDX-License-Identifier: AGPL-3.0-or-later
"""How long a session lives, read back out of whatever is stored.

## Why this file exists at all

`session_seconds_from` reads the number the settings store holds, and the route that calls it only
ever hands it a value the store has just decoded, which is the one input that cannot go wrong, so
it needs a test of its own. Its own docstring names the failure it is built to
avoid: a value it could not read must fall back to the shipped answer and never to zero, because a
zero here signs every user out on the next request.
"""

from __future__ import annotations

import pytest

from sift.slices.auth.tuning import DEFAULT_SESSION_DAYS, session_seconds_from

_A_DAY = 24 * 60 * 60


class TestHowLongASessionLives:
    def test_a_stored_number_is_days(self) -> None:
        assert session_seconds_from(3) == 3 * _A_DAY

    def test_a_numeric_string_is_read_rather_than_refused(self) -> None:
        """A value written by an older version, or by hand, still means what it says."""
        assert session_seconds_from("3") == 3 * _A_DAY

    @pytest.mark.parametrize("stored", [None, "", "a while", object(), [7], {"days": 7}])
    def test_anything_unreadable_falls_back_to_the_shipped_answer(self, stored: object) -> None:
        """The direction that matters. The alternative is not a long session, it is no session:
        `int()` of something unreadable would raise where the caller has nothing to say about it,
        and a zero would end every session on the next request."""
        assert session_seconds_from(stored) == DEFAULT_SESSION_DAYS * _A_DAY

    @pytest.mark.parametrize("stored", [0, -1, -365])
    def test_zero_and_below_still_leave_one_whole_day(self, stored: int) -> None:
        """Not the default, and not zero. Somebody who asked for the shortest session gets the
        shortest one that can be served rather than being handed the longest by a rollback."""
        assert session_seconds_from(stored) == _A_DAY

    def test_the_default_is_a_week(self) -> None:
        """Pinned because it is the answer every unreadable value falls back to; changing it is a
        decision about how often somebody signs in, not a tidy-up."""
        assert DEFAULT_SESSION_DAYS == 7
