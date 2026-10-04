# SPDX-License-Identifier: AGPL-3.0-or-later
"""The `produced_files` table: which file Sift made from which, and how.

Nothing else in Sift records that one file came out of another. Identity is the contents, so a
compressed copy is simply a different file: true, and useless to somebody looking at forty copies
of forty originals trying to work out which is which.

This is the missing sentence, written down once and read by three things that would otherwise each
need their own answer: the copy's page, which links back to the original; the filter that finds
every copy; and the near-duplicate wall, which must not offer a copy and its original as a mistake
somebody made.

Two delete behaviours, and they are deliberately different. Removing the COPY removes this row:
there is nothing left to say. Removing the ORIGINAL leaves the row with an empty source, because
the copy still exists, is still a copy, and still belongs in a list of them; only the link back has
nowhere to go.
"""

from __future__ import annotations

from sift.kernel.db import Connection, register_schema_initializer

COMPONENT = "produced_files"
VERSION = 1

_CREATE_PRODUCED_FILES = """
CREATE TABLE IF NOT EXISTS produced_files (
  id               TEXT PRIMARY KEY,
  asset_id         TEXT NOT NULL UNIQUE REFERENCES assets(id) ON DELETE CASCADE,
  source_asset_id  TEXT REFERENCES assets(id) ON DELETE SET NULL,
  operation        TEXT NOT NULL,
  preset           TEXT,
  target_bytes     INTEGER,
  produced_by      TEXT REFERENCES users(id) ON DELETE SET NULL,
  produced_at      INTEGER NOT NULL
)
"""

_INDEXES = (
    # "what has been made from this file": asked by the original's own page, and by the check
    # that stops a copy and its original being offered as a duplicate pair.
    "CREATE INDEX IF NOT EXISTS ix_produced_source ON produced_files(source_asset_id)",
    # "everything Sift has produced, newest first": the list behind the filter.
    "CREATE INDEX IF NOT EXISTS ix_produced_at ON produced_files(produced_at)",
)


async def initialize(connection: Connection, on_disk: int) -> None:
    if on_disk < 1:
        await connection.execute(_CREATE_PRODUCED_FILES)
        for index in _INDEXES:
            await connection.execute(index)


# It names `assets` and `users` in foreign keys without declaring a dependency on the components
# that create them. SQLite resolves a foreign key's parent by name when a row is written rather than
# when the table is created, so the references hold whichever order the tables were made in.
register_schema_initializer(COMPONENT, VERSION, initialize, baseline=1)
