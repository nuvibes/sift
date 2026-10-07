# SPDX-License-Identifier: AGPL-3.0-or-later
"""The files with no place left, kept as rows by the writes that leave them so.

Maintenance offers the files every folder of which has gone, and asking "which files have no
place" of the library is a walk of every file. So the answer is kept: a trigger on every write
that can give a file its first place or take its last one moves it in or out of this table, in the
write's own transaction, and the count is as long as what it counts.
"""

from __future__ import annotations

from sift.kernel.content.schema import CONTENT_COMPONENT
from sift.kernel.db import Connection, register_schema_initializer

COMPONENT = "placeless"
VERSION = 1

_CREATE = (
    "CREATE TABLE IF NOT EXISTS placeless_assets"
    " (asset_id TEXT PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE) WITHOUT ROWID"
)

# No conflict clause in a trigger body: the statement that fired it would override it.
_TRIGGERS = (
    """
CREATE TRIGGER IF NOT EXISTS placeless_file_added AFTER INSERT ON assets BEGIN
  INSERT INTO placeless_assets (asset_id) SELECT NEW.id
   WHERE NOT EXISTS (SELECT 1 FROM asset_locations WHERE asset_id = NEW.id)
     AND NOT EXISTS (SELECT 1 FROM placeless_assets WHERE asset_id = NEW.id);
END
""",
    """
CREATE TRIGGER IF NOT EXISTS placeless_file_removed AFTER DELETE ON assets BEGIN
  DELETE FROM placeless_assets WHERE asset_id = OLD.id;
END
""",
    """
CREATE TRIGGER IF NOT EXISTS placeless_place_added AFTER INSERT ON asset_locations BEGIN
  DELETE FROM placeless_assets WHERE asset_id = NEW.asset_id;
END
""",
    """
CREATE TRIGGER IF NOT EXISTS placeless_place_removed AFTER DELETE ON asset_locations BEGIN
  INSERT INTO placeless_assets (asset_id) SELECT OLD.asset_id
   WHERE EXISTS (SELECT 1 FROM assets WHERE id = OLD.asset_id)
     AND NOT EXISTS (SELECT 1 FROM asset_locations WHERE asset_id = OLD.asset_id)
     AND NOT EXISTS (SELECT 1 FROM placeless_assets WHERE asset_id = OLD.asset_id);
END
""",
    """
CREATE TRIGGER IF NOT EXISTS placeless_place_moved
AFTER UPDATE OF asset_id ON asset_locations WHEN OLD.asset_id IS NOT NEW.asset_id BEGIN
  DELETE FROM placeless_assets WHERE asset_id = NEW.asset_id;
  INSERT INTO placeless_assets (asset_id) SELECT OLD.asset_id
   WHERE NOT EXISTS (SELECT 1 FROM asset_locations WHERE asset_id = OLD.asset_id)
     AND NOT EXISTS (SELECT 1 FROM placeless_assets WHERE asset_id = OLD.asset_id);
END
""",
)

_FILL = (
    "INSERT INTO placeless_assets (asset_id) SELECT a.id FROM assets a"
    " WHERE NOT EXISTS (SELECT 1 FROM asset_locations l WHERE l.asset_id = a.id)"
)

#: The placeless files stranded at or before a moment, or at an unknown one. The place is asked
#: again of each, so a row the triggers kept wrongly can only be too many, never offered. CROSS
#: JOIN keeps the kept rows outermost, as the planner has no statistics for them.
STRANDED = """
SELECT a.id FROM placeless_assets p
CROSS JOIN assets a ON a.id = p.asset_id
WHERE (a.stranded_at IS NULL OR a.stranded_at <= ?)
  AND NOT EXISTS (SELECT 1 FROM asset_locations l WHERE l.asset_id = a.id)
"""


async def initialize(connection: Connection, on_disk: int) -> None:
    if on_disk < 1:
        for statement in (_CREATE, *_TRIGGERS, _FILL):
            await connection.execute(statement)


register_schema_initializer(
    COMPONENT, VERSION, initialize, depends_on=[CONTENT_COMPONENT], baseline=1
)
