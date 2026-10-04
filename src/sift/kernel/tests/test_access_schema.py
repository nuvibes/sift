# SPDX-License-Identifier: AGPL-3.0-or-later
"""The boot checks the catalog keeps pass over a database with no files under it.

Each puts back what a rebuild of another component's table can take away, and each is guarded on
that table being there: the schema tests build a catalog with nothing under it, and a check that
named a missing table would stop the boot rather than skip.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sift.kernel.access.schema import _keep_the_filename_index, _keep_the_unindexed_count
from sift.kernel.db import Database

pytestmark = pytest.mark.anyio


async def test_the_boot_checks_leave_a_database_with_no_files_under_it_alone(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "bare.sqlite3")
    await database.connect()
    try:
        async with database.write() as connection:
            await _keep_the_filename_index(connection)
            await _keep_the_unindexed_count(connection)
        made = await database.fetch_all(
            "SELECT name FROM sqlite_master WHERE type IN ('index', 'trigger', 'table')"
        )
        assert made == []
    finally:
        await database.close()


async def test_the_steps_that_add_marks_and_box_columns_give_what_they_promise() -> None:
    """Steps 72, 87 and 88 on the bare tables they reach: each column and index they promise, and
    a second run of 87 and 88 over a library that has them changes nothing. The catalog is at 88
    or past it, so a library that took them under `schema` is not asked to take them again."""
    import aiosqlite  # nosemgrep: sift-no-database-driver-outside-kernel (a test opening the scratch file it made)

    from sift.kernel.access import schema_columns
    from sift.kernel.access.schema import CATALOG_VERSION

    tables = ("people", "sites", "tags", "folders", *schema_columns.FILINGS_BY_A_BOX)
    async with aiosqlite.connect(":memory:") as connection:
        for table in tables:
            await connection.execute(
                f"CREATE TABLE {table} (id TEXT PRIMARY KEY)"  # nosemgrep: sift-no-string-built-sql
            )
        await schema_columns.keep_from_swaps(connection)
        for _ in range(2):
            await schema_columns.name_the_box_on_filings(connection)
            await schema_columns.mark_folders(connection)
        columns = {
            table: {
                row[1]
                for row in await connection.execute_fetchall(
                    f"PRAGMA table_info({table})"  # nosemgrep: sift-no-string-built-sql
                )
            }
            for table in tables
        }
        indexes = {
            row[0]
            for row in await connection.execute_fetchall(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
    assert CATALOG_VERSION >= 88
    for table in ("people", "sites", "tags"):
        assert "keep_from_swaps" in columns[table]
        assert f"ix_{table}_kept_from_swaps" in indexes
    for table in schema_columns.FILINGS_BY_A_BOX:
        assert "box_id" in columns[table]
    assert {"keep_local", "keep_from_swaps"} <= columns["folders"]
    assert {"ix_folders_kept_local", "ix_folders_kept_from_swaps"} <= indexes
