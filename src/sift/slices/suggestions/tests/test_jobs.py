# SPDX-License-Identifier: AGPL-3.0-or-later
"""The pass as the queue runs it.

The handler is thin on purpose (it calls the service and reports itself finished), so what is
worth asserting is that it is claimed under the name the queue will look for, and that running it
really does file what a pass files.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from sift.kernel.access import Viewer
from sift.kernel.jobs import JobContext, JobQueue, registered_handlers
from sift.slices.suggestions.jobs import SUGGESTION_SCAN, register_handlers, suggestion_scan
from sift.slices.suggestions.service import SuggestionService
from sift.slices.suggestions.tests.conftest import FakeFaces, Library

pytestmark = pytest.mark.integration


async def test_the_pass_is_claimed_under_the_name_the_queue_uses(
    service: SuggestionService, clean_handlers: None
) -> None:
    """A job type nothing can execute is refused by the queue, so the name has to match."""
    register_handlers(service=service)
    assert SUGGESTION_SCAN in registered_handlers()


async def test_running_the_job_files_what_a_pass_files(
    service: SuggestionService,
    library: Library,
    add_file: Callable[..., Any],
    faces: FakeFaces,
    name_folders: Callable[[], Any],
    job_queue: JobQueue,
    clean_handlers: None,
    admin: Viewer,
) -> None:
    for index in range(5):
        await add_file(library, f"Nadia Vance/clip{index}.mp4")
    await name_folders()
    faces.looked = {"Nadia Vance": (5, 5)}
    faces.piles_here = {"Nadia Vance": {"pile-1": 5}}

    # Registered first: the queue refuses a job type nothing can execute, which is the guard doing
    # its job rather than something to work around.
    register_handlers(service=service)
    await job_queue.enqueue(SUGGESTION_SCAN, {})

    claimed = await job_queue.claim("worker-one")
    assert claimed is not None
    await suggestion_scan(
        JobContext(job=claimed, worker_id="worker-one", queue=job_queue), service=service
    )

    assert len((await service.pending(admin)).items) == 1


async def test_a_pass_over_an_empty_library_does_nothing_and_does_not_raise(
    service: SuggestionService, job_queue: JobQueue, clean_handlers: None
) -> None:
    """The answer a fresh install gets on its very first pass."""
    register_handlers(service=service)
    await job_queue.enqueue(SUGGESTION_SCAN, {})
    claimed = await job_queue.claim("worker-one")
    assert claimed is not None
    await suggestion_scan(
        JobContext(job=claimed, worker_id="worker-one", queue=job_queue), service=service
    )
