# SPDX-License-Identifier: AGPL-3.0-or-later
"""What this feature records about the library: which files it has described, and with what model.

The vector table needs an extension that can be absent, so the store creates it on first real
need; this is the ordinary, resumable half a restart or a model change reads. The model is
recorded per file because two models' numbers mix without error and return wrong neighbours.
"""

from __future__ import annotations

from sift.kernel.db import Connection, register_schema_initializer
from sift.slices.semantic.store import index_files

COMPONENT = "semantic"
VERSION = 3

_CREATE_INDEXED = """
CREATE TABLE IF NOT EXISTS semantic_indexed (
  asset_id   TEXT PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
  revision   TEXT NOT NULL,
  frames     INTEGER NOT NULL,
  indexed_at INTEGER NOT NULL
)
"""

_INDEXES = (
    # The sweep asks which files another model described, and must answer "none" on a settled
    # library without reading every row.
    "CREATE INDEX IF NOT EXISTS ix_semantic_revision ON semantic_indexed(revision)",
)


#: One file's whole description, pooled, in an ORDINARY table read by primary key (the frames'
#: virtual table cannot be indexed). Written with the frames (`VectorStore.describes`); the
#: revision is in the key, since one model's numbers mean nothing to another's; no foreign key,
#: since it lives and dies with the frames, swept by the same forgetting
#: (`store._ForgetFromVectors`).
_CREATE_POOLED = """
CREATE TABLE IF NOT EXISTS semantic_pooled (
  asset_id TEXT NOT NULL,
  revision TEXT NOT NULL,
  pooled   BLOB NOT NULL,
  PRIMARY KEY (asset_id, revision)
)
"""


#: Which file each vector is of, by rowid: the virtual tables keep it as text read row by row, so
#: a viewer's scope is a join here. No foreign key, as `semantic_pooled` has none.
_CREATE_KEYS = (
    """
CREATE TABLE IF NOT EXISTS semantic_frame_keys (
  frame    INTEGER PRIMARY KEY,
  asset_id TEXT NOT NULL,
  revision TEXT NOT NULL
)
""",
    "CREATE INDEX IF NOT EXISTS ix_semantic_frame_keys_asset ON semantic_frame_keys(asset_id)",
    """
CREATE TABLE IF NOT EXISTS semantic_file_keys (
  file     INTEGER PRIMARY KEY,
  asset_id TEXT NOT NULL UNIQUE,
  revision TEXT NOT NULL
)
""",
)


async def initialize(connection: Connection, on_disk: int) -> None:
    if on_disk < 1:
        await connection.execute(_CREATE_INDEXED)
        for index in _INDEXES:
            await connection.execute(index)
        await connection.execute(_CREATE_POOLED)
    if on_disk < 3:
        for statement in _CREATE_KEYS:
            await connection.execute(statement)
        await index_files(connection)


# SQLite resolves a foreign key's parent by name when a row is written, so `assets` needs no
# declared dependency here.
register_schema_initializer(COMPONENT, VERSION, initialize, baseline=2)
