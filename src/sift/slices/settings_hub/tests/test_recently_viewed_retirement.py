# SPDX-License-Identifier: AGPL-3.0-or-later
"""The v8 step: "Show recently viewed" is retired, and its stored answers go with it.

The strip it governed was drawn on no screen anybody could open, so no answer to it was ever seen
to take effect and there is nothing to carry across. A row that no declaration matches is the drift
the registry exists to prevent, so the step forgets it, and leaves every other row alone.
"""

from __future__ import annotations

import pytest

from sift.kernel.db import Database
from sift.kernel.settings_registry import get_registered
from sift.slices.settings_hub.schema import initialize_settings

pytestmark = [pytest.mark.integration]

#: The version this step upgrades from, written out: it describes a database in the world.
BEFORE = 7

#: Written out, never imported: the code no longer names it anywhere.
RETIRED = "appearance.show_recently_viewed"
#: A neighbour on the same table, which the step must not touch.
KEPT = "appearance.units"


async def _made(database: Database) -> None:
    async with database.write() as connection:
        await connection.execute("CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY)")
        await connection.execute("INSERT INTO users (id) VALUES ('u1')")
        await initialize_settings(connection, on_disk=0)
        for key, value in ((RETIRED, "false"), (KEPT, '"imperial"')):
            await connection.execute(
                "INSERT INTO user_settings (user_id, key, value) VALUES ('u1', ?, ?)", (key, value)
            )


async def _keys(database: Database) -> set[str]:
    rows = await database.fetch_all("SELECT key FROM user_settings")
    return {str(row["key"]) for row in rows}


def test_the_retired_key_is_declared_nowhere() -> None:
    import sift.main  # noqa: F401 (settings register when their slice is imported)

    assert get_registered(KEPT) is not None, "the registry was not loaded, so this proves nothing"
    assert get_registered(RETIRED) is None
