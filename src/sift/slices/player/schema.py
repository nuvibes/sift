# SPDX-License-Identifier: AGPL-3.0-or-later
"""One table: a row for every sitting somebody spent with a file, kept for ever.

Not in the ledger: a play is a measurement, not an act. No file key: a sitting outlives its file.
A sitting arriving in pieces is one row by its `sitting` id; `play_names` keeps names once each.
"""

from __future__ import annotations

from sift.kernel.db import Connection, register_schema_initializer
from sift.kernel.migrations import check_allows, column_exists, widen_a_check

COMPONENT = "player"
VERSION = 7

# `asset_id` carries no key: a sitting outlives its file. The CHECK lists are written out so a
# schema means the same everywhere; a test holds them equal to the wire.
_CREATE_PLAYS = """
CREATE TABLE IF NOT EXISTS plays (
  id              TEXT PRIMARY KEY,
  user_id         TEXT REFERENCES users(id) ON DELETE CASCADE,
  asset_id        TEXT,
  started_at      INTEGER NOT NULL,
  duration_ms     INTEGER NOT NULL,
  ended_at_ms     INTEGER,
  heat            TEXT,
  made_at         INTEGER NOT NULL,
  -- Which sitting a report is a piece of (see "One row per sitting"), and how often the playhead
  -- was moved by hand.
  sitting         TEXT,
  seeks           INTEGER NOT NULL DEFAULT 0,
  -- Where a sitting happened and what the file was, written with its first piece, because none
  -- of it can be worked out afterwards. NULL where it was not recorded; an invented value would be
  -- indistinguishable from a recorded one.
  screen          TEXT CHECK(screen IN ('panel','corner','theater')),
  opened_from     TEXT CHECK(opened_from IN (
                    'library','search','favorites','recent','loops','person','site','tag',
                    'collection','photo_set','song','organize','downloads','hidden','start',
                    'link','folder','insights','other'
                  )),
  kind            TEXT CHECK(kind IN ('video','image','gif')),
  -- What a sitting was opened from. No keys, for the reason `asset_id` has none.
  loop_id         TEXT,
  kept_filter_id  TEXT,
  theater_session TEXT,
  -- The length of the file, written with the sitting for the reason `kind` is: the view rule
  -- judges a sitting by its kind, its length and its time, and a sitting outlives its file.
  length_ms       INTEGER,
  -- Version 7. Which one thing the screen behind the file was about: the person, tag, Site,
  -- Collection, Photo Set, song or folder of `opened_from`, or the record of the search
  -- (`search_events.id`). No key, for the reason `loop_id` has none.
  opened_from_id  TEXT,
  -- Version 7: what happened inside the sitting (see "Inside a sitting").
  start_ms        INTEGER,
  seek_log        TEXT,
  speeds          TEXT,
  fullscreen_ms   INTEGER,
  completions     INTEGER,
  magnified       INTEGER,
  -- Version 7: the client it happened on (`kernel/client.py`), and what the file carried then.
  device_id       TEXT,
  client_kind     TEXT,
  about           TEXT
)
"""

#: Version 7: the name each thing a sitting was about had, once per name, per User.
_CREATE_PLAY_NAMES = """
CREATE TABLE IF NOT EXISTS play_names (
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind    TEXT NOT NULL CHECK(kind IN ('person','tag','site','song')),
  ref     TEXT NOT NULL,
  name    TEXT NOT NULL,
  since   INTEGER NOT NULL
)
"""

#: The name a thing had at a moment: the latest row at or before it, for one User.
_PLAY_NAMES_INDEX = (
    "CREATE INDEX IF NOT EXISTS ix_play_names_ref ON play_names(user_id, kind, ref, since)"
)

#: The columns version 7 adds where they are missing.
_ADD_V7 = (
    ("opened_from_id", "ALTER TABLE plays ADD COLUMN opened_from_id TEXT"),
    ("start_ms", "ALTER TABLE plays ADD COLUMN start_ms INTEGER"),
    ("seek_log", "ALTER TABLE plays ADD COLUMN seek_log TEXT"),
    ("speeds", "ALTER TABLE plays ADD COLUMN speeds TEXT"),
    ("fullscreen_ms", "ALTER TABLE plays ADD COLUMN fullscreen_ms INTEGER"),
    ("completions", "ALTER TABLE plays ADD COLUMN completions INTEGER"),
    ("magnified", "ALTER TABLE plays ADD COLUMN magnified INTEGER"),
    ("device_id", "ALTER TABLE plays ADD COLUMN device_id TEXT"),
    ("client_kind", "ALTER TABLE plays ADD COLUMN client_kind TEXT"),
    ("about", "ALTER TABLE plays ADD COLUMN about TEXT"),
)

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_plays_asset ON plays(asset_id, started_at)",
    # A person's year, which the stats screens walk.
    "CREATE INDEX IF NOT EXISTS ix_plays_user ON plays(user_id, started_at)",
    # One row per sitting, scoped to user and file since the id is minted in a browser.
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_plays_sitting"
    " ON plays(user_id, asset_id, sitting) WHERE sitting IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS ix_plays_theater_session"
    " ON plays(user_id, theater_session) WHERE theater_session IS NOT NULL",
)


#: Version 6 adds `song` to `opened_from`; the fragment is the exact text versions 1 to 5 wrote.
_OPENED_FROM_WAS = "'collection','photo_set','organize'"
_OPENED_FROM_NOW = "'collection','photo_set','song','organize'"

#: Version 7 adds folders and Insights; older rows keep `library` and `other`.
_OPENED_FROM_V6 = "'link','other'"
_OPENED_FROM_V7 = "'link','folder','insights','other'"


async def initialize_player(connection: Connection, on_disk: int) -> None:
    if on_disk < 1:
        await connection.execute(_CREATE_PLAYS)
        for statement in _INDEXES:
            await connection.execute(statement)
    if 0 < on_disk < 6 and not await check_allows(connection, "plays", "song"):
        await widen_a_check(connection, "plays", was=_OPENED_FROM_WAS, now=_OPENED_FROM_NOW)
    if 0 < on_disk < 7:
        for column, statement in _ADD_V7:
            if not await column_exists(connection, "plays", column):
                await connection.execute(statement)
        if not await check_allows(connection, "plays", "insights"):
            await widen_a_check(connection, "plays", was=_OPENED_FROM_V6, now=_OPENED_FROM_V7)
    if on_disk < 7:
        await connection.execute(_CREATE_PLAY_NAMES)
        await connection.execute(_PLAY_NAMES_INDEX)


register_schema_initializer(
    COMPONENT,
    VERSION,
    initialize_player,
    depends_on=["content", "identity"],
    baseline=5,
)
