# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the scanner remembers about the files it would not take, keyed by path, size and mtime.
A refused file is left where it is, so without this every scan would refuse it again."""

from __future__ import annotations

from sift.kernel.db import Connection, register_schema_initializer
from sift.kernel.ingress import Reason

SCAN_MEMORY_COMPONENT = "scan_memory"
SCAN_MEMORY_VERSION = 2

# One row per path, rewritten when the file changes.
_CREATE_SCAN_REJECTIONS = """
CREATE TABLE IF NOT EXISTS scan_rejections (
  id            TEXT PRIMARY KEY,
  root_id       TEXT NOT NULL REFERENCES library_roots(id) ON DELETE CASCADE,
  rel_path      TEXT NOT NULL,
  size_bytes    INTEGER NOT NULL,
  mtime_ns      INTEGER NOT NULL,
  reason        TEXT NOT NULL,
  detected      TEXT,
  first_seen_at INTEGER NOT NULL,
  last_seen_at  INTEGER NOT NULL,
  UNIQUE(root_id, rel_path)
)
"""

_INDEXES = (
    # The hot path of every scan.
    "CREATE INDEX IF NOT EXISTS ix_scan_rejections_root ON scan_rejections(root_id, rel_path)",
)


#: Refusals under a rule the gate no longer applies, forgotten once so the next scan retries them.
_FORGET_RETIRED_REFUSALS = "DELETE FROM scan_rejections WHERE reason = ?"
_RETIRED_REASONS = (Reason.EXTENSION_CONTRADICTS_SIGNATURE.value,)


async def initialize_scan_memory(connection: Connection, on_disk: int) -> None:
    if on_disk < 1:
        await connection.execute(_CREATE_SCAN_REJECTIONS)
        for index in _INDEXES:
            await connection.execute(index)
    if on_disk < 2:
        for reason in _RETIRED_REASONS:
            await connection.execute(_FORGET_RETIRED_REFUSALS, (reason,))


register_schema_initializer(
    SCAN_MEMORY_COMPONENT,
    SCAN_MEMORY_VERSION,
    initialize_scan_memory,
    depends_on=["library"],
    baseline=1,
)
