# SPDX-License-Identifier: AGPL-3.0-or-later
"""Who hears about a job: the listeners told of stops and settles, and each task run's history line."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence

from sift.kernel.db import Connection, Row
from sift.kernel.jobs.queue_core import QueueCore
from sift.kernel.ledger import Actor, record_event
from sift.kernel.log import get_logger
from sift.kernel.vocabulary import Subject

log = get_logger("sift.kernel.jobs.queue")
# A declared task's run, taken back from the row that just settled, for its History line.
_USER_STILL_THERE = "SELECT 1 FROM users WHERE id = ?"


class HandOffs(QueueCore):
    """The queue's hand-offs to the rest of the process: stop and settle listeners, run records."""

    def listen_for_stops(self, listener: Callable[[Sequence[str]], None]) -> Callable[[], None]:
        """Be told the ids of jobs just asked to stop, so a handler hears it at once rather than at
        its next heartbeat; the row stays the truth. Returns the way to stop hearing. Called after
        the commit; a listener must not block, and one that raises is logged."""
        self._stop_listeners.append(listener)

        def unlisten() -> None:
            if listener in self._stop_listeners:
                self._stop_listeners.remove(listener)

        return unlisten

    def _stop_asked(self, job_ids: Sequence[str]) -> None:
        """Tell every listener these jobs were just asked to stop; never called with none."""
        for listener in list(self._stop_listeners):
            try:
                listener(job_ids)
            except Exception:
                log.exception("job.stop_listener_failed", job_count=len(job_ids))

    def record_runs_of(self, job_type: str, *, task_id: str, title: str) -> None:
        """Write a line in the history for every run of this type, as a run of this task.

        Written in the transaction that settles the row, the only place the line is exact. Its
        subject is the task, so the task's history is one read. Only a run that STARTED writes a
        line; a pass made of pages has its run written by the ledger instead.
        """
        self._runs_recorded[job_type] = (task_id, title)

    def listen_for_settled(self, job_type: str, listener: Callable[[str], Awaitable[None]]) -> None:
        """Be told the id of each job of this type that settles: done, failed for good, cancelled.

        After the commit, from the call that settled it: how a timed task's scheduler places its
        next run without any handler queueing one. A listener that raises is logged.
        """
        self._settled_listeners.setdefault(job_type, []).append(listener)

    async def _tell_settled(self, rows: Sequence[Row]) -> None:
        for row in rows:
            for listener in self._settled_listeners.get(str(row["type"]), ()):
                try:
                    await listener(str(row["id"]))
                except Exception:
                    log.exception("job.settled_listener_failed", job_type=row["type"])

    async def _record_runs(self, connection: Connection, rows: Sequence[Row], outcome: str) -> None:
        """The history line for each settled row that is a declared task's run. See `record_runs_of`."""
        if not self._runs_recorded:
            return
        now = self._now()
        for row in rows:
            named = self._runs_recorded.get(str(row["type"]))
            if named is None or row["started_at"] is None:
                continue
            task_id, title = named
            actor = Actor.sift()
            requested_by = row["requested_by"]
            if requested_by is not None and await connection.execute_fetchall(
                _USER_STILL_THERE, (requested_by,)
            ):
                actor = Actor.user(str(requested_by))
            said = row["error"] if outcome == "failed" else row["note"]
            await record_event(
                connection,
                actor=actor,
                verb="ran",
                subject=Subject("run", task_id, title),
                payload=json.dumps(
                    {
                        "task": task_id,
                        "job": str(row["id"]),
                        "outcome": outcome,
                        "seconds": max(0, now - int(row["started_at"])),
                        "said": said,
                    }
                ),
            )
