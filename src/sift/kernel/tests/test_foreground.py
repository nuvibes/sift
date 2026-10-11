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
        # The first wait is whole; the second is what the job's share has earned since.
        assert foreground.YIELD_LONGEST_SECONDS <= waited < 1.5 * foreground.YIELD_LONGEST_SECONDS
    finally:
        await database.close()


@pytest.mark.usefixtures("one_request")
async def test_a_job_gives_the_screens_a_share_of_its_time_not_a_wait_per_statement() -> None:
    cost = JobCost()
    with foreground.a_requests_statement(), costing(cost):
        began = time.perf_counter()
        for _ in range(20):
            await foreground.screens_first(False)
        took = time.perf_counter() - began
        # The first wait, the share since, and a tick of the timer a wait may overrun by.
        bound = foreground.YIELD_LONGEST_SECONDS + foreground.YIELD_SHARE * took
        bound += foreground.YIELD_SHORTEST_SECONDS
        assert took < 3 * foreground.YIELD_LONGEST_SECONDS, "twenty statements, not twenty waits"
        assert cost.summary()["screens_wait_ms"] <= bound * 1000

        # Two parts of one job asking together share one balance: the second finds it spent.
        second = JobCost()
        with costing(second):

            async def part() -> float:
                at = time.perf_counter()
                await foreground.screens_first(False)
                return time.perf_counter() - at

            waits = sorted(await asyncio.gather(part(), part()))
        assert waits[0] < foreground.YIELD_LONGEST_SECONDS / 2 <= waits[1]
