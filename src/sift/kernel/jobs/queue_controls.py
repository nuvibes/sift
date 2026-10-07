# SPDX-License-Identifier: AGPL-3.0-or-later
"""The operator's controls: cancel, stop everything, retry, clear, and the settled prune."""

from __future__ import annotations

from collections.abc import Callable

from sift.kernel.db import in_clause
from sift.kernel.jobs.queue_handoffs import HandOffs
from sift.kernel.jobs.queue_rows import _fetch, _for_the_record
from sift.kernel.jobs.tuning import (
    CLEAR_BATCH,
    PARKED_RETENTION_SECONDS,
    PRUNE_BATCH,
    SETTLED_RETENTION_SECONDS,
)
from sift.kernel.log import get_logger

log = get_logger("sift.kernel.jobs.queue")
# Cancelling a parent cancels everything under it, however deep. A scan that spawns a probe per
# file leaves the user one thing to cancel, and they expect it to mean all of it.
_CANCEL_TREE = """
WITH RECURSIVE tree(id) AS (
    SELECT id FROM jobs WHERE id = ?
   -- UNION, not UNION ALL: it dedups the visited ids, so a parent_id cycle in a restored or
   -- hand-edited database terminates the walk instead of recursing forever. A real job tree is
   -- acyclic, so the dedup changes nothing for it.
    UNION
    SELECT job.id FROM jobs job JOIN tree ON job.parent_id = tree.id
)
UPDATE jobs
   SET state = 'canceled',
       claimed_by = NULL,
       heartbeat_at = NULL,
       stop_wanted = NULL,
       error = CASE WHEN id = ? AND ? IS NOT NULL THEN ? ELSE error END,
       updated_at = ?
 WHERE id IN (SELECT id FROM tree)
   AND state IN ('queued', 'running', 'blocked', 'paused')
RETURNING id, parent_id, type, started_at, note, requested_by
"""

# A head that had finished when its family was stopped is called off with it, so a pass stopped
# halfway does not read as finished: only the job the stop named, and only when live work went.
_CALL_OFF_THE_HEAD = "UPDATE jobs SET state = 'canceled', updated_at = ? WHERE id = ? AND state = 'done' RETURNING id"

#: Settled jobs old enough to forget, taken from the leaves inward.
#:
#: `NOT EXISTS ... child` is what makes it safe: `parent_id` cascades, so taking only childless
#: rows means the cascade never reaches a row still running. Parked rows go at a window of their
#: own (`PARKED_RETENTION_SECONDS`). Age counts from the settling (`updated_at`). Each type's
#: newest run, and its newest run nobody pressed, is never taken: the scheduler and Tasks read the
#: last run off these rows. The ids are a subquery because `DELETE ... LIMIT` is a build option.
_PRUNE_SETTLED = """
DELETE FROM jobs WHERE id IN (
  SELECT id FROM jobs
   WHERE ((state IN ('done','canceled') AND updated_at < :cutoff)
       OR (state = 'blocked' AND updated_at < :parked_cutoff))
     AND NOT EXISTS (SELECT 1 FROM jobs child WHERE child.parent_id = jobs.id)
     AND (state = 'blocked'
          OR started_at IS NULL
          OR EXISTS (SELECT 1 FROM jobs newer
                      WHERE newer.type = jobs.type
                        AND newer.state IN ('done', 'canceled') AND newer.started_at IS NOT NULL
                        AND newer.updated_at >= jobs.updated_at
                        AND (newer.updated_at > jobs.updated_at OR newer.id > jobs.id)
                        AND (jobs.requested_by IS NOT NULL OR jobs.timing IS NOT NULL
                             OR (newer.requested_by IS NULL AND newer.timing IS NULL))))
   LIMIT :batch
)
"""

_RETRY = """
UPDATE jobs
   SET state = 'queued',
       attempts = 0,
       progress = 0,
       error = NULL,
       claimed_by = NULL,
       heartbeat_at = NULL,
       updated_at = ?
 WHERE id = ? AND state IN ('failed', 'canceled')
RETURNING id, parent_id
"""

# The same statement without the id. `failed` only, deliberately: a cancelled job was stopped by
# somebody on purpose, and sweeping those back into the queue would undo a decision rather than
# recover from a fault.
_RETRY_ALL_FAILED = """
UPDATE jobs
   SET state = 'queued',
       attempts = 0,
       progress = 0,
       error = NULL,
       claimed_by = NULL,
       heartbeat_at = NULL,
       updated_at = ?
 WHERE state = 'failed'
RETURNING id, parent_id
"""

# The same for work somebody STOPPED: a separate statement, so retrying failures can never sweep a
# deliberate cancel back in.
_RETRY_ALL_CANCELED = """
UPDATE jobs
   SET state = 'queued',
       attempts = 0,
       progress = 0,
       error = NULL,
       claimed_by = NULL,
       heartbeat_at = NULL,
       updated_at = ?
 WHERE state = 'canceled'
RETURNING id, parent_id
"""

