# SPDX-License-Identifier: AGPL-3.0-or-later
"""The pieces a thread is folded and worded from, asked one rule at a time.

A receipt's payload is a string its queue wrote, so anything unreadable in it is nothing rather
than an error on a page somebody opened to find out what happened. The folds decide which of two
lines about one press goes, and a line that should have stood and went is a missing act.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any, cast

import pytest

# Imported for their side effect: registering the record's, the faces' and the stash-boxes' tables.
import sift.slices.faces.schema
import sift.slices.stash_boxes.schema
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import sentences as say
from sift.kernel.access.history import (
    Actor,
    Event,
    Link,
    actor_of_source,
    face_sures,
    faces_answered_of,
    kept_answers_of,
    kept_events,
    one_line_per_face_answer,
    one_line_per_kept,
    recognized_lines,
    the_file_named,
    titled,
)
from sift.kernel.access.history_folds import (
    _BOX_WROTE,
    _FOLDS_BY_SOURCE,
    _by_the_box,
    _one_line_per_act,
)
from sift.kernel.access.history_receipts import _decided_line
from sift.kernel.db import Database, Row
from sift.kernel.sorting import sort_key
from sift.kernel.vocabulary import (
    FACE_SAID_NO,
    FACE_SAID_YES,
    KEPT_BY_THE_PRESS,
    RECEIPT_FACES,
    RECEIPT_KEPT,
)

AT = 1_700_000_000
PERSON = "01HX0000000000000000001001"
OTHER = "01HX0000000000000000001002"
FILE = "01HX0000000000000000001003"
BOX = "01HX0000000000000000001004"
ANSWER = ("person", PERSON, BOX, "birthdate")


def line(kind: str, at: int, **fields: Any) -> Event:
    """One line of a thread, of this kind at this moment, with whatever else the case names."""
    plain = Event(at=at, actor=Actor.YOU, actor_name=None, kind=kind, pieces=say.said(kind))
    return replace(plain, **fields)


# --- what a receipt's payload declares ------------------------------------------------------------


@pytest.mark.unit
def test_a_receipt_declares_the_answers_it_kept_and_nothing_unreadable_is_read() -> None:
    whole = {"subject": "person", "local_id": PERSON, "box_id": BOX, "key": "birthdate"}
    payload = json.dumps(
        {RECEIPT_KEPT: [7, {**whole, "how": ""}, {**whole, "how": KEPT_BY_THE_PRESS}]}
    )
    assert kept_answers_of(payload) == ((ANSWER, KEPT_BY_THE_PRESS),)
    assert kept_answers_of("{not json") == ()
    assert kept_answers_of(json.dumps([1, 2])) == ()


@pytest.mark.unit
def test_a_receipt_declares_the_faces_it_answered_and_only_a_yes_or_a_no_counts() -> None:
    payload = json.dumps(
        {
            RECEIPT_FACES: [
                "not an entry",
                {"person_id": PERSON, "asset_id": FILE},
                {"person_id": PERSON, "asset_id": FILE, "how": "maybe"},
                {"person_id": PERSON, "asset_id": FILE, "how": FACE_SAID_YES},
                {"person_id": OTHER, "asset_id": FILE, "how": FACE_SAID_NO},
            ]
        }
    )
    assert faces_answered_of(payload) == (
        (PERSON, FILE, FACE_SAID_YES),
        (OTHER, FILE, FACE_SAID_NO),
    )
    assert faces_answered_of("{not json") == ()


# --- one line per press ---------------------------------------------------------------------------


@pytest.mark.unit
def test_the_newest_standing_receipt_carries_a_kept_answer() -> None:
    newer = line("decided", AT + 10, receipt="r2", kept=((ANSWER, KEPT_BY_THE_PRESS),))
    older = line("decided", AT, receipt="r1", kept=((ANSWER, KEPT_BY_THE_PRESS),))
    kept = line("kept_mine", AT + 5, answer=ANSWER)

    assert one_line_per_kept([newer, older, kept]) == [newer, older]


@pytest.mark.unit
def test_a_face_line_no_receipt_answered_for_stands_as_it_was() -> None:
    receipt = line("decided", AT, receipt="r1", faces=((PERSON, FILE, FACE_SAID_YES),))
    answered = line("confirmed", AT, faces=((PERSON, FILE, FACE_SAID_YES),))
    unanswered = line("confirmed", AT, faces=((OTHER, FILE, FACE_SAID_YES),))

    assert one_line_per_face_answer([receipt, answered, unanswered]) == [receipt, unanswered]


# --- a stored title's words -----------------------------------------------------------------------


@pytest.mark.unit
def test_a_declared_link_is_placed_on_its_words_only_where_the_title_has_them() -> None:
    link = Link(kind="person", id=PERSON, name="Jane Roe")
    assert say.text_of(titled("Kept your answer for Jane Roe", link)) == (
        "Kept your answer for Jane Roe"
    )
    assert say.things_in(titled("Kept your answer for Jane Roe", link))[0].id == PERSON
    assert titled("Kept your answer for somebody", link) == say.said(
        "Kept your answer for somebody"
    )


@pytest.mark.unit
def test_here_in_a_title_is_the_file_and_a_name_before_it_is_left_alone() -> None:
    named = say.said(say.thing("person", PERSON, "Jane Roe"), " was named here")
    file = Link(kind="asset", id=FILE, name="clip.png")
    assert say.text_of(the_file_named(named, file)) == "Jane Roe was named in clip.png"


# --- how sure a face match was --------------------------------------------------------------------


@pytest.mark.anyio
async def test_a_face_match_says_how_sure_it_was_on_the_persons_page(
    temp_db: Database, access: object
) -> None:
    for person_id, name in ((PERSON, "Jane Roe"), (OTHER, "Mary Roe")):
        await temp_db.execute(
            "INSERT INTO people (id, name, name_sort, created_at) VALUES (?, ?, ?, ?)",
            (person_id, name, sort_key(name), AT),
        )
    await temp_db.execute(
        "INSERT INTO assets (id, identity, identity_version, media_type, added_at)"
        " VALUES (?, ?, 1, 'image', ?)",
        (FILE, f"digest-{FILE}", AT),
    )
    for track, person_id, sure in (("t1", PERSON, 0.75), ("t2", OTHER, None)):
        await temp_db.execute(
            "INSERT INTO face_tracks (id, asset_id, started_ms, ended_ms, seen_in, quality,"
            " person_id, confidence, attribution, attributed_at, created_at)"
            " VALUES (?, ?, 0, 0, 1, 1.0, ?, ?, 'matched', ?, ?)",
            (track, FILE, person_id, sure, AT, AT),
        )
    for decision_id, person_id in (("d1", PERSON), ("d2", OTHER)):
        await temp_db.execute(
            "INSERT INTO workbench_decisions (id, queue, title, detail, payload, decided_at, verb,"
            " actor_kind, actor_id, object_kind, object_id)"
            " VALUES (?, 'ledger', 't', '', '{}', ?, 'linked', 'sift', 'faces', 'person', ?)",
            (decision_id, AT, person_id),
        )
    rows = await temp_db.fetch_all(
        "SELECT id, verb, object_kind, object_id, actor_kind, actor_id FROM workbench_decisions"
        " ORDER BY id"
    )
    file = Link(kind="asset", id=FILE, name="clip.png")

    lines = await recognized_lines(temp_db, rows, {"d1": file, "d2": file}, page=("person", PERSON))

    # A match of somebody else, read on this page, keeps its title.
    assert set(lines) == {"d1"}
    assert say.text_of(lines["d1"]).startswith("Sift recognized them in clip.png")
    assert await face_sures(temp_db, [(FILE, PERSON), (FILE, OTHER)]) == {
        (FILE, PERSON): [0.75],
        (FILE, OTHER): [None],
    }
    assert await face_sures(temp_db, []) == {}
    assert await recognized_lines(temp_db, rows, {}, page=("person", PERSON)) == {}


@pytest.mark.anyio
async def test_no_face_is_sure_of_anything_where_faces_were_never_read(
    temp_db: Database, access: object
) -> None:
    await temp_db.execute("DROP TABLE face_tracks")
    assert await face_sures(temp_db, [(FILE, PERSON)]) == {}


# --- who did it, and what a box's answer refused ---------------------------------------------------


@pytest.mark.unit
def test_the_source_word_says_who_decided_and_an_unknown_word_is_still_sift() -> None:
    assert actor_of_source(None, "StashDB") == (Actor.SOMEBODY, None)
    assert actor_of_source("stash_box", "StashDB") == (Actor.STASH_BOX, "StashDB")
    assert actor_of_source("stash_box") == (Actor.STASH_BOX, None)
    assert actor_of_source("folder", "StashDB") == (Actor.SIFT, say.SIFT)
    assert actor_of_source("a pass from a later build") == (Actor.SIFT, say.SIFT)


@pytest.mark.anyio
@pytest.mark.parametrize("subject", ["asset", "person", "tag", "site"])
async def test_a_kept_answer_is_read_the_one_way_on_every_kind_of_page(
    temp_db: Database, subject: str
) -> None:
    await temp_db.initialize_schema()
    await temp_db.execute(
        "INSERT INTO stash_boxes (id, name, endpoint, created_at) VALUES (?, ?, ?, 0)",
        (BOX, "StashDB", "https://stash-box.invalid/graphql"),
    )
    for local_id, key, at in (
        (PERSON, "zeta_note", AT + 1),
        (PERSON, "alpha_note", AT + 1),
        (PERSON, "mid_note", AT),
        (OTHER, "alpha_note", AT),
    ):
        await temp_db.execute(
            "INSERT INTO stash_box_kept (subject, local_id, box_id, key, mine, theirs, decided_at)"
            " VALUES (?, ?, ?, ?, 'here', 'there', ?)",
            (subject, local_id, BOX, key, at),
        )

    events = await kept_events(temp_db, subject, PERSON)

    # Oldest first, and a key breaks a tie within one second.
    assert [(one.at, one.answer) for one in events] == [
        (AT, (subject, PERSON, BOX, "mid_note")),
        (AT + 1, (subject, PERSON, BOX, "alpha_note")),
        (AT + 1, (subject, PERSON, BOX, "zeta_note")),
    ]
    assert {(one.kind, one.actor, one.actor_name) for one in events} == {
        ("kept_mine", Actor.SOMEBODY, None)
    }
    assert "StashDB" in say.text_of(events[0].pieces)


# --- the folds that make one line of a press -------------------------------------------------------


@pytest.mark.unit
def test_a_line_whose_receipt_is_not_on_the_pane_stands() -> None:
    decision = line("decided", AT, receipt="r1")
    naming = line("named", AT, receipt="r1")
    orphan = line("tagged", AT, receipt="r2")

    folded = _one_line_per_act([decision, naming, orphan])

    assert [one.kind for one in folded] == ["decided", "tagged"]
    assert folded[1] is orphan


@pytest.mark.unit
def test_a_box_counts_a_filing_whose_site_is_gone_and_lists_only_what_has_a_page() -> None:
    box = line("enriched", AT, actor=Actor.STASH_BOX, actor_name="StashDB")
    named = line("named", AT, pieces=say.said(say.thing("person", PERSON, "Jane Roe")))
    filed = line("filed", AT, pieces=say.said("Filed under janeroe"))

    said = _by_the_box(box, [named, filed])

    assert said.pieces == say.recognized("StashDB", None, [say.people(1), "the site"])
    assert [(group.kind, [one.id for one in group.links]) for group in said.detail] == [
        ("person", [PERSON])
    ]


@pytest.mark.unit
def test_every_act_a_box_line_takes_is_one_it_counts() -> None:
    """The box's line always has something to say: `_said_by` hands it only these acts."""
    assert {*_FOLDS_BY_SOURCE, "filed"} <= {act for act, _kind, _words in _BOX_WROTE}


@pytest.mark.unit
def test_a_receipt_that_says_only_decided_keeps_its_title_though_its_thing_is_here() -> None:
    about = {"r1": ("photo_set", "01HX0000000000000000001005", "Harbour")}
    stored = {
        "id": "r1",
        "verb": "decided",
        "title": "Grouped 3 pictures",
        "payload": "{}",
        "actor_kind": "sift",
        "actor_id": "filename",
    }

    assert _decided_line(cast(Row, stored), about) == titled("Grouped 3 pictures", None)
    # The same receipt carrying a real verb is worded from the record instead.
    worded = _decided_line(cast(Row, {**stored, "verb": "added"}), about)
    assert worded != titled("Grouped 3 pictures", None)
    assert "Harbour" in say.text_of(worded)
