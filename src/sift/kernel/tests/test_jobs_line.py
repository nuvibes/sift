# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where a waiting job is in the line, counted from the claim index.

`JobQueue.positions_of` answers for every waiting job on a page of Activity, and the line can be a
hundred thousand jobs long during a whole-library pass. These hold that the count is the place a
rank of the whole line gives, that it is read from `ix_jobs_claim_by_id` alone, and that a library
from before the index ended in `run_after` is given the wider one.
"""

from __future__ import annotations

import random

import pytest

from sift.kernel.db import Database, in_clause
from sift.kernel.jobs import JobContext, JobQueue, register_handler
from sift.kernel.jobs import queue as queue_module
from sift.kernel.jobs import schema as jobs_schema
from sift.testing.fixtures import FakeClock

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("clean_handlers")]


async def _nothing(context: JobContext) -> None:
    return None


async def test_a_place_is_the_count_of_the_line_in_front_of_it(temp_db: Database) -> None:
    """Places counted a stretch at a time are the places a rank of the whole line gives: several
    priorities, jobs waiting for their moment among them, and a page holding jobs from anywhere in
    the line, in any order."""
    clock = FakeClock()
    queue = JobQueue(temp_db, clock=clock.now)
    await temp_db.initialize_schema()
    register_handler("probe", _nothing, name="Test job")
    chosen = random.Random(14)
    line: list[tuple[int, str]] = []
    every: list[str] = []
    for n in range(120):
        priority = chosen.choice((5, 10, 10, 50, 200))
        later = chosen.random() < 0.15
        job_id = await queue.enqueue(
            "probe",
            {"n": n},
            priority=priority,
            run_after=int(clock.now()) + 600 if later else None,
        )
        every.append(job_id)
        if not later:
            line.append((priority, job_id))
        clock.advance(1)
    expected = {job_id: place for place, (_, job_id) in enumerate(sorted(line), start=1)}

    for size in (1, 2, 7, 50):
        page = chosen.sample(every, size)
        assert await queue.positions_of(page) == {
            job_id: expected[job_id] for job_id in page if job_id in expected
        }


async def test_the_line_is_counted_from_the_claim_index_alone(temp_db: Database) -> None:
    """Every count of the line reads `ix_jobs_claim_by_id` and nothing else: a count that read the
    table for `run_after` costs a row lookup for every job in front, which is the whole line on the
    newest page."""
    await temp_db.initialize_schema()
    sql, params = in_clause(queue_module._POSITIONS, ["a", "b"])
    plan = [
        str(row["detail"])
        for row in await temp_db.fetch_all(
            "EXPLAIN QUERY PLAN " + sql,  # nosemgrep: sift-no-string-built-sql
            (1, *params, 1),
        )
    ]
    counts = [line for line in plan if "ix_jobs_claim_by_id" in line]
    assert len(counts) == 4, plan
    assert not [line for line in plan if line.startswith("SCAN jobs")], plan
    assert all("USING COVERING INDEX ix_jobs_claim_by_id " in line for line in counts), plan


async def test_a_queue_from_before_the_counted_line_gets_the_wider_claim_index(
    temp_db: Database,
) -> None:
    """Version 14: a library at 13 has its claim index made again with `run_after` on the end, and
    keeps its rows; run twice, it changes nothing more."""
    async with temp_db.write() as connection:
        await connection.execute("DROP TABLE IF EXISTS jobs")
        await jobs_schema.initialize(connection, on_disk=0)
        await connection.execute("ALTER TABLE jobs DROP COLUMN to_read")
        await connection.execute("DROP INDEX ix_jobs_claim_by_id")
        await connection.execute("CREATE INDEX ix_jobs_claim_by_id ON jobs(state, priority, id)")
        await connection.execute(
            "INSERT INTO jobs (id, type, payload, created_at, updated_at, root_id)"
            " VALUES ('J1', 'probe', '{}', 1, 1, 'J1')"
        )
        await jobs_schema.initialize(connection, on_disk=13)
        await jobs_schema.initialize(connection, on_disk=13)

    (made,) = await temp_db.fetch_all(
        "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = 'ix_jobs_claim_by_id'"
    )
    assert "(state, priority, id, run_after)" in str(made["sql"])
    assert [row["id"] for row in await temp_db.fetch_all("SELECT id FROM jobs")] == ["J1"]
