# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Yes or a No on a face is ONE line on every History thread.

The press writes the answer (`face_confirmations`, `face_rejections`) and a receipt with the Undo,
and a thread reading both would say "You said 1 face is not ..." above "1 face was marked as not
them". The receipt declares the faces it answered
(`vocabulary.RECEIPT_FACES`) and `history.one_line_per_face_answer` says the press once, as the
receipt with its Undo, by the file, which a rescan keeps, rather than by the face's track.
"""

from __future__ import annotations

import json

import pytest

# Registering the tables a feature owns, so a kernel database has them. See `test_history.py`.
import sift.slices.faces.schema
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import Repository
from sift.kernel.access.history import history_of_asset
from sift.kernel.access.history_person import history_of_person
from sift.kernel.db import Database
from sift.kernel.vocabulary import FACE_SAID_NO, FACE_SAID_YES, RECEIPT_FACES
from sift.testing.fixtures import Actors, World

pytestmark = pytest.mark.anyio

#: Invented for this file; see `tests/gates/data/names_cast.txt`.
SOMEBODY = "Neve Arbor"
PERSON = "01HX0000000000000000000931"
AT_MS = 1_700_000_000_000


async def _person(database: Database) -> None:
    await database.execute(
        "INSERT INTO people (id, name, created_at) VALUES (?, ?, ?)", (PERSON, SOMEBODY, AT_MS)
    )


async def _refused_face(database: Database, track_id: str, asset_id: str) -> None:
    """A face on the file, refused as the person: the row a No writes."""
    await database.execute(
        "INSERT INTO face_tracks (id, asset_id, started_ms, ended_ms, seen_in, quality, created_at)"
        " VALUES (?, ?, 0, 0, 1, 0.9, ?)",
        (track_id, asset_id, AT_MS),
    )
    await database.execute(
        "INSERT INTO face_rejections (track_id, person_id, created_at) VALUES (?, ?, ?)",
        (track_id, PERSON, AT_MS),
    )


async def _confirmed_face(database: Database, row_id: str, asset_id: str) -> None:
    """A face on the file, confirmed as the person: the memory a Yes writes."""
    await database.execute(
        "INSERT INTO face_confirmations (id, asset_id, person_id, embedding, created_at)"
        " VALUES (?, ?, ?, x'00', ?)",
        (row_id, asset_id, PERSON, AT_MS),
    )


async def _receipt(
    database: Database,
    receipt_id: str,
    asset_id: str,
    how: str,
    *,
    faces: int = 1,
    title: str,
    reversed_at: int | None = None,
) -> None:
    """A Yes or No receipt about faces on one file, declaring them as the faces slice does."""
    payload = {
        "person_id": PERSON,
        RECEIPT_FACES: [{"person_id": PERSON, "asset_id": asset_id, "how": how}] * faces,
    }
    await database.execute(
        "INSERT INTO workbench_decisions (id, queue, user_id, title, detail, payload, decided_at,"
        " reversed_at, verb) VALUES (?, 'identified', NULL, ?, '', ?, ?, ?, 'decided')",
        (receipt_id, title, json.dumps(payload), AT_MS // 1000, reversed_at),
    )
    for kind, subject in (("person", PERSON), ("asset", asset_id)):
        await database.execute(
            "INSERT INTO workbench_decision_subjects (decision_id, kind, subject_id)"
            " VALUES (?, ?, ?)",
            (receipt_id, kind, subject),
        )


def _about_faces(thread: list) -> list[tuple[str | None, str]]:  # type: ignore[type-arg]
    return [
        (one.receipt, one.what)
        for one in thread
        if one.kind in ("confirmed", "rejected") or (one.receipt or "").startswith("R")
    ]


async def test_a_no_on_one_face_is_one_line_on_the_person_and_on_the_file(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    await _person(temp_db)
    await _refused_face(temp_db, "T1", world.solo)
    await _receipt(
        temp_db, "R1", world.solo, FACE_SAID_NO, title=f"You said 1 face is not {SOMEBODY}"
    )

    person = await history_of_person(temp_db, actors.admin, PERSON)
    file = await history_of_asset(temp_db, access, actors.admin, world.solo)

    assert _about_faces(person) == [("R1", f"You said 1 face is not {SOMEBODY}")]
    assert _about_faces(file) == [("R1", f"You said 1 face is not {SOMEBODY}")]
    assert next(one for one in person if one.receipt == "R1").undo is not None


async def test_a_day_the_receipts_account_for_in_part_says_what_is_left(
    temp_db: Database, actors: Actors, world: World
) -> None:
    """Two faces confirmed that day, one by a press with a receipt: the receipt and "1 face was
    confirmed as them", never "2 faces", which would say the receipted one twice."""
    await _person(temp_db)
    await _confirmed_face(temp_db, "C1", world.solo)
    await _confirmed_face(temp_db, "C2", world.solo)
    await _receipt(
        temp_db, "R2", world.solo, FACE_SAID_YES, title=f"You agreed with 1 face for {SOMEBODY}"
    )

    person = await history_of_person(temp_db, actors.admin, PERSON)

    lines = [one.what for one in person if one.kind == "confirmed"]
    assert lines == ["1 face was confirmed as them"], lines
    assert any(one.receipt == "R2" for one in person)


async def test_a_receipt_taken_back_or_declaring_nothing_leaves_the_face_line_standing(
    temp_db: Database, actors: Actors, world: World
) -> None:
    await _person(temp_db)
    await _refused_face(temp_db, "T1", world.solo)
    await _receipt(
        temp_db,
        "R3",
        world.solo,
        FACE_SAID_NO,
        title=f"You said 1 face is not {SOMEBODY}",
        reversed_at=AT_MS // 1000 + 5,
    )

    person = await history_of_person(temp_db, actors.admin, PERSON)

    assert [one.what for one in person if one.kind == "rejected"] == [
        "1 face was marked as not them"
    ]
