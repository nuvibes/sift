# SPDX-License-Identifier: AGPL-3.0-or-later
"""Three tables: a saved wall, the cells in it, and every time a wall was sat in front of.

An arrangement is a layout plus what each cell was set to, not what any cell was playing. A wall
is a way of watching rather than a place in a video, and storing the clip would make loading an
arrangement re-open files somebody watched days ago rather than start a fresh run.

Scoped to the user exactly as saved searches are, and for the same reasons: it is theirs, it is
read back only to them, and it goes when the user goes. Nothing in an arrangement grants
anything either: a cell's source is a query, and running a query goes through the same engine any
typed search does, which is what decides visibility. A saved wall pointed at somebody else's
restricted files draws empty cells.
"""

from __future__ import annotations

from sift.kernel.db import Connection, register_schema_initializer

COMPONENT = "theater"
VERSION = 7

_CREATE_ARRANGEMENTS = """
CREATE TABLE IF NOT EXISTS theater_arrangements (
  id         TEXT PRIMARY KEY,
  user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name       TEXT NOT NULL,
  -- The layout the wall is in, or `custom` for one that was built. Kept beside the shape rather
  -- than replaced by it: a wall saved before walls could be built has only this, and it is what a
  -- shape that cannot be drawn falls back to.
  layout     TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  -- The grid, and where each cell sits in it, as JSON. Null on a wall stored under a layout name
  -- alone, which is drawn from `layout`: the name IS a shape, and the client turns it into one.
  shape      TEXT,
  -- How many PREVIEWS the wall has in the strip under it. Beside the shape rather than inside it,
  -- because it is not part of the grid: the strip is a row under the wall and the shape is the
  -- wall. See the client's `layouts.ts` for the arithmetic.
  strip      INTEGER NOT NULL DEFAULT 0,
  -- One name to one wall, per account. Saving under a name already used is a conflict rather than
  -- a replace: a wall is several cells' worth of setting up, and quietly overwriting one because
  -- the name matched is the kind of loss somebody only notices later.
  UNIQUE(user_id, name)
)
"""

# A cell of a saved wall.
#
# No source KIND column, deliberately. A cell's source is a query and there is no second kind of
# source it could be, so a column that can only ever hold one value would be a choice the schema
# claims to offer and nothing can make. Pinning one file is a query that matches one file.
_CREATE_CELLS = """
CREATE TABLE IF NOT EXISTS theater_cells (
  arrangement_id TEXT NOT NULL REFERENCES theater_arrangements(id) ON DELETE CASCADE,
  position       INTEGER NOT NULL,
  source         TEXT NOT NULL,
  media_kind     TEXT NOT NULL,
  ordering       TEXT NOT NULL,
  end_behaviour  TEXT NOT NULL,
  -- Null means no timer: the cell moves on when the clip ends and not before. A cell of photographs
  -- always carries one, because a still has no end of its own to reach.
  timer_seconds  INTEGER,
  volume         INTEGER NOT NULL,
  -- How the source is searched, when that is a question at all.
  --
  -- Null means the ordinary answer, and nearly every cell is null. It exists because a saved search
  -- can be a SMART one (ranked by meaning rather than matched by word), and that is not an order
  -- laid over the same files, it is a different set of files entirely. A cell that took the query
  -- and dropped this would turn a search returning a couple of hundred into one returning none,
  -- and say "nothing here matches what this cell is set to", which is true and useless.
  sort           TEXT,
  -- What shape the cell is held to, or `dynamic` for the shape of whatever it is playing.
  --
  -- Free text, and not checked against a list anywhere on this side. A shape decides how the
  -- BROWSER draws a cell; the server never draws one, and the client falls back to Dynamic for a
  -- name it cannot draw. So a seventh shape is a client-only change rather than a release of both
  -- halves, and there is no second copy of the list here to drift from the one that matters.
  aspect         TEXT,
  PRIMARY KEY (arrangement_id, position)
)
"""

_INDEXES = (
    # The only way arrangements are ever read: one user's, newest first.
    "CREATE INDEX IF NOT EXISTS ix_theater_arrangements_user "
    "ON theater_arrangements(user_id, created_at)",
)


#: One row per Theater session: when a wall was opened and closed, how it was laid out, what each
#: cell drew from, which saved wall it was, and how many files it showed. See `sessions.py` for why
#: a row of its own and how it is written.
#:
#: `session` is the name the wall minted when it opened, and it is unique per user rather than
#: alone, for the reason `plays.sitting` is: it is the one value here a browser chose, so keyed on
#: it alone one user's name could land on another user's row. `arrangement_id` carries no key: a
#: session outlives the saved wall it used, exactly as a sitting outlives its file.
_CREATE_SESSIONS = """
CREATE TABLE IF NOT EXISTS theater_sessions (
  id             TEXT PRIMARY KEY,
  user_id        TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  session        TEXT NOT NULL,
  started_at     INTEGER NOT NULL,
  ended_at       INTEGER,
  layout         TEXT,
  cells          INTEGER,
  arrangement_id TEXT,
  sources        TEXT,
  files          INTEGER NOT NULL DEFAULT 0,
  made_at        INTEGER NOT NULL,
  UNIQUE(user_id, session)
)
"""

#: A person's Theater time, in order: the one way these rows are read.
_SESSION_INDEX = (
    "CREATE INDEX IF NOT EXISTS ix_theater_sessions_user ON theater_sessions(user_id, started_at)"
)


async def initialize(connection: Connection, on_disk: int) -> None:
    if on_disk < 1:
        await connection.execute(_CREATE_ARRANGEMENTS)
        await connection.execute(_CREATE_CELLS)
        for index in _INDEXES:
            await connection.execute(index)
        await connection.execute(_CREATE_SESSIONS)
        await connection.execute(_SESSION_INDEX)


# `users` comes from the identity component, so the key names a table that exists by the time this
# runs.
register_schema_initializer(COMPONENT, VERSION, initialize, depends_on=["identity"], baseline=7)
