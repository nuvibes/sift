# SPDX-License-Identifier: AGPL-3.0-or-later
"""The file-name pass's decisions, worded when shown: never "own name", "made" or "number"."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from sift.kernel.vocabulary import VIA_FILENAME, Subject
from sift.kernel.workbench import DOER, Named, Recorded
from sift.slices.suggestions.worded import filenames_said, folders_said


def _receipt(
    payload: dict[str, object], title: str = "", *, run: int = 1, **more: object
) -> Recorded:
    return Recorded(
        id="01R",
        queue="filenames",
        payload=json.dumps(payload),
        title=title,
        detail="",
        decided_at=0,
        run=run,
        **more,  # type: ignore[arg-type]
    )


FILED: dict[str, object] = {"kind": "filed", "username_id": "01U", "assets": ["01A"]}
OLD_TITLE = "Filed under quillmoss on Instagram from the file's own name"


def test_a_filing_names_the_file_the_username_the_site_and_how() -> None:
    said = filenames_said(
        _receipt(FILED, OLD_TITLE, subjects=(Subject(kind="asset", id="01A", name="a.jpg"),))
    )
    assert said is not None
    assert said.said == (
        DOER,
        " filed ",
        Named(kind="asset", id="01A", recorded="a.jpg"),
        " under ",
        Named(kind="username", id="01U", recorded="quillmoss"),
        " on Instagram",
        " from its file name",
    )


def test_a_no_on_a_whole_username_says_who_took_how_many_off_which_username() -> None:
    said = filenames_said(
        _receipt(
            {
                "kind": "declined",
                "username_id": "01U",
                "name": "quillmoss",
                "site": "Instagram",
                "files": [["01A", "filename", 0, None], ["01B", "filename", 0, None]],
                "refused": ["01A", "01B"],
            }
        )
    )
    assert said is not None
    assert said.said == (
        DOER,
        " removed 2 files from ",
        Named(kind="username", id="01U", recorded="quillmoss"),
        " on Instagram",
    )


def test_a_no_on_a_username_naming_no_file_is_left_to_its_stored_words() -> None:
    """A record that does not say which username or which files has no line to word."""
    for held in ({"username_id": "01U", "files": []}, {"files": [["01A", "filename", 0, None]]}):
        assert filenames_said(_receipt({"kind": "declined", **held})) is None


def test_a_folded_run_is_worded_as_the_run() -> None:
    said = filenames_said(_receipt(FILED, OLD_TITLE, run=4200, actor_id=VIA_FILENAME))
    assert said is not None
    assert said.said[1] == " filed 4,200 files under "
    assert said.said[-1] == " from their file names"


def test_a_post_set_is_created_and_an_id_is_an_id() -> None:
    made = filenames_said(
        _receipt(
            {"kind": "post_set", "photo_set_id": "01S", "assets": ["a", "b"]},
            subjects=(Subject(kind="photo_set", id="01S", name="quillmoss"),),
        )
    )
    assert made is not None
    assert made.said == (
        DOER,
        " created ",
        Named(kind="photo_set", id="01S", recorded="quillmoss", kind_said=True),
        " from 2 photos posted together",
    )
    numbered = filenames_said(
        _receipt(
            {
                "kind": "numbered",
                "platform": "Instagram",
                "number": "123",
                "agreed": 6,
                "username_id": "01U",
                "name": "quillmoss",
            }
        )
    )
    assert numbered is not None
    assert numbered.said[1] == " matched Instagram ID 123 to "
    assert filenames_said(_receipt({"kind": "folder"})) is None


def test_a_filing_that_carried_an_id_says_which() -> None:
    said = filenames_said(
        _receipt({**FILED, "number": "10000000001"}, OLD_TITLE, actor_id=VIA_FILENAME)
    )
    assert said is not None
    assert said.said[-2:] == (" from its file name", ", which carries ID 10000000001")


def test_a_receipt_that_recorded_too_little_is_not_worded_at_all() -> None:
    """Said with a piece missing, a receipt would read as a different act: a filing of nothing,
    a Photo Set of no photos. It is left to its stored title instead."""
    payloads: list[dict[str, object]] = [
        {"kind": "filed", "assets": ["01A"]},
        {"kind": "filed", "username_id": "01U"},
        {"kind": "post_set", "photo_set_id": "01S", "assets": []},
        {"kind": "numbered", "site": "Instagram", "number": "123", "username_id": "01U"},
    ]
    for payload in payloads:
        assert filenames_said(_receipt(payload, OLD_TITLE)) is None, payload


def test_a_receipt_whose_payload_is_not_an_object_is_not_worded() -> None:
    for payload in ("not json at all", "[1, 2]"):
        recorded = Recorded(
            id="01R",
            queue="filenames",
            payload=payload,
            title=OLD_TITLE,
            detail="",
            decided_at=0,
        )
        assert filenames_said(recorded) is None, payload


def test_the_card_words_its_decisions_with_the_passs_own_words() -> None:
    """One wording for one decision, wherever it is drawn: the card hands its record to the same
    reader rather than keeping a second copy of the words."""
    from typing import Any, cast

    from sift.slices.suggestions.queue import FiledFromFilenamesQueue

    recorded = _receipt(FILED, OLD_TITLE, actor_id=VIA_FILENAME)
    queue = FiledFromFilenamesQueue(cast(Any, None))

    worded = queue.worded(recorded)

    assert worded is not None
    assert worded == filenames_said(recorded)


CONFIRMED: dict[str, object] = {
    "kind": "confirmed",
    "claim_id": "01C",
    "written": {
        "attributed": [["01A", "01P"], ["01B", "01P"]],
        "faces": [],
        "created_people": ["01P"],
        "alias": None,
        "username_linked": None,
        "remembered": [["01F", "01P"], ["01G", "01P"]],
        "filed": [],
        "namesakes": [["01Q", "01G"]],
    },
}


def test_a_yes_on_a_folder_is_a_sentence_naming_the_person_and_the_folder() -> None:
    """Never the receipt's title ("quillmoss \u2014 2 files"): the doer, the count, the person and
    the folder, both linked by the reader, and the namesake said under it."""
    said = folders_said(
        _receipt(
            CONFIRMED,
            "quillmoss \u2014 2 files",
            object_kind="person",
            object_id="01P",
            object_name="quillmoss",
        )
    )
    assert said is not None
    assert said.said == (
        DOER,
        " filed 2 files under ",
        Named(kind="person", id="01P", recorded="quillmoss"),
        " from the folder ",
        Named(kind="folder", id="01F"),
    )
    assert said.more == ("A new person added, 1 other folder with that name answered.",)


def test_a_yes_naming_a_site_or_a_no_keeps_its_stored_words() -> None:
    """A Site's Yes records no person, and a No records no filing: neither has a line to word."""
    assert folders_said(_receipt(CONFIRMED)) is None
    assert (
        folders_said(
            _receipt(
                {"kind": "ignored", "claim_id": "01C", "name_key": "quillmoss"},
                object_kind="person",
                object_id="01P",
            )
        )
        is None
    )


