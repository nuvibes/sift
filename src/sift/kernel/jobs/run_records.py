# SPDX-License-Identifier: AGPL-3.0-or-later
"""A run as the ledger reads it back, and the reads that give runs back."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field

from sift.kernel.db import Database, Row, in_clause
from sift.kernel.jobs.families import Family

_RECENT = "SELECT * FROM work_runs ORDER BY started_at DESC LIMIT ?"
_ONE = "SELECT * FROM work_runs WHERE id = ?"
#: Which of these ids are runs on record. See `Ledger.recorded_among`.
_RECORDED_AMONG = "SELECT id FROM work_runs WHERE id IN (?*)"
#: The last run of a family that ENDED, a stopped one included, on whichever machine: what a long
#: pass's row on Tasks says ran last. A stopped run is the last run, and it was canceled; the pace's
#: own read leaves it out, because its pace is not the machine's.
_LAST_ENDED_OF_FAMILY = """
SELECT * FROM work_runs
 WHERE family = ? AND finished_at IS NOT NULL
 ORDER BY finished_at DESC, id DESC
 LIMIT 1
"""

#: THE LAST FINISHED RUN FOR ANY OF THESE PRODUCTS: the one answer to "when did this task last run"
#: for a task whose work is products, read by its row on Tasks and by the Faces screen alike.
#:
#: Bound: the products as one JSON list, then the family whose runs from before `made_for` was kept
#: stand in: those were not migrated, and by family is all they can say. A stopped run counts (it
#: is the last run, and it was canceled); no machine filter, because the library was worked through
#: wherever that happened. Newest by when it ended, the id breaking a tie in one second.
LAST_RUN_FOR_PRODUCTS = """
SELECT * FROM work_runs
 WHERE finished_at IS NOT NULL
   AND (EXISTS (SELECT 1 FROM json_each(work_runs.made_for) made
                 WHERE made.value IN (SELECT value FROM json_each(?)))
        OR (made_for IS NULL AND family = ?))
 ORDER BY finished_at DESC, id DESC
 LIMIT 1
"""

#: The same for several asks at once, each `[products, family]`, in one statement.
LAST_RUNS_FOR_PRODUCTS = """
SELECT asked.key AS asked, r.* FROM json_each(?) AS asked
  JOIN work_runs r ON r.id = (
       SELECT w.id FROM work_runs w
        WHERE w.finished_at IS NOT NULL
          AND (EXISTS (SELECT 1 FROM json_each(w.made_for) made
                        WHERE made.value IN (SELECT value FROM json_each(asked.value, '$[0]')))
               OR (w.made_for IS NULL AND w.family = json_extract(asked.value, '$[1]')))
        ORDER BY w.finished_at DESC, w.id DESC
        LIMIT 1)
