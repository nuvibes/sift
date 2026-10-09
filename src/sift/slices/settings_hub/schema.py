# SPDX-License-Identifier: AGPL-3.0-or-later
"""The tables the settings feature stores values in: overrides of the default only, as JSON.

`user_settings` is each user's own, `app_settings` the instance's (admin only), and
`interface_state` where somebody left things on a screen, which is not a setting.
"""

from __future__ import annotations

from sift.kernel.db import Connection, register_schema_initializer
from sift.kernel.version import app_version

SETTINGS_COMPONENT = "settings"
SETTINGS_VERSION = 18

_CREATE_USER_SETTINGS = """
CREATE TABLE IF NOT EXISTS user_settings (
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  key     TEXT NOT NULL,
  value   TEXT NOT NULL,
  PRIMARY KEY(user_id, key)
)
"""

_CREATE_APP_SETTINGS = """
CREATE TABLE IF NOT EXISTS app_settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
)
"""


_CREATE_INTERFACE_STATE = """
CREATE TABLE IF NOT EXISTS interface_state (
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  key     TEXT NOT NULL,
  value   TEXT NOT NULL,
  PRIMARY KEY(user_id, key)
)
"""


#: Version 12: stored keys renamed, old spelling first. Steps are written out, never imported:
#: a step describes a database in the world and must not move with the code.
_RENAMED_KEYS_12 = (("edit.animation_format", "edit.gif_format"),)
_MOVE_APP_KEY = "UPDATE OR IGNORE app_settings SET key = ? WHERE key = ?"
_MOVE_USER_KEY = "UPDATE OR IGNORE user_settings SET key = ? WHERE key = ?"
_DROP_APP_KEY = "DELETE FROM app_settings WHERE key = ?"
_DROP_USER_KEY = "DELETE FROM user_settings WHERE key = ?"

#: Version 13: the backup's "How often" word becomes a count of days, and Off a pressed-only When.
_CARRY_BACKUP_DAYS_13 = (
    "INSERT OR IGNORE INTO app_settings (key, value) "
    "SELECT 'backup.every_days', ? FROM app_settings "
    "WHERE key = 'backup.schedule' AND value = ?"
)
_BACKUP_DAYS_13 = (('"daily"', "1"), ('"weekly"', "7"))
_BACKUP_OFF_IS_PRESS_13 = (
    "INSERT INTO app_settings (key, value) "
    "SELECT 'tasks.backup.when', '\"press\"' FROM app_settings "
    "WHERE key = 'backup.schedule' AND value = '\"off\"' "
    "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
)
_DROP_BACKUP_SCHEDULE_13 = "DELETE FROM app_settings WHERE key = 'backup.schedule'"

#: Version 14: each stored font family name carried to the face it meant on that key.
_CARRY_FACE_14 = "UPDATE user_settings SET value = ? WHERE key = ? AND value = ?"
_FACES_14 = (
    ("appearance.theme_face_display", '"grotesk"', '"space-grotesk"'),
    ("appearance.theme_face_display", '"geist"', '"geist-mono"'),
    ("appearance.theme_face_body", '"archivo"', '"instrument-sans"'),
    ("appearance.theme_face_body", '"grotesk"', '"inter"'),
    ("appearance.theme_face_body", '"manrope"', '"public-sans"'),
)

#: Version 15: a custom colour chosen before becomes the first kept one (never the starting blue).
_KEEP_CUSTOM_COLOUR_15 = (
    "INSERT OR IGNORE INTO user_settings (user_id, key, value) "
    "SELECT user_id, 'appearance.theme_accent_swatches', '[' || value || ']' FROM user_settings "
    "WHERE key = 'appearance.theme_accent_hex' AND value <> '\"#2563eb\"' "
    "AND value GLOB '\"#[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]\"'"
)

#: Version 16: an existing library keeps its backups however old ("never"); a new one takes seven.
_KEEP_BACKUPS_HOWEVER_OLD_16 = (
    "INSERT OR IGNORE INTO app_settings (key, value) VALUES ('backup.keep_days', '0')"
)

#: Version 17: keys removed with nothing in their place, so no value lingers that nothing reads.
_REMOVED_KEYS_17 = ("download.photo_sets",)

#: Version 18: the release this library last started as; a new library is given the running one.
_CREATE_VERSION_BOOTED = """
CREATE TABLE IF NOT EXISTS version_booted (
  id      INTEGER PRIMARY KEY CHECK (id = 1),
  version TEXT NOT NULL
)
"""
_A_NEW_LIBRARY_BOOTED = "INSERT OR IGNORE INTO version_booted (id, version) VALUES (1, ?)"


async def initialize_settings(connection: Connection, on_disk: int) -> None:
    if on_disk < 1:
        await connection.execute(_CREATE_USER_SETTINGS)
        await connection.execute(_CREATE_APP_SETTINGS)
        await connection.execute(_CREATE_INTERFACE_STATE)
    if 0 < on_disk < 12:
        await _rename_keys_12(connection)
    if 0 < on_disk < 13:
        await _carry_backup_days_13(connection)
    if 0 < on_disk < 14:
        await _carry_faces_14(connection)
    if 0 < on_disk < 15:
        await connection.execute(_KEEP_CUSTOM_COLOUR_15)
    if 0 < on_disk < 16:
        await connection.execute(_KEEP_BACKUPS_HOWEVER_OLD_16)
    if 0 < on_disk < 17:
        await _drop_removed_keys_17(connection)
    if on_disk < 18:
        await connection.execute(_CREATE_VERSION_BOOTED)
    if on_disk < 1 and app_version():
        await connection.execute(_A_NEW_LIBRARY_BOOTED, (app_version(),))


async def _rename_keys_12(connection: Connection) -> None:
    for old, new in _RENAMED_KEYS_12:
        # The new spelling wins where both are stored, and the old row goes.
        await connection.execute(_MOVE_APP_KEY, (new, old))
        await connection.execute(_MOVE_USER_KEY, (new, old))
        await connection.execute(_DROP_APP_KEY, (old,))
        await connection.execute(_DROP_USER_KEY, (old,))


async def _carry_backup_days_13(connection: Connection) -> None:
    for word, days in _BACKUP_DAYS_13:
        await connection.execute(_CARRY_BACKUP_DAYS_13, (days, word))
    await connection.execute(_BACKUP_OFF_IS_PRESS_13)
    await connection.execute(_DROP_BACKUP_SCHEDULE_13)


async def _carry_faces_14(connection: Connection) -> None:
    for key, family, face in _FACES_14:
        await connection.execute(_CARRY_FACE_14, (face, key, family))


async def _drop_removed_keys_17(connection: Connection) -> None:
    for key in _REMOVED_KEYS_17:
        await connection.execute(_DROP_APP_KEY, (key,))
        await connection.execute(_DROP_USER_KEY, (key,))


# `user_settings.user_id` references `users`, which the kernel's identity component creates.
register_schema_initializer(
    SETTINGS_COMPONENT,
    SETTINGS_VERSION,
    initialize_settings,
    depends_on=["identity"],
    baseline=11,
)
