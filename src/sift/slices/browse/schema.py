# SPDX-License-Identifier: AGPL-3.0-or-later
"""One table: a record of who saved a file onto their own machine, and when.

Watching is already counted per person elsewhere. Saving is different because it is the one action
that puts a copy somewhere Sift will never see again, so it is the one an admin may want to look
back at, and looking at it is admin-only.

Be honest about what it is: a record, not a control. Anyone who can play a file can keep the bytes
that made it play. This says what happened; it does not stop anything.

Both references are deliberately weak, and in different directions. Deleting a user takes their
saves with it: the record is about a person, and a person removed from the instance should not
leave one behind. Deleting an asset keeps the row and clears the reference, so the log still says
that something was saved, and when, but can no longer say what: the file it named is gone, and
holding a dangling id would only claim more than the row can support.

That is a real limit rather than a design goal: an admin reading the log months later gets "a save
happened" for anything since removed, not "this file left". If naming the file after its deletion
ever matters, the column has to stop being a foreign key and start being a copy of the name.
"""

from __future__ import annotations

from sift.kernel.db import Connection, register_schema_initializer

SAVE_LOG_COMPONENT = "save_log"
SAVE_LOG_VERSION = 1

_CREATE_SAVE_LOG = """
CREATE TABLE IF NOT EXISTS save_log (
  id       TEXT PRIMARY KEY,
  user_id  TEXT REFERENCES users(id)  ON DELETE CASCADE,
  asset_id TEXT REFERENCES assets(id) ON DELETE SET NULL,
  saved_at INTEGER NOT NULL
)
"""

_INDEXES = (
    # An admin-only view: one person's saves, most recent first.
    "CREATE INDEX IF NOT EXISTS ix_save_user ON save_log(user_id, saved_at)",
)


async def initialize_save_log(connection: Connection, on_disk: int) -> None:
    if on_disk < 1:
        await connection.execute(_CREATE_SAVE_LOG)
        for index in _INDEXES:
            await connection.execute(index)


# `users` comes from the identity component and `assets` from content, so both are built before
# these keys name them.
register_schema_initializer(
    SAVE_LOG_COMPONENT,
    SAVE_LOG_VERSION,
    initialize_save_log,
    depends_on=["content", "identity"],
    baseline=1,
)
