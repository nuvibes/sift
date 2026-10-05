# SPDX-License-Identifier: AGPL-3.0-or-later
"""The `jobs` table.

One table. Everything slow in Sift (scanning a folder, probing a file, making a thumbnail,
downloading, transcoding, emptying the trash) is a row in it, and the row is the only thing
that survives a restart. Nothing about a job lives in memory that is not first written here.
"""

from __future__ import annotations

from sift.kernel.db import Connection, register_schema_initializer
from sift.kernel.migrations import column_exists

COMPONENT = "jobs"
VERSION = 15

# The state list is repeated in `JobState`, since a CHECK takes no placeholder; a test holds the two
# in step. `paused` is a state, not a flag, so nothing that asks of the state can claim it.
# `to_read` is a walk's files still to read by media kind, as JSON; NULL until it is counted.
_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS jobs (
  id           TEXT PRIMARY KEY,
  parent_id    TEXT REFERENCES jobs(id) ON DELETE CASCADE,
  type         TEXT NOT NULL,
  state        TEXT NOT NULL DEFAULT 'queued'
               CHECK(state IN ('queued','running','done','failed','canceled','blocked','paused')),
  priority     INTEGER NOT NULL DEFAULT 100,
  payload      TEXT NOT NULL,
  progress     REAL NOT NULL DEFAULT 0,
  attempts     INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 3,
  claimed_by   TEXT,
  heartbeat_at INTEGER,
  error        TEXT,
  -- What a job did, in a sentence, for whoever asked for it, in the handler's own words: the
  -- counterpart of `error`, so a job that succeeded and did nothing can say so.
  note         TEXT,
  -- The earliest moment a job may be claimed. NULL is "now", which almost every job is, and the
  -- claim reads `run_after IS NULL OR run_after <= ?`. Recurring maintenance is a row like
  -- everything else rather than a timer in memory, so a machine switched off for a week comes back
  -- and runs it.
  run_after    INTEGER,
  created_at   INTEGER NOT NULL,
  updated_at   INTEGER NOT NULL,
  -- How many files this job is about: one for the ordinary job, and a scan's count once it knows.
  -- What is LEFT of a kind is units times the unfinished fraction rather than a count of rows.
  units        INTEGER NOT NULL DEFAULT 1,
  -- The moment a worker actually picked this job up, set on the claim. `created_at` is not it: a
  -- recurring job's row is written when its predecessor finishes. A retry overwrites it.
  started_at   INTEGER,
  -- How a pause reaches a job that is already running, written here because the process running it
  -- is disposable. A word rather than a flag, so the handler is told WHY it is asked to stop;
  -- `pause` is the only word written today. Cleared by the claim, with `error`.
  stop_wanted  TEXT,
  -- The user whose press put this job in the queue, so the history's `ran` line can name who did
  -- it. A user id, not a name and not a key: a job row is pruned within a week. NULL is nobody's
  -- press (a schedule, a file arriving, a pass handing out its own work), and a child does not
  -- inherit it.
  requested_by TEXT,
  -- Whether this row was asked for AT A TIME: `now` runs at once whatever quiet hours say, `quiet`
  -- is held to quiet hours. NULL is work nobody pressed, whose timing is its task's When, read at
  -- the claim. Unlike `requested_by` it reaches the children of the same family
  -- (`JobContext.enqueue_child`).
  timing       TEXT,
  -- The top of the family this row belongs to, or its own id when it IS the top. Written once at
  -- the insert (`JobQueue.enqueue` copies the parent's) and never moved, so a family's steps are
  -- read through `ix_jobs_family` rather than a recursive walk of the tree.
  root_id      TEXT,
  to_read      TEXT
)
"""

# The queue is ordered BY ID, not by the wall clock: `created_at` is a wall-clock second and a
# clock steps backwards, while an id is a ULID minted under a floor that never goes down
# (`kernel.ids.new_id`), so it is the order the jobs were queued in whatever the clock did.
# The claim's scan path: the oldest queued job of the highest priority, and nothing else read.
# `run_after` last, so the claim's condition and a count of the line (`_POSITIONS` in the queue)
# are answered from the index without reading a row. Version 14.
_CLAIM_INDEX = (
    "CREATE INDEX IF NOT EXISTS ix_jobs_claim_by_id ON jobs(state, priority, id, run_after)"
)

_INDEXES = (
    _CLAIM_INDEX,
    # The watchdog's: running rows whose heartbeat has gone quiet.
    "CREATE INDEX IF NOT EXISTS ix_jobs_watchdog ON jobs(state, heartbeat_at)",
    # Fan-out. Rolling a child's progress up to its parent, cancelling a parent's whole tree, and
    # the cascade behind the foreign key all filter on this; without it each one is a full scan.
    "CREATE INDEX IF NOT EXISTS ix_jobs_parent ON jobs(parent_id)",
    # The queue SCREEN: the newest rows of one state. The unfiltered list is the primary key's own
    # index walked backwards and needs nothing here.
    "CREATE INDEX IF NOT EXISTS ix_jobs_state_by_id ON jobs(state, id)",
    # A family's steps counted by state, and whether any is failed or running: each one a seek on
    # (family, state), capped, rather than a walk of the tree. See `JobQueue.step_counts`.
    "CREATE INDEX IF NOT EXISTS ix_jobs_family ON jobs(root_id, state)",
    # A family's steps listed in the order they were handed out, a page at a time.
    "CREATE INDEX IF NOT EXISTS ix_jobs_family_by_id ON jobs(root_id, id)",
    # The top rows, newest first: the page Activity folds. Partial, so it holds the tops only.
    # `root_id = id` rather than `parent_id IS NULL` (the same rows) because the planner meets the
    # second with `ix_jobs_parent` and sorts every top instead; see `_FILTERS` in the queue.
    "CREATE INDEX IF NOT EXISTS ix_jobs_tops_by_id ON jobs(id) WHERE root_id = id",
    # Each type's newest run, which the prune never takes (see `_PRUNE_SETTLED`). Partial, so it
    # holds settled runs only. The WHERE must match the prune's own terms word for word, or the
    # planner cannot use it.
    "CREATE INDEX IF NOT EXISTS ix_jobs_last_run ON jobs(type, updated_at, id)"
    " WHERE state IN ('done', 'canceled') AND started_at IS NOT NULL",
)

# The live questions asked of one kind of work, and the quiet rows Activity leaves out. Version 13.
#
# A live row (waiting, running, held) is a handful among a table of settled ones, and the
# statistics are taken when the queue is empty, so they say a state holds a fifth of the table:
# with no index that starts from the type, "is any of this type waiting" is a read of every row.
_BY_TYPE_INDEXES = (
    # Every question about one type: its rows in one state, newest first; how many it has in each
    # state; whether any is live. Type first, so a count of a type is a range of this index, and
    # `id` last, so a page of one type in one state is read in the order the list draws it.
    "CREATE INDEX IF NOT EXISTS ix_jobs_by_type ON jobs(type, state, id)",
    # The tops nobody pressed, by type and state: what the list's `quiet` rule leaves out, counted.
    # Partial, so the count reads these rows only, and its WHERE is the rule's own two terms word
    # for word (`_QUIET_COUNT_HEAD`), or the planner cannot use it.
    "CREATE INDEX IF NOT EXISTS ix_jobs_unpressed_tops ON jobs(type, state)"
    " WHERE root_id = id AND requested_by IS NULL",
)


async def initialize(connection: Connection, on_disk: int) -> None:
    if on_disk < 1:
        await connection.execute(_CREATE_TABLE)
        for index in (*_INDEXES, *_BY_TYPE_INDEXES):
            await connection.execute(index)
    if 0 < on_disk < 13:
        for index in _BY_TYPE_INDEXES:
            await connection.execute(index)
    if 0 < on_disk < 14:
        await connection.execute("DROP INDEX IF EXISTS ix_jobs_claim_by_id")
        await connection.execute(_CLAIM_INDEX)
    if 0 < on_disk < 15 and not await column_exists(connection, "jobs", "to_read"):
        await connection.execute("ALTER TABLE jobs ADD COLUMN to_read TEXT")


register_schema_initializer(COMPONENT, VERSION, initialize, baseline=12)
