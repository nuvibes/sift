# SPDX-License-Identifier: AGPL-3.0-or-later
"""A settled disagreement's line on the record, worded when shown from what it recorded.

The stored titles kept their words for good: "Kept your answer for Ada Byron" about a person
called something else (the title named the box's offer), and a detail printing the field's key
with raw values: "breast_type: 'FAKE' became 'NATURAL'". Each receipt shape is worded here, the
two older ones from the fixed formats their writer used.
"""

from __future__ import annotations

import json

from sift.kernel.workbench import DOER, Named, Piece, Recorded, Worded
from sift.slices.stash_boxes.reconcile import ReconcileReceipts

PERSON = "01PERSON"
STASH, FANS = "01BOXSTASH", "01BOXFANS"


def _receipt(payload: dict[str, object], *, title: str = "", detail: str = "") -> Recorded:
    return Recorded(
        id="01R",
        queue="reconcile",
        payload=json.dumps(payload),
        title=title,
        detail=detail,
        decided_at=0,
        user_id="01USER",
    )


def _words(worded: Worded | None) -> str:
    """The pieces as a reader would say them, with every thing shown as `[kind:id]`."""
    assert worded is not None

    def one(piece: Piece) -> str:
        if piece is DOER:
            return "You"
        if isinstance(piece, Named):
            return f"[{piece.kind}:{piece.id or piece.recorded}]"
        return str(piece)

    return "".join(one(piece) for piece in worded.said)


def _worded(recorded: Recorded) -> Worded | None:
    return ReconcileReceipts.worded(ReconcileReceipts.__new__(ReconcileReceipts), recorded)


def test_keeping_yours_says_the_field_both_values_the_box_and_the_person() -> None:
    said = _worded(
        _receipt(
            {
                "subject": "person",
                "local_id": PERSON,
                "key": "breast_type",
                "box_id": STASH,
                "mine": "NATURAL",
                "theirs": "FAKE",
            }
        )
    )
    assert _words(said) == (
        f"You kept your breast type for [person:{PERSON}], Natural, over [box:{STASH}]'s Fake"
    )


def test_a_kept_name_written_before_the_payload_carried_values_reads_them_back() -> None:
    said = _worded(
        _receipt(
            {"subject": "person", "local_id": PERSON, "key": "name", "box_id": FANS},
            title="Kept your answer for Ada Byron",
            detail="name: 'Esme Wrenfield' was kept over FansDB's 'Ada Byron'",
        )
    )
    assert said is not None
    assert _words(said) == f"You kept the name [person:{PERSON}] over [box:{FANS}]'s Ada Byron"
    kept = said.said[2]
    assert isinstance(kept, Named) and kept.recorded == "Esme Wrenfield" and kept.as_recorded


def test_a_box_answer_chosen_before_the_payload_carried_it_reads_the_old_detail() -> None:
    said = _worded(
        _receipt(
            {"subject": "person", "local_id": PERSON, "key": "breast_type", "mine": "FAKE"},
            title="Took FansDB's answer for Ada Byron",
            detail="breast_type: 'FAKE' became 'NATURAL'",
        )
    )
    assert _words(said) == (
        f"You chose [box:FansDB]'s breast type for [person:{PERSON}], Natural, over your Fake"
    )


def test_a_length_says_its_unit_and_a_value_with_the_joining_words_still_reads() -> None:
    said = _worded(
        _receipt(
            {"subject": "person", "local_id": PERSON, "key": "height_cm", "mine": 177},
            title="Took FansDB's answer for Ada Byron",
            detail="height_cm: 177 became 157",
        )
    )
    assert _words(said).endswith(", 157 cm, over your 177 cm")


def test_choosing_theirs_names_every_other_box_it_discarded() -> None:
    said = _worded(
        _receipt(
            {
                "subject": "person",
                "local_id": PERSON,
                "key": "breast_type",
                "mine": "NATURAL",
                "theirs": "FAKE",
                "from_box_id": STASH,
                "set_aside": [{"box_id": FANS, "was": None}],
            }
        )
    )
    assert said is not None
    assert said.more == (Named(kind="box", id=FANS), "'s answer was discarded too")


