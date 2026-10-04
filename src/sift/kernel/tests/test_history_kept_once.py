# SPDX-License-Identifier: AGPL-3.0-or-later
"""One press on a stash-box's disagreement is ONE line on every History thread.

"Keep yours" writes the answer kept (`stash_box_kept`) and a receipt with the Undo, and a thread
drawing both would say "Kept your title for clip.mp4: ..." above "Your title, ..., was kept over
PMVStash's ...".
The receipt declares what it kept (`vocabulary.RECEIPT_KEPT`) and `history.one_line_per_kept` says
the press once, as the receipt with its Undo. Here no area words the receipt, so it would say its
STORED title with the raw values; a keep then takes the kept line's words, built by the one value
rule. A "Take theirs" that set another box's answer aside keeps its own line, which already says so.
"""

from __future__ import annotations

import json

import pytest

# Registering the tables a feature owns, so a kernel database has them. See `test_history.py`.
import sift.slices.stash_boxes.schema
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import Repository
from sift.kernel.access.history import history_of_asset
from sift.kernel.access.history_person import history_of_person
from sift.kernel.db import Database
from sift.kernel.vocabulary import KEPT_BY_TAKING_ANOTHER, KEPT_BY_THE_PRESS, RECEIPT_KEPT
from sift.testing.fixtures import Actors, World

pytestmark = pytest.mark.anyio

#: Invented for this file; see `tests/gates/data/names_cast.txt`.
SOMEBODY = "Neve Arbor"
PERSON = "01HX0000000000000000000921"
AT = 1_700_000_000


async def _a_person_and_a_box(database: Database) -> None:
    await database.execute(
        "INSERT INTO people (id, name, created_at) VALUES (?, ?, ?)", (PERSON, SOMEBODY, AT)
    )
    await database.execute(
        "INSERT INTO stash_boxes (id, name, endpoint, created_at) VALUES ('box', 'StashDB', ?, ?)",
        ("https://example.invalid/graphql", AT),
    )


async def _kept(database: Database, subject: str, local_id: str, key: str) -> None:
    """The answer a press kept, written as the stash-box slice writes one: each side as JSON."""
    await database.execute(
        "INSERT INTO stash_box_kept (subject, local_id, box_id, key, mine, theirs, decided_at)"
        " VALUES (?, ?, 'box', ?, ?, ?, ?)",
        (subject, local_id, key, json.dumps("NATURAL"), json.dumps("FAKE"), AT),
    )


async def _receipt(
    database: Database,
    receipt_id: str,
    subject: str,
    local_id: str,
    key: str,
    how: str,
    *,
    title: str,
    reversed_at: int | None = None,
    declared: bool = True,
) -> None:
    """A settle receipt about one record, declaring the answer it kept where `declared`."""
    payload: dict[str, object] = {"subject": subject, "local_id": local_id, "key": key}
    if declared:
        payload[RECEIPT_KEPT] = [
            {"subject": subject, "local_id": local_id, "box_id": "box", "key": key, "how": how}
        ]
    await database.execute(
        "INSERT INTO workbench_decisions (id, queue, user_id, title, detail, payload, decided_at,"
        " reversed_at, verb) VALUES (?, 'reconcile', NULL, ?, '', ?, ?, ?, 'decided')",
        (receipt_id, title, json.dumps(payload), AT, reversed_at),
    )
    await database.execute(
        "INSERT INTO workbench_decision_subjects (decision_id, kind, subject_id) VALUES (?, ?, ?)",
        (receipt_id, subject, local_id),
    )


async def test_keeping_your_answer_is_one_line_on_a_person_said_by_the_one_value_rule(
    temp_db: Database, actors: Actors
) -> None:
    await _a_person_and_a_box(temp_db)
    await _kept(temp_db, "person", PERSON, "breast_type")
    await _receipt(
        temp_db,
        "R1",
        "person",
        PERSON,
        "breast_type",
        KEPT_BY_THE_PRESS,
        title=f"Kept your breast type for {SOMEBODY}: NATURAL, not StashDB's FAKE",
    )

    thread = await history_of_person(temp_db, actors.admin, PERSON)

    about = [one for one in thread if "StashDB" in one.what]
    assert len(about) == 1, [one.what for one in about]
    (line,) = about
    # The receipt's moment and Undo, a value said by the one rule: "Natural", never "NATURAL".
    assert line.receipt == "R1"
    assert line.undo is not None
    assert "Natural" in line.what and "NATURAL" not in line.what


async def test_keeping_your_answer_is_one_line_on_a_file(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    await _a_person_and_a_box(temp_db)
    await _kept(temp_db, "asset", world.solo, "title")
    await _receipt(
        temp_db,
        "R2",
        "asset",
        world.solo,
        "title",
        KEPT_BY_THE_PRESS,
        title="Kept your title for clip.mp4: NATURAL, not StashDB's FAKE",
    )

    thread = await history_of_asset(temp_db, access, actors.admin, world.solo)

    about = [one for one in thread if "StashDB" in one.what]
    assert [(one.receipt, one.kind) for one in about] == [("R2", "decided")]
    assert "was kept over StashDB's" in about[0].what


async def test_an_answer_set_aside_by_taking_another_leaves_the_take_as_the_line(
    temp_db: Database, actors: Actors
) -> None:
    await _a_person_and_a_box(temp_db)
    await _kept(temp_db, "person", PERSON, "breast_type")
    title = f"Took FansDB's breast type for {SOMEBODY}: FAKE; StashDB's NATURAL set aside"
    await _receipt(
        temp_db, "R3", "person", PERSON, "breast_type", KEPT_BY_TAKING_ANOTHER, title=title
    )

    thread = await history_of_person(temp_db, actors.admin, PERSON)

    assert not [one for one in thread if one.kind == "kept_mine"]
    (take,) = [one for one in thread if one.receipt == "R3"]
    assert take.what.startswith("Took FansDB's breast type")


async def test_a_kept_answer_whose_receipt_was_taken_back_or_says_nothing_still_stands(
    temp_db: Database, actors: Actors
) -> None:
    """A receipt taken back no longer describes the answer, and one that declares nothing cannot be
    matched to it: the kept line is then the only thing saying it, so it stays."""
    await _a_person_and_a_box(temp_db)
    await _kept(temp_db, "person", PERSON, "breast_type")
    await _receipt(
        temp_db,
        "R4",
        "person",
        PERSON,
        "breast_type",
        KEPT_BY_THE_PRESS,
        title="Kept your breast type",
        reversed_at=AT + 5,
    )
    await _receipt(
        temp_db,
        "R5",
        "person",
        PERSON,
        "breast_type",
        KEPT_BY_THE_PRESS,
        title="Kept your breast type again",
        declared=False,
    )

    thread = await history_of_person(temp_db, actors.admin, PERSON)

    (kept,) = [one for one in thread if one.kind == "kept_mine"]
    assert kept.what == "Your breast type, Natural, was kept over StashDB's Fake"
