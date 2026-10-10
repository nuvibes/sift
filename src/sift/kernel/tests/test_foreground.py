# SPDX-License-Identifier: AGPL-3.0-or-later
"""A job's statements wait a moment for a request's statement, and never anything else."""

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
async def test_a_jobs_statement_waits_while_a_requests_statement_runs() -> None:
    cost = JobCost()
    with foreground.a_requests_statement(), costing(cost):
        assert foreground.statements_answering() == 1
        waiting = asyncio.create_task(foreground.screens_first(False))
        await asyncio.sleep(0.02)
        assert not waiting.done(), "a request's statement is running"
    await asyncio.wait_for(waiting, 0.05)
    assert foreground.statements_answering() == 0
    assert 15 <= cost.summary()["screens_wait_ms"] < 100, "the wait is on the job's record"


@pytest.mark.usefixtures("one_request")
async def test_a_request_with_no_statement_running_holds_nobody() -> None:
    with costing(JobCost()):
        began = time.monotonic()
        await foreground.screens_first(False)
        assert time.monotonic() - began < foreground.YIELD_LONGEST_SECONDS / 2


@pytest.mark.usefixtures("one_request")
async def test_the_wait_is_bounded_and_never_inside_a_write_or_outside_a_job() -> None:
    with foreground.a_requests_statement():
        with costing(JobCost()):
            began = time.monotonic()
            await foreground.screens_first(False)
            assert foreground.YIELD_LONGEST_SECONDS <= time.monotonic() - began < 1.0
            began = time.monotonic()
            await foreground.screens_first(True)
            assert time.monotonic() - began < foreground.YIELD_LONGEST_SECONDS / 2, "the writer"
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
    with foreground.a_requests_statement():
        assert foreground.statements_answering() == 0, "outside a request, nobody's statement"
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
        with foreground.a_requests_statement(), costing(JobCost()):
            began = time.monotonic()
            await database.fetch_one("SELECT 1")
            async with database.write():
                await database.fetch_all("SELECT 1")
            waited = time.monotonic() - began
        assert 2 * foreground.YIELD_LONGEST_SECONDS <= waited < 3 * foreground.YIELD_LONGEST_SECONDS
    finally:
        await database.close()
