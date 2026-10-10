# SPDX-License-Identifier: AGPL-3.0-or-later
"""The queue read whole: live work by type and payload, tallies, and the shape of the current run."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from sift.kernel.db import in_clause
from sift.kernel.ids import floor_at
from sift.kernel.jobs.families import AGAIN
from sift.kernel.jobs.queue_claim import _EXCLUSIVE_HELD
from sift.kernel.jobs.queue_core import QueueCore
from sift.kernel.jobs.queue_rows import (
    MAX_PAGE_SIZE,
    UNLOCK_WAIT,
    FilesToRead,
    Job,
    JobState,
    LiveProducts,
    LiveWork,
    PressedWork,
    WorkKind,
    WorkSummary,
    _check_payload,
    _fetch,
    _products_named,
    _to_job,
)

#: How far back "how fast" looks: several files of slow work, yet quick to follow the workers.
RECENT_WORK_SECONDS = 120

#: The soonest a waiting job of one type may run (see `next_scheduled`).
_NEXT_SCHEDULED = (
    "SELECT MIN(run_after) AS due FROM jobs "
    "WHERE type = ? AND state = 'queued' AND run_after IS NOT NULL"
)

#: The next moment a row put off to later comes due, of any type (`ix_jobs_queued_later`).
_NEXT_DUE = (
    "SELECT MIN(run_after) AS due FROM jobs"
    " WHERE state = 'queued' AND run_after IS NOT NULL AND run_after > ?"
)

#: The same for several types together, each type a seek of `ix_jobs_by_type`.
_NEXT_SCHEDULED_OF = (
    "SELECT type, MIN(run_after) AS due FROM jobs"
    " WHERE type IN (SELECT value FROM json_each(?)) AND state = 'queued' AND run_after IS NOT NULL"
    " GROUP BY type"
)

# When the current batch of work began: the oldest unfinished job that is DUE, so tomorrow's backup
# or a paused row cannot pin "this run" to days ago. One live-state IN, so state indexes drive it.
_BURST_START = """
SELECT MIN(created_at) AS since
  FROM jobs
 WHERE unlikely(state IN ('running', 'blocked', 'queued'))
   AND (state != 'queued' OR run_after IS NULL OR run_after <= ?)
"""

# The dashboard's numbers, none of them a read of the week of settled rows: the tallies by type
# and state (kept by the table's triggers), the live rows, and the settled rows of this run and of
# the last minutes through the partial indexes whose terms these repeat word for word; `+type`
# keeps the planner off walking the type index for its order.
_TALLIES = "SELECT type, state, n FROM job_tallies WHERE n > 0"

_LIVE_SUMMARY = """
SELECT type, state, SUM(created_at >= ?) AS in_run, SUM(units * (1 - progress)) AS left_units
  FROM jobs
 WHERE unlikely(state IN ('queued', 'running', 'blocked', 'paused'))
 GROUP BY type, state
"""

_SETTLED_IN_RUN = """
SELECT type, state, COUNT(*) AS in_run FROM jobs INDEXED BY ix_jobs_settled_by_created
 WHERE state IN ('done', 'failed') AND created_at >= ?
 GROUP BY +type, +state
"""

_DONE_LATELY = """
SELECT type, COUNT(*) AS lately FROM jobs INDEXED BY ix_jobs_done_by_updated
 WHERE state = 'done' AND updated_at >= ? GROUP BY +type
"""

# Is this work happening AT ALL (waiting, under way, held or paused), in any of the payloads given?
# One seek of the live rows' index (`ix_jobs_live_payload`) per payload, whose WHERE this repeats.
_LIVE_LIKE = (
    "SELECT id FROM jobs WHERE type = ? AND payload IN (SELECT value FROM json_each(?))"
    " AND state IN ('queued', 'running', 'blocked', 'paused') LIMIT 1"
)

_LIVE_PAYLOADS = (
    "SELECT payload FROM jobs WHERE type = ? "
    "AND unlikely(state IN ('queued', 'running', 'blocked', 'paused'))"
)

# The products the live rows of some types name, counted by type, state and quiet hours: a
# handful of lines however many rows, with no payload read into Python.
_LIVE_PRODUCTS = """
SELECT type, state, COALESCE(timing, '') = 'quiet' AS quiet,
       json_extract(payload, '$.products') AS products, COUNT(*) AS n
  FROM jobs
 WHERE type IN (SELECT value FROM json_each(?))
   AND unlikely(state IN ('queued', 'running', 'blocked', 'paused'))
 GROUP BY 1, 2, 3, 4
