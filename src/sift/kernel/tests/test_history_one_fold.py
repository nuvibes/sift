# SPDX-License-Identifier: AGPL-3.0-or-later
"""One fold for every History thread: a link row's line belongs to the receipt on that file whose
object is the linked thing, matched by id (`history_folds.RECEIPT_OF_A_NAMING`).

Two rules, one per tab, would disagree on real rows: a naming with a source that a receipt
accounted for would fold on the file tab and be counted again on the person tab, and a sourceless
naming a receipt named as a subject within a second would fold on the person tab while the file tab
drew it. Each case below reads both ends and asserts they agree.
"""

from __future__ import annotations

import pytest

# Imported for their side effect: registering the tables a feature owns.
import sift.slices.faces.schema
import sift.slices.stash_boxes.schema
import sift.slices.suggestions.schema
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import Repository
from sift.kernel.access.history import Event, history_of_asset
from sift.kernel.access.history_entity import history_of_tag
from sift.kernel.access.history_folds import counted_apart_from_receipts
from sift.kernel.access.history_person import history_of_person
from sift.kernel.db import Database
from sift.testing.fixtures import Actors

pytestmark = pytest.mark.anyio

AT = 1_700_000_000
PERSON = "01HX0000000000000000000A01"
TAG = "01HX0000000000000000000A02"
FILE = "01HX0000000000000000000A03"
OTHER_FILE = "01HX0000000000000000000A04"
RECEIPT = "01HX0000000000000000000A05"
ROOT = "01HX0000000000000000000A06"


async def _file(database: Database, asset_id: str) -> None:
    await database.execute(
        "INSERT INTO assets (id, identity, identity_version, media_type, added_at)"
        " VALUES (?, ?, 1, 'video', ?)",
        (asset_id, f"digest-{asset_id}", AT),
    )
    await database.execute(
        "INSERT OR IGNORE INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
        (ROOT, "library", "/library", 0),
    )
    await database.execute(
        "INSERT INTO asset_locations (id, asset_id, root_id, folder_id, rel_path, filename,"
        " first_seen_at, last_seen_at) VALUES (?, ?, ?, NULL, ?, ?, 0, 0)",
        (f"loc-{asset_id}", asset_id, ROOT, f"{asset_id}.mp4", f"{asset_id}.mp4"),
    )


async def _named(database: Database, asset_id: str, source: str | None) -> None:
    await database.execute(
        "INSERT OR IGNORE INTO people (id, name, created_at) VALUES (?, 'Neve Alder', ?)",
        (PERSON, AT),
    )
    await database.execute(
        "INSERT INTO asset_people (asset_id, person_id, source, decided_at) VALUES (?, ?, ?, ?)",
        (asset_id, PERSON, source, AT + 60),
    )


async def _receipt(
    database: Database,
    *,
    object_kind: str | None,
    object_id: str | None,
    about: tuple[tuple[str, str], ...],
    reversed_at: int | None = None,
    at: int = AT + 60,
) -> None:
    """One receipt taken in the same press as the rows, with what it was about as its subjects."""
    await database.execute(
        "INSERT INTO workbench_decisions (id, queue, user_id, title, detail, payload, decided_at,"
        " reversed_at, verb, object_kind, object_id)"
        " VALUES (?, 'suggestions', NULL, 'Chose a name', 'It named them.', '{}', ?, ?,"
        " 'decided', ?, ?)",
        (RECEIPT, at, reversed_at, object_kind, object_id),
    )
    for kind, subject in about:
        await database.execute(
            "INSERT INTO workbench_decision_subjects (decision_id, kind, subject_id)"
            " VALUES (?, ?, ?)",
            (RECEIPT, kind, subject),
        )


def _kinds(events: list[Event]) -> list[str]:
    return [one.kind for one in events if one.kind in ("named", "tagged", "decided")]


