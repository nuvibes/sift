# SPDX-License-Identifier: AGPL-3.0-or-later
"""A real queue and a real claim for the Build, with stand-in handlers: what is tested is what the
Build asks for."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from sift.kernel.content import ContentStore, LibraryStore
from sift.kernel.ids import new_id
from sift.kernel.jobs import JobContext, JobQueue, register_handler
from sift.kernel.jobs.worker_pool import SystemCapabilities
from sift.slices.importing.jobs import RUNS


@pytest.fixture
def capabilities(content_store: ContentStore, library_store: LibraryStore) -> SystemCapabilities:
    """What a handler may do as the system."""
    return SystemCapabilities(content=content_store, library=library_store)


@pytest.fixture
def handlers(clean_handlers: None) -> None:
    """Claim the job types this pass queues with no-op handlers, so the queue accepts them."""

    async def nothing(_context: JobContext) -> None:
        return None

    for run_type, file_type in RUNS.values():
        register_handler(run_type, nothing, name="Going over the library")
        register_handler(file_type, nothing, name="Making what one file lacks")
    register_handler("thumbnail", nothing, name="Generating thumbnail")
    register_handler("preview", nothing, name="Generating preview")


@pytest.fixture
def context_for(
    job_queue: JobQueue, capabilities: SystemCapabilities, handlers: None
) -> Callable[[str, dict[str, object]], Awaitable[JobContext]]:
    """A handler's context for a job really queued and claimed: progress, cancellation and fan-out
    are fenced on the claim."""

    async def build(job_type: str, payload: dict[str, object]) -> JobContext:
        job_id = await job_queue.enqueue(job_type, payload)
        worker_id = new_id()
        while True:
            job = await job_queue.claim(worker_id)
            assert job is not None, "the queue lost a job this test enqueued"
            if job.id == job_id:
                return JobContext(
                    job=job, worker_id=worker_id, queue=job_queue, capabilities=capabilities
                )

    return build
