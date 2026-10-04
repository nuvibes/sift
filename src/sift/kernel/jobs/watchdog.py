# SPDX-License-Identifier: AGPL-3.0-or-later
"""The sweep that takes jobs back from workers that stopped answering.

Boot recovery handles the crash: the process died, so every `running` row is an orphan and is
requeued. This handles the other half, which is worse because nothing announces it: the process
is alive, the worker is not. A handler waiting forever on a socket, a subprocess that will not
exit, a deadlock. The row says `running`, nothing is running it, and without this it stays that
way until the next restart.

The signal is the heartbeat: a live job stamps one every few seconds, so a stamp that has gone
quiet for minutes means nobody is there. The threshold is generous on purpose. Reclaiming a job
that is merely slow means running it twice.
"""

from __future__ import annotations

import asyncio
import contextlib

from sift.kernel.jobs.queue import JobQueue
from sift.kernel.jobs.tuning import STALE_AFTER_SECONDS, SWEEP_INTERVAL_SECONDS
from sift.kernel.log import get_logger

log = get_logger(__name__)

_STOPPED_RESPONDING = "the worker running this job stopped responding"


async def sweep(queue: JobQueue, *, stale_after: int = STALE_AFTER_SECONDS) -> None:
    """Requeue every running job whose heartbeat has gone quiet, and forget what is settled and old.

    Two housekeeping passes on one timer. They are unrelated jobs of work and share this only
    because they want the same cadence (often enough to matter, rare enough to cost nothing) and
    a second timer for a delete that usually removes no rows would be a second thing to reason about
    for no gain.

    The prune is second deliberately. Reclaiming is what recovers a stuck queue and it should not
    wait behind housekeeping if the delete is ever slow.
    """
    requeued, failed = await queue.reclaim(stale_after=stale_after, error=_STOPPED_RESPONDING)
    if requeued or failed:
        log.warning(
            "jobs.watchdog.reclaimed",
            requeued=len(requeued),
            failed=len(failed),
            job_ids=[*requeued, *failed],
        )

    removed = await queue.prune_settled()
    if removed:
        log.info("jobs.watchdog.pruned", removed=removed)


async def run_watchdog(
    queue: JobQueue,
    stop: asyncio.Event,
    *,
    stale_after: int = STALE_AFTER_SECONDS,
    interval: float = SWEEP_INTERVAL_SECONDS,
) -> None:
    """Sweep on a timer until told to stop."""
    while not stop.is_set():
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)
        if stop.is_set():
            return

        try:
            await sweep(queue, stale_after=stale_after)
        except Exception:
            # The watchdog is the thing that recovers from failure. It does not get to die of one.
            log.exception("jobs.watchdog.failed")
