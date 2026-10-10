# SPDX-License-Identifier: AGPL-3.0-or-later
"""A job's statements wait a moment for the requests being answered, and never anything else."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from sift.kernel import foreground
from sift.kernel.db import Database
from sift.kernel.log import JobCost, costing


@pytest.fixture
def one_request() -> Iterator[None]:
    foreground.request_began()
    try:
        yield
    finally:
        foreground.request_ended()


@pytest.mark.usefixtures("one_request")
async def test_a_jobs_statement_waits_for_the_request_until_it_is_answered() -> None:
    with costing(JobCost()):
        waiting = asyncio.create_task(foreground.screens_first(False))
        await asyncio.sleep(0.02)
        assert not waiting.done(), "a request is being answered"
        foreground.request_ended()
        await asyncio.wait_for(waiting, 0.05)
        foreground.request_began()


@pytest.mark.usefixtures("one_request")
async def test_the_wait_is_bounded_and_never_inside_a_write_or_outside_a_job() -> None:
    with costing(JobCost()):
        began = time.monotonic()
        await foreground.screens_first(False)
        assert foreground.YIELD_LONGEST_SECONDS <= time.monotonic() - began < 1.0
        began = time.monotonic()
        await foreground.screens_first(True)
        assert time.monotonic() - began < foreground.YIELD_LONGEST_SECONDS / 2, "holds the writer"
    began = time.monotonic()
    await foreground.screens_first(False)
    assert time.monotonic() - began < foreground.YIELD_LONGEST_SECONDS / 2, "a request's own"


async def test_answering_counts_the_requests_and_never_goes_below_nought() -> None:
    foreground.request_began()
    foreground.request_began()
    assert foreground.answering() == 2
    for _ in range(3):
        foreground.request_ended()
    assert foreground.answering() == 0
    with costing(JobCost()):
        await asyncio.wait_for(foreground.screens_first(False), 0.05)


@pytest.mark.usefixtures("one_request")
async def test_a_jobs_reads_and_writes_wait_and_a_requests_do_not(tmp_path: Path) -> None:
    database = Database(tmp_path / "test.sqlite3")
    await database.connect()
    try:
        began = time.monotonic()
        await database.fetch_all("SELECT 1")
        async with database.write() as connection:
            await connection.execute("SELECT 1")
        assert time.monotonic() - began < foreground.YIELD_LONGEST_SECONDS
        with costing(JobCost()):
            began = time.monotonic()
            await database.fetch_one("SELECT 1")
            async with database.write():
                await database.fetch_all("SELECT 1")
            waited = time.monotonic() - began
        assert 2 * foreground.YIELD_LONGEST_SECONDS <= waited < 3 * foreground.YIELD_LONGEST_SECONDS
    finally:
        await database.close()
