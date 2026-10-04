# SPDX-License-Identifier: AGPL-3.0-or-later
"""A shoot's decisions, worded when shown: a Photo Set is created, never "made"."""

from __future__ import annotations

import json

from sift.kernel.workbench import DOER, Named, Recorded
from sift.slices.shoots.queue import ShootQueue


def _receipt(payload: dict[str, object]) -> Recorded:
    return Recorded(
        id="01R", queue="shoots", payload=json.dumps(payload), title="", detail="", decided_at=0
    )


def _worded(recorded: Recorded):  # type: ignore[no-untyped-def]
    return ShootQueue.worded(ShootQueue.__new__(ShootQueue), recorded)


def test_a_shoot_made_into_a_set_is_created_with_its_name_and_size() -> None:
    said = _worded(
        _receipt(
            {"kind": "shoot", "photo_set_id": "01S", "name": "Cassia Lynn", "assets": ["a"] * 4}
        )
    )
    assert said is not None
    assert said.said == (
        DOER,
        " created ",
        Named(kind="photo_set", id="01S", recorded="Cassia Lynn", kind_said=True),
        " from 4 photos",
    )


def test_a_naming_names_the_person_and_one_that_recorded_nothing_keeps_its_title() -> None:
    said = _worded(_receipt({"kind": "named", "person_id": "01P", "assets": ["a"]}))
    assert said is not None
    assert said.said == (
        DOER,
        " named ",
        Named(kind="person", id="01P"),
        " in 1 photo of one shoot",
    )
    assert _worded(_receipt({"kind": "shoot"})) is None


def test_a_set_whose_record_cannot_be_read_is_still_created_under_no_recorded_name() -> None:
    """An older decision wrote no name, and one that cannot be read names none: the set is still
    said, by what it is called now."""
    recorded = Recorded(
        id="01R",
        queue="shoots",
        payload=json.dumps({"kind": "shoot", "photo_set_id": "01S", "assets": ["a"]}),
        title="",
        detail="",
        decided_at=0,
    )
    said = _worded(recorded)
    assert said is not None
    assert said.said[2] == Named(kind="photo_set", id="01S", recorded=None, kind_said=True)

    from sift.slices.shoots.queue import _called

    assert _called("{not json") is None


def test_a_record_of_a_kind_this_card_never_wrote_keeps_its_title() -> None:
    assert _worded(_receipt({"kind": "merged", "photo_set_id": "01S", "assets": ["a"]})) is None
