# SPDX-License-Identifier: AGPL-3.0-or-later
"""The History line a file gained when the answers applied on a picture match alone were taken back.

The pass that took them back ran once and is retired; the lines it wrote are stored History and
still read in their own words.
"""

from __future__ import annotations

import json

from sift.kernel.access.sentences import (
    BY_HAND,
    PICTURE_ALONE,
    TOOK_BACK,
    Piece,
    event_said,
    feed_line,
    text_of,
)
from sift.kernel.vocabulary import Subject
from sift.kernel.workbench import DOER, Named, Recorded
from sift.slices.stash_boxes.queue import NAME, TaggerQueue


def test_the_file_says_which_boxes_were_taken_back_and_why() -> None:
    line = event_said(
        "removed",
        by="Sift",
        here="file",
        object_kind="box",
        object_id="b1",
        object_name="StashDB",
        payload={TOOK_BACK: PICTURE_ALONE, "boxes": ["StashDB", "FansDB"]},
    )

    assert text_of(line.pieces) == (
        "Sift took back what StashDB and FansDB said about this file, because each matched on"
        " the picture alone"
    )


def test_a_person_taking_an_answer_back_is_said_without_a_reason() -> None:
    line = event_said(
        "removed",
        by="You",
        here="file",
        object_kind="box",
        object_id="b1",
        object_name="StashDB",
        payload={TOOK_BACK: BY_HAND, "boxes": ["StashDB"]},
    )

    assert text_of(line.pieces) == "You took back what StashDB said about this file"


def test_the_feed_says_how_many_files_a_take_back_was_about() -> None:
    line = feed_line(
        "removed",
        by="Sift",
        subjects=[("asset", Piece("first.mp4")), ("asset", Piece("second.mp4"))],
        object_kind="box",
        object_piece=None,
        payload={TOOK_BACK: PICTURE_ALONE, "boxes": ["FansDB", "PMVStash"], "files": 370},
    )

    assert text_of(line.pieces) == (
        "Sift took back what FansDB and PMVStash said about 370 files, because each matched on"
        " the picture alone"
    )


# --- the receipt's own line, on a page ------------------------------------------------------------


def _receipt(payload: dict[str, object], *, page: tuple[str, str] | None) -> Recorded:
    return Recorded(
        id="r1",
        queue=NAME,
        payload=json.dumps(payload),
        title="Took back what FansDB and StashDB said about 3 files",
        detail="",
        decided_at=1,
        subjects=tuple(Subject(kind="asset", id=one) for one in ("f1", "f2", "f3")),
        page=page,
    )


def _worded(recorded: Recorded) -> tuple[object, ...]:
    worded = TaggerQueue.worded(TaggerQueue.__new__(TaggerQueue), recorded)
    assert worded is not None, "the receipt fell back to its stored title"
    return worded.said


def test_a_take_back_receipt_on_a_files_page_says_who_which_boxes_this_file_and_why() -> None:
    payload = {TOOK_BACK: PICTURE_ALONE, "boxes": ["FansDB", "StashDB"], "files": 3}

    said = _worded(_receipt(payload, page=("asset", "f2")))

    assert said == (
        DOER,
        " took back what ",
        "FansDB",
        " and ",
        "StashDB",
        " said about ",
        Named(kind="asset", id="f2"),
        ", because each matched on the picture alone",
    )


def test_a_take_back_receipt_off_a_files_page_says_how_many_and_by_hand_gives_no_reason() -> None:
    many = _worded(_receipt({TOOK_BACK: PICTURE_ALONE, "boxes": ["FansDB"], "files": 3}, page=None))
    assert many[-2:] == ("3 files", ", because it matched on the picture alone")

    by_hand = _worded(_receipt({TOOK_BACK: BY_HAND, "boxes": ["FansDB"], "files": 3}, page=None))
    assert by_hand[-1] == "3 files"


def test_on_a_files_page_only_the_boxes_that_spoke_about_that_file_are_named() -> None:
    """A take-back over many files names every box on the library's line, and on one file's page
    only the boxes whose refused answer that file held: FansDB alone spoke about f2."""
    payload = {
        TOOK_BACK: PICTURE_ALONE,
        "boxes": ["FansDB", "PMVStash", "StashDB"],
        "files": 3,
        "by_file": {"f1": ["FansDB", "PMVStash"], "f2": ["FansDB"], "f3": ["StashDB"]},
    }

    on_its_page = _worded(_receipt(payload, page=("asset", "f2")))
    off_it = _worded(_receipt(payload, page=None))

    assert on_its_page == (
        DOER,
        " took back what ",
        "FansDB",
        " said about ",
        Named(kind="asset", id="f2"),
        ", because it matched on the picture alone",
    )
    assert off_it[1:6] == (" took back what ", "FansDB", ", ", "PMVStash", " and ")


def test_a_take_back_of_one_file_names_it_wherever_it_is_read() -> None:
    """Off the file's own page, a take-back about one file names that file rather than "1 file"."""
    one = Recorded(
        id="r1",
        queue=NAME,
        payload=json.dumps({TOOK_BACK: BY_HAND, "boxes": ["FansDB"]}),
        title="Took back what FansDB said about 1 file",
        detail="",
        decided_at=1,
        subjects=(Subject(kind="asset", id="f1"),),
    )

    assert _worded(one)[-1] == Named(kind="asset", id="f1")
