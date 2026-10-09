# SPDX-License-Identifier: AGPL-3.0-or-later
"""What every part of the queue shares: the database, the clock, and the one door its writes use."""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable, Iterable, Sequence
from contextlib import AbstractAsyncContextManager
from typing import NamedTuple

from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, telling
from sift.kernel.db import Connection, Database
from sift.kernel.jobs.queue_rows import WorkSummary
from sift.kernel.jobs.quiet_hours import AT_NOW, AT_QUIET
from sift.kernel.jobs.switchboard import QuietHold, Switchboard
from sift.kernel.log import get_logger

#: How long a whole-table work summary stays true: `WorkAhead`'s five seconds, for one dashboard.
SUMMARY_FRESH_FOR_SECONDS = 5

log = get_logger(__name__)


class Arrival(NamedTuple):
    """A row just queued, as much of it as says whether a worker could take it now."""

    job_type: str
    family: str
    timing: str | None
    run_after: int | None


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
        self._work_listeners: list[Callable[[], None]] = []
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

    def _family_holds(self) -> tuple[bool, str, str]:
        """Nothing held here: `SwitchboardReads` keeps the families' holds and answers for them."""
        return (True, "[]", "[]")

    def _claimable_now(self, arrival: Arrival) -> bool:
        """Whether the claim could take this row now, from what the queue already knows without a
        read: its moment, a family's hold, and quiet hours and readiness as last asked. A wrong no
        costs one idle poll, so what is not known counts as claimable."""
        if arrival.run_after is not None and arrival.run_after > self._now():
            return False
        free, families, spared = self._family_holds()
        if (
            not free
            and arrival.timing != AT_NOW
            and arrival.family in json.loads(families)
            and arrival.job_type not in json.loads(spared)
        ):
            return False
        if self._quiet_seen is not None and not self._quiet_seen[1].open:
            quiet = self._quiet_seen[1]
            if arrival.timing == AT_QUIET or (
                arrival.timing is None and arrival.job_type in quiet.types
            ):
                return False
        return self._not_ready is None or arrival.job_type not in self._not_ready[1]

    def _work_arrived(self, arrivals: Iterable[Arrival]) -> None:
        """Tell every listener work was queued that a worker could take now; nothing for a row
        that must wait, so no idle worker claims for nothing. A listener that raises is logged."""
        if not any(self._claimable_now(arrival) for arrival in arrivals):
            return
        for listener in list(self._work_listeners):
            try:
                listener()
            except Exception:
                log.exception("job.work_listener_failed")
