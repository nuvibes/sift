# SPDX-License-Identifier: AGPL-3.0-or-later
"""The v16 step: a library that already existed keeps its automatic backups however old they are.

A new library deletes automatic backups older than seven days. A library made before that rule kept
the newest few and nothing else, and an upgrade must not start deleting its files: it gets "never"
(zero) written, which the Backup pane shows and anybody can change. A new library has no row.
"""

from __future__ import annotations

import pytest

from sift.kernel.db import Database
from sift.slices.settings_hub.schema import initialize_settings

pytestmark = [pytest.mark.integration]

#: The version this step upgrades from, written out: it describes a database in the world.
BEFORE = 15

#: Written out, never imported: the step must not move with the code.
KEY = "backup.keep_days"


async def _made(database: Database, stored: dict[str, str]) -> None:
    async with database.write() as connection:
        await connection.execute("CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY)")
        await initialize_settings(connection, on_disk=0)
        for key, value in stored.items():
            await connection.execute(
                "INSERT INTO app_settings (key, value) VALUES (?, ?)", (key, value)
            )


async def _stepped(database: Database, on_disk: int = BEFORE) -> dict[str, str]:
    async with database.write() as connection:
        await initialize_settings(connection, on_disk=on_disk)
    rows = await database.fetch_all("SELECT key, value FROM app_settings")
    return {str(row["key"]): str(row["value"]) for row in rows}


async def test_a_library_that_already_existed_keeps_its_backups_however_old(
    temp_db: Database,
) -> None:
    await _made(temp_db, {"backup.keep": "7"})
    assert await _stepped(temp_db) == {"backup.keep": "7", KEY: "0"}


async def test_a_number_of_days_already_chosen_is_left_alone(temp_db: Database) -> None:
    await _made(temp_db, {KEY: "14"})
    assert (await _stepped(temp_db))[KEY] == "14"


async def test_a_new_library_stores_nothing_and_takes_the_default(temp_db: Database) -> None:
    await _made(temp_db, {})
    assert KEY not in await _stepped(temp_db, on_disk=0)


async def test_a_library_already_past_the_step_is_left_alone(temp_db: Database) -> None:
    await _made(temp_db, {})
    assert KEY not in await _stepped(temp_db, BEFORE + 1)
