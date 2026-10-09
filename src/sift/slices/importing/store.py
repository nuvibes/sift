# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading and writing what a folder answers differently."""

from __future__ import annotations

import json
import time
from collections.abc import Iterable, Mapping

from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, telling
from sift.kernel.db import Database
from sift.kernel.wiring import Part

ROOT_PREFS: Part[RootPreferences] = Part("importing_root_prefs")

_FOR_ROOT = "SELECT key, value FROM root_import_prefs WHERE root_id = ?"

_FOR_ROOTS = """
SELECT root_id, key, value FROM root_import_prefs
 WHERE root_id IN (SELECT value FROM json_each(?))
"""

_SET = """
INSERT INTO root_import_prefs (root_id, key, value, updated_at)
VALUES (?, ?, ?, ?)
ON CONFLICT(root_id, key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
"""

_CLEAR = "DELETE FROM root_import_prefs WHERE root_id = ? AND key = ?"

#: Read whole and judged in Python: "false" is only one way a JSON answer can be falsy.
_ANSWERS_TO = "SELECT root_id, value FROM root_import_prefs WHERE key = ?"

_CLEAR_ALL = "DELETE FROM root_import_prefs WHERE root_id = ?"


class RootPreferences:
    """What each library root says, where it says anything; values are JSON, so a bool stays one."""

    def __init__(self, database: Database) -> None:
        self._db = database

    async def for_root(self, root_id: str) -> dict[str, object]:
        """Every override this folder carries. Empty when it follows the library."""
        rows = await self._db.fetch_all(_FOR_ROOT, (root_id,))
        return {str(row["key"]): json.loads(str(row["value"])) for row in rows}

    async def for_roots(self, root_ids: Iterable[str]) -> dict[str, dict[str, object]]:
        """What each of these folders answers differently, `{}` for one that follows the library."""
        wanted = list(dict.fromkeys(root_ids))
        if not wanted:
            return {}
        rows = await self._db.fetch_all(_FOR_ROOTS, (json.dumps(wanted),))
        found: dict[str, dict[str, object]] = {str(root_id): {} for root_id in wanted}
        for row in rows:
            found.setdefault(str(row["root_id"]), {})[str(row["key"])] = json.loads(
                str(row["value"])
            )
        return found

    async def refusing(self, key: str) -> list[str]:
        """The folders that answer this switch NO, overriding the library. Usually none."""
        rows = await self._db.fetch_all(_ANSWERS_TO, (key,))
        return [str(row["root_id"]) for row in rows if not json.loads(str(row["value"]))]

    async def set(self, root_id: str, values: Mapping[str, object | None]) -> None:
        """Write this folder's answers; None removes the override, so the folder keeps following."""
        now = int(time.time())
        # A folder's answers are settings, drawn under Import tasks for every admin.
        async with telling(self._db, EVERY_ADMIN, About.SETTINGS) as connection:
            for key, value in values.items():
                if value is None:
                    await connection.execute(_CLEAR, (root_id, key))
                else:
                    await connection.execute(_SET, (root_id, key, json.dumps(value), now))

    async def clear(self, root_id: str) -> None:
        """Put this folder back to following the library, whatever it was saying."""
        async with telling(self._db, EVERY_ADMIN, About.SETTINGS) as connection:
            await connection.execute(_CLEAR_ALL, (root_id,))
