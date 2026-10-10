# SPDX-License-Identifier: AGPL-3.0-or-later
"""The boot finds the tables the planner's statistics no longer describe and analyzes each again,
so a partial index counted empty when it was made is not walked for ever as if it cost nothing."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from sift.kernel import db as db_module
from sift.kernel.db import Database, stale_tables

pytestmark = pytest.mark.usefixtures("clean_registry")

# A face-like table: most rows have no person, a partial index keeps the named ones.
_TRACKS = (
    "CREATE TABLE track (id INTEGER PRIMARY KEY, asset_id TEXT NOT NULL, person_id TEXT,"
    " seen INTEGER NOT NULL)",
    "CREATE INDEX ix_track_asset ON track (asset_id)",
    "CREATE INDEX ix_track_person ON track (person_id, seen) WHERE person_id IS NOT NULL",
)
_ADD = "INSERT INTO track (asset_id, person_id, seen) VALUES (?, ?, ?)"
_BY_FILE = "SELECT 1 FROM track WHERE person_id IS NOT NULL AND asset_id = ?"


@pytest.fixture
async def database(tmp_path: Path) -> AsyncIterator[Database]:
    database = Database(tmp_path / "stale.sqlite3")
    await database.connect()
    try:
        async with database.write() as connection:
            for statement in _TRACKS:
                await connection.execute(statement)
        yield database
    finally:
        await database.close()


async def _add(database: Database, start: int, count: int, *, named: bool) -> None:
    async with database.write() as connection:
        await connection.executemany(
            _ADD,
            [(f"a{n}", f"p{n % 40}" if named else None, n) for n in range(start, start + count)],
        )


async def _analyzed(database: Database) -> None:
    async with database.write() as connection:
        await connection.execute("ANALYZE")


async def _stale(database: Database) -> list[str]:
    async with database.read() as connection:
        return await stale_tables(connection)


async def _plan(database: Database) -> str:
    plan_of = "EXPLAIN QUERY PLAN " + _BY_FILE  # nosemgrep: sift-no-string-built-sql
    rows = await database.fetch_all(plan_of, ("a1",))  # nosemgrep: sift-no-string-built-sql
    return " ".join(str(row[3]) for row in rows)


@pytest.mark.unit
async def test_an_empty_table_never_analyzed_is_not_stale(database: Database) -> None:
    assert await _stale(database) == []


@pytest.mark.unit
async def test_a_table_never_analyzed_is_stale_once_it_holds_a_row_and_the_boot_counts_it(
    database: Database,
) -> None:
    await _add(database, 0, 10, named=False)
    assert await _stale(database) == ["track"]
    assert await database.refresh_statistics(reason="boot", every_table=True, force=True)
    assert await _stale(database) == []
    told = await database.fetch_all("SELECT stat FROM sqlite_stat1 WHERE idx = 'ix_track_asset'")
    assert [str(row["stat"]).split()[0] for row in told] == ["10"]


@pytest.mark.unit
async def test_a_table_grown_twice_over_is_stale_and_one_grown_by_half_is_not(
    database: Database,
) -> None:
    await _add(database, 0, 1000, named=False)
    await _analyzed(database)
    await _add(database, 1000, 500, named=False)
    assert await _stale(database) == []
    await _add(database, 1500, 600, named=False)
    assert await _stale(database) == ["track"]


@pytest.mark.unit
async def test_a_table_shrunk_twice_over_is_stale(database: Database) -> None:
    await _add(database, 0, 1000, named=False)
    await _analyzed(database)
    async with database.write() as connection:
        await connection.execute("DELETE FROM track WHERE id > 400")
    assert await _stale(database) == ["track"]


@pytest.mark.unit
async def test_a_partial_index_counted_empty_is_stale_only_once_it_holds_a_row(
    database: Database,
) -> None:
    await _add(database, 0, 1000, named=False)
    await _analyzed(database)
    assert await _stale(database) == []
    # Fewer rows than would move the table: only the index's own emptiness can say it.
    await _add(database, 1000, 300, named=True)
    assert await _stale(database) == ["track"]


@pytest.mark.unit
async def test_an_index_made_after_the_last_analyze_makes_its_table_stale(
    database: Database,
) -> None:
    """A catalog step that adds an index leaves it with no statistics row, and the planner then
    seeks through an older index: the table is stale until it is analyzed again."""
    async with database.write() as connection:
        await connection.execute(
            "CREATE TABLE later (id INTEGER PRIMARY KEY, kind TEXT, prio INTEGER)"
        )
        await connection.executemany(
            "INSERT INTO later (kind, prio) VALUES (?, ?)", [("a", n) for n in range(50)]
        )
        await connection.execute("ANALYZE later")
    assert "later" not in await _stale(database)
    async with database.write() as connection:
        await connection.execute("CREATE INDEX ix_later_kind ON later (kind, prio)")
    assert "later" in await _stale(database)
    async with database.write() as connection:
        await connection.execute("ANALYZE later")
    assert "later" not in await _stale(database)


async def test_the_boot_refresh_analyzes_what_is_stale_and_the_plan_follows_the_file(
    database: Database,
) -> None:
    """The case that held every write: an index counted empty is chosen over the file's own."""
    await _add(database, 0, 1000, named=False)
    await _analyzed(database)
    await _add(database, 1000, 300, named=True)
    assert "ix_track_person" in await _plan(database)
    assert await database.refresh_statistics(reason="boot", every_table=True, force=True)
    assert await _stale(database) == []
    assert "ix_track_asset" in await _plan(database)


@pytest.mark.unit
async def test_a_name_no_statement_can_carry_plainly_and_a_count_that_is_not_one_are_passed_by(
    database: Database,
) -> None:
    async with database.write() as connection:
        await connection.execute('CREATE TABLE "odd name" (who TEXT)')
        await connection.execute("CREATE TABLE plain (who TEXT)")
        await connection.execute('CREATE INDEX "odd index" ON plain (who) WHERE who IS NOT NULL')
        await connection.executemany("INSERT INTO plain (who) VALUES (?)", [(None,)] * 10)
    await _analyzed(database)
    async with database.write() as connection:
        await connection.executemany(
            'INSERT INTO "odd name" (who) VALUES (?)', [(f"w{n}",) for n in range(10)]
        )
        await connection.execute("INSERT INTO plain (who) VALUES ('w')")
        await connection.execute(
            "INSERT INTO sqlite_stat1 (tbl, idx, stat) VALUES ('track', NULL, 'unordered')"
        )
    assert await _stale(database) == []


@pytest.mark.unit
async def test_a_refresh_that_fails_leaves_the_boot_standing(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def refused(_connection: object) -> list[str]:
        raise RuntimeError("the disk went away")

    monkeypatch.setattr(db_module, "stale_tables", refused)
    assert await database.refresh_statistics(reason="boot", every_table=True, force=True)