"""


@dataclass(frozen=True, slots=True)
class RunRecord:
    """A run as read back from the table."""

    id: str
    family: str
    started_at: int
    finished_at: int | None
    stopped: bool
    jobs_done: int
    jobs_failed: int
    worker_ms: int
    files: dict[str, dict[str, int]]
    stages: dict[str, dict[str, int]]
    machine: str
    profile: str
    settings: dict[str, object]
    version: str
    products: dict[str, dict[str, int]] = field(default_factory=dict)
    """What a Build run made, per product: `n` files and `ms` of worker time. Empty for a run of
    any other family, and for a Build recorded before this was kept."""
    accelerator: str = ""
    """What the graphics card was doing: `on`, `off` or `latched_off`. Empty for a run recorded
    before this was kept, which is not the same claim as `off`."""
    requested_by: str | None = None
    """The user whose press this run carried out, or None: nobody pressed, or it was recorded
    before this was kept."""
    made_for: tuple[str, ...] | None = None
    """The products this run's work was for, or None for a run recorded before this was kept,
    which is not the claim that it was for none."""
    ended_with: dict[str, int] = field(default_factory=dict)
    """Why its jobs failed, in plain words, with how many of each."""
    time_left: dict[str, object] | None = None
    """How often its time left held the real finish (`time_left.score`)."""

    @property
    def seconds(self) -> int | None:
        if self.finished_at is None:
            return None
        return max(0, self.finished_at - self.started_at)

    @property
    def files_total(self) -> int:
        return sum(int(one.get("n", 0)) for one in self.files.values())

    @property
    def bytes_total(self) -> int:
        return sum(int(one.get("bytes", 0)) for one in self.files.values())

    @property
    def jobs_per_minute(self) -> float | None:
        took = self.seconds
        if took is None or took <= 0 or self.jobs_done <= 0:
            return None
        return self.jobs_done * 60 / took

    @property
    def files_per_minute(self) -> float | None:
        """The pace a person compares: files, whatever the jobs were shaped like."""
        took = self.seconds
        if took is None or took <= 0 or self.files_total <= 0:
            return None
        return self.files_total * 60 / took


def run_record(row: Row) -> RunRecord:
    return RunRecord(
        id=str(row["id"]),
        family=str(row["family"]),
        started_at=int(row["started_at"]),
        finished_at=None if row["finished_at"] is None else int(row["finished_at"]),
        stopped=bool(row["stopped"]),
        jobs_done=int(row["jobs_done"]),
        jobs_failed=int(row["jobs_failed"]),
        worker_ms=int(row["worker_ms"]),
        files=json.loads(str(row["files"])),
        stages=json.loads(str(row["stages"])),
        machine=str(row["machine"]),
        profile=str(row["profile"]),
        settings=json.loads(str(row["settings"])),
        version=str(row["version"]),
        products=json.loads(str(row["products"])),
        accelerator=str(row["accelerator"]),
        requested_by=None if row["requested_by"] is None else str(row["requested_by"]),
        made_for=None if row["made_for"] is None else tuple(json.loads(str(row["made_for"]))),
        ended_with=json.loads(str(row["ended_with"] or "{}")),
        time_left=json.loads(str(row["time_left"])) if row["time_left"] else None,
    )


class RunReads:
    """The ledger's reads of the runs it has written."""

    _db: Database

    async def last_run_for(self, products: Sequence[str], family: Family) -> RunRecord | None:
        """The last finished run for any of these products, or None. `family` answers for the runs
        recorded before products were kept, which were not migrated. See `LAST_RUN_FOR_PRODUCTS`."""
        row = await self._db.fetch_one(
            LAST_RUN_FOR_PRODUCTS, (json.dumps(list(products)), family.value)
        )
        return None if row is None else run_record(row)

    async def last_runs_for(
        self, asks: Sequence[tuple[Sequence[str], Family]]
    ) -> list[RunRecord | None]:
        """`last_run_for` for each of these asks, in their order, from one statement."""
        if not asks:
            return []
        bound = json.dumps([[list(products), family.value] for products, family in asks])
        found = {
            int(row["asked"]): run_record(row)
            for row in await self._db.fetch_all(LAST_RUNS_FOR_PRODUCTS, (bound,))
        }
        return [found.get(index) for index in range(len(asks))]

    async def last_run(self, family: Family) -> RunRecord | None:
        """The last run of a family that ended, stopped or not, or None. See
        `_LAST_ENDED_OF_FAMILY`."""
        row = await self._db.fetch_one(_LAST_ENDED_OF_FAMILY, (family.value,))
        return None if row is None else run_record(row)

    async def recent(self, *, limit: int = 30) -> list[RunRecord]:
        return [run_record(row) for row in await self._db.fetch_all(_RECENT, (limit,))]

    async def get(self, run_id: str) -> RunRecord | None:
        row = await self._db.fetch_one(_ONE, (run_id,))
        return None if row is None else run_record(row)

    async def recorded_among(self, run_ids: Sequence[str]) -> set[str]:
        """Which of these ids are runs this ledger holds: the runs a report can be made for.

        One statement for a page of History, whose "ran" lines name two kinds of run under one
        subject kind: a pass over the library, which is a row here, and a task's own run, whose
        subject is the TASK (`JobQueue.record_runs_of`) and has no row here to report on.
        """
        wanted = sorted(set(run_ids))
        if not wanted:
            return set()
        asked, values = in_clause(_RECORDED_AMONG, wanted)
        return {str(row["id"]) for row in await self._db.fetch_all(asked, values)}
