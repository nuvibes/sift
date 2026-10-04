# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a swap records: this install's device, each session, and each file's received chunks.

## Three tables, and what none of them holds

`swap_device` is one row: the id this install is known by to a peer, and which sealed secret holds
the Ed25519 key it was made from. The key itself is in the `secrets` table, sealed under the
admin's master key like a stash-box's key; this row only remembers which secret it is.

`swap_sessions` is one row per swap, host or guest, from the press to its end: the state, the
counts, the session's own measured rate (the screen's estimate reads it) and why it ended. The
token's SECRET is never here: a row is readable by anybody holding a backup, and the secret is
the whole lock, so a session cannot outlive the process that holds it in memory, and the boot
step in `session.py` ends every one a restart interrupted.

`swap_manifests` is one row per file a guest is receiving: its size, the chunk size, the chunks
already verified (`done`, a JSON list), where they are staged, and which bytes they are of: the
whole file's digest, or the sender's `version` of it where the whole is checked at the end. A
dropped connection resumes at the first missing chunk from here, and an unfinished row is kept a
week so a restart's next swap with the same device can adopt it (`store.SessionStore.adopt`).

## A swap that sends and receives

A session can carry files both ways (`two_way`). The columns named for sending (`offered_files`,
`wanted_files`, `sent_files`, `sent_bytes`) keep their meaning on every row: they count the
direction from the host to the guest, sent on the host's row and received on the guest's. The
`back_` columns count the other direction, from the guest to the host: received on the host's row
and sent on the guest's. A one-way session leaves them at zero, so a row reads the same whichever
build wrote it.

**No row anywhere holds a peer's address.** The token carries the host's VPN address to the guest
and nothing writes it down: not these rows, not the log, not the job's payload.

## `depends_on=["content"]`, and what it does not do

None of the three names another component's table, so today the dependency orders nothing: these
tables would come up correctly before or after `content`. It is declared so that the day a swap
table gains a foreign key to `assets` (a received file's row, say), the order is already right,
and it costs nothing until then. It is NOT what makes a landing safe; that is the import's own.
"""

from __future__ import annotations

from sift.kernel.db import Connection, register_schema_initializer
from sift.kernel.log import get_logger
from sift.kernel.migrations import column_exists

log = get_logger(__name__)

COMPONENT = "swap"
VERSION = 5

# `CHECK (id = 1)`: one install, one device id. A second row would be a second name for the same
# install with nothing to say which one a peer was told.
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

# When a tunnel cut the session off, or NULL while it is not. A column beside the state rather than a
# state of its own: the session is still in the step it was in (offered, transferring) and resumes
# there when the same token joins again, so the step is kept and the cut is said beside it. A new
# value in the state's CHECK would also have meant rebuilding the table, and the manifests' foreign
# key cascades a rebuild's DROP into every chunk a guest has verified.
_ADD_CUT_OFF_AT = "ALTER TABLE swap_sessions ADD COLUMN cut_off_at INTEGER"

# The second direction of a swap that sends and receives (see the module docstring). Added one
# column at a time, each only where it is missing, for the same reason `cut_off_at` is a column: a
# rebuild would cascade into every manifest.
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

# THE FOLDER A SWAP MADE for everything it landed (`ingest.swap_folder`), by its id: written by the
# landing as it makes it, so the folder is known as the swap's by what it is rather than by its name,
# and a person renaming it does not take that away (`landed_folders`). NULL on a session that
# landed nothing.
_ADD_FOLDER_ID = "ALTER TABLE swap_sessions ADD COLUMN folder_id TEXT"

# A session that landed files before the column was there: its folder is the one the landing
# named `Swap-<short id>` directly inside the folder it chose. Found by the folder index on the
# parent and the name; a folder renamed or deleted since is not found, and that session's row
# stays empty.
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
        have = {
            str(row["name"])
            for row in await connection.execute_fetchall("PRAGMA table_info(swap_sessions)")
        }
        for name, statement in _ADD_TWO_WAY:
            if name not in have:
                await connection.execute(statement)
    if on_disk < 4:
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
    if on_disk < 5 and not await column_exists(connection, "swap_manifests", "version"):
        await connection.execute(_ADD_VERSION)


register_schema_initializer(COMPONENT, VERSION, initialize, depends_on=["content"], baseline=1)
