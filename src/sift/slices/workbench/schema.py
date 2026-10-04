# SPDX-License-Identifier: AGPL-3.0-or-later
"""The ledger: what was decided or done, and enough to put it back.

One press can be hundreds of writes, so every decision records what it did as it did it, and undo
reads that record: it touches only what the decision touched, a mistake is findable a day later,
and its sentence describes the library as it was. Nothing is deleted; a reversal stamps
`reversed_at`. The payload is opaque to this slice (only the area that made a decision knows how to
undo it), so `workbench_decision_subjects` is a SECOND record, written in the same transaction, of
what each decision was about (a kind and an id) that anything may read.

Every act in the library leaves a row here, so a file's, a person's and the install's history are
readings of ONE record: a verb from one closed list (`kernel/ledger.py`), an object with the name it
had at the time, an actor snapshot that outlives a deleted user (`user_id` goes NULL), and a count
for a whole-library pass's one event. `kernel/ledger.py` is the only writer, and its one event per
file rule keeps this from becoming the largest table.
"""

from __future__ import annotations

from sift.kernel.db import Connection, register_schema_initializer
from sift.kernel.migrations import column_exists

COMPONENT = "workbench"
VERSION = 16

# `user_id` carries a key with `ON DELETE SET NULL`; `actor_kind` and `actor_id` are the snapshot
# that keeps saying who did an act after the user is deleted (see the module docstring).
# `object_name` is the name the object had AT THE TIME, left NULL where nobody wrote it down: the
# name it has today would be a lookup posing as a snapshot.
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

# The client an act was taken from (`kernel/client.py`: the window kind and device id), stamped by
# the door on a signed-in User's acts in a request; NULL for Sift's own acts and wherever unsaid.
_ADD_CLIENT = (
    ("client_kind", "ALTER TABLE workbench_decisions ADD COLUMN client_kind TEXT"),
    ("device_id", "ALTER TABLE workbench_decisions ADD COLUMN device_id TEXT"),
)

_INDEXES = (
    # The record screen: most recent first. The ordering is what the screen is for.
    "CREATE INDEX IF NOT EXISTS ix_workbench_decided ON workbench_decisions(decided_at DESC, id DESC)",
    # What has been done to this thing, newest first: the whole-install feed read the other way.
    "CREATE INDEX IF NOT EXISTS ix_workbench_object"
    " ON workbench_decisions(object_kind, object_id, decided_at DESC)",
    # Everything ONE ACTOR did, in order (a user's History thread): on `actor_id`, the snapshot that
    # survives deletion. Ids are ULIDs and pass names words, so `actor_kind` is not needed.
    "CREATE INDEX IF NOT EXISTS ix_workbench_actor ON workbench_decisions(actor_id, decided_at DESC)",
)

# `subject_id` carries no foreign key, deliberately: a link row vanishing with the person would
# quietly rewrite the past. `name` is what the subject was CALLED at the moment the event happened,
# which is the difference between "a person was removed" and a named person being removed. NULL
# where nobody wrote it down, for the reason `object_name` is.
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
    # The question the history screen asks, "which decisions touched this file"; without it, a walk
    # of every link row to draw one pane.
    "CREATE INDEX IF NOT EXISTS ix_workbench_subject"
    " ON workbench_decision_subjects(kind, subject_id)",
)


async def initialize_workbench(connection: Connection, on_disk: int) -> None:
    if on_disk < 1:
        for statement in (_CREATE_DECISIONS, *_INDEXES, _CREATE_SUBJECTS, *_SUBJECT_INDEXES):
            await connection.execute(statement)
    if 0 < on_disk < 16:
        # Each only where it is missing, so a step replayed over a table that has it adds nothing.
        for column, statement in _ADD_CLIENT:
            if not await column_exists(connection, "workbench_decisions", column):
                await connection.execute(statement)


# `user_id` references `users` (the identity component). It leads: other components' steps that
# record what they did write these tables in this build's shape.
register_schema_initializer(
    COMPONENT, VERSION, initialize_workbench, depends_on=["identity"], baseline=15, leads=True
)
