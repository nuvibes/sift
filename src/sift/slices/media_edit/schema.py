# SPDX-License-Identifier: AGPL-3.0-or-later
"""The `produced_files` table: which file Sift made from which, and how.

Removing the copy removes its row; removing the original leaves the row with no source.
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
    "CREATE INDEX IF NOT EXISTS ix_produced_source ON produced_files(source_asset_id)",
    "CREATE INDEX IF NOT EXISTS ix_produced_at ON produced_files(produced_at)",
)


async def initialize(connection: Connection, on_disk: int) -> None:
    if on_disk < 1:
        await connection.execute(_CREATE_PRODUCED_FILES)
        for index in _INDEXES:
            await connection.execute(index)


# SQLite resolves a foreign key's parent when a row is written, so table order does not matter.
register_schema_initializer(COMPONENT, VERSION, initialize, baseline=1)
