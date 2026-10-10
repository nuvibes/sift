# SPDX-License-Identifier: AGPL-3.0-or-later
"""Recorded imports replayed through the ledger and Activity's rows, each row's time left scored
against when its work really ended: in its words, within a minute, by third of its life."""

from __future__ import annotations

from typing import Any

import pytest

from sift.kernel.db import Database
from sift.slices.media_jobs.tests import replay

pytestmark = pytest.mark.integration

#: Each recording, the one played before it, the pool's workers, and its read bound by the pool.
#: Per row: words holding the end in all and by third, minutes with a time, minutes stopped, word
#: changes an hour, the numbers holding the end, and the slow end's move a minute, in percent.
WHOLE = {
    "import-without-hold.csv.gz": (
        "import-with-hold.csv.gz",
        {
            "scan": (99, [96, 100, 100], 89, 0, 31, 88, 2),
            "generate": (100, [100, 100, 100], 90, 0, 25, 65, 6),
            "fingerprint": (100, [100, 100, 100], 90, 0, 21, 57, 12),
        },
    ),
    # Recorded under the family hold, which no longer exists: the passes' first third waited for
    # the walk's end, which nothing now makes them do.
    "import-with-hold.csv.gz": (
        "import-stall-and-eco.csv.gz",
        {
            "scan": (100, [None, 100, 100], 50, 0, 55, 8, 44),
            "generate": (89, [37, 100, 100], 82, 0, 39, 62, 8),
            "fingerprint": (82, [5, 100, 100], 83, 0, 55, 66, 0),
        },
    ),
    "import-stall-and-eco.csv.gz": (
        "import-without-hold.csv.gz",
        {
            "scan": (99, [97, 100, 100], 88, 0, 42, 90, 6),
            "generate": (97, [97, 98, 98], 88, 0, 45, 45, 0),
            "fingerprint": (98, [97, 98, 100], 90, 0, 45, 40, 0),
        },
    ),
}


def _whole(said: dict[str, Any]) -> tuple[Any, ...]:
    keys = ("words", "thirds", "said", "stalled", "changes_an_hour", "numbers", "move")
    return tuple(
        [None if one is None else round(one) for one in said[key]]
        if key == "thirds"
        else round(said[key])
        for key in keys
    )


@pytest.mark.parametrize("name", sorted(WHOLE))
async def test_a_recorded_import_is_said_as_it_really_went(temp_db: Database, name: str) -> None:
    before, expected = WHOLE[name]
    record = replay.load(name)
    said = await replay.replay(record, temp_db, workers=12, after=replay.load(before))
    scored = replay.scored(record, said)
    assert {family: _whole(one) for family, one in scored.items()} == expected


async def test_an_hour_of_a_share_paced_import_holds_its_words_and_its_files(
    temp_db: Database,
) -> None:
    record = replay.load("share-hour.csv.gz")
    said = await replay.replay(
        record,
        temp_db,
        workers=23,
        pool_bound=lambda _at: False,
        after=replay.load("import-without-hold.csv.gz"),
    )
    held = {n: round(replay.horizon(record, said, "scan", n) or 0) for n in (100, 300, 500)}
    assert held == {100: 90, 300: 93, 500: 94}
    steady = {family: replay.steadiness(said, family) for family in ("scan", "generate")}
    assert {family: round(one["changes_an_hour"] or 0) for family, one in steady.items()} == {
        "scan": 11,
        "generate": 13,
    }
    # The first two minutes say "Measuring." until the read has a pace of its own.
    assert all(round(one["said"] or 0) == 97 and one["move"] == 0 for one in steady.values())
