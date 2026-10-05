# SPDX-License-Identifier: AGPL-3.0-or-later
"""Taking work out: the claim, the caps it honours, and what has the queue to itself."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from sift.kernel.db import Connection, Row, in_clause
from sift.kernel.jobs.queue_rows import Job, _fetch, _to_job
from sift.kernel.jobs.queue_settle import PAUSE_CHUNK, PAUSED_FOR_BENCHMARK
from sift.kernel.jobs.queue_switchboard import SwitchboardReads
from sift.kernel.jobs.tuning import WAITED_ON_PRIORITY
from sift.kernel.log import get_logger

log = get_logger("sift.kernel.jobs.queue")
#: Whether a worker's next claim would be something a person pressed: anything queued and claimable
#: at the waited-on urgency or better, one seek of `ix_jobs_claim_by_id`. Asked by the process's own
#: loops before each piece of housekeeping, so nothing a person waits on shares the database.
_SOMEBODY_WAITING = """
SELECT EXISTS (SELECT 1 FROM jobs
                WHERE state = 'queued' AND priority <= ?
                  AND (run_after IS NULL OR run_after <= ?)) AS waiting
"""

# The claim. `attempts` goes up on the attempt, not on the failure, so a job whose handler takes the
# process down still runs out of attempts and stops. The last three `?` are a family's hold.
_CLAIM = """
UPDATE jobs
   SET state = 'running',
       claimed_by = ?,
       heartbeat_at = ?,
       attempts = attempts + 1,
       error = NULL,
       stop_wanted = NULL,
       updated_at = ?,
       started_at = ?
 WHERE id = (
       SELECT id FROM jobs
        WHERE state = 'queued' AND (run_after IS NULL OR run_after <= ?)
          AND (? OR NOT (COALESCE(timing, '') = 'quiet'
                         OR (timing IS NULL AND type IN (SELECT value FROM json_each(?)))))
          AND (? OR NOT (root_id IN (SELECT value FROM json_each(?))
                         AND COALESCE(timing, '') <> 'now'
                         AND type NOT IN (SELECT value FROM json_each(?))))
        ORDER BY priority, id
        LIMIT 1
 )
RETURNING *
"""

# The same, minus the job types that already have as many running as they are allowed. Sift runs
# one transcode at a time and a dozen thumbnails; without this a burst of transcodes fills every
# worker and nothing else moves.
_CLAIM_EXCLUDING = """
UPDATE jobs
   SET state = 'running',
       claimed_by = ?,
       heartbeat_at = ?,
       attempts = attempts + 1,
       error = NULL,
       stop_wanted = NULL,
       updated_at = ?,
       started_at = ?
 WHERE id = (
       SELECT id FROM jobs
        WHERE state = 'queued' AND (run_after IS NULL OR run_after <= ?)
          AND type NOT IN (?*)
          AND (? OR NOT (COALESCE(timing, '') = 'quiet'
                         OR (timing IS NULL AND type IN (SELECT value FROM json_each(?)))))
          AND (? OR NOT (root_id IN (SELECT value FROM json_each(?))
                         AND COALESCE(timing, '') <> 'now'
                         AND type NOT IN (SELECT value FROM json_each(?))))
        ORDER BY priority, id
        LIMIT 1
 )
RETURNING *
"""

_RUNNING_BY_TYPE = (
    "SELECT type, COUNT(*) AS running FROM jobs WHERE state = 'running' GROUP BY type"
)

# THE QUEUE TO ITSELF: whether a job of a type that has it (`worker_pool._EXCLUSIVE`) runs or waits
# claimable now. Asked inside the write lock, so a seek of `ix_jobs_by_type` per type and state.
_EXCLUSIVE_HELD = """
SELECT state FROM jobs
 WHERE type IN (?*)
   AND unlikely(state IN ('running', 'queued'))
   AND (state = 'running' OR run_after IS NULL OR run_after <= ?)
"""

# The claim while such a job is waiting: that type only. `+priority` keeps the planner on the type's
# own waiting rows rather than every waiting row in claim order, inside the write lock.
_CLAIM_ONLY = """
UPDATE jobs
   SET state = 'running',
       claimed_by = ?,
       heartbeat_at = ?,
       attempts = attempts + 1,
       error = NULL,
       stop_wanted = NULL,
       updated_at = ?,
       started_at = ?
 WHERE id = (
       SELECT id FROM jobs
        WHERE state = 'queued' AND (run_after IS NULL OR run_after <= ?)
          AND type IN (?*)
          AND (? OR NOT (COALESCE(timing, '') = 'quiet'
                         OR (timing IS NULL AND type IN (SELECT value FROM json_each(?)))))
        ORDER BY +priority, id
        LIMIT 1
 )
RETURNING *
"""

# What a benchmark holds back: every waiting row the claim would take now, but its own kind.
_PAUSE_CLAIMABLE = """
UPDATE jobs SET state = 'paused', stop_wanted = ?, updated_at = ?
 WHERE id IN (
       SELECT id FROM jobs
        WHERE state = 'queued' AND (run_after IS NULL OR run_after <= ?)
          AND type NOT IN (?*)
          AND (? OR NOT (COALESCE(timing, '') = 'quiet'
                         OR (timing IS NULL AND type IN (SELECT value FROM json_each(?)))))
          AND (? OR NOT (root_id IN (SELECT value FROM json_each(?))
                         AND COALESCE(timing, '') <> 'now'
                         AND type NOT IN (SELECT value FROM json_each(?))))
        LIMIT ?
 )
