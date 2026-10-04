# SPDX-License-Identifier: AGPL-3.0-or-later
"""The v17 step: a stored value for a key removed with nothing in its place goes.

A downloaded gallery lands as files in its folder, so the switch that grouped one into a Photo Set
was removed; its stored answer, the instance's or anybody's own, decides nothing and is dropped.
Every other stored value stays as it was.
"""

from __future__ import annotations

import pytest

from sift.kernel.db import Database
from sift.slices.settings_hub.schema import initialize_settings

pytestmark = [pytest.mark.integration]

#: The version this step upgrades from, written out: it describes a database in the world.
BEFORE = 16

#: Written out, never imported: the step must not move with the code.
REMOVED = "download.photo_sets"
KEPT = "download.remember"


async def _made(database: Database) -> None:
    async with database.write() as connection:
        await connection.execute("CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY)")
        await connection.execute("INSERT INTO users (id) VALUES ('u1')")
        await initialize_settings(connection, on_disk=0)
        for key in (REMOVED, KEPT):
            await connection.execute(
                "INSERT INTO app_settings (key, value) VALUES (?, 'true')", (key,)
            )
            await connection.execute(
                "INSERT INTO user_settings (user_id, key, value) VALUES ('u1', ?, 'true')", (key,)
            )


async def _stored(database: Database) -> tuple[set[str], set[str]]:
    app = await database.fetch_all("SELECT key FROM app_settings")
    user = await database.fetch_all("SELECT key FROM user_settings")
    return {str(row["key"]) for row in app}, {str(row["key"]) for row in user}


async def test_a_removed_keys_stored_values_go_and_the_rest_stay(temp_db: Database) -> None:
    await _made(temp_db)
    async with temp_db.write() as connection:
        await initialize_settings(connection, on_disk=BEFORE)
    assert await _stored(temp_db) == ({KEPT}, {KEPT})


async def test_a_library_already_past_the_step_is_left_alone(temp_db: Database) -> None:
    await _made(temp_db)
    async with temp_db.write() as connection:
        await initialize_settings(connection, on_disk=BEFORE + 1)
    assert await _stored(temp_db) == ({REMOVED, KEPT}, {REMOVED, KEPT})
