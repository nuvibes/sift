# SPDX-License-Identifier: AGPL-3.0-or-later
"""The one table this slice owns, and the step that creates it.

A migration's "already current" arm is the arm that runs on every install after the first, and it
is the one nothing exercises: the interesting half is creating the table, so that is what gets a
test and the other half is left as a branch nobody has taken. It matters here for the ordinary
reason a re-run matters: `CREATE TABLE` without `IF NOT EXISTS` is an error the second time, and
an upgrade that raises on a database it has already upgraded will not open.
"""

from __future__ import annotations

import pytest

from sift.kernel.db import Database
from sift.slices.importing.schema import VERSION, initialize

pytestmark = pytest.mark.anyio


async def _tables(db: Database) -> set[str]:
    rows = await db.fetch_all("SELECT name FROM sqlite_master WHERE type = 'table'")
    return {str(row["name"]) for row in rows}


async def test_a_fresh_database_is_given_the_table(temp_db: Database) -> None:
    """The KNOWN POSITIVE. Without it the no-op below would pass against a step that does nothing
    at all, on a database where the table happened to exist already."""
    async with temp_db.write() as connection:
        await connection.execute("DROP TABLE IF EXISTS root_import_prefs")
        assert "root_import_prefs" not in await _tables(temp_db)

        await initialize(connection, on_disk=0)

    assert "root_import_prefs" in await _tables(temp_db)


async def test_a_library_already_holding_the_table_keeps_what_is_in_it(temp_db: Database) -> None:
    """The arm every boot after the first takes, with a row in the table to show it was kept."""
    async with temp_db.write() as connection:
        await connection.execute("CREATE TABLE library_roots (id TEXT PRIMARY KEY)")
        await connection.execute("INSERT INTO library_roots (id) VALUES ('r1')")
        await initialize(connection, on_disk=0)
        await connection.execute(
            "INSERT INTO root_import_prefs (root_id, key, value, updated_at)"
            " VALUES ('r1', 'k', 'v', 0)"
        )

        await initialize(connection, on_disk=VERSION)

    rows = await temp_db.fetch_all("SELECT value FROM root_import_prefs")
    assert [str(row["value"]) for row in rows] == ["v"]
