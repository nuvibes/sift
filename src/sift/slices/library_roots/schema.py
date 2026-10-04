# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the scanner remembers about the files it would not take.

The roots and the folders are the kernel's tables: the access rules join against them, so they
have to exist before anything that resolves a permission. This slice owns one table of its own,
and it exists for a reason worth stating.

Sift never moves a file it finds in somebody's library. A file that fails the ingress gate
(something named `.mp4` that is not one, a truncated download, a file that is empty) is refused,
logged, and left exactly where it is. That is the promise, and it has a consequence nothing else
in the system handles: the file is still there on the next pass. A scanner with no memory finds
it, refuses it, and logs it again, on every scan, forever, and a watched folder never goes quiet.
The log fills with one refusal and the real ones are lost in it.

So the refusal is written down. The kernel deliberately keeps no state about a file it rejected
(it is a gate, and it answers about the bytes in front of it), which means remembering belongs to
whoever does the scanning.

The memory is keyed on what the file was, not on what it contained: path, size and modification
time. Not the digest, and that is the point. Hashing means reading the whole file, and the whole
reason the gate refused it in four kilobytes was to avoid reading the rest. Remembering it by
digest would read every rejected file on every scan: the exact cost this table exists to save.
The trade is honest and worth naming: a file edited so that its size and mtime both land back
where they were is checked again only on the next pass that sees it change. It is refused either
way, because the gate still reads it before anything else does.
"""

from __future__ import annotations

from sift.kernel.db import Connection, register_schema_initializer
from sift.kernel.ingress import Reason

SCAN_MEMORY_COMPONENT = "scan_memory"
SCAN_MEMORY_VERSION = 2

# `ON DELETE CASCADE` from the root, because a refusal is a fact about a file in a library, and a
# library nobody is watching any more has no files to have facts about.
#
# `UNIQUE(root_id, rel_path)` is what makes the upsert an upsert: one row per path, rewritten when
# the file changes, rather than a row per time the scanner met it.
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
    # The scan asks "have I already refused this one, and was it this same file?" once per file it
    # walks, which is the hot path of every pass over a library.
    "CREATE INDEX IF NOT EXISTS ix_scan_rejections_root ON scan_rejections(root_id, rel_path)",
)


#: The refusals whose rule the gate no longer applies. A refusal is remembered against the bytes
#: at a path and a scan walks past it while those bytes are unchanged (see `jobs._decide`), so a
#: file refused under a retired rule would stay refused for as long as nobody touched it. The
#: gate does not refuse a picture or video because its name names another kind: the bytes decide
#: (see `ingress.verify_ingress`). Forgetting those refusals once, here, is what lets the next
#: scan read the files again and take them in, with nobody pressing Try again on each one.
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


# The foreign key points at `library_roots`, which the kernel's `library` component creates.
register_schema_initializer(
    SCAN_MEMORY_COMPONENT,
    SCAN_MEMORY_VERSION,
    initialize_scan_memory,
    depends_on=["library"],
    baseline=1,
)
