# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pages of the queue: the list and its tallies, a family's steps, and where a job is in the line."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from sift.kernel.db import in_clause
from sift.kernel.jobs.queue_core import QueueCore
from sift.kernel.jobs.queue_rows import (
    _FOLDED,
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    Job,
    JobPage,
    JobState,
    StepCounts,
    StepsPage,
    _to_job,
)

#: Where a family's step count per state stops and says "at least": an exact count of a library
#: scan's family would walk it all on every refresh, while ANY step in a state stays exact.
STEP_COUNT_CAP = 1000

#: Each filter's condition and the name it binds, emitted only when given (a guarded
#: `(:x IS NULL OR ...)` cannot use an index); the page and the count share this one list.
_WHERE = "\n WHERE "
_AND = "\n   AND "

_FILTERS: tuple[tuple[str, str], ...] = (
    ("state", "state = :state"),
    ("job_type", "type = :job_type"),
    ("parent_id", "parent_id = :parent_id"),
    # The tops only: every row that heads a family, written as `ix_jobs_tops_by_id` states it (not
    # `parent_id IS NULL`, which the planner answers through the wrong index). Binds nothing.
    ("tops_only", "root_id = id"),
    # Every row but the types named (a JSON list): the upkeep a listing leaves off.
    ("leaving_out", "type NOT IN (SELECT value FROM json_each(:leaving_out))"),
    # Only the types named (a JSON list), for "Older tasks"; an empty list matches no row.
    ("among", "type IN (SELECT value FROM json_each(:among))"),
    # Every row but the quiet ones: by-itself work heading its own family, unpressed, waiting,
    # running or done. Failed and canceled rows stay, since the bulk actions act on all of them.
    (
        "quiet",
        "NOT (root_id = id AND requested_by IS NULL AND state IN ('queued', 'running', 'done')"
        " AND type IN (SELECT value FROM json_each(:quiet)))",
    ),
    # The tops whose folded row shows this state: a state's tab, in the page's statement only.
    ("folded", "(" + _FOLDED + ") = :folded"),
)


#: The page's own filters for a page of one type in every state: the type kept off `ix_jobs_by_type`
#: (`+type`), so the walk newest first stops at the page instead of sorting every row of the type.
_PAGE_FILTERS: tuple[tuple[str, str], ...] = tuple(
    (name, "+type = :job_type" if name == "job_type" else condition) for name, condition in _FILTERS
)


def _where(params: Mapping[str, Any], filters: Sequence[tuple[str, str]] = _FILTERS) -> str:
    """The WHERE clause for the filters actually present in `params`, or nothing at all: one
    dictionary decides both the clause and the bindings, so no clause names an unbound name."""
    given = [condition for name, condition in filters if params.get(name) is not None]
    return _WHERE + _AND.join(given) if given else ""


#: Where each waiting job on a page is in the line, in one statement: each place the count of the
#: stretch of `ix_jobs_claim_by_id` since the one before it, added up. CLAIMABLE, by `_CLAIM`'s own
#: condition; a position, not an estimate, since limits and priority can still pass a job.
_POSITIONS = """
WITH line AS NOT MATERIALIZED (
  SELECT priority, id FROM jobs WHERE state = 'queued' AND (run_after IS NULL OR run_after <= ?)
), ordered AS (
  SELECT id, priority, LAG(priority) OVER w AS was_priority, LAG(id) OVER w AS was_id FROM jobs
   WHERE id IN (?*) AND +state = 'queued' AND (run_after IS NULL OR run_after <= ?)
  WINDOW w AS (ORDER BY priority, id)
), stretch AS (
  SELECT o.id, o.priority,
    (SELECT COUNT(*) FROM line WHERE priority = o.priority AND id <= o.id
        AND id > CASE WHEN o.was_priority = o.priority THEN o.was_id ELSE '' END)
    + CASE WHEN o.was_priority IS NULL THEN (SELECT COUNT(*) FROM line WHERE priority < o.priority)
      WHEN o.was_priority < o.priority THEN
        (SELECT COUNT(*) FROM line WHERE priority = o.was_priority AND id > o.was_id)
        + (SELECT COUNT(*) FROM line WHERE priority > o.was_priority AND priority < o.priority)
      ELSE 0 END AS ahead
  FROM ordered o
)
SELECT id, SUM(ahead) OVER (ORDER BY priority, id) AS place FROM stretch
"""

