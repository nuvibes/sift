# SPDX-License-Identifier: AGPL-3.0-or-later
"""Taking work out: the claim, the caps it honours, and what has the queue to itself."""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping, Sequence

from sift.kernel.db import Connection, Row, in_clause
from sift.kernel.jobs.families import Family
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
# process down still runs out of attempts and stops. One seek per kind of work and timing that
# waits (`ix_jobs_claim_heads`), so a claim reads a few dozen rows however long the queue and
# whatever is at its cap; and the rows put off to a moment now past (`ix_jobs_queued_later`), which
# go before the work of their urgency that never waited: a settle asked for during a run runs in it.
# Then the read (the scan family: a file's first step) up to its cap, so files keep arriving, and
# every other worker on the oldest work: no kind waits while another runs. Held products are read
# row by row.
_CLAIM = """
UPDATE jobs
   SET state = 'running',
       claimed_by = :worker,
       heartbeat_at = :now,
       attempts = attempts + 1,
       error = NULL,
       note = NULL,
       stop_wanted = NULL,
       updated_at = :now,
       started_at = :now
 WHERE id = (
       SELECT id FROM jobs
        WHERE id IN (
              SELECT (SELECT head.id FROM jobs AS head INDEXED BY ix_jobs_claim_heads
                       WHERE head.state = 'queued' AND head.run_after IS NULL
                         AND head.type = kind.type AND head.timing IS kind.timing
                         AND (:products IS NULL
                              OR NOT EXISTS (SELECT 1 FROM json_each(head.payload, '$.products'))
                              OR EXISTS (SELECT 1 FROM json_each(head.payload, '$.products') AS made
                                          WHERE made.value NOT IN (SELECT value FROM json_each(:products))))
                       ORDER BY head.priority, head.id
                       LIMIT 1)
                FROM (SELECT tally.type AS type, timing.value AS timing
                        FROM job_tallies AS tally,
                             (SELECT NULL AS value UNION ALL SELECT 'now' UNION ALL SELECT 'quiet')
                             AS timing
                       WHERE tally.state = 'queued' AND tally.n > 0
                         AND tally.type NOT IN (SELECT value FROM json_each(:capped))) AS kind
               WHERE :open OR NOT (kind.timing IS 'quiet'
                                   OR (kind.timing IS NULL
                                       AND kind.type IN (SELECT value FROM json_each(:quiet))))
              UNION ALL
              SELECT due.id FROM jobs AS due INDEXED BY ix_jobs_queued_later
               WHERE due.state = 'queued' AND due.run_after IS NOT NULL AND due.run_after <= :now
                 AND due.type NOT IN (SELECT value FROM json_each(:capped))
                 AND (:open OR NOT (COALESCE(due.timing, '') = 'quiet'
                                    OR (due.timing IS NULL
                                        AND due.type IN (SELECT value FROM json_each(:quiet)))))
                 AND (:products IS NULL
                      OR NOT EXISTS (SELECT 1 FROM json_each(due.payload, '$.products'))
                      OR EXISTS (SELECT 1 FROM json_each(due.payload, '$.products') AS made
                                  WHERE made.value NOT IN (SELECT value FROM json_each(:products))))
              UNION ALL
              SELECT (SELECT asked.id FROM jobs AS asked INDEXED BY ix_jobs_by_type
                       WHERE asked.state = 'queued' AND asked.run_after IS NULL
                         AND asked.type = beside.value
                         AND (json_type(asked.payload, '$.paths') IS NOT NULL
                              OR json_type(asked.payload, '$.folder_id') IS NOT NULL)
                         AND (:open OR NOT (COALESCE(asked.timing, '') = 'quiet'
                                            OR (asked.timing IS NULL
                                                AND asked.type IN (SELECT value FROM json_each(:quiet)))))
                       ORDER BY asked.priority, asked.id
                       LIMIT 1)
                FROM json_each(:beside) AS beside
        )
        ORDER BY priority, run_after IS NULL, run_after,
                 type NOT IN (SELECT value FROM json_each(:reading)), id
        LIMIT 1
 )
RETURNING *
"""


