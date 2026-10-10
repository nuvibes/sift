# SPDX-License-Identifier: AGPL-3.0-or-later
"""What happens to unfinished work when Sift starts.

Pull the plug during a scan and the rows are still there, still saying `running`, still holding a
worker id from a process that no longer exists. That is the whole of the damage: there is no
half-written queue file, no lost message, no lease to expire, and undoing it is one statement.

It works because of an assumption worth stating out loud: **one Sift, one database.** At boot,
this process is the only one there is, so a `running` row cannot belong to anybody. Run two Sifts
against the same file and this would requeue the other one's live jobs out from under it.
"""

from __future__ import annotations

from sift.kernel.jobs.queue import JobQueue
from sift.kernel.log import get_logger

log = get_logger(__name__)

_INTERRUPTED = "Sift was restarted while this job was running"


async def recover(queue: JobQueue) -> tuple[list[str], list[str]]:
    """Requeue what was running when Sift stopped, and resume what a benchmark cut short paused.
    Returns (requeued, failed). Called once, before the workers start, so nothing runs twice.

    A job out of attempts is failed: one that brings the process down every time must stop.
    """
    # First, so a walk the restart cut short keeps the attempt its work did not fail.
    refunded = await queue.refund_progressed()
    requeued, failed = await queue.reclaim(stale_after=None, error=_INTERRUPTED)
    # After the reclaim, which lands a benchmark's pause still asked as a paused row.
    resumed = await queue.resume_after_benchmark()

    if requeued or failed or resumed:
        log.info(
            "jobs.recovered",
            requeued=len(requeued),
            failed=len(failed),
            resumed_after_benchmark=len(resumed),
            not_charged=len(refunded),
        )

    return requeued, failed
