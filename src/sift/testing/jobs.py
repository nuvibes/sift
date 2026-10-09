# SPDX-License-Identifier: AGPL-3.0-or-later
"""Put jobs and users into a running app's database through a short-lived connection."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from sift.kernel.db import Database

# `root_id` as the queue's own insert decides it, so a seeded tree folds as a queued one does.
_INSERT_JOB = """
INSERT INTO jobs (id, parent_id, type, state, payload, error, created_at, updated_at, root_id)
VALUES (?, ?, ?, ?, ?, ?, 0, 0,
        COALESCE((SELECT COALESCE(parent.root_id, parent.id) FROM jobs parent WHERE parent.id = ?), ?))
"""

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
    """Put one job in the queue; `blocked`, not `queued`, is the state no worker claims."""
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
