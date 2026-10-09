# SPDX-License-Identifier: AGPL-3.0-or-later
"""The `file_moves` table: both ends of every rename or move, so it can be undone; never deleted."""

from __future__ import annotations

from sift.kernel.db import Connection, register_schema_initializer

COMPONENT = "file_moves"
VERSION = 4

_CREATE_FILE_MOVES = """
CREATE TABLE IF NOT EXISTS file_moves (
  id             TEXT PRIMARY KEY,
  kind           TEXT NOT NULL,
  location_id    TEXT,
  asset_id       TEXT,
  root_id        TEXT NOT NULL,
  from_rel_path  TEXT NOT NULL,
  from_folder_id TEXT,
  to_rel_path    TEXT NOT NULL,
  to_folder_id   TEXT,
  moved_by       TEXT REFERENCES users(id) ON DELETE SET NULL,
  -- A move nobody asked for is Sift's own, and the line says so rather than "Somebody".
  -- `moved_by` cannot carry it: the column references an account.
  moved_by_sift  INTEGER NOT NULL DEFAULT 0,
  -- Why Sift made a move of its own, as a word the History line says
  -- (`sentences.RENAMED_BECAUSE`). NULL for a person's move, which needs no reason given.
  reason         TEXT,
  moved_at       INTEGER NOT NULL,
  undone_at      INTEGER
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_file_moves_moved_at ON file_moves(moved_at)",
    "CREATE INDEX IF NOT EXISTS ix_file_moves_asset ON file_moves(asset_id)",
)


async def initialize(connection: Connection, on_disk: int) -> None:
    if on_disk < 1:
        await connection.execute(_CREATE_FILE_MOVES)
        for index in _INDEXES:
            await connection.execute(index)


# SQLite resolves a foreign key's parent when a row is written, so no dependency is declared.
register_schema_initializer(COMPONENT, VERSION, initialize, baseline=4)
