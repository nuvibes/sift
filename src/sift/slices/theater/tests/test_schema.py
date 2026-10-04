# SPDX-License-Identifier: AGPL-3.0-or-later
"""The tables, run twice.

A schema initializer runs on every boot and is handed the version already on disk. Every other test
in the slice exercises the first run, because a test database is always fresh, so the second run,
which is what every existing install actually does, is the one shape that would otherwise never be
tried. An initializer that rebuilt what was there would empty it.

Against a database of this file's own rather than the running application's: the app's connections
belong to its event loop, and a write issued from a test's loop meets a lock held on that one.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from sift.kernel.db import Database
from sift.slices.theater import schema

_A_USER = "01HX0000000000000000000001"
_A_WALL = "01HX0000000000000000000002"


def test_a_second_boot_leaves_the_walls_alone(tmp_path: Path) -> None:
    async def run() -> list[str]:
        database = Database(tmp_path / "theater.db", readers=1)
        await database.connect()
        try:
            async with database.write() as connection:
                # Only the column the arrangements key on. The identity component builds the real
                # one; what matters here is that the reference resolves.
                await connection.execute("CREATE TABLE users (id TEXT PRIMARY KEY)")
                await connection.execute("INSERT INTO users (id) VALUES (?)", (_A_USER,))
                await schema.initialize(connection, on_disk=0)
                await connection.execute(
                    "INSERT INTO theater_arrangements "
                    "(id, user_id, name, layout, created_at, updated_at) VALUES (?, ?, ?, ?, 0, 0)",
                    (_A_WALL, _A_USER, "Front room", "side_by_side"),
                )
                await schema.initialize(connection, on_disk=schema.VERSION)
            rows = await database.fetch_all("SELECT name FROM theater_arrangements")
            return [str(row["name"]) for row in rows]
        finally:
            await database.close()

    assert asyncio.run(run()) == ["Front room"]