def _yes(written: Mapping[str, object], **more: Any) -> Recorded:
    return _receipt(
        {"kind": "confirmed", "claim_id": "01C", "written": dict(written)},
        object_kind="person",
        object_id="01P",
        object_name="quillmoss",
        **more,
    )


def test_what_else_a_yes_wrote_is_said_under_it_and_nothing_when_it_wrote_nothing_else() -> None:
    one_face = {
        "attributed": [["01A", "01P"]],
        "faces": ["01X"],
        "alias": "quill moss",
        "username_linked": "01U",
        "remembered": [["01F", "01P"]],
    }
    said = folders_said(_yes(one_face))
    assert said is not None
    assert said.said[1] == " filed 1 file under "
    assert said.more == ("1 face named, the spelling remembered, the username linked.",)
    many = {**one_face, "faces": ["01X", "01Y", "01Z"], "alias": None, "username_linked": None}
    many["namesakes"] = [["01Q", "01G"], ["01R", "01H"]]
    said = folders_said(_yes(many))
    assert said is not None
    assert said.more == ("3 faces named, 2 other folders with that name answered.",)
    bare = folders_said(_yes({"attributed": [], "remembered": [["01F", "01P"]]}))
    assert bare is not None and bare.more == ()


def test_a_yes_that_kept_no_standing_answer_names_the_folder_its_receipt_names() -> None:
    """A folder whose every file was held back keeps no answer, so the folder the line names is
    the one the receipt is about; a receipt naming no folder at all has no line to word."""
    odd: dict[str, object] = {"attributed": [], "remembered": [[""], "01F", []]}
    said = folders_said(_yes(odd, subjects=(Subject(kind="folder", id="01F", name="Shoots"),)))
    assert said is not None
    assert said.said[-1] == Named(kind="folder", id="01F")
    assert folders_said(_yes(odd)) is None
