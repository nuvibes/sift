# SPDX-License-Identifier: AGPL-3.0-or-later
"""Takes back jobs whose workers stopped answering: a heartbeat quiet for minutes means nobody."""

from __future__ import annotations

import asyncio
import contextlib

from sift.kernel.jobs.queue import JobQueue
from sift.kernel.jobs.tuning import STALE_AFTER_SECONDS, SWEEP_INTERVAL_SECONDS
from sift.kernel.log import get_logger

log = get_logger(__name__)

_STOPPED_RESPONDING = "the worker running this job stopped responding"


async def sweep(queue: JobQueue, *, stale_after: int = STALE_AFTER_SECONDS) -> None:
    """Requeue jobs with a quiet heartbeat, then prune what is settled and old, reclaiming first."""
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
            # The watchdog recovers from failure; it does not get to die of one.
            log.exception("jobs.watchdog.failed")
