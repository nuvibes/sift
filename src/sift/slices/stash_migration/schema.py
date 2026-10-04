# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a Stash run could not import yet, kept until the file it belongs to arrives.

A scene or an image whose file this library does not hold is not imported: nothing lands without
its file. What the run would have written for it is kept here instead, in the run's own shape, and
the pass that runs after new files are fingerprinted (`service.StashMigration.land`) applies it
through the same writers the moment a file here carries one of its fingerprints or sits where it
did. A person, a Site or a tag that only such a scene or image carries waits here too, as its
whole record, and is made at that moment if it is still absent.

## The five tables

`stash_waiting` is one row per scene or image, with `package` holding what the run would have
written (its fields, its rating and counts, its markers, the galleries a picture is in). `user_id`
is whoever pressed Run, because a rating and a heart are theirs; it is set to nothing when that
User goes, and the rest of the package still lands.

`stash_waiting_files` is the file identity Stash kept for each: its path there, where that path is
on this device by the read's folders (`here`), its OSHash and video fingerprint, its size and its
length. The pass asks one question of these rows (which of them does a file here now carry), so
they are narrow columns rather than a field inside the package.

`stash_waiting_entities` is the record of each person, Site and tag that waits, by kind and name,
because many scenes name one person and the record is written once.

`stash_galleries` is which Photo Set each Stash gallery became, and which of its pictures are here,
so a second run adds to that set instead of making another, and a picture that arrives later joins
it. Keyed by the Stash database the read named as well as the gallery's id there, because two Stash
databases number their galleries from one alike.

`stash_groups` is the same for a Stash group (a movie, in older Stash): which Collection it became,
and which of its scenes are here, so a second run or a scene that arrives later adds to that
Collection rather than making another.

A run replaces the first three whole: what waits is what the newest run found. The last two are
kept.
"""

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


#: Version 2: what waits is filed under Sift's own words (file, picture), not Stash's (scene, image).
#: A CHECK constraint is the set of words Sift chose, and 0.1.204 shipped version 1 with Stash's, so
#: a library at 1 is brought across by rebuilding the table: SQLite cannot change a CHECK in place.
#: The child table is rebuilt with it, child first on the way down, because dropping the parent
#: under it would cascade its rows away. Renaming the parent afterwards rewrites the child's
#: reference to the new name (SQLite's own rename, 3.26 and later).
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


#: Version 3: Stash's groups become Collections, remembered like its galleries.
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


# `users` comes from the identity component, so it is built before `user_id` names it.
register_schema_initializer(COMPONENT, VERSION, initialize, depends_on=["identity"], baseline=1)
