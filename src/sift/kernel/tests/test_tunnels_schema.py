# SPDX-License-Identifier: AGPL-3.0-or-later
"""The tunnels' tables, as the kernel's: what a new library gets, and that one at this version is
left alone.
"""

from __future__ import annotations

from sift.kernel.db import Database
from sift.kernel.tunnels import schema

_NOW_SHAPE = ["id", "name", "secret_id", "enabled", "created_at", "updated_at", "can_host"]


async def _columns(database: Database, table: str) -> list[str]:
    rows = await database.fetch_all("SELECT name FROM pragma_table_info(?) ORDER BY cid", (table,))
    return [str(row["name"]) for row in rows]


async def _names(database: Database, kind: str) -> set[str]:
    rows = await database.fetch_all("SELECT name FROM sqlite_master WHERE type = ?", (kind,))
    return {str(row["name"]) for row in rows}


async def test_a_new_library_gets_the_tables_in_todays_shape(temp_db: Database) -> None:
    await temp_db.initialize_schema()

    assert await _columns(temp_db, "tunnels") == _NOW_SHAPE
    assert await _columns(temp_db, "tunnel_routes") == ["scope", "route", "updated_at"]
    assert "download_routes" not in await _names(temp_db, "table")
    indexes = await _names(temp_db, "index")
    assert "ux_tunnels_name" in indexes and "ux_tunnels_port" not in indexes
    assert await temp_db.schema_version(schema.COMPONENT) == schema.VERSION == 1


async def test_a_library_already_at_this_version_is_left_exactly_as_it_is(
    temp_db: Database,
) -> None:
    """Called directly, because the kernel would not call it at all in this case: a step told the
    library is already at its version creates nothing, so a later version's shape is never
    overwritten by this one's. A table missing on purpose stays missing."""
    await temp_db.initialize_schema()
    await temp_db.execute("DROP TABLE tunnel_routes")

    async with temp_db.write() as connection:
        await schema.initialize(connection, on_disk=schema.VERSION)

    assert "tunnel_routes" not in await _names(temp_db, "table")
