# SPDX-License-Identifier: AGPL-3.0-or-later
"""The queue read whole: live work by type and payload, tallies, and the shape of the current run."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from sift.kernel.db import in_clause
from sift.kernel.ids import floor_at
from sift.kernel.jobs.families import AGAIN
from sift.kernel.jobs.queue_core import QueueCore
from sift.kernel.jobs.queue_rows import (
    _UNFINISHED,
    MAX_PAGE_SIZE,
    UNLOCK_WAIT,
    Job,
    JobState,
    LiveProducts,
    LiveWork,
    PressedWork,
    WorkKind,
    WorkSummary,
    _check_payload,
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

# When the current batch of work began: the oldest unfinished job that is DUE, so tomorrow's backup
# or a paused row cannot pin "this run" to days ago. One live-state IN, so state indexes drive it.
_BURST_START = """
SELECT MIN(created_at) AS since
  FROM jobs
 WHERE unlikely(state IN ('running', 'blocked', 'queued'))
   AND (state != 'queued' OR run_after IS NULL OR run_after <= ?)
"""

# Everything the dashboard needs about the work's shape in ONE pass over the table: the state
# tallies, the part in this run, and how many finished lately, which is what an ETA is made of.
_WORK_SUMMARY = """
SELECT type,
       state,
       COUNT(*) AS n,
       SUM(created_at >= ?) AS in_run,
       SUM(updated_at >= ?) AS lately,
       SUM(units * (1 - progress)) AS left_units
  FROM jobs
 GROUP BY type, state
"""

# Is this work happening AT ALL (waiting, under way, held or paused)? For deciding whether work is
# NEEDED. Live rows are few, but the statistics are taken on an empty queue, so every statement
# about them says `unlikely(...)`, and `test_queue_plans` holds each to a plan that is no walk.
_LIVE_LIKE = (
    "SELECT id FROM jobs WHERE type = ? AND payload = ? "
    "AND unlikely(state IN ('queued', 'running', 'blocked', 'paused')) LIMIT 1"
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

# Every file's row of some types, of the runs over some files made from one id on, live or finished,
# so a run's bar reads done of total; cancelled rows and rows about no file are left out. The first
# placeholder is the path of "again"; the last two are the same id.
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
    "SELECT type, COUNT(*) AS pending FROM jobs "
    "WHERE unlikely(state IN ('queued', 'running', 'blocked')) GROUP BY type"
)

# The same, less the rows waiting for a later moment: what decides that a pass has ENDED
# (`Ledger.settle`), since a row put off to later begins the next run rather than ending this one.
_DUE_BY_TYPE = (
    "SELECT type, COUNT(*) AS pending FROM jobs"
    " WHERE unlikely(state IN ('queued', 'running', 'blocked'))"
    " AND NOT (state = 'queued' AND run_after IS NOT NULL AND run_after > ?) GROUP BY type"
)

# Every live row by type AND state: what the Tasks rows say of each kind of work.
_LIVE_BY_TYPE = (
    "SELECT type, state, COUNT(*) AS n FROM jobs "
    "WHERE unlikely(state IN ('queued', 'running', 'blocked', 'paused')) GROUP BY type, state"
)

# The parked rows whose reason is the password, by kind. `substr` rather than LIKE, because LIKE
# folds case and reads `_` as a wildcard, and this is an exact prefix. See `WaitingForPassword`.
_PARKED_UNTIL_UNLOCKED = (
    "SELECT type, COUNT(*) AS n FROM jobs "
    "WHERE state = 'blocked' AND substr(error, 1, ?) = ? GROUP BY type"
)

#: Which of a named handful of jobs are still going to be worked on. See `unfinished_among`.
_UNFINISHED_AMONG = (
    "SELECT id FROM jobs WHERE id IN (?*) AND state IN ('queued', 'running', 'blocked', 'paused')"
)

#: How many ids one of those asks about at once: under SQLite's default ceiling of 999.
_ASK_ABOUT_AT_ONCE = 500

#: How many jobs of the named types have not finished, `blocked` and `paused` included: a pass
#: must not call itself finished while a piece of it is deliberately held.
_OUTSTANDING = """
SELECT COUNT(*) AS n FROM jobs
 WHERE type IN (SELECT value FROM json_each(?))
   AND unlikely(state IN ('queued', 'running', 'blocked', 'paused'))