# Throwing away failures that will never succeed. Childless rows only, for `_PRUNE_SETTLED`'s
# reason: the cascade can then never take a running row. Nothing but `failed`.
_CLEAR_ALL_FAILED = """
DELETE FROM jobs
 WHERE state = 'failed'
   AND NOT EXISTS (SELECT 1 FROM jobs child WHERE child.parent_id = jobs.id)
RETURNING id, parent_id
"""

# Throwing away stopped work, childless rows only (the cascade rule) and batched (one writer).
#: How many batches one clear may take: far past any library, so a press can never become a request
#: that never returns.
_MOST_CLEAR_PASSES = 50_000

_CLEAR_ALL_CANCELED = """
DELETE FROM jobs WHERE id IN (
  SELECT id FROM jobs
   WHERE state = 'canceled'
     AND NOT EXISTS (SELECT 1 FROM jobs child WHERE child.parent_id = jobs.id)
   LIMIT ?
)
RETURNING id, parent_id
"""

# Stopping the whole queue in one statement. Every cancellable state, `running` included: work is
# produced by work, so stopping only the waiting rows would leave the producer refilling them. A
# running job drops its work at its next heartbeat and its fenced writes cannot land; nothing is
# deleted, and a file never read is picked up by the next scan of its folder.
_CANCEL_EVERYTHING = """
UPDATE jobs
   SET state = 'canceled',
       claimed_by = NULL,
       heartbeat_at = NULL,
       stop_wanted = NULL,
       updated_at = ?
 WHERE state IN ('queued', 'running', 'blocked', 'paused')
RETURNING id, root_id
"""

# The heads of the families that stop took work from, where the head itself had already finished:
# called off with their families, for the reason `_CALL_OFF_THE_HEAD` gives.
_CALL_OFF_THE_HEADS = (
    "UPDATE jobs SET state = 'canceled', updated_at = ? WHERE id IN (?*) AND state = 'done'"
)

#: How many heads one statement names: under SQLite's limit on bound values, with room to spare.
_HEADS_PER_ASK = 400

# The roll-up after it, matched on the moment rather than on ids: every row this run cancelled
# carries the same `updated_at`, and a list of ids would meet SQLite's parameter cap on a big queue.
_ROLL_UP_CANCELED = """
UPDATE jobs
   SET progress = COALESCE((
       SELECT CAST(COUNT(*) FILTER (WHERE child.state IN ('done', 'failed', 'canceled')) AS REAL)
              / COUNT(*)
         FROM jobs child
        WHERE child.parent_id = jobs.id
   ), progress),
       updated_at = ?
 WHERE id IN (
       SELECT parent_id FROM jobs
        WHERE parent_id IS NOT NULL AND state = 'canceled' AND updated_at = ?
 )
"""