_LIST_HEAD = "SELECT * FROM jobs"
#: A total by state and type alone, read off the tallies the table's triggers keep, of the types
#: `:only_types` names where given; a few dozen rows, so the guarded terms cost nothing.
_TALLIED_TOTAL = """
SELECT COALESCE(SUM(n), 0) AS total FROM job_tallies
 WHERE (:state IS NULL OR state = :state) AND (:job_type IS NULL OR type = :job_type)
   AND (:only_types IS NULL OR type IN (SELECT value FROM json_each(:only_types)))
"""
_COUNT_HEAD = "SELECT COUNT(*) AS total FROM jobs"
#: The rows of a few named types, for the total a listing that leaves them out subtracts, walked
#: from the list (`CROSS JOIN`) so each type is a probe of `ix_jobs_last_run`.
_UPKEEP_COUNT_HEAD = (
    "SELECT COUNT(*) AS total FROM (SELECT jobs.* FROM json_each(:only_types) AS upkeep"
    " CROSS JOIN jobs ON jobs.type = upkeep.value)"
)
#: The quiet rows among `:quiet_types`, for the total a listing that leaves them out subtracts and
#: the tallies by state, walked from the types into `ix_jobs_unpressed_tops`.
_QUIET_COUNT_HEAD = (
    "SELECT COUNT(*) AS total FROM (SELECT jobs.* FROM json_each(:quiet_types) AS hushed"
    " CROSS JOIN jobs ON jobs.type = hushed.value"
    " WHERE jobs.root_id = jobs.id AND jobs.requested_by IS NULL"
    " AND jobs.state IN ('queued', 'running', 'done'))"
)
_QUIET_BY_STATE = (
    "SELECT jobs.state AS state, COUNT(*) AS total FROM json_each(:quiet_types) AS hushed"
    " CROSS JOIN jobs ON jobs.type = hushed.value"
    " WHERE jobs.root_id = jobs.id AND jobs.requested_by IS NULL"
    " AND jobs.state IN ('queued', 'running', 'done')"
    " GROUP BY jobs.state"
)
_LIST_ORDER = "\n ORDER BY id DESC"


def _list_page(params: Mapping[str, Any]) -> str:
    """One page of the queue, newest first."""
    filters = _FILTERS if params.get("state") is not None else _PAGE_FILTERS
    return _LIST_HEAD + _where(params, filters) + _LIST_ORDER + _PAGE


def _list_total(params: Mapping[str, Any]) -> str:
    """How many rows that page is a page of: a statement of its own, since a window count over the
    page must read every matching row before the LIMIT applies, and a count can use an index."""
    return _COUNT_HEAD + _where(params)


def _upkeep_total(params: Mapping[str, Any]) -> str:
    """How many of the rows `_list_total` counts are of the types `:only_types` names."""
    return _UPKEEP_COUNT_HEAD + _where(params)


def _quiet_total(params: Mapping[str, Any]) -> str:
    """How many of the rows `_list_total` counts are quiet rows of the types `:quiet_types` names."""
    return _QUIET_COUNT_HEAD + _where(params)


#: The tops a page of families lists, counted by the state their folded row shows. `noqa: S608`:
#: the only thing concatenated in is `_FOLDED`, a constant above.
_FAMILY_TALLY_HEAD = "SELECT (" + _FOLDED + ") AS folded, COUNT(*) AS total FROM jobs"  # noqa: S608
_FAMILY_TALLY_TAIL = "\n GROUP BY 1"