"""


class Reads(QueueCore):
    """The queue read as a whole, by type, state and payload."""

    async def is_live(self, job_type: str, payload: Mapping[str, Any] | None = None) -> bool:
        """Whether this exact job is waiting or under way, its payload serialised as `enqueue` writes
        it so the two agree on "identical"."""
        body = dict(payload or {})
        _check_payload(body)
        row = await self._db.fetch_one(_LIVE_LIKE, (job_type, json.dumps(body)))
        return row is not None

    async def live_payloads(self, job_type: str) -> list[dict[str, Any]]:
        """The payload of every job of this kind that is waiting or under way: for a counter that
        has to know what a run was ASKED for, read from the queue so it answers after a restart."""
        rows = await self._db.fetch_all(_LIVE_PAYLOADS, (job_type,))
        return [json.loads(row["payload"]) for row in rows]

    async def live_products(self, job_types: Sequence[str]) -> list[LiveProducts]:
        """The live rows of these types, counted by type, state, quiet hours and the products their
        payload names (`_LIVE_PRODUCTS`): grouped in SQL, so the answer is a few lines however long
        the run."""
        if not job_types:
            return []
        rows = await self._db.fetch_all(_LIVE_PRODUCTS, (json.dumps(sorted(set(job_types))),))
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
        rows = await self._db.fetch_all(
            _LIVE_BY_PRESS, (f"$.{AGAIN}", json.dumps(sorted(set(job_types))))
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
        """Every file's row of these types, of the runs over some files made since this second, live
        or finished, counted by type, state, products and "again" (`_PRESSED_SINCE`). `since` is in
        seconds; the read is a range of ids from `ids.floor_at`."""
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
        rows = await self._db.fetch_all(_LIVE_TOPS, (json.dumps(sorted(set(job_types))),))
        return [json.loads(row["payload"]) for row in rows]

    async def live_asset_ids(self, job_type: str) -> list[str]:
        """The file each waiting or running job of this type is about, where its payload names
        one. See `_LIVE_ASSET_IDS`."""
        rows = await self._db.fetch_all(_LIVE_ASSET_IDS, (job_type,))
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
        rows = await self._db.fetch_all("SELECT state, COUNT(*) AS n FROM jobs GROUP BY state")
        return {row["state"]: row["n"] for row in rows}

    async def waiting_for_password(self) -> dict[str, int]:
        """How many jobs of each kind are parked until somebody gives the password: what the
        shell's unlock bar follows, read through the few blocked rows. See `WaitingForPassword`."""
        rows = await self._db.fetch_all(_PARKED_UNTIL_UNLOCKED, (len(UNLOCK_WAIT), UNLOCK_WAIT))
        return {str(row["type"]): int(row["n"]) for row in rows}

    async def work_summary(self, *, window_seconds: int = RECENT_WORK_SECONDS) -> WorkSummary:
        """The shape of the work: what each kind has left, how much of the run it has done, how fast.

        One pass over the table (`_WORK_SUMMARY`), held for a few seconds: the scan reads every row,
        the screen asks on every job during an import, and a report on work must not slow it.
        """
        now = int(self._now())
        held = self._work_summary
        if held is not None and now - held[0] < self._summary_fresh_for:
            return held[1]
        (start,) = await self._db.fetch_all(_BURST_START, (now,))
        since = start["since"]
        rows = await self._db.fetch_all(
            _WORK_SUMMARY, (since if since is not None else now + 1, now - window_seconds)
        )

        states: dict[str, dict[str, int]] = {}
        run: dict[str, WorkKind] = {}
        for row in rows:
            kind, state = str(row["type"]), str(row["state"])
            states.setdefault(kind, {})[state] = int(row["n"])
            here = run.setdefault(kind, WorkKind())
            in_run = int(row["in_run"] or 0)
            if state in _UNFINISHED:
                here.outstanding += in_run
                here.left_units += float(row["left_units"] or 0)
            elif state == JobState.DONE.value:
                here.done += in_run
                # Per MINUTE rather than per second, because a rate under one a second reads as
                # 0.0 and an import of large files is exactly that.
                here.per_minute = round(int(row["lately"] or 0) * 60 / window_seconds, 2)
            elif state == JobState.FAILED.value:
                here.failed += in_run
        answer = WorkSummary(states=states, run=run, since=since)
        self._work_summary = (now, answer)
        return answer

    async def counts_by_type(self) -> dict[str, dict[str, int]]:
        """How many jobs of each KIND are in each state, over the whole table: a denominator for
        "how far through is Sift" has to count every row, not a screen's page."""
        rows = await self._db.fetch_all("SELECT type, state, COUNT(*) AS n FROM jobs GROUP BY 1, 2")
        tally: dict[str, dict[str, int]] = {}
        for row in rows:
            tally.setdefault(str(row["type"]), {})[str(row["state"])] = int(row["n"])
        return tally

    async def unfinished_by_type(self) -> dict[str, int]:
        """How much unfinished work each kind of job has: what the machine's budget divides on, read
        on a timer, so one query however many kinds of work exist."""
        rows = await self._db.fetch_all(_UNFINISHED_BY_TYPE)
        return {row["type"]: row["pending"] for row in rows}

    async def due_by_type(self) -> dict[str, int]:
        """How much unfinished work each kind has that is due now (`_DUE_BY_TYPE`): read on the pool's
        timer for the work ledger, which closes the run of every family with none."""
        rows = await self._db.fetch_all(_DUE_BY_TYPE, (int(self._now()),))
        return {row["type"]: row["pending"] for row in rows}

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

    async def unfinished_among(self, job_ids: Sequence[str]) -> set[str]:
        """Which of these jobs are still going to be worked on: the boot sweep of the job
        workspaces asks about a handful, in chunks under SQLite's parameter cap."""
        living: set[str] = set()
        ids = list(job_ids)
        for start in range(0, len(ids), _ASK_ABOUT_AT_ONCE):
            chunk = ids[start : start + _ASK_ABOUT_AT_ONCE]
            sql, params = in_clause(_UNFINISHED_AMONG, chunk)
            living.update(str(row["id"]) for row in await self._db.fetch_all(sql, params))
        return living

    async def next_scheduled(self, job_type: str) -> int | None:
        """The earliest moment a WAITING job of this type may run, or None: what Scheduled tasks
        calls "next". Rows with no time are skipped, since NULL means as soon as a worker is free (Run
        now); its own statement, because the minimum of a page is wrong past the page."""
        (row,) = await self._db.fetch_all(_NEXT_SCHEDULED, (job_type,))
        due = row["due"]
        return None if due is None else int(due)
