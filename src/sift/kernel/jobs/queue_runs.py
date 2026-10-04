# SPDX-License-Identifier: AGPL-3.0-or-later
"""Each scheduled task's runs, read off the job rows: the last few, and the last one finished."""

from __future__ import annotations

import json
from collections.abc import Sequence

from sift.kernel.db import Row
from sift.kernel.jobs.queue_core import QueueCore
from sift.kernel.jobs.queue_rows import _FOLDED, JobState, TaskRun

#: The last few runs of each scheduled task, in one read for all of them, so the screen's cost does
#: not grow with the number of tasks.
#:
#: From the JOB rows, not `work_runs`: the ledger records one run per family, and every task's job is
#: in the `other` family. Ordered by when the work BEGAN. A run is a family (`TaskRun`): running
#: while any row is live, ended when the last did, its state folded by `_FOLDED` as Activity folds
#: it. Driven from the types asked about, never a read of the table, and built from module constants
#: only (`noqa: S608`).
_TASK_RUNS = (
    """
SELECT id, type, started_at, created_at, state, error, place, runs,
       (SELECT MAX(step.updated_at) FROM jobs AS step WHERE step.root_id = run.id) AS ended_at,
       (SELECT page.note FROM jobs AS page
         WHERE page.root_id = run.id AND page.type = run.type
         ORDER BY page.id DESC LIMIT 1) AS said
  FROM (
  SELECT id, type, started_at, created_at, state, error,
         ROW_NUMBER() OVER (
           -- ordered by the clock: a run is placed by when it began, a moment only the clock
           -- records (`started_at`); created_at stands in for a row from before that column
           PARTITION BY type ORDER BY COALESCE(started_at, created_at) DESC, id DESC
         ) AS place,
         COUNT(*) OVER (PARTITION BY type) AS runs
    FROM (
      SELECT jobs.id, jobs.type, jobs.started_at, jobs.created_at,
             CASE WHEN jobs.state = 'failed' THEN jobs.error END AS error,
             CASE WHEN EXISTS (SELECT 1 FROM jobs AS step
                                WHERE step.root_id = jobs.id
                                  AND step.state IN ('queued', 'running', 'blocked', 'paused'))
                  THEN 'running'
                  ELSE ("""  # noqa: S608
    + _FOLDED
    + """) END AS state
        FROM json_each(:types) AS asked
       CROSS JOIN jobs ON jobs.type = asked.value
       WHERE jobs.state IN ('running', 'done', 'failed', 'canceled')
         AND jobs.root_id = jobs.id
         -- A waiting row taken back before it started is not a run: counting it would place the
         -- next run of a weekly task from the moment it was switched off, and show it as the last.
         AND (jobs.state != 'canceled' OR jobs.started_at IS NOT NULL)
         AND (:started_by = 'anyone'
              OR (:started_by = 'schedule') = (jobs.requested_by IS NULL AND jobs.timing IS NULL))
    )
   -- bound: every state a run can be in, or only the three it can have ENDED in
   WHERE state IN (SELECT value FROM json_each(:states))
) AS run
 WHERE place <= :most
 ORDER BY type, place
"""
)


def _task_runs() -> str:
    """`_TASK_RUNS` for the two readers that ask it. Handed out the way `_family_tally` hands out
    its statement: text put together from this module's constants and nothing a caller supplied."""
    return _TASK_RUNS


#: The states a run can be in, for `JobQueue.task_runs`, and the three it can have ENDED in, for
#: `JobQueue.last_finished_runs`. Bound into `_TASK_RUNS` so both ask it through one statement.
_A_RUN_IS: tuple[str, ...] = ("running", "done", "failed", "canceled")
_A_RUN_ENDED: tuple[str, ...] = ("done", "failed", "canceled")

#: Who started a run, for `JobQueue.last_finished_runs`: anybody; the task's own schedule (a head
#: row nobody pressed, `requested_by` and `timing` both empty); or a press. Bound into `_TASK_RUNS`
#: as `:started_by`, where "schedule" keeps the first kind of row and "press" every other: the
#: scheduler places a task's next run from its own runs alone (`TaskClock`).
ANYONE = "anyone"
STARTED_BY_SCHEDULE = "schedule"
STARTED_BY_PRESS = "press"
_STARTED_BY: tuple[str, ...] = (ANYONE, STARTED_BY_SCHEDULE, STARTED_BY_PRESS)


def _task_runs_from(rows: Sequence[Row]) -> dict[str, list[TaskRun]]:
    """`_TASK_RUNS`'s rows as each type's runs, newest first."""
    runs: dict[str, list[TaskRun]] = {}
    for row in rows:
        began = row["started_at"]
        going = row["state"] == "running"
        runs.setdefault(str(row["type"]), []).append(
            TaskRun(
                id=str(row["id"]),
                started_at=None if began is None else int(began),
                finished_at=None if going else int(row["ended_at"]),
                state=JobState(row["state"]),
                note=None if going else (row["said"] or None),
                runs_total=int(row["runs"]),
                error=(row["error"] or None) if row["state"] == "failed" else None,
            )
        )
    return runs


class TaskRuns(QueueCore):
    """What the job rows say about each scheduled task's runs."""

    async def task_runs(
        self, job_types: Sequence[str], *, most: int = 5
    ) -> dict[str, list[TaskRun]]:
        """The newest few runs of each of these job types, newest first, and how many there are.

        One statement for every type (`_TASK_RUNS`); a type that has never run is an absent key.
        What bounds it is the settled prune: a week of rows, and always each type's newest run.
        """
        if not job_types:
            return {}
        rows = await self._db.fetch_all(
            _task_runs(),
            {
                "types": json.dumps(list(job_types)),
                "states": json.dumps(list(_A_RUN_IS)),
                "most": most,
                "started_by": ANYONE,
            },
        )
        return _task_runs_from(rows)

    async def last_finished_runs(
        self,
        job_types: Sequence[str],
        *,
        ended_in: Sequence[JobState] = (),
        started_by: str = ANYONE,
    ) -> dict[str, TaskRun]:
        """Each of these job types' last FINISHED run, keyed by type; a type that never finished one
        is absent.

        The one answer to "when did this task last run", read by its Tasks row and by Activity, so
        the two cannot differ. A run is its whole family, so one whose handed-out work still waits is
        still going. `ended_in` narrows to runs that ended so; `started_by` to the runs its own
        schedule started (the scheduler places the next run from those) or to the others.
        """
        if not job_types:
            return {}
        if started_by not in _STARTED_BY:
            raise ValueError(f"started_by must be one of {', '.join(_STARTED_BY)}")
        ended = tuple(one.value for one in ended_in) or _A_RUN_ENDED
        if not set(ended) <= set(_A_RUN_ENDED):
            raise ValueError("a run ends done, failed or canceled")
        rows = await self._db.fetch_all(
            _task_runs(),
            {
                "types": json.dumps(list(job_types)),
                "states": json.dumps(list(ended)),
                "most": 1,
                "started_by": started_by,
            },
        )
        return {job_type: runs[0] for job_type, runs in _task_runs_from(rows).items()}
