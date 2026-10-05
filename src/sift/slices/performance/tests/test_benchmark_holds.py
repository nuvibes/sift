# SPDX-License-Identifier: AGPL-3.0-or-later
"""A benchmark pressed while work runs, on the real queue, pool and ledger."""

from __future__ import annotations

import asyncio
import importlib
import sys
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

import sift.slices.workbench.schema  # noqa: F401
from sift.kernel import subprocess as tools
from sift.kernel.db import Database
from sift.kernel.jobs import JobContext, JobQueue, JobState, register_handler
from sift.kernel.jobs import ledger as ledger_module
from sift.kernel.jobs.families import Family
from sift.kernel.jobs.ledger import Ledger
from sift.kernel.jobs.work_ahead import Ahead
from sift.kernel.jobs.worker_pool import WorkerPool
from sift.slices.media_jobs.activity_wire import KindOfWork
from sift.slices.performance import selftest
from sift.slices.performance.benchmark import (
    BENCHMARK,
    FirstBenchmark,
    ask_for_run,
    run_benchmark,
)
from sift.slices.performance.selftest import Measurement
from sift.slices.performance.tests.test_runner import a_measurement, a_runner, a_store
from sift.wiring.workers import _less

pytestmark = pytest.mark.integration

WORK = "stand_in_picture"

SLEEP = "import time; time.sleep(1)"

activity = importlib.import_module("sift.slices.media_jobs.router")


class Pictures:
    def __init__(self) -> None:
        self.go = asyncio.Event()
        self.running = 0

    async def handler(self, _context: JobContext) -> None:
        self.running += 1
        try:
            await self.go.wait()
        finally:
            self.running -= 1


class Holds:
    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.measuring = asyncio.Event()
        self.state = selftest.SelfTest()
        self.notes: list[str] = []
        self.lengths: dict[str, float] = {}

    async def run(self) -> None:
        self.measuring.set()
        await self.release.wait()
        self.state.measurement = a_measurement()

    async def measured(self) -> bool:
        return False

    async def lasted(self, kind: str) -> float | None:
        return self.lengths.get(kind)

    async def keep_length(self, kind: str, seconds: float) -> None:
        self.lengths[kind] = seconds

    def recommend(
        self, measurement: Measurement, *, current: dict[str, int]
    ) -> list[selftest.Recommendation]:
        return selftest.recommend(measurement, current=current)


async def _until(check: Callable[[], bool], seconds: float = 10) -> None:
    deadline = time.monotonic() + seconds
    while not check():
        assert time.monotonic() < deadline, "the condition never became true"
        await asyncio.sleep(0.02)


async def _states(queue: JobQueue) -> dict[str, int]:
    return (await queue.counts_by_type()).get(WORK, {})


