# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a swap records: this install's device, each session, and each file's received chunks.

The token's secret is never stored, and no row holds a peer's address. A session counts both
ways: the sending columns are host to guest, the `back_` columns guest to host.
"""

from __future__ import annotations

from sift.kernel.db import Connection, register_schema_initializer
from sift.kernel.log import get_logger
from sift.kernel.migrations import column_exists

log = get_logger(__name__)

COMPONENT = "swap"
VERSION = 5

# `CHECK (id = 1)`: one install, one device id.
_CREATE_DEVICE = """
CREATE TABLE IF NOT EXISTS swap_device (
  id             INTEGER PRIMARY KEY CHECK (id = 1),
  device_id      TEXT NOT NULL,
  key_secret_id  TEXT NOT NULL,
  created_at     INTEGER NOT NULL
)
"""

_CREATE_SESSIONS = """
CREATE TABLE IF NOT EXISTS swap_sessions (
  id            TEXT PRIMARY KEY,
  role          TEXT NOT NULL CHECK (role IN ('host', 'guest')),
  state         TEXT NOT NULL CHECK (state IN (
    'waiting', 'connected', 'offered', 'transferring', 'done', 'ended', 'failed')),
  tunnel_id     TEXT,
  peer_device   TEXT,
  token_expires INTEGER,
  chosen        TEXT NOT NULL DEFAULT '[]',
  offered_files INTEGER NOT NULL DEFAULT 0,
  wanted_files  INTEGER NOT NULL DEFAULT 0,
  sent_files    INTEGER NOT NULL DEFAULT 0,
  sent_bytes    INTEGER NOT NULL DEFAULT 0,
  rate_bps      INTEGER,
  dest_folder_id TEXT,
  started_at    INTEGER NOT NULL,
  ended_at      INTEGER,
  end_reason    TEXT
)
"""

_CREATE_MANIFESTS = """
CREATE TABLE IF NOT EXISTS swap_manifests (
  session_id   TEXT NOT NULL REFERENCES swap_sessions(id) ON DELETE CASCADE,
  file_key     TEXT NOT NULL,
  size         INTEGER NOT NULL,
  chunk_size   INTEGER NOT NULL,
  done         TEXT NOT NULL DEFAULT '[]',
  digest       TEXT,
  staged_path  TEXT,
  updated_at   INTEGER NOT NULL,
  PRIMARY KEY (session_id, file_key)
) WITHOUT ROWID
"""

# When a tunnel cut the session off, or NULL: a column, since rebuilding the table would cascade
# its DROP into every verified chunk.
_ADD_CUT_OFF_AT = "ALTER TABLE swap_sessions ADD COLUMN cut_off_at INTEGER"

# The second direction, added a column at a time where missing, for the same reason.
_ADD_TWO_WAY = (
    ("two_way", "ALTER TABLE swap_sessions ADD COLUMN two_way INTEGER NOT NULL DEFAULT 0"),
    (
        "back_offered",
        "ALTER TABLE swap_sessions ADD COLUMN back_offered INTEGER NOT NULL DEFAULT 0",
    ),
    ("back_wanted", "ALTER TABLE swap_sessions ADD COLUMN back_wanted INTEGER NOT NULL DEFAULT 0"),
    ("back_files", "ALTER TABLE swap_sessions ADD COLUMN back_files INTEGER NOT NULL DEFAULT 0"),
    ("back_bytes", "ALTER TABLE swap_sessions ADD COLUMN back_bytes INTEGER NOT NULL DEFAULT 0"),
)

# The folder a swap made for what it landed, by id, so a rename does not lose it.
_ADD_FOLDER_ID = "ALTER TABLE swap_sessions ADD COLUMN folder_id TEXT"

# Before the column: the folder named `Swap-<short id>` in the chosen folder, if still there.
_FILL_FOLDER_ID = """
UPDATE swap_sessions
   SET folder_id = (SELECT f.id FROM folders f
                     WHERE f.parent_id = swap_sessions.dest_folder_id
                       AND f.name = 'Swap-' || substr(swap_sessions.id, -8))
 WHERE folder_id IS NULL AND dest_folder_id IS NOT NULL
"""

# The sender's version of the file, for a sender that checks the whole at the end (`pieces`).
_ADD_VERSION = "ALTER TABLE swap_manifests ADD COLUMN version TEXT"

_INDEXES = (
    # The boot step ends every session a restart interrupted, by state.
    "CREATE INDEX IF NOT EXISTS ix_swap_sessions_state ON swap_sessions(state)",
    # The week's sweep reads manifests oldest first.
    "CREATE INDEX IF NOT EXISTS ix_swap_manifests_age ON swap_manifests(updated_at)",
)


async def initialize(connection: Connection, on_disk: int) -> None:
    if on_disk < 1:
        await connection.execute(_CREATE_DEVICE)
        await connection.execute(_CREATE_SESSIONS)
        await connection.execute(_CREATE_MANIFESTS)
        for statement in _INDEXES:
            await connection.execute(statement)
    if on_disk < 2:
        await connection.execute(_ADD_CUT_OFF_AT)
    if on_disk < 3:
        await _add_two_way(connection)
    if on_disk < 4:
        await _add_folder_id(connection, on_disk)
    if on_disk < 5 and not await column_exists(connection, "swap_manifests", "version"):
        await connection.execute(_ADD_VERSION)


async def _add_two_way(connection: Connection) -> None:
    have = {
        str(row["name"])
        for row in await connection.execute_fetchall("PRAGMA table_info(swap_sessions)")
    }
    for name, statement in _ADD_TWO_WAY:
        if name not in have:
            await connection.execute(statement)


async def _add_folder_id(connection: Connection, on_disk: int) -> None:
    if not await column_exists(connection, "swap_sessions", "folder_id"):
        await connection.execute(_ADD_FOLDER_ID)
    if on_disk > 0:
        await connection.execute(_FILL_FOLDER_ID)
        found = list(
            await connection.execute_fetchall(
                "SELECT COUNT(*) AS n FROM swap_sessions WHERE folder_id IS NOT NULL"
            )
        )
        log.info("swap.folders_known", swaps=int(found[0]["n"]))


register_schema_initializer(COMPONENT, VERSION, initialize, depends_on=["content"], baseline=1)
