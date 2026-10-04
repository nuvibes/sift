# SPDX-License-Identifier: AGPL-3.0-or-later
"""A job parked until somebody gives the password says so, is counted, and is announced.

A restart keeps a session signed in and seals every saved key, so a run that needs one parks, and
a screen that said only that it was blocked would leave it there unexplained. These hold the three
things that make it visible: the sentence it is parked with, the count the shell's unlock bar
follows, and the announcement that tells a window already open.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

import pytest

from sift.kernel import changes
from sift.kernel.changes import About, ChangeBus
from sift.kernel.db import Database
from sift.kernel.jobs import (
    JobBlocked,
    JobContext,
    JobQueue,
    JobState,
    WaitingForPassword,
    WorkerPool,
    register_handler,
    waits_for_password,
)

pytestmark = pytest.mark.usefixtures("clean_handlers")

#: Any id at all. What a queue write reaches is every admin, settled at the moment of sending.
ANOTHER_WINDOW = "01HX00000000000000000WIN3"


@pytest.fixture
async def told(temp_db: Database) -> AsyncIterator[ChangeBus]:
    """A bus the queue's writes are heard on, put back afterwards so no later test collects them."""
    bus = ChangeBus()
    changes.listens(bus)
    yield bus
    changes.listens(None)


async def _settle(queue: JobQueue) -> None:
    """Wait until nothing is queued or running."""
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        counts = await queue.counts()
        if not counts.get("queued") and not counts.get("running"):
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"the queue never settled: {await queue.counts()}")


async def _run(queue: JobQueue) -> None:
    pool = WorkerPool(queue, concurrency=1, poll_interval=0.01)
    await pool.start()
    try:
        await _settle(queue)
    finally:
        await pool.stop()


def test_the_sentence_names_what_is_sealed() -> None:
    assert str(WaitingForPassword("the stash-box keys")) == (
        "Waiting for your password to unlock the stash-box keys."
    )
    assert isinstance(WaitingForPassword("the AcoustID key"), JobBlocked)


def test_only_a_wait_for_the_password_reads_as_one() -> None:
    assert waits_for_password("Waiting for your password to unlock the AcoustID key.")
    assert not waits_for_password("The models are not on disk.")
    assert not waits_for_password("waiting for your password to unlock it")
    assert not waits_for_password(None)


@pytest.mark.integration
async def test_a_job_parked_for_the_password_is_counted_and_announced(
    job_queue: JobQueue, told: ChangeBus
) -> None:
    async def sealed(context: JobContext) -> None:
        raise WaitingForPassword("the stash-box keys")

    async def no_models(context: JobContext) -> None:
        raise JobBlocked("The models are not on disk.")

    register_handler("starters", sealed, name="Test job")
    register_handler("scan", no_models, name="Test job")
    parked = await job_queue.enqueue("starters", max_attempts=3)
    await job_queue.enqueue("scan", max_attempts=3)
    window = told.subscribe(ANOTHER_WINDOW)

    await _run(job_queue)

    job = await job_queue.get(parked)
    assert job is not None
    assert job.state is JobState.BLOCKED
    assert job.attempts == 0, "a wait for the password spent an attempt"
    assert job.error == "Waiting for your password to unlock the stash-box keys."
    assert waits_for_password(job.error)
    # The models' wait is parked too, and is not a wait for the password: the bar asks only for
    # what the password releases.
    assert await job_queue.waiting_for_password() == {"starters": 1}
    assert About.JOBS in window.take(as_admin=True).about, (
        "a job parked for the password told no window, so the unlock bar waits for a reload"
    )


@pytest.mark.integration
async def test_a_release_leaves_nothing_waiting_for_the_password(job_queue: JobQueue) -> None:
    async def sealed(context: JobContext) -> None:
        raise WaitingForPassword("the AcoustID key")

    register_handler("lookup", sealed, name="Test job")
    await job_queue.enqueue("lookup", max_attempts=3)
    await _run(job_queue)
    assert await job_queue.waiting_for_password() == {"lookup": 1}

    await job_queue.unblock()

    assert await job_queue.waiting_for_password() == {}
