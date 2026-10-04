# SPDX-License-Identifier: AGPL-3.0-or-later
"""The faces decisions' lines, worded when shown: "discarded", never "set aside"; a match names the
person and the file rather than saying "here" on a screen that is no file."""

from __future__ import annotations

import json
from dataclasses import replace

from sift.kernel.vocabulary import Subject
from sift.kernel.workbench import DOER, Named, Recorded
from sift.slices.faces.worded import identified_said, ignored_said


def _receipt(payload: dict[str, object], title: str, *subjects: Subject) -> Recorded:
    return Recorded(
        id="01R",
        queue="q",
        payload=json.dumps(payload),
        title=title,
        detail="",
        decided_at=0,
        subjects=subjects,
    )


def test_a_group_set_aside_reads_as_discarded_with_its_size() -> None:
    said = ignored_said(_receipt({"pile_id": "01PILE"}, "A group of 90 faces set aside"))
    assert said is not None
    assert said.said == (
        DOER,
        " discarded ",
        Named(kind="pile", id="01PILE", recorded="a group of 90 faces"),
    )


def test_a_group_brought_back_is_restored_and_an_unknown_title_keeps_its_words() -> None:
    back = ignored_said(_receipt({"pile_id": "01PILE"}, "A group of 1 face put back"))
    assert back is not None and back.said[1] == " restored "
    # A title that already says restored reads the same.
    now = ignored_said(_receipt({"pile_id": "01PILE"}, "A group of 1 face restored"))
    assert now is not None and now.said[1] == " restored "
    assert ignored_said(_receipt({"pile_id": "01PILE"}, "Something else")) is None


def test_one_match_names_the_person_the_file_and_how_sure() -> None:
    said = identified_said(
        _receipt(
            {"person_id": "01P", "track_ids": ["01T"]},
            "Sift named Esme Wrenfield here, 75% sure",
            Subject(kind="asset", id="01A", name="image.png"),
        )
    )
    assert said is not None
    assert said.said == (
        DOER,
        " recognized ",
        Named(kind="person", id="01P"),
        " in ",
        Named(kind="asset", id="01A", recorded="image.png"),
        ", 75% sure",
    )


def test_a_run_counts_the_faces_it_recorded_rather_than_the_title() -> None:
    said = identified_said(
        _receipt(
            {"person_id": "01P", "track_ids": ["a", "b", "c"]}, "Sift matched 344 more faces to X"
        )
    )
    assert said is not None and said.said[-1] == " in 3 more faces"
    agreed = identified_said(
        _receipt({"person_id": "01P", "track_ids": ["a"]}, "You agreed with 1 match for X")
    )
    assert agreed is not None and agreed.said[1] == " confirmed 1 face as "


def test_a_no_about_one_face_written_with_the_plural_verb_reads_singular() -> None:
    """A No about one face stored as "You said 1 face are not ...", with "They were" under it, is
    worded from the count, and the state from the payload."""
    said = identified_said(
        _receipt(
            {"person_id": "01P", "track_ids": ["01T"], "attribution": {"01T": "matched"}},
            "You said 1 face are not Esme Wrenfield",
        )
    )
    assert said is not None
    assert said.said == (DOER, " said 1 face is not ", Named(kind="person", id="01P"))
    first = said.more[0]
    assert isinstance(first, str)
    assert first.startswith("It was Recognized by Sift. Taking this back puts the name on it")
    # A No about several faces is worded from its payload too, its stored line kept under it.
    several = replace(
        _receipt(
            {"person_id": "01P", "track_ids": ["01T", "01U"]},
            "You said 2 faces are not Esme Wrenfield",
        ),
        detail="They were Recognized by Sift.",
    )
    worded = identified_said(several)
    assert worded is not None
    assert worded.said == (DOER, " said 2 faces are not ", Named(kind="person", id="01P"))
    assert worded.more == ("They were Recognized by Sift.",)


def test_a_no_written_since_the_verb_agreed_names_the_person_by_the_readers_word() -> None:
    """A person's own page does not say "You said 1 face is not <her name>" beside lines saying
    "them" because a No stored with the right verb keeps its stored title. Every No is worded, and
    the person is a `Named` the reader says as "them" on their page."""
    said = identified_said(
        _receipt(
            {"person_id": "01P", "track_ids": ["01T"]}, "You said 1 face is not Esme Wrenfield"
        )
    )
    assert said is not None
    assert said.said == (DOER, " said 1 face is not ", Named(kind="person", id="01P"))


def test_an_agreement_about_one_face_stored_with_those_appearance_reads_that_appearance() -> None:
    """ "leaves those appearance waiting" as the stored detail of an agreement about one face is
    read right without a migration; a detail about several faces keeps its words."""
    stored = (
        "Sift learned from 1 face of theirs. Taking this back leaves those appearance waiting "
        "under Needs your input, as they were, and removes the pictures. No file is touched and "
        "nothing is deleted."
    )
    one = replace(
        _receipt({"person_id": "01P", "track_ids": ["01T"]}, "You agreed with 1 face for X"),
        detail=stored,
    )
    said = identified_said(one)
    assert said is not None
    assert said.said == (DOER, " confirmed 1 face as ", Named(kind="person", id="01P"))
    assert said.more == (stored.replace("those appearance ", "that appearance "),)
    several = replace(
        _receipt(
            {"person_id": "01P", "track_ids": ["01T", "01U"]}, "You agreed with 2 faces for X"
        ),
        detail=stored.replace("appearance ", "appearances "),
    )
    assert identified_said(several) is None


def test_a_rematch_run_says_how_sure_from_the_least_to_the_most() -> None:
    said = identified_said(
        _receipt(
            {"person_id": "01P", "track_ids": ["a", "b"]},
            "Sift named 14 more faces as X, between 56% and 63% sure",
        )
    )
    assert said is not None
    assert said.said == (
        DOER,
        " recognized ",
        Named(kind="person", id="01P"),
        " in 2 more faces, 56% to 63% sure",
    )


def test_a_receipt_whose_payload_names_no_person_and_faces_keeps_its_stored_words() -> None:
    """Unreadable, not an object, or missing the person or the faces: nothing to word it from."""
    title = "Sift named Esme Wrenfield here, 75% sure"
    unreadable = replace(_receipt({}, title), payload="{not json")
    a_list = replace(_receipt({}, title), payload=json.dumps(["01P"]))
    assert identified_said(unreadable) is None
    assert identified_said(a_list) is None
    assert identified_said(_receipt({"person_id": "01P"}, title)) is None
    assert identified_said(_receipt({"person_id": "", "track_ids": ["a"]}, title)) is None
