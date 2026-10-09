# SPDX-License-Identifier: AGPL-3.0-or-later
"""The ledger: what was decided or done, and enough to put it back."""

from __future__ import annotations

from sift.kernel.access.history_presses import keep_presses
from sift.kernel.db import Connection, register_schema_initializer, register_schema_invariant
from sift.kernel.migrations import column_exists

COMPONENT = "workbench"
VERSION = 19

# `actor_kind` and `actor_id` outlive a deleted user; `object_name` is the name at the time.
_CREATE_DECISIONS = """
CREATE TABLE IF NOT EXISTS workbench_decisions (
  id          TEXT PRIMARY KEY,
  queue       TEXT NOT NULL,
  user_id     TEXT REFERENCES users(id) ON DELETE SET NULL,
  title       TEXT NOT NULL,
  detail      TEXT NOT NULL,
  payload     TEXT NOT NULL,
  decided_at  INTEGER NOT NULL,
  reversed_at INTEGER,
  verb        TEXT,
  actor_kind  TEXT,
  actor_id    TEXT,
  object_kind TEXT,
  object_id   TEXT,
  object_name TEXT,
  touched     INTEGER,
  client_kind TEXT,
  device_id   TEXT
)
"""

_ADD_CLIENT = (
    ("client_kind", "ALTER TABLE workbench_decisions ADD COLUMN client_kind TEXT"),
    ("device_id", "ALTER TABLE workbench_decisions ADD COLUMN device_id TEXT"),
)

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_workbench_decided ON workbench_decisions(decided_at DESC, id DESC)",
    "CREATE INDEX IF NOT EXISTS ix_workbench_object"
    " ON workbench_decisions(object_kind, object_id, decided_at DESC)",
    # Everything one actor did, on the snapshot that survives deletion.
    "CREATE INDEX IF NOT EXISTS ix_workbench_actor ON workbench_decisions(actor_id, decided_at DESC)",
)

# No foreign key on `subject_id`: a link vanishing with the person would rewrite the past.
_CREATE_SUBJECTS = """
CREATE TABLE IF NOT EXISTS workbench_decision_subjects (
  decision_id TEXT NOT NULL REFERENCES workbench_decisions(id) ON DELETE CASCADE,
  kind        TEXT NOT NULL,
  subject_id  TEXT NOT NULL,
  name        TEXT,
  PRIMARY KEY (decision_id, kind, subject_id)
)
"""

_SUBJECT_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_workbench_subject"
    " ON workbench_decision_subjects(kind, subject_id)",
)


async def initialize_workbench(connection: Connection, on_disk: int) -> None:
    if on_disk < 1:
        for statement in (_CREATE_DECISIONS, *_INDEXES, _CREATE_SUBJECTS, *_SUBJECT_INDEXES):
            await connection.execute(statement)
    if 0 < on_disk < 16:
        # Each only where it is missing, so a replayed step adds nothing.
        for column, statement in _ADD_CLIENT:
            if not await column_exists(connection, "workbench_decisions", column):
                await connection.execute(statement)
    if on_disk < 19:
        await keep_presses(connection)


register_schema_initializer(
    COMPONENT, VERSION, initialize_workbench, depends_on=["identity"], baseline=15, leads=True
)
# Every boot as well: a rebuild of the table takes its triggers.
register_schema_invariant("workbench_presses", keep_presses)
