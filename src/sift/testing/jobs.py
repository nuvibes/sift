# SPDX-License-Identifier: AGPL-3.0-or-later
"""Helpers for putting jobs, and users, into a running application's database.

Some tests need rows that already exist before the first request: a route that takes a job id has
to be called with one that resolves, or what comes back is a 404 that reads as a refusal rather
than as "there is no such job".

Like the session helpers beside them, these open their own short-lived connection to the
application's database file rather than reaching into the running app's: those belong to the
app's event loop and are not a synchronous test thread's to touch. WAL makes a second writer safe.
They go through the kernel's database handle rather than the driver, which is what everything else
in the app does and means these get the same pragmas as the code they are testing.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from sift.kernel.db import Database

# `root_id` the way the queue's own insert decides it: the parent's family, or the row's own id at
# the top, so a seeded tree folds exactly as a queued one does.
_INSERT_JOB = """
INSERT INTO jobs (id, parent_id, type, state, payload, error, created_at, updated_at, root_id)
VALUES (?, ?, ?, ?, ?, ?, 0, 0,
        COALESCE((SELECT COALESCE(parent.root_id, parent.id) FROM jobs parent WHERE parent.id = ?), ?))
"""

# Written out per column rather than assembled from a name, because the rule against building SQL
# from strings is the rule here too: a helper is exactly where the first exception gets made.
_SET_ROLE = "UPDATE users SET role = ? WHERE id = ?"
_SET_DISABLED = "UPDATE users SET disabled = ? WHERE id = ?"


def _write(db_path: Path, sql: str, params: tuple[object, ...]) -> None:
    async def run() -> None:
        database = Database(db_path, readers=1)
        await database.connect()
        try:
            async with database.write() as connection:
                await connection.execute(sql, params)
        finally:
            await database.close()

    asyncio.run(run())


def seed_job(
    db_path: Path,
    job_id: str,
    *,
    state: str = "failed",
    job_type: str = "probe",
    error: str | None = None,
    payload: dict[str, object] | None = None,
    parent_id: str | None = None,
) -> None:
    """Put one job in the queue, in a known state, with a known id, under `parent_id` if given.

    Note that `queued` is rarely what a test wants. A booted application has real workers in
    it, and a queued job is precisely what they take: it will be claimed, run and settled before
    the test gets to it. `blocked` is the cancellable state that nothing claims.

    `payload` is what says which file or folder the job is about, which is what the dashboard reads
    to put a name on the row. Empty by default: most jobs here are about the queue rather than
    about anything in particular.
    """
    body = json.dumps(payload if payload is not None else {})
    _write(
        db_path,
        _INSERT_JOB,
        (job_id, parent_id, job_type, state, body, error, parent_id, job_id),
    )


_INSERT_RUN = """
INSERT INTO work_runs (id, family, started_at, updated_at, finished_at, jobs_done)
VALUES (?, 'scan', 1, 2, 2, 1)
"""


def seed_run(db_path: Path, run_id: str) -> None:
    """One finished run in the work ledger, with a known id, for a route that reads one back."""
    _write(db_path, _INSERT_RUN, (run_id,))


def set_role(db_path: Path, user_id: str, role: str) -> None:
    """Change a user's role underneath whatever is already talking to it."""
    _write(db_path, _SET_ROLE, (role, user_id))


def set_disabled(db_path: Path, user_id: str, *, disabled: bool = True) -> None:
    """Disable a user underneath whatever is already talking to it."""
    _write(db_path, _SET_DISABLED, (int(disabled), user_id))
