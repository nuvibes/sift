# SPDX-License-Identifier: AGPL-3.0-or-later
"""What each press holds stays the record summed again through every way a label moves."""

from __future__ import annotations

import random

import pytest

# Imported for its side effect: registering the record's tables, so a kernel database has them.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access.history_press_totals import TOTALS_DIFFERENCES, TOTALS_DROPS
from sift.kernel.access.history_presses import PRESS_DIFFERENCES
from sift.kernel.db import Database

pytestmark = pytest.mark.anyio

AT = 1_700_000_000


@pytest.fixture
async def record(temp_db: Database) -> Database:
    await temp_db.initialize_schema()
    return temp_db


def _id(n: int) -> str:
    return f"01HX{n:022d}"


async def _act(
    db: Database, n: int, at: int, *, on: str | None = None, by: str | None = "tagger"
) -> None:
    await db.execute(
        "INSERT INTO workbench_decisions (id, queue, title, detail, payload, decided_at, verb,"
        " actor_kind, actor_id, object_kind, object_id, object_name)"
        " VALUES (?, 'faces', '', '', '{}', ?, 'filed', 'sift', ?, 'person', ?, ?)",
        (_id(n), at, by, on, None if on is None else f"name {on}"),
    )
    for subject in (f"file{n % 3}", f"file{n % 5}"):
        await db.execute(
            "INSERT OR IGNORE INTO workbench_decision_subjects (decision_id, kind, subject_id, name)"
            " VALUES (?, 'asset', ?, ?)",
            (_id(n), subject, subject.upper()),
        )


async def _exact(db: Database) -> bool:
    return not await db.fetch_all(TOTALS_DIFFERENCES) and not await db.fetch_all(PRESS_DIFFERENCES)


async def _presses(db: Database) -> int:
    row = await db.fetch_one("SELECT COUNT(DISTINCT press_id) AS n FROM workbench_press_objects")
    assert row is not None
    return int(row["n"])


async def test_the_totals_follow_arrivals_joins_partings_moves_and_undo(record: Database) -> None:
    db = record
    for n in range(10):
        await _act(db, n, AT + n * 10, on=f"p{n % 2}")
    for n in range(10, 14):
        await _act(db, n, AT + 200 + n, on="p0")
    assert await _presses(db) == 2 and await _exact(db)

    # A bridge joins them: the smaller press's rows are added into the larger's.
    await _act(db, 100, AT + 150, on="p1")
    assert await _presses(db) == 1 and await _exact(db)

    # Undone and done again in place: only the standing count moves.
    await db.execute("UPDATE workbench_decisions SET reversed_at = ? WHERE id = ?", (AT, _id(3)))
    assert await _exact(db)
    await db.execute("UPDATE workbench_decisions SET reversed_at = NULL WHERE id = ?", (_id(3),))
    assert await _exact(db)

    # The bridge going parts them; a move joins them again and another parts them.
    await db.execute("DELETE FROM workbench_decisions WHERE id = ?", (_id(100),))
    assert await _presses(db) == 2 and await _exact(db)
    await db.execute(
        "UPDATE workbench_decisions SET decided_at = ? WHERE id = ?", (AT + 150, _id(5))
    )
    assert await _exact(db)
    await db.execute(
        "UPDATE workbench_decisions SET decided_at = ? WHERE id = ?", (AT + 9_000, _id(5))
    )
    assert await _exact(db)

    # A thing merged into another, on the acts and on their subjects, as a merge rewrites both.
    await db.execute(
        "UPDATE workbench_decisions SET object_id = 'p0', object_name = NULL WHERE object_id = 'p1'"
    )
    await db.execute(
        "UPDATE OR IGNORE workbench_decision_subjects SET subject_id = 'file0'"
        " WHERE subject_id = 'file1'"
    )
    assert await _exact(db)
    await db.execute("DELETE FROM workbench_decision_subjects WHERE subject_id = 'file2'")
    assert await _exact(db)


async def test_an_act_by_nobody_and_on_nothing_is_counted_as_untold(record: Database) -> None:
    db = record
    await _act(db, 1, AT, by=None)
    await _act(db, 2, AT + 1, by=None)
    row = await db.fetch_one("SELECT acts, untold, standing FROM workbench_press_objects")
    assert row is not None and (row["acts"], row["untold"], row["standing"]) == (2, 2, 2)
    assert await _exact(db)


async def test_a_record_at_version_eighteen_is_summed_once(record: Database) -> None:
    """The version 19 step: a record labelled with no totals gains every press's."""
    db = record
    from sift.slices.workbench.schema import initialize_workbench

    async with db.write() as connection:
        for statement in (
            *TOTALS_DROPS,
            "DROP TABLE workbench_press_objects",
            "DROP TABLE workbench_press_subjects",
            "DROP TABLE workbench_totals_owed",
        ):
            await connection.execute(statement)
    for n in range(5):
        await _act(db, n, AT + n * 5_000)
    async with db.write() as connection:
        await initialize_workbench(connection, 18)
    assert await _presses(db) == 5 and await _exact(db)


async def test_a_random_walk_of_every_change_keeps_them_exact(record: Database) -> None:
    """Arrivals, goings, moves, undos and merges of things in a seeded order, checked after each."""
    db = record
    pick = random.Random(16)
    alive: list[int] = []
    for step in range(120):
        roll = pick.random()
        if roll < 0.45 or len(alive) < 4:
            n = 1_000 + step
            await _act(db, n, AT + pick.randrange(0, 2_000), on=pick.choice(("p0", "p1", None)))
            alive.append(n)
        elif roll < 0.6:
            gone = alive.pop(pick.randrange(len(alive)))
            await db.execute("DELETE FROM workbench_decisions WHERE id = ?", (_id(gone),))
        elif roll < 0.75:
            await db.execute(
                "UPDATE workbench_decisions SET decided_at = ? WHERE id = ?",
                (AT + pick.randrange(0, 2_000), _id(pick.choice(alive))),
            )
        elif roll < 0.85:
            await db.execute(
                "UPDATE workbench_decisions SET reversed_at = CASE WHEN reversed_at IS NULL"
                " THEN ? END WHERE id = ?",
                (AT, _id(pick.choice(alive))),
            )
        elif roll < 0.93:
            await db.execute(
                "UPDATE workbench_decisions SET object_id = ? WHERE id = ?",
                (pick.choice(("p0", "p1", "p2")), _id(pick.choice(alive))),
            )
        else:
            await db.execute(
                "UPDATE OR IGNORE workbench_decision_subjects SET subject_id = ?"
                " WHERE decision_id = ?",
                (f"file{pick.randrange(6)}", _id(pick.choice(alive))),
            )
        assert await _exact(db), f"step {step}"


async def test_totals_lost_while_their_triggers_stand_are_summed_again(record: Database) -> None:
    db = record
    from sift.kernel.access.history_presses import keep_presses

    for n in range(3):
        await _act(db, n, AT + n * 5_000)
    async with db.write() as connection:
        await connection.execute("DROP TABLE workbench_press_objects")
        await connection.execute("DROP TABLE workbench_press_subjects")
        await keep_presses(connection)
    assert await _presses(db) == 3 and await _exact(db)