def _family_tally(params: Mapping[str, Any]) -> str:
    """How many families a folded page is a page of are in each folded state: built from the page's
    own filters but never `folded`, which narrows the list to one of these numbers."""
    return _FAMILY_TALLY_HEAD + _where(params) + _FAMILY_TALLY_TAIL


#: The page window, shared by the statement above so the two orderings cannot drift apart.
_PAGE = "\n LIMIT :limit OFFSET :offset"


#: The top of one job's tree, its type and who pressed it: the job's `root_id`, then that row.
_TOP_OF = (
    "SELECT top.type AS type, top.requested_by AS requested_by"
    " FROM jobs AS me JOIN jobs AS top ON top.id = me.root_id WHERE me.id = ?"
)

# Every family on a page, counted by state in one statement: each count a seek on `ix_jobs_family`
# stopped at `:cap`, the states bound from `JobState`. A step is every row of the family but the
# top. `_ROLL_UP` looks one level down and cannot answer this.
_STEP_COUNTS = """
WITH tops(root) AS (SELECT value FROM json_each(:roots)),
     states(state) AS (SELECT value FROM json_each(:states))
SELECT tops.root AS root, states.state AS state,
       (SELECT COUNT(*) FROM (
          SELECT 1 FROM jobs step
           WHERE step.root_id = tops.root AND step.state = states.state AND step.id != tops.root
           LIMIT :cap)) AS steps
  FROM tops CROSS JOIN states
"""

# The one file a family is about, when its steps agree on exactly one: a download's steps name its
# file, a folder scan's name hundreds. At most two distinct files among `:cap` steps.
_FAMILY_FILE = """
SELECT tops.value AS root,
       (SELECT json_group_array(asset) FROM (
          SELECT DISTINCT json_extract(step.payload, '$.asset_id') AS asset
            FROM (SELECT payload FROM jobs
                   WHERE root_id = tops.value AND id != tops.value
                   LIMIT :cap) AS step
           WHERE json_extract(step.payload, '$.asset_id') IS NOT NULL
           LIMIT 2)) AS assets
  FROM json_each(:roots) AS tops
"""

# A page of one family's steps in the order they were handed out, and how many there are, stopped
# at the cap. `ix_jobs_family_by_id` is this order, so a page of a scan's steps is a range read.
_STEPS_PAGE = """
SELECT * FROM jobs
 WHERE root_id = :root AND id != :root
 ORDER BY id
 LIMIT :limit OFFSET :offset
"""
_STEPS_TOTAL = """
SELECT COUNT(*) AS total FROM (
  SELECT 1 FROM jobs WHERE root_id = :root AND id != :root LIMIT :cap
)
"""