def claim_parameters(
    worker_id: str,
    now: int,
    capped: Collection[str],
    quiet: tuple[bool, str],
    products: str | None,
    beside: Collection[str] = (),
) -> dict[str, object]:
    """`_CLAIM`'s parameters: the types at their cap, quiet hours (open, types), the held products,
    and the walks at their cap whose one place beside it is free (`_ASKED_RUNNING`)."""
    from sift.kernel.jobs.worker_pool import registered_families

    reading = sorted(
        kind for kind, family in registered_families().items() if family is Family.SCAN
    )
    return {
        "reading": json.dumps(reading),
        "worker": worker_id,
        "now": now,
        "capped": json.dumps(sorted(capped)),
        "open": quiet[0],
        "quiet": quiet[1],
        "products": products,
        "beside": json.dumps(sorted(beside)),
    }


# A walk somebody or the watcher asked for (some named files, or one folder) runs beside a whole
# walk at its type's cap, in one place of its own: which types of these already fill that place.
_ASKED_RUNNING = """
SELECT DISTINCT type FROM jobs
 WHERE type IN (SELECT value FROM json_each(?)) AND state = 'running'
   AND (json_type(payload, '$.paths') IS NOT NULL OR json_type(payload, '$.folder_id') IS NOT NULL)
"""

# Which of those types wait as walks (a root and no file, as `_TO_READ` reads a walk): one seek of
# each type's oldest waiting row, so a type of per-file rows is never read through for an asked-for
# walk it cannot hold.
_WALKS_WAITING = """
SELECT kind.value AS type FROM json_each(?) AS kind
 WHERE (SELECT json_type(walk.payload, '$.asset_id') IS NULL FROM jobs AS walk
         WHERE walk.type = kind.value AND walk.state = 'queued'
         ORDER BY walk.id LIMIT 1)
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
       note = NULL,
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

    async def claim(
        self,
        worker_id: str,
        *,
        limits: Mapping[str, int] | None = None,
        held_products: Collection[str] = (),
    ) -> Job | None:
        """Take the next job, atomically. Returns None when there is nothing to take. A job whose
        payload names products, every one of them in `held_products`, is passed over: paused.

        The count and the claim share one short `write()` block, so no other writer starts a capped
        type between them; readiness and quiet hours are asked BEFORE the lock.
        """
        now = self._now()
        held_back = await self._not_ready_types()
        # QUIET HOURS, asked before the lock for the reason readiness is: it is a settings read.
        quiet = await self._held()
        hold = (quiet.open, json.dumps(sorted(quiet.types)))
        products = json.dumps(sorted(held_products)) if held_products else None

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

            self._full = (now, frozenset(at_capacity))
            beside = await self._beside_the_cap(connection, at_capacity - held_back, limits)
            rows = list(
                await connection.execute_fetchall(
                    _CLAIM,
                    claim_parameters(worker_id, now, at_capacity, hold, products, beside),
                )
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
                    (PAUSED_FOR_BENCHMARK, now, now, *params, *hold, chunk),
                )
            paused += len(rows)
            if len(rows) < chunk:
                return paused

    @staticmethod
    async def _beside_the_cap(
        connection: Connection, at_capacity: Collection[str], limits: Mapping[str, int] | None
    ) -> list[str]:
        """The walks at their cap, not paused, whose place for an asked-for walk is free."""
        from sift.kernel.jobs.worker_pool import registered_families

        walks = [
            kind
            for kind in at_capacity
            if registered_families().get(kind) is Family.SCAN and (limits or {}).get(kind, 1) > 0
        ]
        if walks:
            rows = await _fetch(connection, _WALKS_WAITING, (json.dumps(sorted(walks)),))
            walks = [str(row["type"]) for row in rows]
        if not walks:
            return []
        rows = await _fetch(connection, _ASKED_RUNNING, (json.dumps(sorted(walks)),))
        return sorted(set(walks) - {str(row["type"]) for row in rows})

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
