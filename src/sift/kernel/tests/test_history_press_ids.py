# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every act carries its press's label, one label a press: kept when its opening goes, the smaller
side relabelled where two presses join, the later part where one parts."""

from __future__ import annotations

import pytest

# Imported for its side effect: registering the record's tables, so a kernel database has them.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access.history_presses import PRESS_DIFFERENCES
from sift.kernel.db import Database

pytestmark = pytest.mark.anyio


@pytest.fixture
async def record(temp_db: Database) -> Database:
    await temp_db.initialize_schema()
    return temp_db


AT = 1_700_000_000


async def _act(db: Database, n: int, at: int, by: str = "tagger") -> None:
    await db.execute(
        "INSERT INTO workbench_decisions"
        " (id, queue, title, detail, payload, decided_at, verb, actor_kind, actor_id)"
        " VALUES (?, 'ledger', '', '', '{}', ?, 'filed', 'sift', ?)",
        (f"01HX{n:022d}", at, by),
    )


async def _labels(db: Database) -> dict[int, str]:
    rows = await db.fetch_all("SELECT id, press_id FROM workbench_decisions")
    return {int(str(row["id"])[4:]): str(row["press_id"]) for row in rows}


async def _exact(db: Database) -> bool:
    return not await db.fetch_all(PRESS_DIFFERENCES)


async def test_a_press_keeps_its_label_through_joins_partings_and_its_opening_going(
    record: Database,
) -> None:
    temp_db = record
    for n in range(10):
        await _act(temp_db, n, AT + n * 10)
    for n in range(10, 13):
        await _act(temp_db, n, AT + 200 + n)
    before = await _labels(temp_db)
    assert len(set(before.values())) == 2 and await _exact(temp_db)

    # An act bridging the two: the three acts take the ten's label.
    await _act(temp_db, 100, AT + 150)
    joined = await _labels(temp_db)
    assert set(joined.values()) == {before[0]} and await _exact(temp_db)

    # An act just before the opening takes the opening over and keeps the label.
    await _act(temp_db, 101, AT - 30)
    assert set((await _labels(temp_db)).values()) == {before[0]} and await _exact(temp_db)

    # The bridge going parts them: the earlier part keeps the label, the later takes a new one.
    await temp_db.execute("DELETE FROM workbench_decisions WHERE id = ?", (f"01HX{100:022d}",))
    parted = await _labels(temp_db)
    assert parted[0] == before[0] and parted[10] != before[0] and await _exact(temp_db)

    # A move across joins them again; a move away parts them and leaves the act alone.
    moved = f"01HX{5:022d}"
    await temp_db.execute(
        "UPDATE workbench_decisions SET decided_at = ? WHERE id = ?", (AT + 150, moved)
    )
    assert len(set((await _labels(temp_db)).values())) == 1 and await _exact(temp_db)
    await temp_db.execute(
        "UPDATE workbench_decisions SET decided_at = ? WHERE id = ?", (AT + 9_000, moved)
    )
    assert len(set((await _labels(temp_db)).values())) == 3 and await _exact(temp_db)

    # Its openings going leave the rest of a press its label.
    kept = await _labels(temp_db)
    for n in (101, 0, 1):
        await temp_db.execute("DELETE FROM workbench_decisions WHERE id = ?", (f"01HX{n:022d}",))
    after = await _labels(temp_db)
    assert all(after[n] == kept[n] for n in after) and await _exact(temp_db)


async def test_a_record_at_version_seventeen_is_labelled_once(record: Database) -> None:
    """The version 18 step: a record with marks and no labels gains every act's label."""
    temp_db = record
    from sift.slices.workbench.schema import initialize_workbench

    async with temp_db.write() as connection:
        for statement in (
            "DROP TRIGGER workbench_press_arrives",
            "DROP TRIGGER workbench_press_moves",
            "DROP TRIGGER workbench_press_goes",
            "DROP INDEX ix_workbench_press",
            "ALTER TABLE workbench_decisions DROP COLUMN press_id",
        ):
            await connection.execute(statement)
    for n in range(4):
        await _act(temp_db, n, AT + n)
    await _act(temp_db, 9, AT + 5_000)
    await _act(temp_db, 10, AT, by="namer")
    async with temp_db.write() as connection:
        await initialize_workbench(connection, 17)
    labels = await _labels(temp_db)
    assert len(set(labels.values())) == 3 and await _exact(temp_db)
