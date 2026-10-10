# SPDX-License-Identifier: AGPL-3.0-or-later
"""A long job's plan: what it will work through, written before it starts, and how far it got.

A walk of a library decides every file before it opens one. Written down, that decision is what a
restart carries on from: the files up to the last settled batch are not decided or opened again.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sift.kernel.jobs.queue_core import QueueCore
from sift.kernel.jobs.queue_rows import _fetch

#: Plan rows per write, so no single write holds the writer long.
PLAN_WRITE_BATCH = 500

_STILL_MINE = "SELECT 1 FROM jobs WHERE id = ? AND state = 'running' AND claimed_by = ?"

_ADD_STEPS = (
    "INSERT OR REPLACE INTO job_plan (job_id, seq, rel_path, size, mtime_ns, kind, verdict)"
    " VALUES (?, ?, ?, ?, ?, ?, ?)"
)

_STEPS_FROM = (
    "SELECT seq, rel_path, size, mtime_ns, kind, verdict FROM job_plan"
    " WHERE job_id = ? AND seq >= ? ORDER BY seq LIMIT ?"
)

_MARK_OF = "SELECT settled FROM job_plan_marks WHERE job_id = ?"

# Fenced as every running job's write is: a worker that lost the job moves nothing.
_SETTLE = """
INSERT INTO job_plan_marks (job_id, settled, worker_id)
SELECT ?, ?, ? WHERE EXISTS (
  SELECT 1 FROM jobs WHERE id = ? AND state = 'running' AND claimed_by = ?)
ON CONFLICT(job_id) DO UPDATE SET settled = excluded.settled, worker_id = excluded.worker_id
RETURNING job_id
"""

_DROP_STEPS = "DELETE FROM job_plan WHERE job_id = ? AND seq >= ? AND seq < ?"

_DROP_MARK = "DELETE FROM job_plan_marks WHERE job_id = ?"

# A running job whose plan moved during the claim the restart cut short did its work; the restart,
# not the job, ended it. One that takes the process down before settling anything still runs out.
_REFUND_PROGRESSED = """
UPDATE jobs SET attempts = MAX(attempts - 1, 0)
 WHERE state = 'running' AND EXISTS (
   SELECT 1 FROM job_plan_marks m WHERE m.job_id = jobs.id AND m.worker_id = jobs.claimed_by)
RETURNING id
"""


@dataclass(frozen=True, slots=True)
class PlanStep:
    """One file of a plan, as it was when the plan was made."""

    seq: int
    rel_path: str
    size: int
    mtime_ns: int
    kind: str
    verdict: str


class Plans(QueueCore):
    """A job's plan rows and its checkpoint."""

    async def write_plan(self, job_id: str, worker_id: str, steps: Sequence[PlanStep]) -> bool:
        """Write steps, a batch per write. False when the job is no longer this worker's."""
        for at in range(0, len(steps), PLAN_WRITE_BATCH):
            async with self._db.write() as connection:
                if not await _fetch(connection, _STILL_MINE, (job_id, worker_id)):
                    return False
                await connection.executemany(
                    _ADD_STEPS,
                    [
                        (job_id, s.seq, s.rel_path, s.size, s.mtime_ns, s.kind, s.verdict)
                        for s in steps[at : at + PLAN_WRITE_BATCH]
                    ],
                )
        return True

    async def plan_of(self, job_id: str) -> tuple[list[PlanStep], int]:
        """Every step of a job's plan in order, and how many of them are settled."""
        steps: list[PlanStep] = []
        while True:
            rows = await self._db.fetch_all(
                _STEPS_FROM, (job_id, steps[-1].seq + 1 if steps else 0, PLAN_WRITE_BATCH)
            )
            steps.extend(
                PlanStep(
                    seq=int(row["seq"]),
                    rel_path=str(row["rel_path"]),
                    size=int(row["size"]),
                    mtime_ns=int(row["mtime_ns"]),
                    kind=str(row["kind"]),
                    verdict=str(row["verdict"]),
                )
                for row in rows
            )
            if len(rows) < PLAN_WRITE_BATCH:
                break
        marks = await self._db.fetch_all(_MARK_OF, (job_id,))
        return steps, int(marks[0]["settled"]) if marks else 0

    async def settle_plan(self, job_id: str, worker_id: str, settled: int) -> bool:
        """Record that every step below `settled` is done with. One small write."""
        async with self._db.write() as connection:
            rows = await _fetch(
                connection,
                _SETTLE,
                (job_id, settled, worker_id, job_id, worker_id),
            )
        return bool(rows)

    async def drop_plan_from(self, job_id: str, seq: int, end: int) -> None:
        """Remove the steps from `seq` up to `end`, a batch per write."""
        for at in range(seq, end, PLAN_WRITE_BATCH):
            async with self._db.write() as connection:
                await connection.execute(_DROP_STEPS, (job_id, at, min(end, at + PLAN_WRITE_BATCH)))

    async def forget_plan(self, job_id: str, steps: int) -> None:
        """Remove a finished job's plan and its checkpoint."""
        await self.drop_plan_from(job_id, 0, steps)
        async with self._db.write() as connection:
            await connection.execute(_DROP_MARK, (job_id,))

    async def refund_progressed(self) -> list[str]:
        """Give back the attempt of each running job whose plan moved in the claim it is on.
        Called at boot, before the reclaim, so a restart is never charged to the work."""
        async with self._db.write() as connection:
            rows = await _fetch(connection, _REFUND_PROGRESSED, ())
        return [str(row["id"]) for row in rows]