async def test_work_a_benchmark_holds_back_is_paused_says_so_and_its_wait_is_not_its_time(
    temp_db: Database, job_queue: JobQueue, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Twelve tasks running and twelve waiting when it is pressed."""
    clock = [1_000_000.0]
    monkeypatch.setattr(
        ledger_module, "time", SimpleNamespace(time=lambda: clock[0], monotonic=time.monotonic)
    )
    pictures, holds = Pictures(), Holds()
    first = FirstBenchmark()

    async def measure(context: JobContext) -> None:
        async def nothing(*_args: object, **_kwargs: object) -> None:
            return None

        async def none_current() -> dict[str, int]:
            return {}

        await run_benchmark(
            context,
            runner=cast(Any, holds),
            first=first,
            saves=cast(Any, nothing),
            current=none_current,
            notify=nothing,
            storage_of=nothing,
        )

    register_handler(
        WORK, pictures.handler, name="Making a stand-in picture", family=Family.GENERATE
    )
    register_handler(
        BENCHMARK, measure, name="Benchmarking this device", alone=True, exclusive=True
    )
    book = Ledger(temp_db, families_of={WORK: Family.GENERATE})
    await book.start()

    async def tick() -> None:
        due = _less(await job_queue.due_by_type(), await job_queue.held_for_family_by_type())
        await book.settle(due, settings={})

    for index in range(24):
        await job_queue.enqueue(WORK, {"n": index})
    pool = WorkerPool(job_queue, concurrency=12, poll_interval=0.02, watchdog=False, ledger=book)
    await pool.start()
    try:
        await _until(lambda: pictures.running == 12)
        bench = await ask_for_run(job_queue, requested_by="admin-1")
        assert bench is not None
        pictures.go.set()
        await asyncio.wait_for(holds.measuring.wait(), 10)
        pictures.go.clear()

        held = await _states(job_queue)
        assert held.get(JobState.QUEUED.value, 0) == 0, held
        assert held.get(JobState.PAUSED.value) == 12, "what it holds back is paused by it"
        await job_queue.enqueue(WORK, {"n": "arrived while it measured"})
        await tick()
        clock[0] += 720
        await tick()
        summary = await job_queue.work_summary()
        work = {WORK: KindOfWork(done=12, outstanding=summary.run[WORK].outstanding, failed=0)}
        families, _, _ = await activity._families_and_holds(
            job_queue, work, None, pool, summary.states, Ahead()
        )
        assert families[Family.GENERATE.value].reason == activity.PAUSED_FOR_THE_BENCHMARK

        holds.release.set()
        await _until(lambda: pictures.running == 12)
        clock[0] += 12
        pictures.go.set()
        await _until(lambda: pictures.running == 0)

        async def all_done() -> bool:
            return (await _states(job_queue)).get(JobState.DONE.value) == 25

        deadline = time.monotonic() + 10
        while not await all_done():
            assert time.monotonic() < deadline
            await asyncio.sleep(0.02)
        await tick()
    finally:
        holds.release.set()
        pictures.go.set()
        await pool.stop()

    note = (await job_queue.get(bench)).note  # type: ignore[union-attr]
    assert note is not None and "Sift paused 12 tasks while it measured" in note
    runs = await book.recent_runs(Family.GENERATE)
    after = next(one for one in runs if one.files_total == 13 and one.started_at > 1_000_000)
    assert after.seconds == 12, "the 12 minutes it waited are not its running time"
    assert all(one.seconds is not None and one.seconds <= 12 for one in runs)


async def test_a_canceled_run_stops_measuring_ends_its_tools_and_keeps_the_last_result(
    temp_db: Database, job_queue: JobQueue, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = await a_store(tmp_path)
    runner = a_runner(tmp_path, store)

    async def instant(**_kwargs: object) -> Measurement:
        return a_measurement()

    monkeypatch.setattr(selftest, "measure", instant)
    await runner.run()
    kept = await runner.rates()

    steps: list[float] = []
    ends: list[float] = []
    spawned: list[Any] = []
    spawn = tools._spawn

    async def recorded(*args: Any, **kwargs: Any) -> Any:
        process = await spawn(*args, **kwargs)
        spawned.append(process)
        return process

    async def tool() -> None:
        try:
            await tools.run([sys.executable, "-c", SLEEP], time_limit=30)
        finally:
            ends.append(time.monotonic())

    async def ladder(**kwargs: Any) -> Measurement:
        done: list[selftest.Level] = []
        for at_once in (1, 2, 4, 8):
            steps.append(time.monotonic())
            await asyncio.gather(tool(), tool())
            done.append(selftest.Level(at_once, 1.0, at_once, 0.0, 0.0))
            kwargs["report"](Measurement(cores=8, levels=tuple(done)))
        return Measurement(cores=8, levels=tuple(done))

    monkeypatch.setattr(tools, "_spawn", recorded)
    monkeypatch.setattr(selftest, "measure", ladder)

    async def measure(context: JobContext) -> None:
        async def nothing(*_args: object, **_kwargs: object) -> None:
            return None

        async def none_current() -> dict[str, int]:
            return {}

        await run_benchmark(
            context,
            runner=runner,
            first=FirstBenchmark(),
            saves=cast(Any, nothing),
            current=none_current,
            notify=nothing,
            storage_of=nothing,
        )

    register_handler(
        BENCHMARK, measure, name="Benchmarking this device", alone=True, exclusive=True
    )
    pool = WorkerPool(job_queue, concurrency=2, poll_interval=0.02, watchdog=False)
    await pool.start()
    try:
        bench = await ask_for_run(job_queue, requested_by="admin-1")
        assert bench is not None
        await _until(lambda: len(steps) == 2 and len(spawned) == 4)
        assert runner.state.measurement == a_measurement(), "the last result stays on screen"
        assert runner.progress is not None and len(runner.progress.levels) == 1
        canceled = time.monotonic()
        await job_queue.cancel(bench)
        await asyncio.sleep(3.5)
    finally:
        await pool.stop()

    left = [one for one in spawned if one.poll() is None]
    for one in left:
        one.kill()
    assert left == [], "every tool the run started has ended"
    assert max(ends) - canceled < 2 and steps[-1] < canceled, (max(ends), steps[-1], canceled)
    assert len(steps) == 2 and len(spawned) == 4, "nothing more was measured"
    assert await runner.rates() == kept, "a canceled run keeps nothing"
    assert runner.state.measurement == a_measurement() and not runner.state.running
    assert runner.progress is None
