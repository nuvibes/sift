# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the switchboard and quiet hours hold back, asked at most every few seconds."""

from __future__ import annotations

import json
from collections.abc import Collection
from dataclasses import dataclass

from sift.kernel.jobs.queue_core import QueueCore
from sift.kernel.jobs.queue_rows import JobState
from sift.kernel.jobs.switchboard import QuietHold

#: How long "can this family run here" stays true: the pool's own `RECONFIGURE_SECONDS`.
READINESS_FRESH_FOR_SECONDS = 3

# Unfinished work by type, less what quiet hours or a family's hold keep waiting: the family
# tallies, so a few rows however long the queue.
_DEMAND_BY_TYPE = """
SELECT type, SUM(n) AS pending FROM job_family_tallies
 WHERE state IN ('queued', 'running', 'blocked')
   AND NOT (state = 'queued' AND NOT ?
            AND (timing = 'quiet' OR (timing = '' AND type IN (SELECT value FROM json_each(?)))))
   AND NOT (state = 'queued' AND NOT ?
            AND root_id IN (SELECT value FROM json_each(?)) AND timing <> 'now'
            AND type NOT IN (SELECT value FROM json_each(?)))
 GROUP BY type
 HAVING SUM(n) > 0
"""

# A held family's waiting rows by type from its tallies, less those put off to later, which the
# tallies cannot tell from the rest (`ix_jobs_queued_later`).
_HELD_FOR_A_FAMILY = """
SELECT root_id, type, SUM(n) AS held FROM (
  SELECT root_id, type, n FROM job_family_tallies
   WHERE root_id IN (SELECT value FROM json_each(:families)) AND state = 'queued'
     AND timing <> 'now' AND type NOT IN (SELECT value FROM json_each(:spared))
  UNION ALL
  SELECT COALESCE(root_id, id), type, -COUNT(*) FROM jobs
   WHERE state = 'queued' AND run_after IS NOT NULL AND run_after > :now
     AND COALESCE(root_id, id) IN (SELECT value FROM json_each(:families))
     AND COALESCE(timing, '') <> 'now' AND type NOT IN (SELECT value FROM json_each(:spared))
   GROUP BY 1, 2
)
 GROUP BY root_id, type
 HAVING SUM(n) > 0
"""

_FAMILY_OF = "SELECT COALESCE(root_id, id) AS family FROM jobs WHERE id = ?"

# Quiet-hours work by type and state: what Tasks counts as held, and what keeps the device awake.
_HELD_BY_TYPE = """
SELECT type, state, COUNT(*) AS held FROM jobs
 WHERE unlikely(state IN ('queued', 'running'))
   AND (COALESCE(timing, '') = 'quiet'
        OR (timing IS NULL AND type IN (SELECT value FROM json_each(?))))
   AND (? IS NULL OR run_after IS NULL OR run_after < ?)
 GROUP BY type, state
"""


@dataclass(frozen=True, slots=True)
class FamilyHold:
    """A running job keeping its family's other waiting work back, all but its `spared` kinds."""

    family: str
    spared: frozenset[str]


