# SPDX-License-Identifier: AGPL-3.0-or-later
"""What this feature records about the library: which files it has described, and with what.

The vector table needs an extension that can be absent, and Sift must boot without it, losing only
this feature; so the store creates it on first real need. Here is the ordinary, resumable half:
one row per described file with the model that described it, which a restart, an interrupted
sweep or a model change reads. The model is recorded per file because two models' numbers mix
without error and return wrong neighbours, so another model's row is stale, not done.
"""

from __future__ import annotations

from sift.kernel.db import Connection, register_schema_initializer

COMPONENT = "semantic"
VERSION = 2

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


#: One file's whole description, pooled, in an ORDINARY table read by primary key: the frames sit
#: in a virtual table that cannot be indexed, so reading one file's back is a scan. Written with
#: the frames from numbers in hand (`VectorStore.describes`). The revision is in the key, since one
#: model's numbers mean nothing to another's. No foreign key, deliberately: it must live and die
#: with the frames, which take no key, so it is swept by the same forgetting
#: (`store._ForgetFromVectors`).
_CREATE_POOLED = """
CREATE TABLE IF NOT EXISTS semantic_pooled (
  asset_id TEXT NOT NULL,
  revision TEXT NOT NULL,
  pooled   BLOB NOT NULL,
  PRIMARY KEY (asset_id, revision)
)
"""


async def initialize(connection: Connection, on_disk: int) -> None:
    if on_disk < 1:
        await connection.execute(_CREATE_INDEXED)
        for index in _INDEXES:
            await connection.execute(index)
        await connection.execute(_CREATE_POOLED)


# SQLite resolves a foreign key's parent by name when a row is written, so `assets` needs no
# declared dependency here.
register_schema_initializer(COMPONENT, VERSION, initialize, baseline=2)
