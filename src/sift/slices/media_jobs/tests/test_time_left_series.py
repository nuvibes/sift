# SPDX-License-Identifier: AGPL-3.0-or-later
"""A recorded day of a large library on a network share, its read and its four passes all running,
replayed through the ledger and Activity's rows. Nothing finished in it, so each time said is
scored against the finish the next hours' real work points to, each family done with its slowest
type and a pass no sooner than the read."""

from __future__ import annotations

import pytest

from sift.kernel.db import Database
from sift.slices.media_jobs.tests import series

pytestmark = pytest.mark.integration

HOUR = 3600.0


def _held(said: list[series.Said], day: series.Day, over: float | None) -> dict[str, int | None]:
    return {
        family: None if one["held"] is None else round(one["held"])
        for family, one in series.scored(day, said, over).items()
    }


def test_what_the_screen_said_that_day_held_the_read_and_missed_the_passes() -> None:
    day = series.load("library-day.csv.gz")
    said = series.recorded(day)
    assert _held(said, day, HOUR) == {
        "scan": 100,
        "generate": 33,
        "fingerprint": 4,
        "identify": 2,
        "semantic": 75,
    }
    # Every family said one time or none: 4.5 hours of a folder count said nothing at all.
    assert {round(one["said"] or 0) for one in series.scored(day, said, None).values()} == {55}


async def test_each_family_says_its_own_time_and_a_floor_while_the_walk_counts(
    temp_db: Database,
) -> None:
    day = series.load("library-day.csv.gz")
    said = await series.play(day, temp_db)
    scored = series.scored(day, said, HOUR)
    assert {family: round(one["said"] or 0) for family, one in scored.items()} == {
        "scan": 98,
        "generate": 98,
        "fingerprint": 97,
        "identify": 98,
        "semantic": 98,
    }
    assert _held(said, day, HOUR) == {
        "scan": 99,
        "generate": 98,
        "fingerprint": 100,
        "identify": 53,
        "semantic": 96,
    }
    assert _held(said, day, 3 * HOUR) == {
        "scan": 100,
        "generate": 99,
        "fingerprint": 100,
        "identify": 62,
        "semantic": 98,
    }
    # The passes with a window each say their own: on that day every one said the same.
    windows: dict[float, dict[str, tuple[int | None, int | None]]] = {}
    for one in said:
        if one.family != "scan" and one.slow and not one.at_least:
            windows.setdefault(one.at, {})[one.family] = (one.quick, one.slow)
    three = [
        {mine["fingerprint"], mine["identify"], mine["semantic"]}
        for mine in windows.values()
        if {"fingerprint", "identify", "semantic"} <= set(mine)
    ]
    assert len(three) == 334 and all(len(one) == 3 for one in three)
