# SPDX-License-Identifier: AGPL-3.0-or-later
"""The operator's controls: cancel, stop everything, retry, clear, and the settled prune."""

from __future__ import annotations

import json
from collections.abc import Callable, Collection, Sequence

from sift.kernel.db import Row, in_clause
from sift.kernel.jobs.queue_handoffs import HandOffs
from sift.kernel.jobs.queue_rows import _fetch, _for_the_record
from sift.kernel.jobs.tuning import (
    PARKED_RETENTION_SECONDS,
    PRUNE_BATCH,
    SETTLED_RETENTION_SECONDS,
)
from sift.kernel.log import get_logger

log = get_logger("sift.kernel.jobs.queue")
#: How many rows one write of a bulk control takes. A stop of a whole import is hundreds of thousands
#: of rows, seconds of the writer in one statement, so they go a chunk at a time.
_STOP_CHUNK = 500

# Cancelling a parent cancels everything under it, however deep. A scan that spawns a probe per
# file leaves the user one thing to cancel, and they expect it to mean all of it. Read, then
# cancelled by `_CANCEL_ROWS`.
_CANCEL_TREE = """
WITH RECURSIVE tree(id) AS (
    SELECT id FROM jobs WHERE id = ?
   -- UNION, not UNION ALL: it dedups the visited ids, so a parent_id cycle in a restored or
   -- hand-edited database terminates the walk instead of recursing forever. A real job tree is
   -- acyclic, so the dedup changes nothing for it.
    UNION
    SELECT job.id FROM jobs job JOIN tree ON job.parent_id = tree.id
)
SELECT id, state, priority FROM jobs
 WHERE id IN (SELECT id FROM tree)
   AND state IN ('queued', 'running', 'blocked', 'paused')
"""

