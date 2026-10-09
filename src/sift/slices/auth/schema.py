# SPDX-License-Identifier: AGPL-3.0-or-later
"""The `sessions` table: server-side rows, so a logout really ends one and access is read fresh.

Only the token's hash is stored, so a stolen database yields no cookie to replay."""

from __future__ import annotations

from sift.kernel.db import Connection, register_schema_initializer
from sift.kernel.migrations import column_exists

SESSIONS_COMPONENT = "sessions"
SESSIONS_VERSION = 3


# `locked_at` makes the lock the server's; `unlock_failures` caps PIN guesses per session.
_CREATE_SESSIONS = """
CREATE TABLE IF NOT EXISTS sessions (
  id              TEXT PRIMARY KEY,
  user_id         TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash      TEXT NOT NULL UNIQUE,
  created_at      INTEGER NOT NULL,
  last_seen_at    INTEGER NOT NULL,
  expires_at      INTEGER NOT NULL,
  locked_at       INTEGER,
  unlock_failures INTEGER NOT NULL DEFAULT 0,
  device_id       TEXT,
  client_kind     TEXT
)
"""

# The device outlives the session, so it is a column, not the session's id.
_ADD_DEVICE = (
    ("device_id", "ALTER TABLE sessions ADD COLUMN device_id TEXT"),
    ("client_kind", "ALTER TABLE sessions ADD COLUMN client_kind TEXT"),
)

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_sessions_user ON sessions(user_id)",
    "CREATE INDEX IF NOT EXISTS ix_sessions_expires ON sessions(expires_at)",
)


async def initialize_sessions(connection: Connection, on_disk: int) -> None:
    if on_disk < 1:
        await connection.execute(_CREATE_SESSIONS)
        for index in _INDEXES:
            await connection.execute(index)
    if 0 < on_disk < 3:
        # Only where missing, so a replayed step adds nothing.
        for column, statement in _ADD_DEVICE:
            if not await column_exists(connection, "sessions", column):
                await connection.execute(statement)


register_schema_initializer(
    SESSIONS_COMPONENT,
    SESSIONS_VERSION,
    initialize_sessions,
    depends_on=["identity"],
    baseline=2,
)