"""

# The file each live row of one type is about, without parsing a payload apiece.
_LIVE_ASSET_IDS = """
SELECT json_extract(payload, '$.asset_id') AS asset_id FROM jobs
 WHERE type = ? AND unlikely(state IN ('queued', 'running', 'blocked', 'paused'))
   AND json_type(payload, '$.asset_id') = 'text'
"""

# The file every live row of any type is about: what is on its way by itself.
_LIVE_FILES = """
SELECT DISTINCT json_extract(payload, '$.asset_id') AS asset_id FROM jobs
 WHERE unlikely(state IN ('queued', 'running', 'blocked', 'paused'))
   AND json_type(payload, '$.asset_id') = 'text'
"""

# The live rows of some types, split by whether a person pressed them for some files or ran them
# over some folders (the top names `roots`), grouped by products and "again" (the first
# placeholder, `families.AGAIN`): so Activity describes a run over forty files by those files.
_LIVE_BY_PRESS = """
SELECT j.type AS type,
       COALESCE(top.requested_by IS NOT NULL
                AND json_type(top.payload, '$.asset_id') = 'text', 0) AS pressed,
       json_extract(j.payload, '$.products') AS products,
       COALESCE(json_extract(j.payload, ?), 0) AS again,
       COALESCE(json_type(top.payload, '$.roots') = 'array', 0) AS folders,
       COUNT(*) AS n,
       MIN(COALESCE(top.created_at, j.created_at)) AS since
  FROM jobs AS j
  LEFT JOIN jobs AS top ON top.id = j.root_id
 WHERE j.type IN (SELECT value FROM json_each(?))
   AND unlikely(j.state IN ('queued', 'running', 'blocked', 'paused'))
 GROUP BY 1, 2, 3, 4, 5
"""

# The payload of the top of every family with a live row of some types: what a run was asked for,
# while its tasks remain after its own page has finished.
_LIVE_TOPS = """
SELECT payload FROM jobs
 WHERE id IN (
   SELECT DISTINCT root_id FROM jobs
    WHERE type IN (SELECT value FROM json_each(?))
      AND unlikely(state IN ('queued', 'running', 'blocked', 'paused'))
 )
"""

# Every file's row of some types of the runs over some files from one id on, live or finished, less
# cancelled rows and rows about no file. The first placeholder is "again"'s path; the last two, the id.
_PRESSED_SINCE = """
SELECT j.type AS type, j.state AS state,
       json_extract(j.payload, '$.products') AS products,
       COALESCE(json_extract(j.payload, ?), 0) AS again,
       COALESCE(json_type(top.payload, '$.roots') = 'array', 0) AS folders,
       COUNT(*) AS n
  FROM jobs AS j
  JOIN jobs AS top ON top.id = j.root_id
 WHERE j.type IN (SELECT value FROM json_each(?))
   AND j.state IN ('queued', 'running', 'blocked', 'paused', 'done', 'failed')
   AND j.id >= ?
   AND top.id >= ?
   AND json_type(j.payload, '$.asset_id') = 'text'
   AND (json_type(top.payload, '$.roots') = 'array'
        OR (top.requested_by IS NOT NULL AND json_type(top.payload, '$.asset_id') = 'text'))
 GROUP BY 1, 2, 3, 4, 5
