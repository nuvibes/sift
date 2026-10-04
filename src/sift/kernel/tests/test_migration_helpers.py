# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a schema step asks of the library it finds, and the one edit it may make in place."""

from __future__ import annotations

import pytest

from sift.kernel.db import Database
from sift.kernel.migrations import (
    MigrationError,
    check_allows,
    column_exists,
    rebuild_in_place,
    widen_a_check,
)

pytestmark = pytest.mark.anyio

_PARENT_AND_CHILD = (
    "CREATE TABLE jobs (id TEXT PRIMARY KEY,"
    " parent_id TEXT REFERENCES jobs(id) ON DELETE CASCADE,"
    " state TEXT NOT NULL CHECK(state IN ('queued','done')))",
    "CREATE TABLE downloads (id TEXT PRIMARY KEY,"
    " job_id TEXT REFERENCES jobs(id) ON DELETE SET NULL)",
    "INSERT INTO jobs (id, parent_id, state) VALUES ('parent', NULL, 'queued')",
    "INSERT INTO jobs (id, parent_id, state) VALUES ('child', 'parent', 'queued')",
    "INSERT INTO downloads (id, job_id) VALUES ('d', 'child')",
)


async def test_a_check_is_widened_without_a_row_moving(temp_db: Database) -> None:
    """The table is a parent of itself and of another, so a rebuild would take rows with it."""
    async with temp_db.write() as connection:
        for statement in _PARENT_AND_CHILD:
            await connection.execute(statement)
        assert not await check_allows(connection, "jobs", "paused")

        await widen_a_check(
            connection, "jobs", was="'queued','done')", now="'queued','done','paused')"
        )

        assert await check_allows(connection, "jobs", "paused")
        await connection.execute("UPDATE jobs SET state = 'paused' WHERE id = 'child'")
        jobs = await connection.execute_fetchall("SELECT id FROM jobs ORDER BY id")
        downloads = await connection.execute_fetchall("SELECT job_id FROM downloads")
    assert [row[0] for row in jobs] == ["child", "parent"]
    assert [row[0] for row in downloads] == ["child"]
    with pytest.raises(Exception, match="CHECK"):
        await temp_db.execute("UPDATE jobs SET state = 'lost' WHERE id = 'parent'")


async def test_a_definition_it_does_not_recognise_is_refused_and_left_alone(
    temp_db: Database,
) -> None:
    """The fragment has to be there exactly once, or nothing is changed."""
    async with temp_db.write() as connection:
        await connection.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY, state TEXT)")
        with pytest.raises(MigrationError, match="not the shape"):
            await widen_a_check(connection, "jobs", was="'queued','done')", now="'paused')")
        (stored,) = await connection.execute_fetchall(
            "SELECT sql FROM sqlite_master WHERE name = 'jobs'"
        )
    assert "CHECK" not in str(stored[0])


async def test_a_table_that_is_not_there_allows_nothing(temp_db: Database) -> None:
    async with temp_db.write() as connection:
        assert not await check_allows(connection, "nowhere", "anything")


async def test_a_column_is_asked_for_by_name_and_an_absent_table_has_none(
    temp_db: Database,
) -> None:
    async with temp_db.write() as connection:
        await connection.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY, state TEXT)")
        assert await column_exists(connection, "jobs", "state")
        assert not await column_exists(connection, "jobs", "paused_at")
        assert not await column_exists(connection, "nowhere", "state")


#: A table another table's trigger names in its body, which is what makes the rebuild fail by
#: default: between the drop and the rename the name does not exist, and a rename reparses every
#: trigger in the schema.
_NAMED_BY_A_TRIGGER = (
    "CREATE TABLE grants (id TEXT PRIMARY KEY, kind TEXT CHECK(kind IN ('a')))",
    "CREATE TABLE folders (id TEXT PRIMARY KEY)",
    "CREATE TRIGGER folders_moved AFTER UPDATE ON folders BEGIN"
    " DELETE FROM grants WHERE id = old.id; END",
    "INSERT INTO grants (id, kind) VALUES ('g', 'a')",
)

_WIDER_GRANTS = (
    "CREATE TABLE grants_rebuilt (id TEXT PRIMARY KEY, kind TEXT CHECK(kind IN ('a','b')))",
    "INSERT INTO grants_rebuilt (id, kind) SELECT id, kind FROM grants",
    "DROP TABLE grants",
    "ALTER TABLE grants_rebuilt RENAME TO grants",
)


async def _legacy_alter_table(temp_db: Database) -> int:
    async with temp_db.write() as connection:
        (row,) = await connection.execute_fetchall("PRAGMA legacy_alter_table", ())
    return int(row[0])


async def test_a_table_another_trigger_names_is_rebuilt_and_the_trigger_still_reads_it(
    temp_db: Database,
) -> None:
    async with temp_db.write() as connection:
        for statement in _NAMED_BY_A_TRIGGER:
            await connection.execute(statement)

        await rebuild_in_place(connection, _WIDER_GRANTS)

        await connection.execute("INSERT INTO grants (id, kind) VALUES ('h', 'b')")
        kept = await connection.execute_fetchall("SELECT id FROM grants ORDER BY id")
    assert [row[0] for row in kept] == ["g", "h"]
    assert await _legacy_alter_table(temp_db) == 0, "the rename rule was left changed"


async def test_a_rebuild_that_fails_puts_the_rename_rule_back(temp_db: Database) -> None:
    """Every step runs on the one writer connection, so a flag left on would change every later
    rename's meaning."""
    with pytest.raises(Exception, match="no such table"):
        async with temp_db.write() as connection:
            await rebuild_in_place(connection, ("DROP TABLE nowhere",))
    assert await _legacy_alter_table(temp_db) == 0
