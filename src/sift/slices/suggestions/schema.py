# SPDX-License-Identifier: AGPL-3.0-or-later
"""The folder suggestions' tables: open questions, standing answers, and where the last pass got
to."""

from __future__ import annotations

from sift.kernel.db import Connection, register_schema_initializer

COMPONENT = "suggestions"
VERSION = 11

# `kind` is person, site (a shared filename word) or username; a NULL `group_id` means no face.
_CREATE_CLAIMS = """
CREATE TABLE IF NOT EXISTS folder_claims (
  id          TEXT PRIMARY KEY,
  folder_id   TEXT NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
  kind        TEXT NOT NULL CHECK(kind IN ('person','site','username')),
  name_key    TEXT NOT NULL,
  proposed    TEXT NOT NULL,
  person_id   TEXT REFERENCES people(id) ON DELETE CASCADE,
  group_id    TEXT,
  site        TEXT,
  is_username INTEGER NOT NULL DEFAULT 0,
  evidence    TEXT NOT NULL CHECK(evidence IN ('face_group','name_only','filenames',
              'known_in_filenames','username_folder','by_hand')),
  state       TEXT NOT NULL DEFAULT 'pending'
              CHECK(state IN ('pending','confirmed','rejected')),
  created_at  INTEGER NOT NULL,
  decided_at  INTEGER,
  UNIQUE(folder_id, name_key)
)
"""

# The permanent no, keyed by the folded name so a renamed folder cannot bring it back.
_CREATE_REJECTIONS = """
CREATE TABLE IF NOT EXISTS claim_rejections (
  name_key   TEXT PRIMARY KEY,
  created_at INTEGER NOT NULL
)
"""

# Cascades from both sides: a rule about a gone folder or person would misfile new files.
_CREATE_FOLDER_PEOPLE = """
CREATE TABLE IF NOT EXISTS folder_people (
  folder_id  TEXT NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
  person_id  TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  created_at INTEGER NOT NULL,
  PRIMARY KEY(folder_id, person_id)
)
"""

# `signature` is only compared for equality, so its contents can change without a migration.
_CREATE_PASSES = """
CREATE TABLE IF NOT EXISTS folder_passes (
  folder_id  TEXT PRIMARY KEY REFERENCES folders(id) ON DELETE CASCADE,
  signature  TEXT NOT NULL,
  scanned_at INTEGER NOT NULL
)
"""

# Keyed by the file, which keeps its id through a rename, unlike a folder's name.
_CREATE_FILENAME_REFUSALS = """
CREATE TABLE IF NOT EXISTS filename_refusals (
  asset_id   TEXT PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
  created_at INTEGER NOT NULL
)
"""

# Username numbers no name is known for yet, so the card can say how many files wait on one.
_CREATE_WAITING_NUMBERS = """
CREATE TABLE IF NOT EXISTS username_numbers_waiting (
  site     TEXT NOT NULL,
  number   TEXT NOT NULL,
  files    INTEGER NOT NULL,
  seen_at  INTEGER NOT NULL,
  PRIMARY KEY(site, number)
)
"""

# Keeps a silent folder filing taken back; binds only the pass, not a person's own answer.
_CREATE_FOLDER_REFUSALS = """
CREATE TABLE IF NOT EXISTS folder_refusals (
  folder_id  TEXT NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
  person_id  TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  created_at INTEGER NOT NULL,
  PRIMARY KEY(folder_id, person_id)
)
"""

_INDEXES = (
    # By id: a ULID never steps backwards when the wall clock does.
    "CREATE INDEX IF NOT EXISTS ix_folder_claims_state_by_id ON folder_claims(state, id)",
    "CREATE INDEX IF NOT EXISTS ix_folder_claims_name ON folder_claims(name_key)",
    "CREATE INDEX IF NOT EXISTS ix_folder_people_person ON folder_people(person_id)",
)


async def initialize(connection: Connection, on_disk: int) -> None:
    if on_disk < 1:
        for statement in (
            _CREATE_CLAIMS,
            _CREATE_REJECTIONS,
            _CREATE_FOLDER_PEOPLE,
            _CREATE_PASSES,
            _CREATE_FILENAME_REFUSALS,
            _CREATE_WAITING_NUMBERS,
            *_INDEXES,
        ):
            await connection.execute(statement)
    if on_disk < 11:
        await connection.execute(_CREATE_FOLDER_REFUSALS)


# SQLite resolves a foreign key's parent when a row is written, so no dependency is declared.
register_schema_initializer(COMPONENT, VERSION, initialize, baseline=10)