class SwitchboardReads(QueueCore):
    """What the board and quiet hours hold back, kept for a few seconds between asks."""

    _holds_by_job: dict[str, FamilyHold] | None = None

    async def _held(self) -> QuietHold:
        """What quiet hours hold back right now, kept for `READINESS_FRESH_FOR_SECONDS`."""
        now = self._now()
        if self._quiet_seen is not None and now - self._quiet_seen[0] < READINESS_FRESH_FOR_SECONDS:
            return self._quiet_seen[1]
        answer = await self._switchboard.quiet_hold()
        self._quiet_seen = (now, answer)
        return answer

    def forget_quiet_hours(self) -> None:
        """Ask quiet hours again at the next claim: a saved When or range reaches the queue immediately."""
        self._quiet_seen = None

    async def demand_by_type(self) -> dict[str, int]:
        """`_DEMAND_BY_TYPE`."""
        quiet = await self._held()
        rows = await self._db.fetch_all(
            _DEMAND_BY_TYPE, (quiet.open, json.dumps(sorted(quiet.types)), *self._family_holds())
        )
        return {row["type"]: row["pending"] for row in rows}

    @property
    def _holds(self) -> dict[str, FamilyHold]:
        if self._holds_by_job is None:
            self._holds_by_job = {}
        return self._holds_by_job

    async def hold_family(self, job_id: str, *, spared: Collection[str]) -> None:
        """Keep this job's family waiting until `lift_hold`, all but a row pressed `now`."""
        row = await self._db.fetch_one(_FAMILY_OF, (job_id,))
        family = job_id if row is None else str(row["family"])
        self._holds[job_id] = FamilyHold(family=family, spared=frozenset(spared))

    def lift_hold(self, job_id: str) -> bool:
        """End this job's hold. False when it held nothing."""
        return self._holds.pop(job_id, None) is not None

    def _family_holds(self) -> tuple[bool, str, str]:
        holds = list(self._holds.values())
        spared = sorted({kind for hold in holds for kind in hold.spared})
        return (not holds, json.dumps(sorted({hold.family for hold in holds})), json.dumps(spared))

    async def _held_for_families(self) -> list[tuple[str, str, int]]:
        if not self._holds:
            return []
        _, families, spared = self._family_holds()
        rows = await self._db.fetch_all(
            _HELD_FOR_A_FAMILY, {"families": families, "now": int(self._now()), "spared": spared}
        )
        return [(str(row["root_id"]), str(row["type"]), int(row["held"])) for row in rows]

    async def held_for_family_by_type(self) -> dict[str, int]:
        """The waiting rows a family's hold keeps back now, by type: held, not due."""
        held: dict[str, int] = {}
        for _family, job_type, count in await self._held_for_families():
            held[job_type] = held.get(job_type, 0) + count
        return held

    async def holder_of(self, job_types: Collection[str]) -> str | None:
        """The job (a scan) holding back waiting work of these types, or None."""
        wanted = set(job_types)
        families = {family for family, kind, _ in await self._held_for_families() if kind in wanted}
        holders = sorted(job_id for job_id, hold in self._holds.items() if hold.family in families)
        return holders[0] if holders else None

    async def held_by_type(
        self,
        types: frozenset[str] | None = None,
        *,
        due_before: int | None = None,
        waiting_only: bool = False,
    ) -> dict[str, int]:
        """Quiet-hours work not yet finished, by type, open or not; `due_before` leaves out rows due
        from then on, `waiting_only` what has started."""
        if types is None:
            types = (await self._held()).types
        listed = json.dumps(sorted(types))
        # Kept like the Tasks page's other long reads: exact on a small queue, answered from the
        # last reading and read again behind it on a large one.
        rows = await self._kept_read(
            ("held_by_type", listed, due_before),
            lambda: self._db.fetch_all(_HELD_BY_TYPE, (listed, due_before, due_before)),
        )
        held: dict[str, int] = {}
        for row in rows:
            if waiting_only and row["state"] != JobState.QUEUED.value:
                continue
            held[row["type"]] = held.get(row["type"], 0) + int(row["held"])
        return held

    async def held_and_waiting_by_type(
        self, types: frozenset[str]
    ) -> tuple[dict[str, int], dict[str, int]]:
        """`held_by_type` for these types, and its `waiting_only` reading, from one statement."""
        listed = json.dumps(sorted(types))
        rows = await self._kept_read(
            ("held_by_type", listed, None),
            lambda: self._db.fetch_all(_HELD_BY_TYPE, (listed, None, None)),
        )
        held: dict[str, int] = {}
        waiting: dict[str, int] = {}
        for row in rows:
            held[row["type"]] = held.get(row["type"], 0) + int(row["held"])
            if row["state"] == JobState.QUEUED.value:
                waiting[row["type"]] = waiting.get(row["type"], 0) + int(row["held"])
        return held, waiting

    async def _not_ready_types(self) -> set[str]:
        """The job types whose family cannot run here now, kept `READINESS_FRESH_FOR_SECONDS`; a
        family with no answer is ready, since holding its work back would invent a fault."""
        from sift.kernel.jobs.worker_pool import gated_by_readiness, registered_families

        now = self._now()
        if self._not_ready is not None and now - self._not_ready[0] < READINESS_FRESH_FOR_SECONDS:
            return set(self._not_ready[1])
        answers = await self._switchboard.readiness()
        stopped = {family for family, state in answers.items() if not state.ready}
        held = frozenset(
            job_type
            for job_type, family in registered_families().items()
            if family in stopped and gated_by_readiness(job_type)
        )
        self._not_ready = (now, held)
        return set(held)
