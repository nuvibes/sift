# SPDX-License-Identifier: AGPL-3.0-or-later
"""The `sessions` table.

`users` is not here. It is created by the kernel, because permission resolution joins it on every
request and the kernel cannot depend on a feature built on top of it. This slice owns the sessions
that hang off those users, and nothing else in the schema.

A session is a server-side row, not a self-contained token. That is deliberate and it is what
makes two things possible: logging out actually invalidates a login rather than hoping a token
expires, and every permission (including whether the user still exists and is still enabled)
is read fresh from the database on each request instead of being trusted from something the client
holds. A stateless token would have to carry a copy of the answer, and a copy is a thing that goes
stale the moment an admin revokes access.

Only the hash of the token is stored. The token itself lives in the caller's cookie and nowhere on
the server, so a stolen database yields no cookie anyone can replay.
"""

from __future__ import annotations

from sift.kernel.db import Connection, register_schema_initializer
from sift.kernel.migrations import column_exists

SESSIONS_COMPONENT = "sessions"
SESSIONS_VERSION = 3


# `locked_at` is what makes the app lock the server's rather than the screen's.
#
# While it is set the server refuses every authenticated request on this session, not just a page
# request, but the cookie replayed by hand from anywhere. That is the whole difference between a
# lock and a cover: a cover leaves the session working, so another tab, a reload or a command-line
# client walks straight past it.
#
# `unlock_failures` counts wrong PINs against a locked session, and the session is destroyed once
# there have been a few. A PIN is six digits and can only be tried against a session that
# already exists, so the standing per-user throttle is not enough on its own: this caps the
# guessing at a handful per session rather than at a rate.
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

# Version 3: the device a session began on and the kind of window it was (`kernel/client.py`),
# written at sign-in and filled in for a session signed in before this step the first time it asks
# who it is. NULL until then. The device outlives the session (it is the browser or the app
# install), which is why it is a column here and not the session's own id.
_ADD_DEVICE = (
    ("device_id", "ALTER TABLE sessions ADD COLUMN device_id TEXT"),
    ("client_kind", "ALTER TABLE sessions ADD COLUMN client_kind TEXT"),
)

_INDEXES = (
    # Every logout-everywhere and every disable-the-user walks a user's sessions.
    "CREATE INDEX IF NOT EXISTS ix_sessions_user ON sessions(user_id)",
    # Expiry cleanup scans by the horizon rather than by user.
    "CREATE INDEX IF NOT EXISTS ix_sessions_expires ON sessions(expires_at)",
)


async def initialize_sessions(connection: Connection, on_disk: int) -> None:
    if on_disk < 1:
        await connection.execute(_CREATE_SESSIONS)
        for index in _INDEXES:
            await connection.execute(index)
    if 0 < on_disk < 3:
        # Each only where it is missing, so a step replayed over a table that has it adds nothing.
        for column, statement in _ADD_DEVICE:
            if not await column_exists(connection, "sessions", column):
                await connection.execute(statement)


# `users` lives in the kernel's identity component; sessions reference it, so it must exist first.
register_schema_initializer(
    SESSIONS_COMPONENT,
    SESSIONS_VERSION,
    initialize_sessions,
    depends_on=["identity"],
    baseline=2,
)
