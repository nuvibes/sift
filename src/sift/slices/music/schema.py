# SPDX-License-Identifier: AGPL-3.0-or-later
"""The music tables: one fingerprint per file (empty when silent), staged ones by identity, the
key index and the verified pairs."""

from __future__ import annotations

from sift.kernel.access import waiting
from sift.kernel.access.visibility import Counted, register_counted
from sift.kernel.access.waiting import Waiting, register_waiting
from sift.kernel.content import songs
from sift.kernel.content.backlog import marks
from sift.kernel.db import Connection, register_schema_initializer

COMPONENT = "music"
VERSION = 6

_CREATE_FINGERPRINTS = """
CREATE TABLE IF NOT EXISTS audio_fingerprints (
  asset_id     TEXT PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
  algorithm    INTEGER NOT NULL,
  tool         TEXT NOT NULL,
  duration_ms  INTEGER NOT NULL,
  offset_ms    INTEGER NOT NULL DEFAULT 0,
  fingerprint  BLOB NOT NULL,
  computed_at  INTEGER NOT NULL,
  indexed_scheme INTEGER
)
"""

_CREATE_PENDING = """
CREATE TABLE IF NOT EXISTS audio_fingerprints_pending (
  identity     TEXT PRIMARY KEY,
  algorithm    INTEGER NOT NULL,
  tool         TEXT NOT NULL,
  duration_ms  INTEGER NOT NULL,
  offset_ms    INTEGER NOT NULL DEFAULT 0,
  fingerprint  BLOB NOT NULL,
  computed_at  INTEGER NOT NULL
)
"""

# `CHECK (id = 1)`: one answer, not a log of them.
_CREATE_CATCHUP = """
CREATE TABLE IF NOT EXISTS music_catchup (
  id       INTEGER PRIMARY KEY CHECK (id = 1),
  until    INTEGER NOT NULL,
  asked_at INTEGER NOT NULL
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_audio_pending_age ON audio_fingerprints_pending(computed_at)",
    "CREATE INDEX IF NOT EXISTS ix_audio_keys_asset ON audio_fingerprint_keys(asset_id)",
    # The primary key reads a file's pairs where it is `a_id`.
    "CREATE INDEX IF NOT EXISTS ix_music_pairs_b ON music_pairs(b_id)",
)


#: Counted per user against the whole library.
WAITING_KIND = "music_waiting"
WAITING_SCOPE = "library"

_CREATE_WAITING = """
CREATE TABLE IF NOT EXISTS music_waiting (
  asset_id TEXT PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE
) WITHOUT ROWID
"""

#: A probed file with a sound track and no fingerprint row waits; the kernel writes the SQL.
WAITING = Waiting(
    table="music_waiting",
    done="audio_fingerprints",
    filled=("acodec", "probed_at"),
)


_CREATE_KEYS = """
CREATE TABLE IF NOT EXISTS audio_fingerprint_keys (
  key       INTEGER NOT NULL,
  asset_id  TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  PRIMARY KEY (key, asset_id)
) WITHOUT ROWID
"""

_CREATE_PAIRS = """
CREATE TABLE IF NOT EXISTS music_pairs (
  a_id        TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  b_id        TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  ber         REAL NOT NULL,
  offset_s    REAL NOT NULL,
  windows     INTEGER NOT NULL,
  matching    INTEGER NOT NULL,
  computed_at INTEGER NOT NULL,
  PRIMARY KEY (a_id, b_id),
  CHECK (a_id < b_id)
) WITHOUT ROWID
"""

#: Version 4: moved onto the songs by catalog step 81, then dropped here.
_DROP_NAMES = "DROP TABLE IF EXISTS music_names"

_CREATE_NAME_REFUSALS = """
CREATE TABLE IF NOT EXISTS music_name_refusals (
  asset_id   TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  song       TEXT NOT NULL,
  refused_at INTEGER NOT NULL,
  PRIMARY KEY (asset_id, song)
) WITHOUT ROWID
"""

_CREATE_LOOKUPS = """
CREATE TABLE IF NOT EXISTS music_lookups (
  asset_id     TEXT PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
  looked_up_at INTEGER NOT NULL,
  lengths_sent TEXT NOT NULL,
  status       TEXT NOT NULL CHECK (status IN ('named', 'nothing', 'refused', 'failed')),
  recording_id TEXT,
  title        TEXT,
  artists      TEXT,
  score        REAL
)
"""

#: One row, never a setting, which any admin write could point at another secret.
_CREATE_LOOKUP_KEY = """
CREATE TABLE IF NOT EXISTS music_lookup_key (
  id        INTEGER PRIMARY KEY CHECK (id = 1),
  secret_id TEXT NOT NULL,
  set_at    INTEGER NOT NULL
)
"""


_KEPT_ARTISTS = """
SELECT recording_id, artists FROM music_lookups
 WHERE status = 'named' AND recording_id IS NOT NULL AND artists IS NOT NULL AND artists <> ''
 ORDER BY looked_up_at, asset_id
"""


_MARKS = (
    *marks("audio_fingerprints", watched=("asset_id", "algorithm", "indexed_scheme")),
    *marks("music_waiting"),
)


async def initialize(connection: Connection, on_disk: int) -> None:
    if on_disk < 1:
        for statement in (
            _CREATE_FINGERPRINTS,
            _CREATE_PENDING,
            _CREATE_CATCHUP,
            _CREATE_WAITING,
            _CREATE_KEYS,
            _CREATE_PAIRS,
            _CREATE_NAME_REFUSALS,
            _CREATE_LOOKUPS,
            _CREATE_LOOKUP_KEY,
            *_INDEXES,
        ):
            await connection.execute(statement)
        await waiting.start(connection, WAITING)
    if 0 < on_disk < 4:
        await connection.execute(_DROP_NAMES)
    # Version 5: credit the artists of answers already kept; a song credited already is left alone.
    if 0 < on_disk < 5:
        rows = await connection.execute_fetchall(_KEPT_ARTISTS)
        await songs.credit_kept_answers(
            connection, [(str(row["recording_id"]), row["artists"]) for row in rows]
        )
    if on_disk < 6:
        for statement in _MARKS:
            await connection.execute(statement)


# Depends on `content` (triggers on `assets`) and `catalog` (step 81 reads `music_names`).
register_schema_initializer(
    COMPONENT, VERSION, initialize, depends_on=["content", "catalog"], baseline=3
)
register_waiting(WAITING)

#: The scope is written into the text so the statement is a constant.
_WAITING_SOURCE = "(SELECT asset_id, 'library' AS scope FROM music_waiting)"
if f"'{WAITING_SCOPE}'" not in _WAITING_SOURCE:  # pragma: no cover (an edit)
    raise RuntimeError("the waiting source and its scope disagree")

# Counted per user by the visibility component, so the card hides what the vault hides.
register_counted(
    Counted(
        WAITING_KIND,
        _WAITING_SOURCE,
        "scope",
        "music_waiting",
        keys=(("asset_id",),),
    ),
    component=COMPONENT,
)
