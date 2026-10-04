# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the queue's test files share: the worker names, a handler that does nothing, and the waits."""

from __future__ import annotations

import asyncio
import time

from sift.kernel.jobs import (
    Job,
    JobContext,
    JobQueue,
    JobState,
    register_handler,
)

WORKER = "worker-one"
OTHER_WORKER = "worker-two"


def noop_handler(job_type: str = "probe") -> str:
    """Register a handler that does nothing, so `enqueue` will accept the type."""

    async def handler(context: JobContext) -> None:
        return None

    register_handler(job_type, handler, name="Test job")
    return job_type


class Recorder:
    """A handler that records every job it is given."""

    def __init__(self) -> None:
        self.seen: list[str] = []

    async def handler(self, context: JobContext) -> None:
        self.seen.append(context.job.id)


async def drain(queue: JobQueue, *, give_up_after: float = 10.0) -> None:
    """Wait until nothing is queued or running."""
    deadline = time.monotonic() + give_up_after
    while time.monotonic() < deadline:
        counts = await queue.counts()
        if not counts.get("queued") and not counts.get("running"):
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"the queue never drained: {await queue.counts()}")


async def wait_for_state(queue: JobQueue, job_id: str, state: JobState) -> Job:
    """Wait for a job to reach a state, and hand it back."""
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        job = await queue.get(job_id)
        assert job is not None
        if job.state is state:
            return job
        await asyncio.sleep(0.01)
    raise AssertionError(f"{job_id} never reached {state}")


def _a_job(job_type: str = "download") -> Job:
    """A job row in memory, for the checks that need a context but no worker to have run it."""
    return Job(
        id="01HQABCDEFGHJKMNPQRSTVWXYZ",
        parent_id=None,
        type=job_type,
        state=JobState.RUNNING,
        priority=0,
        payload={},
        progress=0.0,
        attempts=1,
        max_attempts=3,
        claimed_by=WORKER,
        heartbeat_at=None,
        error=None,
        note=None,
        run_after=None,
        created_at=0,
        updated_at=0,
    )


async def _state(queue: JobQueue, job_id: str, state: str) -> None:
    async with queue._db.write() as connection:
        await connection.execute("UPDATE jobs SET state = ? WHERE id = ?", (state, job_id))
