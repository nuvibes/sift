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
    """Requeue everything that was running when Sift last stopped. Returns (requeued, failed).

    Called once, before the workers start: otherwise a worker could claim a job in the same
    moment this is putting it back, and the job would run twice.

    A job that has already used up its attempts is failed rather than requeued. It is not being
    punished for the restart: it took an attempt each of the previous times it was claimed, and
    a job that brings the process down every time it runs is one Sift must eventually stop
    running, or it never starts up properly again.
    """
    requeued, failed = await queue.reclaim(stale_after=None, error=_INTERRUPTED)

    if requeued or failed:
        log.info("jobs.recovered", requeued=len(requeued), failed=len(failed))

    return requeued, failed