"""

# The newest rows of a type that is always a few rows: through `ix_jobs_by_type` and sorted, where
# the list's page of one type walks the table, which suits a type with thousands.
_NEWEST_OF_TYPE = "SELECT * FROM jobs WHERE type = ? ORDER BY id DESC LIMIT ?"

# The same for the rows not yet claimed: what a press can still pull forward.
_QUEUED_PAYLOADS = "SELECT payload FROM jobs WHERE type = ? AND state = 'queued'"

# Everything not finished, by type: whether a kind of work still WANTS the machine. `paused` is the
# one state left out: it asks for none of the machine until somebody resumes it.
_UNFINISHED_BY_TYPE = (
    "SELECT type, SUM(n) AS pending FROM job_tallies"
    " WHERE state IN ('queued', 'running', 'blocked') GROUP BY type HAVING SUM(n) > 0"
)

# What ends a pass (`Ledger.settle`): the same, less rows put off to later, which begin the next
# run, and while a job has the queue to itself, all but what runs and its own kind. The tallies,
# less the few put off (`ix_jobs_queued_later`), which no tally can tell from the rest.
_DUE_BY_TYPE = """
SELECT type, SUM(n) AS pending FROM (
  SELECT type, state, n FROM job_tallies WHERE state IN ('queued', 'running', 'blocked')
  UNION ALL
  SELECT type, 'queued', -COUNT(*) FROM jobs
   WHERE state = 'queued' AND run_after IS NOT NULL AND run_after > :now GROUP BY type
)
 WHERE :everything OR state = 'running' OR type IN (SELECT value FROM json_each(:alone))
 GROUP BY type
 HAVING SUM(n) > 0
"""

# Every live row by type AND state: what the Tasks rows say of each kind of work.
_LIVE_BY_TYPE = (
    "SELECT type, state, n FROM job_tallies"
    " WHERE state IN ('queued', 'running', 'blocked', 'paused') AND n > 0"
)

# The parked rows whose reason is the password, by kind. `substr` rather than LIKE, because LIKE
# folds case and reads `_` as a wildcard, and this is an exact prefix. See `WaitingForPassword`.
_PARKED_UNTIL_UNLOCKED = (
    "SELECT type, COUNT(*) AS n FROM jobs "
    "WHERE state = 'blocked' AND substr(error, 1, ?) = ? GROUP BY type"
)

# The walks waiting or under way, due now: a walk names a root and no file; one naming `paths` is a
# few files nobody counts ahead. What is left of one under way is its units times what it has not
# read; one still waiting has no units yet, and all it counted is left.
_TO_READ = """
SELECT CASE WHEN state = 'running' THEN units * (1 - progress) END AS left, to_read,
       to_read IS NULL AND json_type(payload, '$.paths') IS NULL AS uncounted
  FROM jobs
 WHERE type IN (SELECT value FROM json_each(?))
   AND unlikely(state IN ('queued', 'running'))
   AND NOT (state = 'queued' AND run_after IS NOT NULL AND run_after > ?)
   AND json_type(payload, '$.root_id') = 'text'
   AND json_type(payload, '$.asset_id') IS NULL
"""

# A walk's files still to read by kind: while it waits, or while this worker holds it.
_SET_TO_READ = (
    "UPDATE jobs SET to_read = ? WHERE id = ?"
    " AND (state = 'queued' OR (state = 'running' AND claimed_by = ?)) RETURNING id"
)

#: Which of a named handful of jobs are still going to be worked on. See `unfinished_among`.
_UNFINISHED_AMONG = (
    "SELECT id FROM jobs WHERE id IN (?*) AND state IN ('queued', 'running', 'blocked', 'paused')"
)

#: How many ids one of those asks about in one go: under SQLite's default ceiling of 999.
_ASKED_ABOUT_PER_READ = 500

#: How many jobs of the named types have not finished, `blocked` and `paused` included: a pass
#: must not call itself finished while a piece of it is deliberately held.
_OUTSTANDING = """
SELECT COALESCE(SUM(n), 0) AS n FROM job_tallies
 WHERE type IN (SELECT value FROM json_each(?))
   AND state IN ('queued', 'running', 'blocked', 'paused')