def test_a_value_too_long_for_a_line_is_left_to_the_record() -> None:
    said = _worded(
        _receipt(
            {
                "subject": "tag",
                "local_id": "01TAG",
                "key": "description",
                "mine": "x" * 80,
                "theirs": "y",
                "from_box_id": FANS,
            }
        )
    )
    assert _words(said) == f"You chose [box:{FANS}]'s description for [tag:01TAG]"


def test_an_old_row_whose_detail_does_not_read_keeps_its_stored_title() -> None:
    assert (
        _worded(
            _receipt(
                {"subject": "person", "local_id": PERSON, "key": "name", "box_id": FANS},
                title="Kept your answer for Ada Byron",
                detail="something a later build wrote",
            )
        )
        is None
    )
    assert _worded(_receipt({"subject": "person"})) is None


def test_a_row_about_a_kind_this_build_does_not_have_keeps_its_stored_title() -> None:
    assert _worded(_receipt({"subject": "gallery", "local_id": PERSON, "key": "name"})) is None


def test_keeping_yours_over_a_value_too_long_for_a_line_names_the_field_and_the_box() -> None:
    said = _worded(
        _receipt(
            {
                "subject": "tag",
                "local_id": "01TAG",
                "key": "description",
                "box_id": STASH,
                "mine": "x" * 80,
                "theirs": "y",
            }
        )
    )
    assert _words(said) == f"You kept your description for [tag:01TAG] over [box:{STASH}]'s"


def test_a_row_that_never_recorded_your_value_keeps_its_stored_title() -> None:
    assert _worded(_receipt({"subject": "person", "local_id": PERSON, "key": "birth_date"})) is None


def test_an_old_row_whose_values_are_not_literals_keeps_its_stored_title() -> None:
    """The old detail printed each value with `repr`; one that does not read back as a literal is a
    row that recorded nothing more, not a value to guess at."""
    for detail in ("birth_date: never quoted became also never quoted", "birth_date: 1 became"):
        said = _worded(
            _receipt(
                {"subject": "person", "local_id": PERSON, "key": "birth_date", "mine": "x"},
                title="Took FansDB's answer for Ada Byron",
                detail=detail,
            )
        )
        assert said is None, detail


def test_an_old_kept_row_with_no_box_before_the_value_keeps_its_stored_title() -> None:
    said = _worded(
        _receipt(
            {"subject": "person", "local_id": PERSON, "key": "name", "box_id": FANS},
            title="Kept your answer for Ada Byron",
            detail="name: 'Esme Wrenfield' was kept over 'Ada Byron'",
        )
    )
    assert said is None


def test_three_boxes_set_aside_are_listed_as_a_sentence() -> None:
    said = _worded(
        _receipt(
            {
                "subject": "person",
                "local_id": PERSON,
                "key": "breast_type",
                "mine": "NATURAL",
                "theirs": "FAKE",
                "from_box_id": STASH,
                "set_aside": [
                    {"box_id": "01A", "was": None},
                    "not an entry",
                    {"box_id": "", "was": None},
                    {"box_id": "01B", "was": ["x", "y"]},
                    {"box_id": "01C"},
                ],
            }
        )
    )
    assert said is not None
    assert said.more == (
        Named(kind="box", id="01A"),
        ", ",
        Named(kind="box", id="01B"),
        " and ",
        Named(kind="box", id="01C"),
        "'s answers were discarded too",
    ), "an entry that does not read is skipped rather than guessed at"


def test_a_list_is_said_as_its_items_and_an_empty_one_as_nothing() -> None:
    said = _worded(
        _receipt(
            {
                "subject": "person",
                "local_id": PERSON,
                "key": "aliases",
                "mine": [],
                "theirs": ["Ada Byron", "A. B."],
                "from_box_id": FANS,
            }
        )
    )
    assert _words(said).endswith(", Ada Byron, A. B., over your nothing")


def test_a_value_the_record_does_not_read_out_is_said_as_it_stands() -> None:
    said = _worded(
        _receipt(
            {
                "subject": "person",
                "local_id": PERSON,
                "key": "pmv_creator",
                "mine": False,
                "theirs": True,
                "from_box_id": FANS,
            }
        )
    )
    assert _words(said).endswith(", True, over your False")