class Pages(QueueCore):
    """Pages of the queue, of a family's steps, and the line."""

    async def waiting_unpressed(self, job_type: str) -> list[Job]:
        """The rows of this type waiting to run that nobody pressed and no pass handed out."""
        page = await self.list(job_type=job_type, state=JobState.QUEUED, limit=MAX_PAGE_SIZE)
        return [one for one in page.jobs if one.timing is None and one.parent_id is None]

    async def get(self, job_id: str) -> Job | None:
        row = await self._db.fetch_one("SELECT * FROM jobs WHERE id = ?", (job_id,))
        return None if row is None else _to_job(row)

    async def top_of(self, job_id: str) -> tuple[str, str | None] | None:
        """The top of this job's tree: its type and who pressed it. None for a job gone.

        How a job handed out by a press learns its presser (`WorkerPool._pressed_by`): a child does
        not carry `requested_by`, and the top outlives every row under it.
        """
        row = await self._db.fetch_one(_TOP_OF, (job_id,))
        if row is None:
            return None
        return str(row["type"]), None if row["requested_by"] is None else str(row["requested_by"])

    async def step_counts(self, root_ids: Sequence[str]) -> dict[str, StepCounts]:
        """Each of these tops' families, its steps (all it started, however deep, but the top)
        counted by state in one statement; every top asked about has an answer."""
        wanted = list(dict.fromkeys(one for one in root_ids if one))
        if not wanted:
            return {}
        rows = await self._db.fetch_all(
            _STEP_COUNTS,
            {
                "roots": json.dumps(wanted),
                "states": json.dumps([state.value for state in JobState]),
                "cap": STEP_COUNT_CAP,
            },
        )
        counted: dict[str, dict[str, int]] = {root: {} for root in wanted}
        for row in rows:
            steps = int(row["steps"])
            if steps:
                counted[str(row["root"])][str(row["state"])] = steps
        return {
            root: StepCounts(
                by_state=by_state,
                at_least=any(steps >= STEP_COUNT_CAP for steps in by_state.values()),
            )
            for root, by_state in counted.items()
        }

    async def family_files(self, root_ids: Sequence[str]) -> dict[str, str]:
        """The one file each of these families is about, where its steps agree on one."""
        wanted = list(dict.fromkeys(one for one in root_ids if one))
        if not wanted:
            return {}
        rows = await self._db.fetch_all(
            _FAMILY_FILE, {"roots": json.dumps(wanted), "cap": STEP_COUNT_CAP}
        )
        found: dict[str, str] = {}
        for row in rows:
            assets = json.loads(row["assets"] or "[]")
            if len(assets) == 1 and isinstance(assets[0], str):
                found[str(row["root"])] = assets[0]
        return found

    async def steps(
        self, root_id: str, *, limit: int = DEFAULT_PAGE_SIZE, offset: int = 0
    ) -> StepsPage:
        """A page of one family's steps in the order handed out, each with its `parent_id`; a job
        that heads no family answers an empty page."""
        if limit < 1:
            raise ValueError("a page needs at least one row")
        if offset < 0:
            raise ValueError("a page cannot start before the first row")
        params = {
            "root": root_id,
            "limit": min(limit, MAX_PAGE_SIZE),
            "offset": offset,
            "cap": STEP_COUNT_CAP,
        }
        rows = await self._db.fetch_all(_STEPS_PAGE, params)
        (counted,) = await self._db.fetch_all(_STEPS_TOTAL, params)
        total = int(counted["total"])
        return StepsPage(
            jobs=[_to_job(row) for row in rows], total=total, at_least=total >= STEP_COUNT_CAP
        )

    async def positions_of(self, job_ids: Sequence[str]) -> dict[str, int]:
        """Where each of these jobs is in the line, keyed by job, one-based (1 is taken next). A job
        not waiting in the line (running, finished, blocked, waiting for its moment) is ABSENT, since
        a nought would read as "next". See `_POSITIONS`."""
        wanted = [one for one in dict.fromkeys(job_ids) if one]
        if not wanted:
            return {}
        sql, params = in_clause(_POSITIONS, wanted)
        now = int(self._now())
        rows = await self._db.fetch_all(sql, (now, *params, now))
        return {str(row["id"]): int(row["place"]) for row in rows}

    async def children(self, parent_id: str) -> list[Job]:
        rows = await self._db.fetch_all(
            "SELECT * FROM jobs WHERE parent_id = ? ORDER BY id", (parent_id,)
        )
        return [_to_job(row) for row in rows]

    async def list(
        self,
        *,
        state: JobState | None = None,
        job_type: str | None = None,
        parent_id: str | None = None,
        tops_only: bool = False,
        leaving_out: Sequence[str] = (),
        quiet: Sequence[str] = (),
        among: Sequence[str] | None = None,
        folded: JobState | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> JobPage:
        """A page of the queue, newest first (`id` breaks ties), optionally filtered.

        `tops_only` pages FAMILIES: only the jobs that head one, counted by the state each folded
        row shows (`by_state`); `folded` narrows such a page to one of those states. `leaving_out`
        names types the page and its total leave out (the upkeep Activity does not list); `quiet`
        the work that runs by itself as files arrive, left out where it heads its own family, nobody
        pressed it and it is waiting, running or done; `among` keeps only the types it names (an
        empty list keeps none). Every filter is optional and an omitted one matches everything,
        which is right for a list and would be a bug on a single-object read.
        """
        if limit < 1:
            raise ValueError("a page needs at least one row")
        if offset < 0:
            raise ValueError("a page cannot start before the first row")
        if folded is not None and (not tops_only or state is not None):
            raise ValueError("a folded state narrows a page of families, and not by a row's state")
        # A caller asking for a million rows gets a page.
        limit = min(limit, MAX_PAGE_SIZE)

        params: dict[str, Any] = {
            "state": state.value if state else None,
            "job_type": job_type,
            "parent_id": parent_id,
            "tops_only": True if tops_only else None,
            "among": json.dumps(sorted(set(among))) if among is not None else None,
            "limit": limit,
            "offset": offset,
        }
        # The page walks newest first and stops at the limit; the TOTAL is the tallies' where only
        # state and type filter it, and otherwise subtracts the left-out types' rows, a probe of
        # `ix_jobs_last_run`, rather than read every row's type.
        left_out = json.dumps(sorted(set(leaving_out))) if leaving_out else None
        kept_quiet = json.dumps(sorted(set(quiet))) if quiet else None
        paged = {
            **params,
            "leaving_out": left_out,
            "quiet": kept_quiet,
            "folded": folded.value if folded else None,
        }
        rows = await self._db.fetch_all(_list_page(paged), paged)
        if tops_only and state is None:
            # The families counted by the state their row shows, the page's total out of the same
            # answer: every family is in exactly one state.
            tallied = {**paged, "folded": None}
            by_state = {
                str(row["folded"]): int(row["total"])
                for row in await self._db.fetch_all(_family_tally(tallied), tallied)
            }
            total = sum(by_state.values()) if folded is None else by_state.get(folded.value, 0)
            return JobPage(jobs=[_to_job(row) for row in rows], total=total, by_state=by_state)
        if params["parent_id"] is None and params["tops_only"] is None and params["among"] is None:
            total = await self._tallied({**params, "only_types": None})
            if left_out is not None:
                total -= await self._tallied({**params, "only_types": left_out})
        else:
            total = await self._counted(params, left_out)
        if kept_quiet is not None:
            # Disjoint from the upkeep above by declaration (`register_handler` refuses a type
            # that is both), so the two subtractions never count one row twice.
            asked = {**params, "quiet_types": kept_quiet}
            (hushed,) = await self._db.fetch_all(_quiet_total(asked), asked)
            total -= int(hushed["total"])

        return JobPage(jobs=[_to_job(row) for row in rows], total=total)

    async def _tallied(self, params: Mapping[str, Any]) -> int:
        (row,) = await self._db.fetch_all(_TALLIED_TOTAL, params)
        return int(row["total"])

    async def _counted(self, params: Mapping[str, Any], left_out: str | None) -> int:
        # A COUNT always returns its row; a window count would vanish on a page past the end.
        (counted,) = await self._db.fetch_all(_list_total(params), params)
        total = int(counted["total"])
        if left_out is not None:
            only = {**params, "only_types": left_out}
            (upkeep,) = await self._db.fetch_all(_upkeep_total(only), only)
            total -= int(upkeep["total"])
        return total

    async def quiet_by_state(self, quiet: Sequence[str]) -> dict[str, int]:
        """How many quiet rows of these types are in each state: what the tallies above Activity's
        list take away, so a state's number counts the rows its list draws."""
        if not quiet:
            return {}
        rows = await self._db.fetch_all(
            _QUIET_BY_STATE, {"quiet_types": json.dumps(sorted(set(quiet)))}
        )
        return {str(row["state"]): int(row["total"]) for row in rows}