class Controls(HandOffs):
    """What a person does to the queue as a whole, and the housekeeping that forgets old rows."""

    async def cancel(
        self,
        job_id: str,
        *,
        on_canceled: Callable[[set[str]], None] | None = None,
        why: str | None = None,
    ) -> list[str]:
        """Cancel a job and everything it spawned. Returns the ids actually cancelled.

        A running job's worker is told immediately (`listen_for_stops`), beats, finds the claim gone
        and drops it; nothing it writes meanwhile can land. `on_canceled` is told which kinds of job
        this stopped BEFORE the commit, so the work ledger cannot see a family drain untold. `why`
        is Sift's own reason, kept on the named row; a person's cancel gives none.
        """
        said = None if why is None else _for_the_record(why)
        async with self._writing() as connection:
            rows = await _fetch(connection, _CANCEL_TREE, (job_id, job_id, said, said, self._now()))
            if not rows:
                return []
            if all(str(row["id"]) != job_id for row in rows):
                await _fetch(connection, _CALL_OFF_THE_HEAD, (self._now(), job_id))
            if on_canceled is not None:
                on_canceled({str(row["type"]) for row in rows})
            await self._roll_up(connection, [row["parent_id"] for row in rows])
            await self._record_runs(connection, rows, "canceled")

        await self._tell_settled(rows)
        canceled = [row["id"] for row in rows]
        log.info("job.canceled", job_id=job_id, job_count=len(canceled))
        # Any of them running is dropped immediately rather than at its worker's next heartbeat.
        self._stop_asked(canceled)
        return canceled

    async def clear_canceled(self, *, batch: int = CLEAR_BATCH) -> int:
        """Forget everything that was stopped. Returns how many rows went.

        Loops because a tree comes apart leaf by leaf: only rows nothing hangs off are removed, so
        the cascade cannot take a running child, and a cancelled parent of FINISHED children stays.
        The writer is given back between batches.
        """
        removed = 0
        # A stop that guarantees an end (`_MOST_CLEAR_PASSES`).
        for _ in range(_MOST_CLEAR_PASSES):
            async with self._writing() as connection:
                rows = await _fetch(connection, _CLEAR_ALL_CANCELED, (batch,))
                if not rows:
                    break
                # Their parents' progress is recomputed, as `clear_failed` does.
                await self._roll_up(connection, [row["parent_id"] for row in rows])
            removed += len(rows)

        log.info("job.cleared_all_canceled", job_count=removed)
        return removed

    async def cancel_everything(self) -> int:
        """Stop every job that has not finished. Returns how many were stopped.

        Running and blocked work go too: work produces work, so stopping only what waits leaves the
        producer refilling the queue (`_CANCEL_EVERYTHING`). Nothing is deleted; what it costs is
        the machine time already spent.
        """
        now = self._now()
        async with self._writing() as connection:
            rows = await _fetch(connection, _CANCEL_EVERYTHING, (now,))
            if not rows:
                return 0
            await connection.execute(_ROLL_UP_CANCELED, (now, now))
            heads = sorted({str(row["root_id"]) for row in rows if row["root_id"] is not None})
            for start in range(0, len(heads), _HEADS_PER_ASK):
                sql, params = in_clause(_CALL_OFF_THE_HEADS, heads[start : start + _HEADS_PER_ASK])
                await connection.execute(sql, (now, *params))

        log.info("job.canceled_everything", job_count=len(rows))
        self._stop_asked([str(row["id"]) for row in rows])
        return len(rows)

    async def retry(self, job_id: str) -> bool:
        """Put a failed or cancelled job back in the queue, with a fresh set of attempts."""
        async with self._writing() as connection:
            rows = await _fetch(connection, _RETRY, (self._now(), job_id))
            if not rows:
                return False
            await self._roll_up(connection, [rows[0]["parent_id"]])

        log.info("job.retried", job_id=job_id)
        return True

    async def retry_failed(self) -> int:
        """Put everything that failed back in the queue. Returns how many there were.

        Failures arrive in batches (a reader fixed, a drive back, a codec installed), so one button.
        Cancelled work is left alone (see the statement).
        """
        async with self._writing() as connection:
            rows = await _fetch(connection, _RETRY_ALL_FAILED, (self._now(),))
            if not rows:
                return 0
            await self._roll_up(connection, [row["parent_id"] for row in rows])

        log.info("job.retried_all", job_count=len(rows))
        return len(rows)

    async def retry_canceled(self) -> int:
        """Put everything that was stopped back in the queue. Returns how many there were.

        The other half of `cancel_everything`: what was stopped already has its row and payload, so
        it is offered again without re-reading the folders. Cancelled only: a decision taken back is
        not work that broke. `attempts` restart at nought, as with every retry here.
        """
        async with self._writing() as connection:
            rows = await _fetch(connection, _RETRY_ALL_CANCELED, (self._now(),))
            if not rows:
                return 0
            await self._roll_up(connection, [row["parent_id"] for row in rows])

        log.info("job.retried_all_canceled", job_count=len(rows))
        return len(rows)

    async def clear_failed(self) -> int:
        """Forget everything that failed. Returns how many rows went.

        For failures that cannot succeed (a card that was not there, a share gone): `failed` is the
        one terminal state nothing ages out. The rows go rather than being marked, and their
        parents' progress is recomputed, so a parent can read as finished.
        """
        async with self._writing() as connection:
            rows = await _fetch(connection, _CLEAR_ALL_FAILED, ())
            if not rows:
                return 0
            await self._roll_up(connection, [row["parent_id"] for row in rows])

        log.info("job.cleared_all_failed", job_count=len(rows))
        return len(rows)

    async def prune_settled(
        self,
        *,
        retention_seconds: int = SETTLED_RETENTION_SECONDS,
        parked_seconds: int = PARKED_RETENTION_SECONDS,
        batch: int = PRUNE_BATCH,
    ) -> int:
        """Forget finished jobs older than the retention window, and parked ones older than their
        own longer window (`PARKED_RETENTION_SECONDS`). Returns how many rows went.

        Bounded (`PRUNE_BATCH`): the watchdog runs it again in thirty seconds. Counted by the
        queue's own clock, from when each row SETTLED (`_PRUNE_SETTLED`).
        """
        now = int(self._now())
        cutoff = now - retention_seconds
        async with self._writing() as connection:
            cursor = await connection.execute(
                _PRUNE_SETTLED,
                {"cutoff": cutoff, "parked_cutoff": now - parked_seconds, "batch": batch},
            )
            removed = cursor.rowcount
        return max(removed, 0)
