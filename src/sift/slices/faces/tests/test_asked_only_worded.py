# SPDX-License-Identifier: AGPL-3.0-or-later
"""The asked-only reconcile's record, worded when shown: Sift did it, and whose names came off.

An older stored title named nobody doing it and counted the People nowhere, though the payload
keeps each one; they are worded from the payload when shown.
"""

from __future__ import annotations

import json
from typing import Any

from sift.kernel.workbench import DOER, Named, Recorded
from sift.slices.faces.jobs import AskedOnlyRecords


def _worded(payload: object):  # type: ignore[no-untyped-def]
    recorded = Recorded(
        id="01R", queue="asked-only", payload=json.dumps(payload), title="", detail="", decided_at=0
    )
    return AskedOnlyRecords().worded(recorded)


def test_one_person_is_named_and_many_are_counted() -> None:
    one = _worded({"files": 3, "people": {"01P": 3}})
    assert one is not None
    assert one.said == (
        DOER,
        " took ",
        Named(kind="person", id="01P"),
        " off 3 files where it had only asked you about them",
    )
    many = _worded({"files": 2000, "people": {"01P": 10, "01Q": 1990}})
    assert many is not None
    assert many.said == (
        DOER,
        " took ",
        "2 people",
        " off 2,000 files where it had only asked you about them",
    )
    assert _worded({"files": 0, "people": {}}) is None


def test_a_record_this_build_cannot_read_keeps_its_stored_title() -> None:
    """Not JSON, or JSON that is not a record: nothing to word, so the stored title is shown."""
    recorded = Recorded(
        id="01R", queue="asked-only", payload="not json", title="", detail="", decided_at=0
    )
    assert AskedOnlyRecords().worded(recorded) is None
    assert _worded(["files", 3]) is None


async def test_the_record_draws_no_file_and_offers_nothing_to_take_back() -> None:
    """Putting a name back on a file Sift only asked about would be the fault again, so the
    record is final: no still to draw and no Undo that would put the names back."""
    records = AskedOnlyRecords()
    viewer: Any = None
    assert await records.pictures_of(viewer, "{}") == ()
    assert await records.reverse(viewer, "01R", '{"files": 3, "people": {"01P": 3}}') is False
