# SPDX-License-Identifier: AGPL-3.0-or-later
"""What every part of the queue shares: the database, the clock, and the one door its writes use."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager

from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, telling
from sift.kernel.db import Connection, Database, in_clause
from sift.kernel.jobs.queue_rows import WorkSummary
from sift.kernel.jobs.switchboard import QuietHold, Switchboard

#: How long a whole-table work summary stays true: `WorkAhead`'s five seconds, for one dashboard.
SUMMARY_FRESH_FOR_SECONDS = 5

# A parent's progress is recomputed from its children, never accumulated (that drifts on a retry).
# The COALESCE matters: no children is 0/0, NULL, which would fail the transaction it rides in.
_ROLL_UP = """
UPDATE jobs
   SET progress = COALESCE((
       SELECT CAST(COUNT(*) FILTER (WHERE child.state IN ('done', 'failed', 'canceled')) AS REAL)
              / COUNT(*)
         FROM jobs child
        WHERE child.parent_id = jobs.id
   ), progress),
       updated_at = ?
 WHERE id IN (?*)
"""


class QueueCore:
    """The state every part of the queue shares, one per database."""

    def __init__(
        self,
        database: Database,
        *,
        clock: Callable[[], float] = time.time,
        summary_fresh_for: int = SUMMARY_FRESH_FOR_SECONDS,
        switchboard: Switchboard | None = None,
    ) -> None:
        self._db = database
        self._clock = clock
        self._summary_fresh_for = summary_fresh_for
        #: Whether each kind of work is switched off or can run here; a bare board allows all.
        self._switchboard = switchboard or Switchboard()
        self._work_summary: tuple[int, WorkSummary] | None = None
        #: The types whose family cannot run here, and when that was asked (`_not_ready_types`).
        self._not_ready: tuple[int, frozenset[str]] | None = None
        #: Who is told that running jobs were asked to stop (`listen_for_stops`).
        self._stop_listeners: list[Callable[[Sequence[str]], None]] = []
        self._quiet_seen: tuple[int, QuietHold] | None = None
        #: The types whose every run writes a history line, and their task (`record_runs_of`).
        self._runs_recorded: dict[str, tuple[str, str]] = {}
        #: Who is told that a job of a type has settled (`listen_for_settled`).
        self._settled_listeners: dict[str, list[Callable[[str], Awaitable[None]]]] = {}

    def _now(self) -> int:
        return int(self._clock())

    @property
    def switchboard(self) -> Switchboard:
        """The board this queue consults, read by the worker pool and by the passes screen."""
        return self._switchboard

    def _writing(self) -> AbstractAsyncContextManager[Connection]:
        """A write to the queue, every admin's dashboard told once it lands. Every write but the
        heartbeat (nothing on screen, every few seconds per job) goes through this."""
        return telling(self._db, EVERY_ADMIN, About.JOBS)

    async def _roll_up(self, connection: Connection, parents: Sequence[str | None]) -> None:
        """Recompute each parent's progress from its children, in the caller's transaction: a child
        settling and its parent moving are one fact."""
        ids = sorted({parent for parent in parents if parent is not None})
        if not ids:
            return
        sql, params = in_clause(_ROLL_UP, ids)
        await connection.execute(sql, (self._now(), *params))
