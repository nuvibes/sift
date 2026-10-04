# SPDX-License-Identifier: AGPL-3.0-or-later
"""The v13 step: the backup's How often becomes a count of days (1, 7), and Off becomes a When
of "Only when I press it"; the old key stays answerable (see `retire_setting`)."""

from __future__ import annotations

import pytest

from sift.kernel.db import Database
from sift.slices.settings_hub.schema import initialize_settings

pytestmark = [pytest.mark.integration]

#: The version this step upgrades from, written out: it describes a database in the world.
BEFORE = 12

#: Written out, never imported: the step must not move with the code.
OLD = "backup.schedule"
DAYS = "backup.every_days"
WHEN = "tasks.backup.when"
#: A neighbour on the same table, which the step must not touch.
KEPT = "backup.keep"
#: The rows this step can write or must leave alone. A later step that seeds a key of its own
#: runs in the same upgrade and is that step's test to read, not this one's.
READ = (OLD, DAYS, WHEN, KEPT)


async def _made(database: Database, rows: tuple[tuple[str, str], ...]) -> None:
    async with database.write() as connection:
        await connection.execute("CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY)")
        await initialize_settings(connection, on_disk=0)
        for key, value in rows:
            await connection.execute(
                "INSERT INTO app_settings (key, value) VALUES (?, ?)", (key, value)
            )


async def _stepped(database: Database, on_disk: int = BEFORE) -> dict[str, str]:
    async with database.write() as connection:
        await initialize_settings(connection, on_disk=on_disk)
    rows = await database.fetch_all("SELECT key, value FROM app_settings")
    return {str(row["key"]): str(row["value"]) for row in rows if row["key"] in READ}


async def test_daily_is_carried_as_one_day(temp_db: Database) -> None:
    await _made(temp_db, ((OLD, '"daily"'), (WHEN, '"work"'), (KEPT, "3")))
    assert await _stepped(temp_db) == {DAYS: "1", WHEN: '"work"', KEPT: "3"}


async def test_weekly_is_carried_as_seven_days(temp_db: Database) -> None:
    await _made(temp_db, ((OLD, '"weekly"'), (WHEN, '"quiet"')))
    assert await _stepped(temp_db) == {DAYS: "7", WHEN: '"quiet"'}


async def test_off_makes_the_backup_wait_for_a_press(temp_db: Database) -> None:
    """Off under a When that started it on its own took no backup; after the step it still takes
    none, because the When now says so."""
    await _made(temp_db, ((OLD, '"off"'), (WHEN, '"work"')))
    assert await _stepped(temp_db) == {WHEN: '"press"'}


async def test_off_with_no_when_stored_stores_the_press(temp_db: Database) -> None:
    """Stored rather than left to the default, so a later default cannot start it on its own."""
    await _made(temp_db, ((OLD, '"off"'),))
    assert await _stepped(temp_db) == {WHEN: '"press"'}


async def test_a_count_already_under_the_new_key_is_kept(temp_db: Database) -> None:
    await _made(temp_db, ((OLD, '"daily"'), (DAYS, "3")))
    assert await _stepped(temp_db) == {DAYS: "3"}


async def test_a_library_already_past_the_step_is_left_alone(temp_db: Database) -> None:
    await _made(temp_db, ((OLD, '"weekly"'),))
    assert await _stepped(temp_db, BEFORE + 1) == {OLD: '"weekly"'}
