# SPDX-License-Identifier: AGPL-3.0-or-later
"""What this feature records about the library: which files it has described, and with what model.

The vector table needs an extension that can be absent, so the store creates it on first real
need; this is the ordinary, resumable half a restart or a model change reads. The model is
recorded per file because two models' numbers mix without error and return wrong neighbours.
"""

from __future__ import annotations

from sift.kernel.content.backlog import marks
from sift.kernel.db import Connection, register_schema_initializer
from sift.slices.semantic.store import index_files

COMPONENT = "semantic"
VERSION = 5

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


#: How many files each model has described and how many frames it holds, kept by the triggers
#: below so the settings pane reads rows rather than counting the index.
_CREATE_COUNTS = """
CREATE TABLE IF NOT EXISTS semantic_counts (
  revision TEXT PRIMARY KEY,
  files    INTEGER NOT NULL DEFAULT 0,
  frames   INTEGER NOT NULL DEFAULT 0
) WITHOUT ROWID
"""

#: Files whose numbers are held with no record beside them. A deleted file's record goes with it
#: and its numbers cannot (no foreign key on a virtual table), so every file that has left the
#: library is in here, and Maintenance asks the kernel about these alone.
_CREATE_UNRECORDED = """
CREATE TABLE IF NOT EXISTS semantic_unrecorded (
  asset_id TEXT PRIMARY KEY
) WITHOUT ROWID
"""

# Each count's row is made on its model's first file. No conflict clause in a trigger body: the
# statement that fired it would override it.
_TRIGGERS = (
    """
CREATE TRIGGER IF NOT EXISTS semantic_counts_file_added AFTER INSERT ON semantic_indexed BEGIN
  INSERT INTO semantic_counts (revision) SELECT NEW.revision
   WHERE NOT EXISTS (SELECT 1 FROM semantic_counts WHERE revision = NEW.revision);
  UPDATE semantic_counts SET files = files + 1 WHERE revision = NEW.revision;
  DELETE FROM semantic_unrecorded WHERE asset_id = NEW.asset_id;
END
""",
    """
CREATE TRIGGER IF NOT EXISTS semantic_counts_file_removed AFTER DELETE ON semantic_indexed BEGIN
  UPDATE semantic_counts SET files = files - 1 WHERE revision = OLD.revision;
  INSERT INTO semantic_unrecorded (asset_id) SELECT OLD.asset_id
   WHERE EXISTS (SELECT 1 FROM semantic_file_keys WHERE asset_id = OLD.asset_id)
     AND NOT EXISTS (SELECT 1 FROM semantic_unrecorded WHERE asset_id = OLD.asset_id);
END
""",
    """
CREATE TRIGGER IF NOT EXISTS semantic_counts_file_moved
AFTER UPDATE OF revision ON semantic_indexed WHEN OLD.revision IS NOT NEW.revision BEGIN
  UPDATE semantic_counts SET files = files - 1 WHERE revision = OLD.revision;
  INSERT INTO semantic_counts (revision) SELECT NEW.revision
   WHERE NOT EXISTS (SELECT 1 FROM semantic_counts WHERE revision = NEW.revision);
  UPDATE semantic_counts SET files = files + 1 WHERE revision = NEW.revision;
END
""",
    """
CREATE TRIGGER IF NOT EXISTS semantic_counts_frame_added AFTER INSERT ON semantic_frame_keys BEGIN
  INSERT INTO semantic_counts (revision) SELECT NEW.revision
   WHERE NOT EXISTS (SELECT 1 FROM semantic_counts WHERE revision = NEW.revision);
  UPDATE semantic_counts SET frames = frames + 1 WHERE revision = NEW.revision;
END
""",
    """
CREATE TRIGGER IF NOT EXISTS semantic_counts_frame_removed AFTER DELETE ON semantic_frame_keys BEGIN
  UPDATE semantic_counts SET frames = frames - 1 WHERE revision = OLD.revision;
END
""",
    """
CREATE TRIGGER IF NOT EXISTS semantic_unrecorded_keyed AFTER INSERT ON semantic_file_keys BEGIN
  INSERT INTO semantic_unrecorded (asset_id) SELECT NEW.asset_id
   WHERE NOT EXISTS (SELECT 1 FROM semantic_indexed WHERE asset_id = NEW.asset_id)
     AND NOT EXISTS (SELECT 1 FROM semantic_unrecorded WHERE asset_id = NEW.asset_id);
END
""",
    """
CREATE TRIGGER IF NOT EXISTS semantic_unrecorded_unkeyed AFTER DELETE ON semantic_file_keys BEGIN
  DELETE FROM semantic_unrecorded WHERE asset_id = OLD.asset_id;
END
""",
)

#: The counts and the unrecorded files of an index described before they were kept, taken again
#: from nothing so the step can be run twice.
_FILL = (
    "DELETE FROM semantic_counts",
    "DELETE FROM semantic_unrecorded",
    """
INSERT INTO semantic_counts (revision, files, frames)
SELECT revision, SUM(files), SUM(frames) FROM (
  SELECT revision, COUNT(*) AS files, 0 AS frames FROM semantic_indexed GROUP BY revision
  UNION ALL
  SELECT revision, 0, COUNT(*) FROM semantic_frame_keys GROUP BY revision
) GROUP BY revision
""",
    "INSERT INTO semantic_unrecorded (asset_id) SELECT k.asset_id FROM semantic_file_keys k"
    " WHERE NOT EXISTS (SELECT 1 FROM semantic_indexed i WHERE i.asset_id = k.asset_id)",
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
    # Version 4: a description written or forgotten marks its file for the passes' kept counts.
    if on_disk < 4:
        for statement in marks("semantic_indexed"):
            await connection.execute(statement)
    # Version 5: each model's files and frames, and the files held with no record, kept as rows.
    if on_disk < 5:
        for statement in (_CREATE_COUNTS, _CREATE_UNRECORDED, *_TRIGGERS, *_FILL):
            await connection.execute(statement)


# On the content component for the tables its marks write into.
register_schema_initializer(COMPONENT, VERSION, initialize, depends_on=["content"], baseline=2)