RETURNING id
"""


def _claimed(rows: Sequence[Row], worker_id: str) -> Job | None:
    """The row a claim took, as a job, logged; None where it took nothing."""
    if not rows:
        return None
    job = _to_job(rows[0])
    log.info(
        "job.claimed",
        job_id=job.id,
        job_type=job.type,
        worker_id=worker_id,
        attempt=job.attempts,
    )
    return job


def _capped(limits: Mapping[str, int] | None) -> dict[str, int]:
    """The caps a claim must honour: what the pool was told, plus what the work itself declares.

    A type declared `alone` does its own work twice when two run, on any machine and any setting,
    so its cap is a floor of one that a setting may not raise (`min`), and a type paused at zero
    stays paused. Read through the registry, so every claimer honours it and not only the pool.
    """
    from sift.kernel.jobs.worker_pool import registered_alone

    merged = dict(limits or {})
    for job_type in registered_alone():
        merged[job_type] = min(1, merged.get(job_type, 1))
    return merged


class Claiming(SwitchboardReads):
    """Taking work out of the queue."""

    async def somebody_waiting(self) -> bool:
        """Whether anything a person is waiting for is queued and claimable now. See
        `_SOMEBODY_WAITING`."""
        row = await self._db.fetch_one(_SOMEBODY_WAITING, (WAITED_ON_PRIORITY, int(self._now())))
        return bool(row is not None and row["waiting"])

    async def claim(self, worker_id: str, *, limits: Mapping[str, int] | None = None) -> Job | None:
        """Take the next job, atomically. Returns None when there is nothing to take.

        The count and the claim share one short `write()` block, so no other writer starts a capped
        type between them; readiness, quiet hours and a family's hold are asked BEFORE the lock.
        """
        now = self._now()
        held_back = await self._not_ready_types()
        # QUIET HOURS, asked before the lock for the reason readiness is: it is a settings read.
        quiet = await self._held()
        hold = (quiet.open, json.dumps(sorted(quiet.types)))
        families = self._family_holds()

        from sift.kernel.jobs.worker_pool import exclusive_job_types

        exclusive = sorted(exclusive_job_types())

        async with self._writing() as connection:
            at_capacity = await self._at_capacity(connection, _capped(limits)) | held_back

            # A JOB THAT HAS THE QUEUE TO ITSELF IS ALONE ON THE MACHINE (the device's benchmark
            # must measure the machine, not other work): while one runs nothing else is claimed, and
            # while one waits it is the only thing that can be. Asked inside the lock with the claim.
            if exclusive:
                sql, params = in_clause(_EXCLUSIVE_HELD, exclusive)
                states = {row["state"] for row in await _fetch(connection, sql, (*params, now))}
                if "running" in states:
                    return None
                if "queued" in states:
                    wanted = [one for one in exclusive if one not in at_capacity]
                    if not wanted:
                        return None
                    sql, params = in_clause(_CLAIM_ONLY, wanted)
                    rows = await _fetch(
                        connection, sql, (worker_id, now, now, now, now, *params, *hold)
                    )
                    return _claimed(rows, worker_id)

            if at_capacity:
                sql, params = in_clause(_CLAIM_EXCLUDING, sorted(at_capacity))
                rows = await _fetch(
                    connection, sql, (worker_id, now, now, now, now, *params, *hold, *families)
                )
            else:
                rows = await _fetch(
                    connection, _CLAIM, (worker_id, now, now, now, now, *hold, *families)
                )

        return _claimed(rows, worker_id)

    async def pause_waiting_for_benchmark(self, *, chunk: int = PAUSE_CHUNK) -> int:
        """Pause, as the benchmark's, every waiting row the claim would take now. How many."""
        from sift.kernel.jobs.worker_pool import exclusive_job_types

        quiet = await self._held()
        hold = (quiet.open, json.dumps(sorted(quiet.types)))
        sql, params = in_clause(_PAUSE_CLAIMABLE, sorted(exclusive_job_types()) or [""])
        paused = 0
        while True:
            now = self._now()
            async with self._writing() as connection:
                rows = await _fetch(
                    connection,
                    sql,
                    (PAUSED_FOR_BENCHMARK, now, now, *params, *hold, *self._family_holds(), chunk),
                )
            paused += len(rows)
            if len(rows) < chunk:
                return paused

    @staticmethod
    async def _at_capacity(connection: Connection, limits: Mapping[str, int]) -> set[str]:
        """The job types that already have as many running as they are allowed."""
        if not limits:
            return set()
        # A type capped at zero is paused, and it is at capacity whether or not anything of it is
        # running. It has to be added from the limits rather than found in the rows below: a paused
        # type has nothing running, so it never appears in a count of what is running, and reading
        # only those rows would let the one thing that must not start be the one thing that does.
        paused = {job_type for job_type, limit in limits.items() if limit <= 0}
        rows = await _fetch(connection, _RUNNING_BY_TYPE, ())
        return paused | {
            row["type"]
            for row in rows
            if row["type"] in limits and row["running"] >= limits[row["type"]]
        }
