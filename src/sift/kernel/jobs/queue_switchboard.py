# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the switchboard and quiet hours hold back, asked at most every few seconds."""

from __future__ import annotations

import json

from sift.kernel.jobs.queue_core import QueueCore
from sift.kernel.jobs.queue_rows import JobState
from sift.kernel.jobs.switchboard import QuietHold

#: How long "can this family run here" stays true: the pool's own `RECONFIGURE_SECONDS`.
READINESS_FRESH_FOR_SECONDS = 3

# Unfinished work by type, less what quiet hours keep waiting: the family tallies, so a few rows
# however long the queue.
_DEMAND_BY_TYPE = """
SELECT type, SUM(n) AS pending FROM job_family_tallies
 WHERE state IN ('queued', 'running', 'blocked')
   AND NOT (state = 'queued' AND NOT ?
            AND (timing = 'quiet' OR (timing = '' AND type IN (SELECT value FROM json_each(?)))))
 GROUP BY type
 HAVING SUM(n) > 0
"""

# Quiet-hours work by type and state: what Tasks counts as held, and what keeps the device awake.
_HELD_BY_TYPE = """
SELECT type, state, COUNT(*) AS held FROM jobs
 WHERE unlikely(state IN ('queued', 'running'))
   AND (COALESCE(timing, '') = 'quiet'
        OR (timing IS NULL AND type IN (SELECT value FROM json_each(?))))
   AND (? IS NULL OR run_after IS NULL OR run_after < ?)
 GROUP BY type, state
"""


class SwitchboardReads(QueueCore):
    """What the board and quiet hours hold back, kept for a few seconds between asks."""

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
            _DEMAND_BY_TYPE, (quiet.open, json.dumps(sorted(quiet.types)))
        )
        return {row["type"]: row["pending"] for row in rows}

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
