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

#: Every folder's stored answer to one switch. Read whole and judged in Python, because the value
#: is JSON text and "false" is only one way a stored answer can be falsy.
_ANSWERS_TO = "SELECT root_id, value FROM root_import_prefs WHERE key = ?"

_CLEAR_ALL = "DELETE FROM root_import_prefs WHERE root_id = ?"


class RootPreferences:
    """What each library root says, where it says anything at all.

    Values are stored as JSON text for the reason `app_settings` stores them that way: a switch is
    a boolean, and JSON keeps it one through the round trip rather than handing back a string that
    every reader would have to parse the same way and one of them eventually would not.
    """

    def __init__(self, database: Database) -> None:
        self._db = database

    async def for_root(self, root_id: str) -> dict[str, object]:
        """Every override this folder carries. Empty when it follows the library."""
        rows = await self._db.fetch_all(_FOR_ROOT, (root_id,))
        return {str(row["key"]): json.loads(str(row["value"])) for row in rows}

    async def for_roots(self, root_ids: Iterable[str]) -> dict[str, dict[str, object]]:
        """What each of these folders answers differently, keyed by root, one entry per folder
        asked about and in the order they were asked.

        One statement rather than one per root: this is asked on the way in for every file, and a
        file that sits in three places would otherwise be three round trips before any work starts.

        **A folder that follows the library is here with an empty mapping, not missing.** Handing
        the rows back as they come out of the table would make the answer's key set "the folders
        carrying an override" while it is read as "the folders you asked about", and a caller could
        not tell a folder that follows the library from one it had never named. `for_root` answers
        `{}` for a folder with nothing stored, which is the same question about one folder, and
        these two agree.
        """
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
        """Write this folder's answers. A value of None removes the override.

        Removing rather than storing a copy of the default is what keeps a folder following the
        library: a stored `true` and an absent row look the same today and stop looking the same
        the moment somebody changes the setting the folder was meant to be following.
        """
        now = int(time.time())
        # A folder's answers are settings, drawn on `Settings > Importing` for every admin.
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
