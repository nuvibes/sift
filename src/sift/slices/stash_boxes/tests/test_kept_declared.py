# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every settle receipt says which answers it kept, under the kernel's key, new ones and old.

The History threads draw a kept answer and the receipt of the same press as one line
(`history.one_line_per_kept`), matched on what the receipt declares (`vocabulary.RECEIPT_KEPT`). A
receipt that declared nothing would be drawn beside its kept line as before, so the rule for what
one kept is held here for every generation of receipt, and v15 is held to giving it to the old ones.
"""

from __future__ import annotations

import pytest

from sift.kernel.vocabulary import KEPT_BY_TAKING_ANOTHER, KEPT_BY_THE_PRESS
from sift.slices.stash_boxes.kept import kept_answers

pytestmark = pytest.mark.anyio


def _one(box_id: str, how: str, subject: str = "person") -> dict[str, str]:
    return {"subject": subject, "local_id": "P", "box_id": box_id, "key": "height_cm", "how": how}


def test_a_keep_kept_the_box_it_refused() -> None:
    # The older generation carried the box and nothing else; the current one, both values.
    for recorded in (
        {"subject": "person", "local_id": "P", "key": "height_cm", "box_id": "B"},
        {"subject": "person", "local_id": "P", "key": "height_cm", "box_id": "B", "mine": 1},
    ):
        assert kept_answers(recorded) == [_one("B", KEPT_BY_THE_PRESS)]


def test_a_take_kept_every_box_it_set_aside_and_an_old_take_kept_nothing() -> None:
    took = {
        "subject": "person",
        "local_id": "P",
        "key": "height_cm",
        "mine": 157,
        "from_box_id": "A",
        "set_aside": [{"box_id": "B", "was": None}, {"box_id": "C", "was": ["1", "2"]}],
    }
    assert kept_answers(took) == [
        _one("B", KEPT_BY_TAKING_ANOTHER),
        _one("C", KEPT_BY_TAKING_ANOTHER),
    ]
    assert kept_answers({"subject": "person", "local_id": "P", "key": "height_cm", "mine": 1}) == []


def test_the_old_word_for_a_site_is_read_as_the_word_the_kept_rows_carry() -> None:
    recorded = {"subject": "platform", "local_id": "P", "key": "height_cm", "box_id": "B"}
    assert kept_answers(recorded) == [_one("B", KEPT_BY_THE_PRESS, subject="site")]


@pytest.mark.parametrize(
    "recorded",
    [
        {"local_id": "P", "key": "height_cm", "box_id": "B"},
        {"subject": "person", "key": "height_cm", "box_id": "B"},
        {"subject": "person", "local_id": "P", "box_id": "B"},
    ],
    ids=["no subject", "no record", "no field"],
)
def test_a_receipt_that_does_not_name_its_record_and_field_kept_nothing(
    recorded: dict[str, object],
) -> None:
    assert kept_answers(recorded) == []
