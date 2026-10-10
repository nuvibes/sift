# SPDX-License-Identifier: AGPL-3.0-or-later
"""A long job's plan: written a batch per write, read back in order, checkpointed on the claim,
gone with the job, and the attempt a restart does not spend."""

from __future__ import annotations

import pytest

from sift.kernel.ids import new_id
from sift.kernel.jobs import JobQueue, queue_plans, recover
from sift.kernel.jobs.queue_plans import PlanStep

pytestmark = pytest.mark.usefixtures("clean_handlers")


def steps(start: int, count: int) -> list[PlanStep]:
    return [
        PlanStep(seq, f"f{seq}.jpg", seq * 10, seq * 1_000, "image", "read")
        for seq in range(start, start + count)
    ]


async def claimed(job_queue: JobQueue) -> tuple[str, str]:
    job_id = await job_queue.enqueue("walk", {}, require_handler=False)
    worker = new_id()
    job = await job_queue.claim(worker)
    assert job is not None and job.id == job_id
    return job_id, worker


async def test_a_plan_is_written_a_batch_per_write_and_read_back_in_order(
    job_queue: JobQueue, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(queue_plans, "PLAN_WRITE_BATCH", 2)
    job_id, worker = await claimed(job_queue)
    writes = 0
    write = job_queue._db.write

    def counted() -> object:
        nonlocal writes
        writes += 1
        return write()

    monkeypatch.setattr(job_queue._db, "write", counted)
    assert await job_queue.write_plan(job_id, worker, steps(0, 5))
    assert writes == 3

    read, settled = await job_queue.plan_of(job_id)
    assert read == steps(0, 5) and settled == 0
    assert await job_queue.settle_plan(job_id, worker, 4)
    assert (await job_queue.plan_of(job_id))[1] == 4

    await job_queue.drop_plan_from(job_id, 3, 5)
    assert [step.seq for step in (await job_queue.plan_of(job_id))[0]] == [0, 1, 2]
    await job_queue.forget_plan(job_id, 5)
    assert await job_queue.plan_of(job_id) == ([], 0)


async def test_a_worker_that_lost_the_job_writes_no_plan(job_queue: JobQueue) -> None:
    job_id, _worker = await claimed(job_queue)
    assert not await job_queue.write_plan(job_id, new_id(), steps(0, 1))
    assert not await job_queue.settle_plan(job_id, new_id(), 1)
    assert await job_queue.plan_of(job_id) == ([], 0)


async def test_the_plan_goes_with_its_job(job_queue: JobQueue) -> None:
    job_id, worker = await claimed(job_queue)
    assert await job_queue.write_plan(job_id, worker, steps(0, 3))
    assert await job_queue.settle_plan(job_id, worker, 2)
    await job_queue.complete(job_id, worker)
    async with job_queue._db.write() as connection:
        await connection.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    assert await job_queue.plan_of(job_id) == ([], 0)


async def test_a_restart_refunds_only_the_claim_that_moved_its_plan(job_queue: JobQueue) -> None:
    moved, mover = await claimed(job_queue)
    stuck, _ = await claimed(job_queue)
    assert await job_queue.settle_plan(moved, mover, 1)

    requeued, failed = await recover(job_queue)

    assert sorted(requeued) == sorted([moved, stuck]) and failed == []
    by_id = {job.id: job for job in (await job_queue.list()).jobs}
    assert by_id[moved].attempts == 0
    assert by_id[stuck].attempts == 1
