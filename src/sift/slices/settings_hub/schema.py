# SPDX-License-Identifier: AGPL-3.0-or-later
"""The tables the settings feature stores values in.

A value is stored only where it differs from the default. An absent row means "still the default",
so the tables hold overrides and nothing else: resetting a setting is a delete, and a fresh
user carries no rows at all.

`user_settings` is keyed by user and setting, so every user's value is their own and one
person's change cannot move another's. `app_settings` is keyed by setting alone: it is global to
the instance, and only an admin may write it.

Values are stored as JSON text. A setting can hold a boolean, a number or a string, and JSON keeps
the type through the round trip, so a value read back is the type it was written as, not a string
that has to be parsed back into one by guesswork.

`interface_state` is a third table and deliberately not a fourth column on the first one. A setting
is a preference somebody chose on a screen, and every declared setting is drawn there; where
somebody dragged a row to is not a preference and has no business appearing as a row in Settings.
Keeping them apart is also what lets the registry stay absolute: a value in `user_settings` that
no declaration matches is exactly the drift the registry exists to prevent.
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


#: Version 12: stored keys renamed, old spelling first. Each one is also retired into its new key
#: by the slice that declares it, so an older caller still reads the moved value. Written out here
#: rather than imported: a step describes a database in the world and must not move with the code.
_RENAMED_KEYS_12 = (("edit.animation_format", "edit.gif_format"),)
_MOVE_APP_KEY = "UPDATE OR IGNORE app_settings SET key = ? WHERE key = ?"
_MOVE_USER_KEY = "UPDATE OR IGNORE user_settings SET key = ? WHERE key = ?"
_DROP_APP_KEY = "DELETE FROM app_settings WHERE key = ?"
_DROP_USER_KEY = "DELETE FROM user_settings WHERE key = ?"

#: Version 13: the backup's "How often" was one word (off, daily or weekly) and is now a count of
#: days, with Off gone to the task's When. Daily and weekly carry as 1 and 7 (an answer already
#: stored under the new key is kept); Off makes the backup's When "Only when I press it", stored,
#: so an install that had the schedule off still takes no backup on its own; then the word goes.
#: Written out literally, keys and JSON values alike, for the reason above.
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

#: Version 14: the two font keys held a FAMILY's name, meaning its display face on the first key and
#: its text face on the second; each now holds a face of its own role by the face's own name. Every
#: stored family name is carried to the face it meant there, so nobody's lettering changes. A name a
#: face already has (archivo and manrope as a display face, geist as a text face) stays as it is.
#: Written out literally, keys and JSON values alike, for the reason above.
_CARRY_FACE_14 = "UPDATE user_settings SET value = ? WHERE key = ? AND value = ?"
_FACES_14 = (
    ("appearance.theme_face_display", '"grotesk"', '"space-grotesk"'),
    ("appearance.theme_face_display", '"geist"', '"geist-mono"'),
    ("appearance.theme_face_body", '"archivo"', '"instrument-sans"'),
    ("appearance.theme_face_body", '"grotesk"', '"inter"'),
    ("appearance.theme_face_body", '"manrope"', '"public-sans"'),
)

#: Version 15: a person keeps up to ten colours of their own beside the six named accents. A custom
#: colour chosen before that existed becomes their first kept colour, so trying another one does not
#: forget it. Only a colour of the stored shape (the validator lower-cases every one), never the
#: starting blue, which is the default and is already the named Blue, and never over a list already
#: kept. Written out literally, keys and JSON values alike, for the reason above.
_KEEP_CUSTOM_COLOUR_15 = (
    "INSERT OR IGNORE INTO user_settings (user_id, key, value) "
    "SELECT user_id, 'appearance.theme_accent_swatches', '[' || value || ']' FROM user_settings "
    "WHERE key = 'appearance.theme_accent_hex' AND value <> '\"#2563eb\"' "
    "AND value GLOB '\"#[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]\"'"
)

#: Version 16: automatic backups older than a number of days are deleted, seven for a new library.
#: A library that already existed keeps what it had, the count of backups alone, until somebody
#: chooses a number of days: "never" (zero) is written for it, and only where nothing is stored
#: yet. A new library has no row and takes the declared default. Written out literally, the key and
#: its JSON value alike, for the reason above.
_KEEP_BACKUPS_HOWEVER_OLD_16 = (
    "INSERT OR IGNORE INTO app_settings (key, value) VALUES ('backup.keep_days', '0')"
)

#: Version 17: keys removed with nothing in their place, so no value lingers that nothing reads.
_REMOVED_KEYS_17 = ("download.photo_sets",)

#: Version 18: the release this library last started as, one row, which the first start of a newer
#: release reads to say which defaults its update changed (`defaults.tell_changed_defaults`). A new
#: library is given the running release, since no default changed under anybody there. A library
#: from before has no row, which that step reads as the release before the first one to record it.
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
        for old, new in _RENAMED_KEYS_12:
            # Should both spellings be stored, the new one is kept as the later answer. The old
            # row then goes rather than lingering as a value no declaration reads.
            await connection.execute(_MOVE_APP_KEY, (new, old))
            await connection.execute(_MOVE_USER_KEY, (new, old))
            await connection.execute(_DROP_APP_KEY, (old,))
            await connection.execute(_DROP_USER_KEY, (old,))
    if 0 < on_disk < 13:
        for word, days in _BACKUP_DAYS_13:
            await connection.execute(_CARRY_BACKUP_DAYS_13, (days, word))
        await connection.execute(_BACKUP_OFF_IS_PRESS_13)
        await connection.execute(_DROP_BACKUP_SCHEDULE_13)
    if 0 < on_disk < 14:
        for key, family, face in _FACES_14:
            await connection.execute(_CARRY_FACE_14, (face, key, family))
    if 0 < on_disk < 15:
        await connection.execute(_KEEP_CUSTOM_COLOUR_15)
    if 0 < on_disk < 16:
        await connection.execute(_KEEP_BACKUPS_HOWEVER_OLD_16)
    if 0 < on_disk < 17:
        for key in _REMOVED_KEYS_17:
            await connection.execute(_DROP_APP_KEY, (key,))
            await connection.execute(_DROP_USER_KEY, (key,))
    if on_disk < 18:
        await connection.execute(_CREATE_VERSION_BOOTED)
    if on_disk < 1 and app_version():
        await connection.execute(_A_NEW_LIBRARY_BOOTED, (app_version(),))


# `user_settings.user_id` references `users`, which the kernel's identity component creates.
register_schema_initializer(
    SETTINGS_COMPONENT,
    SETTINGS_VERSION,
    initialize_settings,
    depends_on=["identity"],
    baseline=11,
)
