# SPDX-License-Identifier: AGPL-3.0-or-later
"""The v12 step: the GIF format's stored key moves from its older word to `edit.gif_format`.

The value somebody chose is carried across rather than reset, the old row goes, and every other
row is left alone. The old key stays answerable through the new one (see `retire_setting`).
"""

from __future__ import annotations

import pytest

from sift.kernel.db import Database
from sift.slices.settings_hub.schema import initialize_settings

pytestmark = [pytest.mark.integration]

#: The version this step upgrades from, written out: it describes a database in the world.
BEFORE = 11

#: Written out, never imported: the step must not move with the code.
OLD = "edit.animation_format"
NEW = "edit.gif_format"
#: A neighbour on the same table, which the step must not touch.
KEPT = "compress.target_small_mb"


async def _made(database: Database, rows: tuple[tuple[str, str], ...]) -> None:
    async with database.write() as connection:
        await connection.execute("CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY)")
        await initialize_settings(connection, on_disk=0)
        for key, value in rows:
            await connection.execute(
                "INSERT INTO app_settings (key, value) VALUES (?, ?)", (key, value)
            )


async def _stored(database: Database) -> dict[str, str]:
    """The rows this step concerns. Later steps run in the same call and write rows of their own
    (a library from before them gains their values), which are theirs to test, not this step's."""
    rows = await database.fetch_all("SELECT key, value FROM app_settings")
    return {str(row["key"]): str(row["value"]) for row in rows if row["key"] in (OLD, NEW, KEPT)}


async def test_the_chosen_format_is_carried_to_the_new_key(temp_db: Database) -> None:
    await _made(temp_db, ((OLD, '"webp"'), (KEPT, "12")))
    async with temp_db.write() as connection:
        await initialize_settings(connection, on_disk=BEFORE)
    assert await _stored(temp_db) == {NEW: '"webp"', KEPT: "12"}


async def test_a_value_already_under_the_new_key_is_kept(temp_db: Database) -> None:
    await _made(temp_db, ((OLD, '"webp"'), (NEW, '"gif"')))
    async with temp_db.write() as connection:
        await initialize_settings(connection, on_disk=BEFORE)
    assert await _stored(temp_db) == {NEW: '"gif"'}


async def test_a_library_already_past_the_step_is_left_alone(temp_db: Database) -> None:
    await _made(temp_db, ((OLD, '"webp"'),))
    async with temp_db.write() as connection:
        await initialize_settings(connection, on_disk=BEFORE + 1)
    assert await _stored(temp_db) == {OLD: '"webp"'}