async def test_a_naming_with_a_source_its_receipt_accounts_for_is_one_line_on_both_tabs(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A row two rules would draw twice: the file tab folding it into the card, the person tab
    counting it beside the same card."""
    await _file(temp_db, FILE)
    await _named(temp_db, FILE, "filename")
    await _receipt(
        temp_db,
        object_kind="person",
        object_id=PERSON,
        about=(("asset", FILE), ("person", PERSON)),
        at=AT + 61,
    )

    on_file = await history_of_asset(temp_db, access, actors.admin, FILE)
    on_person = await history_of_person(temp_db, actors.admin, PERSON)

    assert _kinds(on_file) == ["decided"]
    assert _kinds(on_person) == ["decided"]


async def test_a_sourceless_naming_a_receipt_names_only_as_a_subject_stands_on_both_tabs(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The other half: within a second and named as subjects, one rule would fold it on the person
    tab while the file tab drew it. With no object the receipt does not say the naming
    was its act, so both tabs draw both lines."""
    await _file(temp_db, FILE)
    await _named(temp_db, FILE, None)
    await _receipt(
        temp_db, object_kind=None, object_id=None, about=(("asset", FILE), ("person", PERSON))
    )

    on_file = await history_of_asset(temp_db, access, actors.admin, FILE)
    on_person = await history_of_person(temp_db, actors.admin, PERSON)

    assert sorted(_kinds(on_file)) == ["decided", "named"]
    assert sorted(_kinds(on_person)) == ["decided", "named"]


async def test_a_receipt_taken_back_absorbs_nothing_on_either_tab(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A receipt that was taken back removed its rows, so a naming there now was made by
    something else and must not vanish into a card that says it was taken back."""
    await _file(temp_db, FILE)
    await _named(temp_db, FILE, None)
    await _receipt(
        temp_db,
        object_kind="person",
        object_id=PERSON,
        about=(("asset", FILE), ("person", PERSON)),
        reversed_at=AT + 120,
    )

    on_file = await history_of_asset(temp_db, access, actors.admin, FILE)
    on_person = await history_of_person(temp_db, actors.admin, PERSON)

    assert "named" in _kinds(on_file)
    assert "named" in _kinds(on_person)


async def test_the_person_tab_counts_only_the_files_no_card_accounts_for(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """Two files named the same day by the same source, one by a receipt: the day's line counts
    the other one and the card says the first."""
    await _file(temp_db, FILE)
    await _file(temp_db, OTHER_FILE)
    await _named(temp_db, FILE, None)
    await _named(temp_db, OTHER_FILE, None)
    await _receipt(
        temp_db,
        object_kind="person",
        object_id=PERSON,
        about=(("asset", FILE), ("person", PERSON)),
    )

    named = [
        one.what
        for one in await history_of_person(temp_db, actors.admin, PERSON)
        if one.kind == "named"
    ]

    assert named == ["They were named on 1 file"]


async def test_a_tagging_its_receipt_accounts_for_is_one_line_on_the_tag_and_the_file(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The entity tabs read the same fold: a tag's count leaves out a file its card says."""
    await _file(temp_db, FILE)
    await temp_db.execute(
        "INSERT INTO tags (id, name, created_at) VALUES (?, 'beach', ?)", (TAG, AT)
    )
    await temp_db.execute(
        "INSERT INTO asset_tags (asset_id, tag_id, source, decided_at) VALUES (?, ?, ?, ?)",
        (FILE, TAG, "filename", AT + 60),
    )
    await _receipt(temp_db, object_kind="tag", object_id=TAG, about=(("asset", FILE), ("tag", TAG)))

    on_file = await history_of_asset(temp_db, access, actors.admin, FILE)
    on_tag = await history_of_tag(temp_db, actors.admin, TAG)

    assert _kinds(on_file) == ["decided"]
    assert _kinds(on_tag) == ["decided"]


def test_the_groups_no_card_accounts_for_are_added_back_together_by_day() -> None:
    """Grouped by receipt in SQL, so two receipts not on the pane are one day's line again."""
    day = AT // 86400
    rows = [
        {"source": None, "box": None, "day": day, "receipt": "drawn", "files": 5, "at": AT},
        {"source": None, "box": None, "day": day, "receipt": "elsewhere", "files": 2, "at": AT + 9},
        {"source": None, "box": None, "day": day, "receipt": None, "files": 1, "at": AT + 3},
    ]

    (one,) = counted_apart_from_receipts(rows, {"drawn"})  # type: ignore[arg-type]

    assert (one["files"], one["at"]) == (3, AT + 9)


def test_a_day_added_back_together_is_dated_by_its_newest_row_whichever_comes_first() -> None:
    """The rows come in receipt order, not time order, and a row with no moment (written before
    one was kept) is no moment at all: the line is dated by the newest moment any row has."""
    day = AT // 86400
    rows = [
        {"source": None, "box": None, "day": day, "receipt": "a", "files": 1, "at": None},
        {"source": None, "box": None, "day": day, "receipt": "b", "files": 2, "at": AT + 3},
        {"source": None, "box": None, "day": day, "receipt": "c", "files": 4, "at": AT + 9},
        {"source": None, "box": None, "day": day, "receipt": "d", "files": 8, "at": None},
    ]

    (one,) = counted_apart_from_receipts(rows, set())  # type: ignore[arg-type]

    assert (one["files"], one["at"]) == (15, AT + 9)
