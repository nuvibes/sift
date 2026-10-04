# SPDX-License-Identifier: AGPL-3.0-or-later
"""A run of starter pictures in History, worded when shown: Sift did it, from which boxes, for whom.

A stored title such as "Added 300 starter pictures from FansDB and StashDB for 90 people" names
nobody doing it, on a person's page and in the feed.
"""

from __future__ import annotations

import json
from typing import Any, cast

from sift.kernel.workbench import DOER, Named, Recorded
from sift.slices.faces.jobs import StarterRecords


def _worded(payload: object, title: str = "", page: tuple[str, str] | None = None):  # type: ignore[no-untyped-def]
    recorded = Recorded(
        id="01R",
        queue="starters",
        payload=json.dumps(payload),
        title=title,
        detail="",
        decided_at=0,
        page=page,
    )
    return StarterRecords.worded(StarterRecords.__new__(StarterRecords), recorded)


def test_sift_added_them_from_the_boxes_for_the_people_counted() -> None:
    many = _worded(
        {"references": {"01P": ["a", "b"], "01Q": ["c"]}, "boxes": ["FansDB", "StashDB"]}
    )
    assert many is not None
    assert many.said == (
        DOER,
        " added 3 starter pictures",
        " from FansDB and StashDB",
        " for ",
        "2 people",
    )
    one = _worded({"references": {"01P": ["a"], "01Q": []}, "boxes": ["StashDB"]})
    assert one is not None
    assert one.said == (
        DOER,
        " added 1 starter picture",
        " from StashDB",
        " for ",
        Named(kind="person", id="01P"),
    )


def test_an_older_run_reads_its_boxes_from_its_title_and_nothing_is_guessed() -> None:
    older = _worded(
        {"references": {"01P": ["a"], "01Q": ["b"]}},
        title="Added 300 starter pictures from FansDB and StashDB for 90 people",
    )
    assert older is not None
    assert older.said[2] == " from FansDB and StashDB"
    unread = _worded({"references": {"01P": ["a"]}}, title="Something else")
    assert unread is not None and " from " not in "".join(
        p for p in unread.said if isinstance(p, str)
    )
    assert _worded({"references": {}}) is None
    assert _worded("not a record") is None


def test_on_one_persons_page_the_line_is_the_part_about_them() -> None:
    """A person's page does not draw "Sift added 300 starter pictures from FansDB and StashDB for
    90 people". On her page it is her part, named by the reader as "them"."""
    run = {"references": {"01P": ["a", "b", "c", "d"], "01Q": ["e"]}, "boxes": ["FansDB"]}

    hers = _worded(run, page=("person", "01P"))

    assert hers is not None
    assert hers.said == (
        DOER,
        " added 4 starter pictures",
        " from FansDB",
        " for ",
        Named(kind="person", id="01P"),
    )
    # A run from several boxes kept no box per person, so her part names none rather than a guess.
    several = _worded({**run, "boxes": ["FansDB", "StashDB"]}, page=("person", "01P"))
    assert several is not None and " from " not in "".join(
        p for p in several.said if isinstance(p, str)
    )
    # Somebody else's page, or a page the run did not add to, keeps the run's own line.
    elsewhere = _worded(run, page=("person", "01Z"))
    assert elsewhere is not None and "2 people" in elsewhere.said


def test_a_run_this_build_cannot_read_keeps_its_stored_title() -> None:
    recorded = Recorded(
        id="01R", queue="starters", payload="not json", title="", detail="", decided_at=0
    )
    assert StarterRecords.worded(StarterRecords.__new__(StarterRecords), recorded) is None


class _Retiring:
    """The feature, as the Undo reaches it: which starters it was asked to retire."""

    def __init__(self) -> None:
        self.retired: list[list[str]] = []

    async def retire_starters(self, ids: list[str]) -> None:
        self.retired.append(ids)


async def test_undo_retires_every_starter_the_run_filed() -> None:
    """Retired, as a "no" retires them: the rows stay, so the same pictures are not filed
    again by the next link or press."""
    feature = _Retiring()
    records = StarterRecords(cast(Any, feature))
    payload = json.dumps({"references": {"01P": ["a", "b"], "01Q": ["c"]}})

    assert await records.reverse(cast(Any, None), "01R", payload)
    assert feature.retired == [["a", "b", "c"]]
    assert await records.pictures_of(cast(Any, None), payload) == ()


async def test_an_unreadable_run_puts_nothing_back_rather_than_failing() -> None:
    feature = _Retiring()
    records = StarterRecords(cast(Any, feature))
    assert not await records.reverse(cast(Any, None), "01R", "not json")
    assert not await records.reverse(cast(Any, None), "01R", "[1, 2]")
    assert feature.retired == []
