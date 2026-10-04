# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the switchboard and quiet hours hold back, asked at most every few seconds."""

from __future__ import annotations

import json

from sift.kernel.jobs.queue_core import QueueCore
from sift.kernel.jobs.queue_rows import JobState
from sift.kernel.jobs.switchboard import QuietHold

#: How long "can this family run here" stays true: the pool's own `RECONFIGURE_SECONDS`.
READINESS_FRESH_FOR_SECONDS = 3

# Everything not finished, by type, less what quiet hours hold back now: what the machine's
# budget divides on. The first `?` is whether the range is open.
_DEMAND_BY_TYPE = """
SELECT type, COUNT(*) AS pending FROM jobs
 WHERE unlikely(state IN ('queued', 'running', 'blocked'))
   AND NOT (state = 'queued' AND NOT ?
            AND (COALESCE(timing, '') = 'quiet'
                 OR (timing IS NULL AND type IN (SELECT value FROM json_each(?)))))
 GROUP BY type
"""

# Quiet-hours work by type and state: what Tasks counts as held (the waiting part), and what
# keeps the device awake through the range (both).
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
        """What quiet hours hold back right now, kept for `READINESS_FRESH_FOR_SECONDS`: every idle
        worker asks before every claim, and the range moves on a minute."""
        now = self._now()
        if self._quiet_seen is not None and now - self._quiet_seen[0] < READINESS_FRESH_FOR_SECONDS:
            return self._quiet_seen[1]
        answer = await self._switchboard.quiet_hold()
        self._quiet_seen = (now, answer)
        return answer

    def forget_quiet_hours(self) -> None:
        """Ask quiet hours again at the next claim: a saved When or range reaches the queue at once."""
        self._quiet_seen = None

    async def demand_by_type(self) -> dict[str, int]:
        """Everything not finished, by type, less what quiet hours hold back (`_DEMAND_BY_TYPE`)."""
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
        """Quiet-hours work not yet finished, by type, whether or not the range is open. `types` are
        those set to quiet hours (the board's by default); `due_before` leaves out rows due from then
        on, so a timed task's next run does not hold the device awake; `waiting_only` counts what
        has not started."""
        if types is None:
            types = (await self._held()).types
        rows = await self._db.fetch_all(
            _HELD_BY_TYPE, (json.dumps(sorted(types)), due_before, due_before)
        )
        held: dict[str, int] = {}
        for row in rows:
            if waiting_only and row["state"] != JobState.QUEUED.value:
                continue
            held[row["type"]] = held.get(row["type"], 0) + int(row["held"])
        return held

    async def _not_ready_types(self) -> set[str]:
        """The job types whose family cannot run here now, held for `READINESS_FRESH_FOR_SECONDS`:
        such a row waits as a type at its cap does. A family with no answer, or one that could not be
        read, is ready, since holding work back would invent a fault."""
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