"""


class Reads(QueueCore):
    """The queue read as a whole, by type, state and payload."""

    async def is_live(self, job_type: str, payload: Mapping[str, Any] | None = None) -> bool:
        """Whether this exact job is waiting or under way, its payload serialised as `enqueue` writes
        it so the two agree on "identical"."""
        return await self.any_live(job_type, [payload])

    async def any_live(self, job_type: str, payloads: Sequence[Mapping[str, Any] | None]) -> bool:
        """Whether a job of this type with any of these payloads is waiting or under way: one read
        for a job asked for in more than one shape."""
        bodies = [dict(payload or {}) for payload in payloads]
        for body in bodies:
            _check_payload(body)
        texts = json.dumps([json.dumps(body) for body in bodies])
        return await self._db.fetch_one(_LIVE_LIKE, (job_type, texts)) is not None

    async def live_payloads(self, job_type: str) -> list[dict[str, Any]]:
        """The payload of every job of this kind that is waiting or under way: for a counter that
        has to know what a run was ASKED for, read from the queue so it answers after a restart."""
        rows = await self._db.fetch_all(_LIVE_PAYLOADS, (job_type,))
        return [json.loads(row["payload"]) for row in rows]

    async def live_products(self, job_types: Sequence[str]) -> list[LiveProducts]:
        """The live rows of these types, counted in SQL by type, state, quiet hours and products."""
        if not job_types:
            return []
        asked = (json.dumps(sorted(set(job_types))),)
        rows = await self._kept_read(
            ("live_products", *asked), lambda: self._db.fetch_all(_LIVE_PRODUCTS, asked)
        )
        return [
            LiveProducts(
                type=str(row["type"]),
                state=JobState(row["state"]),
                quiet=bool(row["quiet"]),
                products=_products_named(row["products"]),
                count=int(row["n"]),
            )
            for row in rows
        ]

    async def live_by_press(self, job_types: Sequence[str]) -> list[LiveWork]:
        """The live rows of these types, counted by type, products and "again", split by whether a
        person pressed them for some files (`_LIVE_BY_PRESS`): both halves read at one moment."""
        if not job_types:
            return []
        asked = (f"$.{AGAIN}", json.dumps(sorted(set(job_types))))
        rows = await self._kept_read(
            ("live_by_press", *asked), lambda: self._db.fetch_all(_LIVE_BY_PRESS, asked)
        )
        return [
            LiveWork(
                type=str(row["type"]),
                pressed=bool(row["pressed"]),
                products=_products_named(row["products"]),
                again=bool(row["again"]),
                count=int(row["n"]),
                since=int(row["since"]),
                folders=bool(row["folders"]),
            )
            for row in rows
        ]

    async def pressed_since(self, job_types: Sequence[str], since: int) -> list[PressedWork]:
        """Every file's row of these types, of the runs over some files made since `since` (seconds),
        counted by type, state, products and "again" (`_PRESSED_SINCE`)."""
        if not job_types:
            return []
        floor = floor_at(since * 1000)
        rows = await self._db.fetch_all(
            _PRESSED_SINCE,
            (f"$.{AGAIN}", json.dumps(sorted(set(job_types))), floor, floor),
        )
        return [
            PressedWork(
                type=str(row["type"]),
                state=JobState(row["state"]),
                products=_products_named(row["products"]),
                again=bool(row["again"]),
                count=int(row["n"]),
                folders=bool(row["folders"]),
            )
            for row in rows
        ]

    async def live_tops(self, job_types: Sequence[str]) -> list[dict[str, Any]]:
        """The payload of the top of every family with a live row of these types (`_LIVE_TOPS`): a
        run's first page carries its products and folders, and finishes long before its tasks."""
        if not job_types:
            return []
        asked = (json.dumps(sorted(set(job_types))),)
        rows = await self._kept_read(
            ("live_tops", *asked), lambda: self._db.fetch_all(_LIVE_TOPS, asked)
        )
        return [json.loads(row["payload"]) for row in rows]

    async def live_asset_ids(self, job_type: str) -> list[str]:
        """The file each waiting or running job of this type is about, where its payload names
        one. See `_LIVE_ASSET_IDS`."""
        rows = await self._kept_read(
            ("live_asset_ids", job_type), lambda: self._db.fetch_all(_LIVE_ASSET_IDS, (job_type,))
        )
        return [str(row["asset_id"]) for row in rows]

    async def live_files(self) -> list[str]:
        """Every file a waiting or running job of any type is about. See `_LIVE_FILES`."""
        rows = await self._kept_read(("live_files",), lambda: self._db.fetch_all(_LIVE_FILES, ()))
        return [str(row["asset_id"]) for row in rows]

    async def newest_of(self, job_type: str, *, limit: int) -> list[Job]:
        """The newest rows of a type that is always a few, in any state (`_NEWEST_OF_TYPE`)."""
        rows = await self._db.fetch_all(_NEWEST_OF_TYPE, (job_type, min(limit, MAX_PAGE_SIZE)))
        return [_to_job(row) for row in rows]

    async def queued_payloads(self, job_type: str) -> list[dict[str, Any]]:
        """The payload of every job of this kind still WAITING to be claimed: what a press of the
        same work pulls forward, by enqueueing that payload again with `dedupe`."""
        rows = await self._db.fetch_all(_QUEUED_PAYLOADS, (job_type,))
        return [json.loads(row["payload"]) for row in rows]

    async def counts(self) -> dict[str, int]:
        """How many jobs are in each state. What the dashboard is, underneath."""
        counts: dict[str, int] = {}
        for row in await self._db.fetch_all(_TALLIES):
            counts[str(row["state"])] = counts.get(str(row["state"]), 0) + int(row["n"])
        return counts

    async def waiting_for_password(self) -> dict[str, int]:
        """How many jobs of each kind are parked until somebody gives the password: what the
        shell's unlock bar follows, read through the few blocked rows. See `WaitingForPassword`."""
        rows = await self._db.fetch_all(_PARKED_UNTIL_UNLOCKED, (len(UNLOCK_WAIT), UNLOCK_WAIT))
        return {str(row["type"]): int(row["n"]) for row in rows}

    async def work_summary(self, *, window_seconds: int = RECENT_WORK_SECONDS) -> WorkSummary:
        """What each kind has left, how much of the run it has done and how fast, held for a few
        seconds; read from the tallies and the rows of this run, never the whole table."""
        now = int(self._now())
        held = self._work_summary
        if held is not None and now - held[0] < self._summary_fresh_for:
            return held[1]
        (start,) = await self._kept_read(
            ("burst_start",), lambda: self._db.fetch_all(_BURST_START, (now,))
        )
        since = start["since"]
        run_from = since if since is not None else now + 1
        states: dict[str, dict[str, int]] = {}
        run: dict[str, WorkKind] = {}
        for row in await self._db.fetch_all(_TALLIES):
            kind = str(row["type"])
            states.setdefault(kind, {})[str(row["state"])] = int(row["n"])
            run.setdefault(kind, WorkKind())
        live = await self._kept_read(
            ("live_summary", run_from), lambda: self._db.fetch_all(_LIVE_SUMMARY, (run_from,))
        )
        for row in live:
            here = run.setdefault(str(row["type"]), WorkKind())
            here.outstanding += int(row["in_run"] or 0)
            here.left_units += float(row["left_units"] or 0)
        for row in await self._db.fetch_all(_SETTLED_IN_RUN, (run_from,)):
            here = run.setdefault(str(row["type"]), WorkKind())
            if row["state"] == JobState.DONE.value:
                here.done += int(row["in_run"])
            else:
                here.failed += int(row["in_run"])
        lately = {
            str(row["type"]): int(row["lately"])
            for row in await self._db.fetch_all(_DONE_LATELY, (now - window_seconds,))
        }
        for kind, by_state in states.items():
            if JobState.DONE.value in by_state:
                # Per minute: a rate under one a second would read as 0.0.
                run[kind].per_minute = round(lately.get(kind, 0) * 60 / window_seconds, 2)
        answer = WorkSummary(states=states, run=run, since=since)
        self._work_summary = (now, answer)
        return answer

    async def counts_by_type(self) -> dict[str, dict[str, int]]:
        """How many jobs of each KIND are in each state, over the whole table: a denominator for
        "how far through is Sift" has to count every row, not a screen's page."""
        rows = await self._db.fetch_all(_TALLIES)
        tally: dict[str, dict[str, int]] = {}
        for row in rows:
            tally.setdefault(str(row["type"]), {})[str(row["state"])] = int(row["n"])
        return tally

    async def unfinished_by_type(self) -> dict[str, int]:
        """Unfinished work by kind, in one query: what the machine's budget divides on."""
        rows = await self._db.fetch_all(_UNFINISHED_BY_TYPE)
        return {row["type"]: row["pending"] for row in rows}

    async def due_by_type(self) -> dict[str, int]:
        """Unfinished work due now by kind (`_DUE_BY_TYPE`), for the ledger on the pool's timer."""
        from sift.kernel.jobs.worker_pool import exclusive_job_types

        alone = await self.held_by_exclusive()
        rows = await self._db.fetch_all(
            _DUE_BY_TYPE,
            {
                "now": int(self._now()),
                "everything": not alone,
                "alone": json.dumps(sorted(exclusive_job_types())),
            },
        )
        return {row["type"]: row["pending"] for row in rows}

    async def held_by_exclusive(self) -> bool:
        """Whether a job that has the queue to itself runs or waits to (`_EXCLUSIVE_HELD`)."""
        from sift.kernel.jobs.worker_pool import exclusive_job_types

        exclusive = sorted(exclusive_job_types())
        if not exclusive:
            return False
        sql, params = in_clause(_EXCLUSIVE_HELD, exclusive)
        return bool(await self._db.fetch_all(sql, (*params, int(self._now()))))

    async def live_by_type(self) -> dict[str, dict[str, int]]:
        """How many rows of each kind are in each live state: what the Tasks rows say, where waiting
        is what has not started and a running job is "Running now"."""
        rows = await self._db.fetch_all(_LIVE_BY_TYPE)
        tally: dict[str, dict[str, int]] = {}
        for row in rows:
            tally.setdefault(str(row["type"]), {})[str(row["state"])] = int(row["n"])
        return tally

    async def outstanding(self, *job_types: str) -> int:
        """How many jobs of these types have not finished, `blocked` included: what tells "work is
        left" from "something is doing it", which a count of unfinished files cannot."""
        if not job_types:
            return 0
        # The types go in as ONE JSON parameter, so no part of the statement is built by formatting.
        (row,) = await self._db.fetch_all(_OUTSTANDING, (json.dumps(list(job_types)),))
        return int(row["n"])

    async def files_to_read(self, job_types: Sequence[str]) -> FilesToRead:
        """What the live walks of these types still have to read, their files left shared out by
        their kinds (`_TO_READ`)."""
        rows = await self._db.fetch_all(
            _TO_READ, (json.dumps(sorted(set(job_types))), int(self._now()))
        )
        answer = FilesToRead()
        for row in rows:
            answer.uncounted += int(row["uncounted"])
            kinds = json.loads(row["to_read"]) if row["to_read"] else {}
            whole = sum(kinds.values())
            for kind, files in kinds.items():
                if files > 0:
                    left = whole if row["left"] is None else float(row["left"])
                    share = left * files / whole
                    answer.by_kind[kind] = answer.by_kind.get(kind, 0.0) + share
        return answer

    async def set_to_read(
        self, job_id: str, kinds: Mapping[str, int], *, worker_id: str | None = None
    ) -> bool:
        """Write a walk's files still to read by kind, while it waits or while `worker_id` holds it."""
        async with self._writing() as connection:
            rows = await _fetch(
                connection, _SET_TO_READ, (json.dumps(dict(kinds)), job_id, worker_id)
            )
        return bool(rows)

    async def unfinished_among(self, job_ids: Sequence[str]) -> set[str]:
        """Which of these jobs are still going to be worked on: the boot sweep of the job
        workspaces asks about a handful, in chunks under SQLite's parameter cap."""
        living: set[str] = set()
        ids = list(job_ids)
        for start in range(0, len(ids), _ASKED_ABOUT_PER_READ):
            chunk = ids[start : start + _ASKED_ABOUT_PER_READ]
            sql, params = in_clause(_UNFINISHED_AMONG, chunk)
            living.update(str(row["id"]) for row in await self._db.fetch_all(sql, params))
        return living

    async def next_scheduled(self, job_type: str) -> int | None:
        """The earliest moment a WAITING job of this type may run, or None; rows with no time are
        skipped, since NULL means as soon as a worker is free."""
        (row,) = await self._db.fetch_all(_NEXT_SCHEDULED, (job_type,))
        due = row["due"]
        return None if due is None else int(due)

    async def seconds_until_due(self) -> float | None:
        """How long until the next row put off to later comes due, or None: what an idle worker
        waits for when no work arrives."""
        now = self._clock()
        (row,) = await self._db.fetch_all(_NEXT_DUE, (int(now),))
        return None if row["due"] is None else max(0.0, int(row["due"]) - now)

    async def next_scheduled_of(self, job_types: Sequence[str]) -> dict[str, int]:
        """`next_scheduled` for several types in one statement; a type with none is absent."""
        if not job_types:
            return {}
        rows = await self._db.fetch_all(_NEXT_SCHEDULED_OF, (json.dumps(sorted(set(job_types))),))
        return {str(row["type"]): int(row["due"]) for row in rows if row["due"] is not None}
