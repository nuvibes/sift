# SPDX-License-Identifier: AGPL-3.0-or-later
"""The defaults an update changed, said once on History at the first start of the release.

A changed default reaches everybody who never chose a value, silently, so the first start of a
newer release writes one line per setting whose `default_since` is newer and that somebody has not
chosen: "The update to 0.1.218 changed Show every field on a record to on". No Undo: a default is
not a decision, and the setting's own control changes it back.
"""

from __future__ import annotations

import json

from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, telling
from sift.kernel.db import Connection, Database
from sift.kernel.ledger import Actor, record_event
from sift.kernel.log import get_logger
from sift.kernel.settings_registry import Scope, Setting, registered_settings
from sift.kernel.version import release_of
from sift.kernel.vocabulary import UPDATE_TO, VIA_UPDATE, Subject

log = get_logger(__name__)

#: What a library with no recorded start read as: the release before the first one to record it.
BEFORE_THE_RECORD = "0.1.217"

_BOOTED = "SELECT version FROM version_booted WHERE id = 1"
_KEEP_BOOTED = (
    "INSERT INTO version_booted (id, version) VALUES (1, ?) "
    "ON CONFLICT(id) DO UPDATE SET version = excluded.version"
)
_APP_CHOSEN = "SELECT 1 FROM app_settings WHERE key = ?"
#: Somebody who has not chosen: a person with no row for the key.
_SOMEBODY_UNCHOSEN = (
    "SELECT 1 FROM users u WHERE NOT EXISTS "
    "(SELECT 1 FROM user_settings s WHERE s.user_id = u.id AND s.key = ?) LIMIT 1"
)


async def tell_changed_defaults(database: Database, running: str) -> int:
    """One History line per default changed since this library last started, then `running`
    kept as its start; answers how many. Safe twice. An unreadable version writes nothing."""
    now = release_of(running)
    if now is None:
        return 0
    async with telling(database, EVERY_ADMIN, About.SETTINGS) as connection:
        cursor = await connection.execute(_BOOTED)
        row = await cursor.fetchone()
        await cursor.close()
        before = release_of(str(row["version"]) if row is not None else BEFORE_THE_RECORD)
        told: list[str] = []
        if before is not None and before < now:
            for setting in _changed_between(before, now):
                if await _chosen_by_all(connection, setting):
                    continue
                await record_event(
                    connection,
                    actor=Actor.sift(VIA_UPDATE),
                    verb="edited",
                    subject=Subject(kind="setting", id=setting.key, name=setting.label),
                    payload=json.dumps(
                        {
                            "key": setting.key,
                            "after": json.dumps(setting.default),
                            UPDATE_TO: setting.default_since,
                        }
                    ),
                )
                told.append(setting.key)
        await connection.execute(_KEEP_BOOTED, (running.strip(),))
    if told:
        log.info("settings.defaults_told", version=running, keys=sorted(told), lines=len(told))
    return len(told)


def _changed_between(before: tuple[int, int, int], now: tuple[int, int, int]) -> list[Setting]:
    """The settings whose default changed after `before`, up to `now`, oldest change first."""
    found: list[tuple[tuple[int, int, int], str, Setting]] = []
    for setting in registered_settings().values():
        since = release_of(setting.default_since) if setting.default_since else None
        if since is not None and before < since <= now:
            found.append((since, setting.key, setting))
    return [setting for _, _, setting in sorted(found, key=lambda one: (one[0], one[1]))]


async def _chosen_by_all(connection: Connection, setting: Setting) -> bool:
    """Whether the default reaches nobody: a shared value stored, or a value stored by everyone."""
    query = _APP_CHOSEN if setting.scope is Scope.APP else _SOMEBODY_UNCHOSEN
    cursor = await connection.execute(query, (setting.key,))
    found = await cursor.fetchone()
    await cursor.close()
    return found is not None if setting.scope is Scope.APP else found is None