# A stop's rows by id, one chunk. The state is asked again, so a row that settled since the read
# stays settled; `why` lands on the named row only.
_CANCEL_ROWS = """
UPDATE jobs
   SET state = 'canceled',
       claimed_by = NULL,
       heartbeat_at = NULL,
       stop_wanted = NULL,
       error = CASE WHEN id = ? AND ? IS NOT NULL THEN ? ELSE error END,
       updated_at = ?
 WHERE id IN (?*)
   AND state IN ('queued', 'running', 'blocked', 'paused')
RETURNING id, parent_id, root_id, type, started_at, note, requested_by
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
#: One arm per state, each from an index: done rows, nearly all of them, through the index that
#: holds them by when they settled, as the planner otherwise walks every done row of the week.
_PRUNE_SETTLED = """
DELETE FROM jobs WHERE id IN (
  SELECT id FROM (
    SELECT id, type, state, updated_at, started_at, requested_by, timing
      FROM jobs INDEXED BY ix_jobs_done_by_updated
     WHERE state = 'done' AND updated_at < :cutoff
    UNION ALL
    SELECT id, type, state, updated_at, started_at, requested_by, timing FROM jobs
     WHERE state = 'canceled' AND updated_at < :cutoff
    UNION ALL
    SELECT id, type, state, updated_at, started_at, requested_by, timing FROM jobs
     WHERE state = 'blocked' AND updated_at < :parked_cutoff
  ) AS old
   WHERE NOT EXISTS (SELECT 1 FROM jobs child WHERE child.parent_id = old.id)
     AND (old.state = 'blocked'
          OR old.started_at IS NULL
          OR EXISTS (SELECT 1 FROM jobs newer
                      WHERE newer.type = old.type
                        AND newer.state IN ('done', 'canceled') AND newer.started_at IS NOT NULL
                        AND newer.updated_at >= old.updated_at
                        AND (newer.updated_at > old.updated_at OR newer.id > old.id)
                        AND (old.requested_by IS NOT NULL OR old.timing IS NOT NULL
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

# What a retry of every failure takes. `failed` only, deliberately: a cancelled job was stopped by
# somebody on purpose, and sweeping those back into the queue would undo a decision rather than
# recover from a fault.
_RETRY_ALL_FAILED = "SELECT id FROM jobs WHERE state = 'failed'"

# The same for work somebody STOPPED: a separate statement, so retrying failures can never sweep a
# deliberate cancel back in.
_RETRY_ALL_CANCELED = "SELECT id FROM jobs WHERE state = 'canceled'"

# Either retry's rows by id, one chunk, only while still in the state the retry was asked for.
_RETRY_ROWS = """
UPDATE jobs
   SET state = 'queued',
       attempts = 0,
       progress = 0,
       error = NULL,
       claimed_by = NULL,
       heartbeat_at = NULL,
       updated_at = ?
 WHERE id IN (?*)
   AND state = ?
RETURNING id
"""

# Throwing away failures that will never succeed. Childless rows only, for `_PRUNE_SETTLED`'s
# reason: the cascade can then never take a running row. Nothing but `failed`. Read once, so a
# parent whose failures go in an earlier chunk is not taken by a later one.
_CLEAR_ALL_FAILED = """
SELECT id FROM jobs
 WHERE state = 'failed'
   AND NOT EXISTS (SELECT 1 FROM jobs child WHERE child.parent_id = jobs.id)
"""

_CLEAR_FAILED_ROWS = """
DELETE FROM jobs
 WHERE id IN (?*)
   AND state = 'failed'
   AND NOT EXISTS (SELECT 1 FROM jobs child WHERE child.parent_id = jobs.id)
RETURNING id
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

# Stopping the whole queue. Every cancellable state, `running` included: work is produced by work,
# so stopping only the waiting rows would leave the producer refilling them. A running job drops its
# work at its next heartbeat and its fenced writes cannot land; nothing is deleted, and a file
# never read is picked up by the next scan of its folder.
_CANCEL_EVERYTHING = """
SELECT id, state, priority FROM jobs
 WHERE state IN ('queued', 'running', 'blocked', 'paused')
"""

# A family's Cancel: its types, and apart from them every step making only its products. Two
# reads, so each starts from an index rather than one walking the table for the OR.
_CANCEL_TYPES = """
SELECT id, state, priority FROM jobs
 WHERE type IN (SELECT value FROM json_each(?))
   AND unlikely(state IN ('queued', 'running', 'blocked', 'paused'))
"""

_CANCEL_PRODUCTS = """
SELECT id, state, priority FROM jobs
 WHERE unlikely(state IN ('queued', 'running', 'blocked', 'paused'))
   AND EXISTS (SELECT 1 FROM json_each(jobs.payload, '$.products'))
   AND NOT EXISTS (SELECT 1 FROM json_each(jobs.payload, '$.products') AS made
                    WHERE made.value NOT IN (SELECT value FROM json_each(?)))
"""

# The heads of the families that stop took work from, where the head itself had already finished:
# called off with their families, for the reason `_CALL_OFF_THE_HEAD` gives.
_CALL_OFF_THE_HEADS = (
    "UPDATE jobs SET state = 'canceled', updated_at = ? WHERE id IN (?*) AND state = 'done'"
)

#: How many heads one statement names: under SQLite's limit on bound values, with room to spare.
_HEADS_PER_ASK = 400


def _stop_order(rows: Sequence[Row]) -> list[str]:
    """Running work first, since it queues more; then in the order a worker would claim it."""
    ordered = sorted(rows, key=lambda row: (row["state"] != "running", row["priority"], row["id"]))
    return list(dict.fromkeys(str(row["id"]) for row in ordered))


class Controls(HandOffs):
    """What a person does to the queue as a whole, and the housekeeping that forgets old rows."""

    async def _cancel_rows(
        self,
        ids: Sequence[str],
        *,
        named: str | None = None,
        said: str | None = None,
        on_canceled: Callable[[set[str]], None] | None = None,
        settled: bool = False,
    ) -> list[Row]:
        """Cancel these rows `_STOP_CHUNK` at a time, the writer given back between chunks, each
        chunk's running jobs told as soon as it lands. `on_canceled` hears each chunk's kinds before
        its commit; `settled` writes the runs and tells the settle listeners, as a cancel does."""
        canceled: list[Row] = []
        for start in range(0, len(ids), _STOP_CHUNK):
            sql, params = in_clause(_CANCEL_ROWS, ids[start : start + _STOP_CHUNK])
            async with self._writing() as connection:
                rows = await _fetch(connection, sql, (named, said, said, self._now(), *params))
                if rows and on_canceled is not None:
                    on_canceled({str(row["type"]) for row in rows})
                if settled:
                    await self._record_runs(connection, rows, "canceled")
            if not rows:
                continue
            if settled:
                await self._tell_settled(rows)
            self._stop_asked([str(row["id"]) for row in rows])
            canceled.extend(rows)
        return canceled

    async def _stop(
        self,
        reads: Sequence[tuple[str, tuple[object, ...]]],
        *,
        named: str | None = None,
        said: str | None = None,
        on_canceled: Callable[[set[str]], None] | None = None,
        settled: bool = False,
    ) -> list[Row]:
        """Read the live rows a stop takes, off the writer, and cancel them in chunks, running
        work first so what produces more is stopped before the rest."""
        found: list[Row] = []
        for sql, params in reads:
            found.extend(await self._db.fetch_all(sql, params))
        return await self._cancel_rows(
            _stop_order(found), named=named, said=said, on_canceled=on_canceled, settled=settled
        )

    async def _retry_rows(self, read: str, state: str) -> int:
        """Queue again every row the read finds, a chunk at a time, while it is still `state`."""
        ids = [str(row["id"]) for row in await self._db.fetch_all(read)]
        retried = 0
        for start in range(0, len(ids), _STOP_CHUNK):
            sql, params = in_clause(_RETRY_ROWS, ids[start : start + _STOP_CHUNK])
            async with self._writing() as connection:
                rows = await _fetch(connection, sql, (self._now(), *params, state))
            retried += len(rows)
        return retried

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
        rows = await self._stop(
            [(_CANCEL_TREE, (job_id,))],
            named=job_id,
            said=said,
            on_canceled=on_canceled,
            settled=True,
        )
        if not rows:
            return []
        if all(str(row["id"]) != job_id for row in rows):
            async with self._writing() as connection:
                await _fetch(connection, _CALL_OFF_THE_HEAD, (self._now(), job_id))
        canceled = [row["id"] for row in rows]
        log.info("job.canceled", job_id=job_id, job_count=len(canceled))
        return canceled

    async def clear_canceled(self, *, batch: int = _STOP_CHUNK) -> int:
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
            removed += len(rows)

        log.info("job.cleared_all_canceled", job_count=removed)
        return removed

    async def cancel_everything(self) -> int:
        """Stop every job that has not finished. Returns how many were stopped.

        Running and blocked work go too: work produces work, so stopping only what waits leaves the
        producer refilling the queue (`_CANCEL_EVERYTHING`). Nothing is deleted; what it costs is
        the machine time already spent.
        """
        rows = await self._stop([(_CANCEL_EVERYTHING, ())])
        if not rows:
            return 0
        heads = sorted({str(row["root_id"]) for row in rows if row["root_id"] is not None})
        for start in range(0, len(heads), _HEADS_PER_ASK):
            sql, params = in_clause(_CALL_OFF_THE_HEADS, heads[start : start + _HEADS_PER_ASK])
            async with self._writing() as connection:
                await connection.execute(sql, (self._now(), *params))

        log.info("job.canceled_everything", job_count=len(rows))
        return len(rows)

    async def cancel_types(self, job_types: Collection[str], products: Collection[str] = ()) -> int:
        """Stop every unfinished job of these types, and every step making only these products:
        a family's or a sub-task's Cancel. How many."""
        reads: list[tuple[str, tuple[object, ...]]] = []
        if job_types:
            reads.append((_CANCEL_TYPES, (json.dumps(sorted(job_types)),)))
        if products:
            reads.append((_CANCEL_PRODUCTS, (json.dumps(sorted(products)),)))
        if not reads:
            return 0
        rows = await self._stop(reads)
        log.info("job.canceled_types", job_types=sorted(job_types), job_count=len(rows))
        return len(rows)

    async def retry(self, job_id: str) -> bool:
        """Put a failed or cancelled job back in the queue, with a fresh set of attempts."""
        async with self._writing() as connection:
            rows = await _fetch(connection, _RETRY, (self._now(), job_id))
            if not rows:
                return False

        log.info("job.retried", job_id=job_id)
        self._work_arrived_unknown()
        return True

    async def retry_failed(self) -> int:
        """Put everything that failed back in the queue. Returns how many there were.

        Failures arrive in batches (a reader fixed, a drive back, a codec installed), so one button.
        Cancelled work is left alone (see the statement).
        """
        retried = await self._retry_rows(_RETRY_ALL_FAILED, "failed")
        if retried:
            log.info("job.retried_all", job_count=retried)
            self._work_arrived_unknown()
        return retried

    async def retry_canceled(self) -> int:
        """Put everything that was stopped back in the queue. Returns how many there were.

        The other half of `cancel_everything`: what was stopped already has its row and payload, so
        it is offered again without re-reading the folders. Cancelled only: a decision taken back is
        not work that broke. `attempts` restart at nought, as with every retry here.
        """
        retried = await self._retry_rows(_RETRY_ALL_CANCELED, "canceled")
        if retried:
            log.info("job.retried_all_canceled", job_count=retried)
            self._work_arrived_unknown()
        return retried

    async def clear_failed(self) -> int:
        """Forget everything that failed. Returns how many rows went.

        For failures that cannot succeed (a card that was not there, a share gone): `failed` is the
        one terminal state nothing ages out. The rows go rather than being marked.
        """
        ids = [str(row["id"]) for row in await self._db.fetch_all(_CLEAR_ALL_FAILED)]
        cleared = 0
        for start in range(0, len(ids), _STOP_CHUNK):
            sql, params = in_clause(_CLEAR_FAILED_ROWS, ids[start : start + _STOP_CHUNK])
            async with self._writing() as connection:
                cleared += len(await _fetch(connection, sql, params))
        if cleared:
            log.info("job.cleared_all_failed", job_count=cleared)
        return cleared

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
