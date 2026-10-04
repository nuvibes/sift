# SPDX-License-Identifier: AGPL-3.0-or-later
"""The `file_moves` table: what was renamed or moved, and how to put it back.

A rename is not reversible by inspection. Once a file is called something else, nothing on disk
remembers what it was called before, so the only way an undo can exist is if the old address was
written down at the moment it stopped being true. That is all this table is: both ends of every
move, in order.

`undone_at` rather than deleting the row. An entry that has been taken back is still something that
happened, and a history that quietly loses its reversals reads as though the file was never moved
at all. It also makes undoing an undo an ordinary refusal rather than a missing row.

Nothing here cascades except the user, and that one is `ON DELETE SET NULL` deliberately: an
entry must outlive the user who made it, or removing a guest who once renamed
a file would either fail or take the history with it. The location and asset ids are plain columns.
They name rows that can go (a file deleted after being renamed) and the entry is still a true
record of something that was done.
"""

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
    # The history screen and the undo affordance both want the same thing: what happened most
    # recently. Newest first over a table that grows with every rename.
    "CREATE INDEX IF NOT EXISTS ix_file_moves_moved_at ON file_moves(moved_at)",
    # "has this file been moved, and can it be taken back": asked once per asset modal.
    "CREATE INDEX IF NOT EXISTS ix_file_moves_asset ON file_moves(asset_id)",
)


async def initialize(connection: Connection, on_disk: int) -> None:
    if on_disk < 1:
        await connection.execute(_CREATE_FILE_MOVES)
        for index in _INDEXES:
            await connection.execute(index)


# It names `users` in a foreign key without declaring a dependency on the component that creates
# that table. SQLite resolves a foreign key's parent by name when a row is written rather than when
# the table is created, so the reference holds whichever order the two were made in.
register_schema_initializer(COMPONENT, VERSION, initialize, baseline=4)
