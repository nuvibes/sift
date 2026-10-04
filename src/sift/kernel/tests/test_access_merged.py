# SPDX-License-Identifier: AGPL-3.0-or-later
"""A merge re-points the stores that name the one going by a kind word, where each store is there.

A store a feature keeps is absent from a process that never registered the feature, and a merge
there must pass it over rather than fail on a table that is not there.
"""

from __future__ import annotations

import pytest

# Imported for its side effect: registering the record's tables, so a kernel database has them.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access.merged import FOLLOWS, follow
from sift.kernel.db import Database
from sift.kernel.sorting import sort_key

pytestmark = pytest.mark.anyio

AT = 1_700_000_000
KEPT = "01HX0000000000000000000701"
GONE = "01HX0000000000000000000703"
TAGGED = "01HX0000000000000000000710"


async def person(database: Database, person_id: str, name: str) -> None:
    await database.execute(
        "INSERT INTO people (id, name, name_sort, created_at) VALUES (?, ?, ?, ?)",
        (person_id, name, sort_key(name), AT),
    )


async def event(database: Database, decision_id: str, verb: str, subject: str) -> None:
    """One act on the record about one person, naming nobody."""
    await database.execute(
        "INSERT INTO workbench_decisions (id, queue, title, detail, payload, decided_at, verb)"
        " VALUES (?, 'ledger', 't', '', '{}', ?, ?)",
        (decision_id, AT, verb),
    )
    await database.execute(
        "INSERT INTO workbench_decision_subjects (decision_id, kind, subject_id)"
        " VALUES (?, 'person', ?)",
        (decision_id, subject),
    )


async def test_a_store_that_is_not_there_is_passed_over(temp_db: Database, access: object) -> None:
    await person(temp_db, KEPT, "Jane Roe")
    await event(temp_db, TAGGED, "tagged", GONE)
    # A store a slice keeps, and a process that never registered the slice has no such table.
    assert "stash_box_undecided" in {table for table, _ in FOLLOWS}
    await temp_db.execute("DROP TABLE IF EXISTS stash_box_undecided")

    async with temp_db.write() as connection:
        await follow(connection, kind="person", losing=GONE, keeping=KEPT, name="Neve Alder")

    moved = await temp_db.fetch_all(
        "SELECT subject_id, name FROM workbench_decision_subjects WHERE decision_id = ?",
        (TAGGED,),
    )
    # The id follows the survivor, and a row that wrote no name is given the name of the one going.
    assert [(row["subject_id"], row["name"]) for row in moved] == [(KEPT, "Neve Alder")]
