# SPDX-License-Identifier: AGPL-3.0-or-later
"""Putting an app setting into a running application's database.

The same need the library and job seeders answer: a route whose behaviour depends on a setting has
to be called with that setting in a known state, and the way an admin would set it (through the
settings endpoint) needs an admin session a test may not have.

Written straight into the table rather than through the service, deliberately: this is about
arranging the world a route runs in, not about whether the settings feature works. The tests that
are about that use the endpoint.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from sift.kernel.db import Database

_SET_APP = (
    "INSERT INTO app_settings (key, value) VALUES (?, ?) "
    "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
)


def set_app_setting(db_path: Path, key: str, value: str) -> None:
    """Set one instance-wide setting to a JSON-encoded value."""

    async def run() -> None:
        database = Database(db_path, readers=1)
        await database.connect()
        try:
            async with database.write() as connection:
                await connection.execute(_SET_APP, (key, value))
        finally:
            await database.close()

    asyncio.run(run())
