# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a Stash run could not import yet, kept until its file arrives, and which sets and
Collections it made."""

from __future__ import annotations

from sift.kernel.db import Connection, register_schema_initializer

COMPONENT = "stash_migration"
VERSION = 3

_CREATE_WAITING = """
CREATE TABLE IF NOT EXISTS stash_waiting (
  id       TEXT PRIMARY KEY,
  kind     TEXT NOT NULL CHECK(kind IN ('file','picture')),
  stash_id INTEGER NOT NULL,
  read_at  INTEGER NOT NULL,
  user_id  TEXT REFERENCES users(id) ON DELETE SET NULL,
  label    TEXT NOT NULL,
  label_key TEXT NOT NULL,
  package  TEXT NOT NULL,
  UNIQUE (kind, stash_id)
)
"""

_CREATE_FILES = """
CREATE TABLE IF NOT EXISTS stash_waiting_files (
  waiting_id  TEXT NOT NULL REFERENCES stash_waiting(id) ON DELETE CASCADE,
  position    INTEGER NOT NULL,
  path        TEXT NOT NULL,
  here        TEXT,
  oshash      TEXT,
  phash       TEXT,
  size_bytes  INTEGER,
  duration_ms INTEGER,
  PRIMARY KEY (waiting_id, position)
) WITHOUT ROWID
"""

_CREATE_ENTITIES = """
CREATE TABLE IF NOT EXISTS stash_waiting_entities (
  kind   TEXT NOT NULL CHECK(kind IN ('person','site','tag')),
  name   TEXT NOT NULL,
  record TEXT NOT NULL,
  PRIMARY KEY (kind, name)
) WITHOUT ROWID
"""

_CREATE_GALLERIES = """
CREATE TABLE IF NOT EXISTS stash_galleries (
  source       TEXT NOT NULL,
  stash_id     INTEGER NOT NULL,
  name         TEXT NOT NULL,
  photo_set_id TEXT,
  pictures     TEXT NOT NULL DEFAULT '[]',
  PRIMARY KEY (source, stash_id)
) WITHOUT ROWID
"""


#: Version 2: Sift's words in the CHECK; SQLite cannot alter one, so both tables are rebuilt.
_TO_VERSION_2 = (
    _CREATE_WAITING.replace("stash_waiting (", "stash_waiting_v2 (", 1),
    "INSERT INTO stash_waiting_v2 SELECT id,"
    " CASE kind WHEN 'scene' THEN 'file' WHEN 'image' THEN 'picture' ELSE kind END,"
    " stash_id, read_at, user_id, label, label_key, package FROM stash_waiting",
    _CREATE_FILES.replace("stash_waiting_files (", "stash_waiting_files_v2 (", 1).replace(
        "REFERENCES stash_waiting(id)", "REFERENCES stash_waiting_v2(id)", 1
    ),
    "INSERT INTO stash_waiting_files_v2 SELECT * FROM stash_waiting_files",
    "DROP TABLE stash_waiting_files",
    "DROP TABLE stash_waiting",
    "ALTER TABLE stash_waiting_v2 RENAME TO stash_waiting",
    "ALTER TABLE stash_waiting_files_v2 RENAME TO stash_waiting_files",
)


_CREATE_GROUPS = """
CREATE TABLE IF NOT EXISTS stash_groups (
  source        TEXT NOT NULL,
  stash_id      INTEGER NOT NULL,
  name          TEXT NOT NULL,
  collection_id TEXT,
  scenes        TEXT NOT NULL DEFAULT '[]',
  PRIMARY KEY (source, stash_id)
) WITHOUT ROWID
"""


async def initialize(connection: Connection, on_disk: int) -> None:
    if on_disk < 1:
        await connection.execute(_CREATE_WAITING)
        await connection.execute(_CREATE_FILES)
        await connection.execute(_CREATE_ENTITIES)
        await connection.execute(_CREATE_GALLERIES)
    if 0 < on_disk < 2:
        for statement in _TO_VERSION_2:
            await connection.execute(statement)
    if on_disk < 3:
        await connection.execute(_CREATE_GROUPS)


register_schema_initializer(COMPONENT, VERSION, initialize, depends_on=["identity"], baseline=1)
