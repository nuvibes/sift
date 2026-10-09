# SPDX-License-Identifier: AGPL-3.0-or-later
"""The `dedup_candidates` table: near-duplicate pairs to look at. Never deleted, so a dismissal
outlives every scan; ids are stored sorted so the UNIQUE key catches a pair found again."""

from __future__ import annotations

from sift.kernel.db import Connection, register_schema_initializer

COMPONENT = "dedup"
VERSION = 4

_CREATE_CANDIDATES = """
CREATE TABLE IF NOT EXISTS dedup_candidates (
  id              TEXT PRIMARY KEY,
  asset_a         TEXT REFERENCES assets(id) ON DELETE CASCADE,
  asset_b         TEXT REFERENCES assets(id) ON DELETE CASCADE,
  method          TEXT CHECK(method IN ('phash','videohash','video_phash')),
  distance        INTEGER,
  -- How far apart the two run. NULL is the matcher's word for "nobody can say", and the queue
  -- shows such a pair rather than hiding it when the length control is touched.
  duration_gap_ms INTEGER,
  status          TEXT NOT NULL DEFAULT 'pending'
                  CHECK(status IN ('pending','confirmed','dismissed')),
  created_at      INTEGER NOT NULL,
  UNIQUE(asset_a, asset_b, method)
)
"""

_INDEXES = (
    # Status leads so settled rows are skipped as a reviewed library grows.
    "CREATE INDEX IF NOT EXISTS ix_dedup_status ON dedup_candidates(status, distance)",
    "CREATE INDEX IF NOT EXISTS ix_dedup_asset_a ON dedup_candidates(asset_a)",
    "CREATE INDEX IF NOT EXISTS ix_dedup_asset_b ON dedup_candidates(asset_b)",
)


async def initialize(connection: Connection, on_disk: int) -> None:
    if on_disk < 1:
        await connection.execute(_CREATE_CANDIDATES)
        for index in _INDEXES:
            await connection.execute(index)


# SQLite resolves a foreign key's parent when a row is written, so no dependency is declared.
register_schema_initializer(COMPONENT, VERSION, initialize, baseline=4)
