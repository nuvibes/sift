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
from sift.kernel import device_load
from sift.kernel import subprocess as tools
from sift.kernel.db import Database
from sift.kernel.jobs import JobContext, JobQueue, JobState, register_handler
from sift.kernel.jobs import ledger as ledger_module
from sift.kernel.jobs.families import Family
from sift.kernel.jobs.ledger import Ledger
from sift.kernel.jobs.work_ahead import Ahead
from sift.kernel.jobs.worker_pool import WorkerPool
from sift.slices.media_jobs.activity_wire import KindOfWork
from sift.slices.performance import (
    benchmark,
    measure_encoder,
    measure_models,
    measure_together,
    selftest,
)
from sift.slices.performance import budget as budget_module
from sift.slices.performance import runner as runner_module
from sift.slices.performance.benchmark import (
    BENCHMARK,
    AutomaticRun,
    FirstBenchmark,
    FirstFolder,
    ThenScan,
    WhenQuiet,
    ask_for_run,
    run_benchmark,
)
from sift.slices.performance.runner import current_settings
from sift.slices.performance.selftest import Measurement
from sift.slices.performance.tests.test_runner import a_machine, a_measurement, a_runner, a_store
from sift.slices.settings_hub.service import SettingsService

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
        self.unsure: frozenset[str] = frozenset()

    async def run(self, *, first_part: bool = False, since: float | None = None) -> None:
        self.measuring.set()
        await self.release.wait()
        self.state.measurement = a_measurement()

    async def measured(self) -> bool:
        return False

    async def whole_to_come(self) -> bool:
        return True

    async def whole_due(self) -> bool:
        return True

    async def first_values(self) -> dict[str, int]:
        return {}

    async def lasted(self, kind: str) -> float | None:
        return self.lengths.get(kind)

    async def keep_length(self, kind: str, seconds: float) -> None:
        self.lengths[kind] = seconds

    def recommend(
        self, measurement: Measurement, *, current: dict[str, int]
    ) -> list[selftest.Recommendation]:
        return selftest.recommend(measurement, current=current)


async def _done(_context: JobContext) -> None:
    return None


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
    clock = [1_000_000.0]
    monkeypatch.setattr(
        ledger_module, "time", SimpleNamespace(time=lambda: clock[0], monotonic=time.monotonic)
    )
    # A run here ends the tick its family drains: the gap a batch rides over is not this test's.
    monkeypatch.setattr(ledger_module, "RUN_GAP_SECONDS", 0.0)
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
        due = await job_queue.due_by_type()
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

    job = await job_queue.get(bench)
    assert job is not None and job.note is not None
    assert "Sift paused 12 tasks while it measured" in job.note
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


#: Seconds each step took in a first benchmark on a share on a busy device, played 250 times faster.
SCALE = 1 / 250
TAKES = {1: 4.7, 2: 4.4, 4: 5.5, 8: 6.3, 12: 8.8, 16: 10.7}
CLIP, DECODE, READ, WALK, REST = 3.0, 1.0, 2.2, 4.0, 836.0
SPEEDS = {1: 15.7, 2: 18.6, 3: 20.0, 4: 22.3, 6: 22.5, 8: 22.9}
SHARE = selftest.StorageToMeasure(storage="\\\\nas\\media\\", label="Clips", remote=True, roots=())


class Busy:
    def busy(self) -> list[str]:
        return ["other programs"]


def _run(first: FirstBenchmark) -> AutomaticRun:
    assert first.run is not None
    return first.run


async def _played(seconds: float) -> None:
    await asyncio.sleep(seconds * SCALE)


def _ladders_played(
    monkeypatch: pytest.MonkeyPatch, slower: float = 1.0, *, rest: bool = True
) -> None:
    async def clip(into: Path, _settings: object) -> Path:
        await _played(CLIP * slower)
        return into / "clip.mp4"

    async def encode(_source: Path, _into: Path, index: int, *_rest: object) -> bool:
        await _played(TAKES[min(at for at in TAKES if at > index)] * slower)
        return True

    async def decode(*_args: object, repeats: int) -> selftest.Decode:
        await _played(DECODE * 2 * repeats * slower)
        return selftest.Decode(frames_per_second=1400.0, seek_seconds=0.03)

    def walk(_roots: object, **_kwargs: object) -> list[Path]:
        time.sleep(WALK * SCALE * slower)
        return [Path(f"clip-{index}.mp4") for index in range(selftest.SAMPLE_FILES)]

    async def read(_files: object, *, at_once: int, **_kwargs: object) -> selftest.StorageLevel:
        await _played(READ * slower)
        megabytes = int(SPEEDS[at_once] * 1_000_000)
        return selftest.StorageLevel(at_once=at_once, seconds=1.0, bytes_read=megabytes, seeks=600)

    async def the_rest(*_args: object) -> None:
        await _played(REST)

    monkeypatch.setattr(selftest, "build_clip", clip)
    monkeypatch.setattr(selftest, "_encode_once", encode)
    monkeypatch.setattr(selftest, "measure_decode", decode)
    monkeypatch.setattr(selftest, "sample_files", walk)
    monkeypatch.setattr(selftest, "_read_level", read)
    if rest:
        monkeypatch.setattr(runner_module.SelfTestRunner, "_measure_more", the_rest)
    monkeypatch.setattr(device_load.READER, "latest", Busy())


@pytest.mark.usefixtures("first_folder_benchmarks")
async def test_a_first_folder_s_files_wait_for_the_first_part_and_the_rest_runs_when_nothing_waits(
    temp_db: Database,
    job_queue: JobQueue,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ladders_played(monkeypatch)
    store = await a_store(tmp_path)
    runner = a_runner(tmp_path, store, machine=a_machine(24), on=(SHARE,))
    first = FirstBenchmark()
    hub = SettingsService(temp_db)
    released: list[float] = []

    async def storage(_root_id: str) -> selftest.StorageToMeasure:
        return SHARE

    async def measure(context: JobContext) -> None:
        async def nothing(_changed: set[str]) -> None:
            return None

        await run_benchmark(
            context,
            runner=runner,
            first=first,
            saves=hub,
            current=lambda: current_settings(hub),
            notify=nothing,
            storage_of=storage,
        )

    async def scan(_root_id: str, _by: str | None) -> None:
        released.append(time.monotonic())
        await job_queue.enqueue(WORK, {"scan": True})

    register_handler(
        BENCHMARK, measure, name="Benchmarking this device", alone=True, exclusive=True
    )
    register_handler(WORK, _done, name="Reading a stand-in folder")
    job_queue.listen_for_settled(BENCHMARK, ThenScan(queue=job_queue, first=first, scan=scan))
    quiet = WhenQuiet(
        runner=runner, queue=job_queue, first=first, kinds=lambda: [WORK], since_input=lambda: None
    )
    quiet.listen()

    async def one_folder() -> int:
        return 1

    pool = WorkerPool(job_queue, concurrency=4, poll_interval=0.02, watchdog=False)
    await pool.start()
    try:
        added = time.monotonic()
        trigger = FirstFolder(
            runner=runner, queue=job_queue, roots=one_folder, first=first, storage=storage
        )
        assert await trigger("root-1", "admin-1", True)
        await _until(lambda: runner.state.running)
        held = (await job_queue.newest_of(BENCHMARK, limit=1))[0].note
        await _until(lambda: bool(released), seconds=30)
        # The first part's budget played faster, and a second of the queue's own real work.
        waited = released[0] - added
        assert waited <= budget_module.FIRST_PART_SECONDS * SCALE + 1.0, f"{waited / SCALE:.0f} s"
        assert held == f"{benchmark.MEASURING_FIRST} {benchmark.HELD}"
        ended = await job_queue.get(_run(first).job_id)
        assert ended is not None and ended.note is not None and benchmark.WHOLE_LATER in ended.note
        assert await runner.whole_to_come(), "Performance says the full benchmark is to come"

        await _until(lambda: first.run is not None and first.run.state == "running", seconds=30)
        assert (_run(first).said, _run(first).holds) == (benchmark.RUNNING, False)
        await _until(lambda: first.run is not None and not first.run.going, seconds=30)
        assert not await runner.whole_to_come()
        assert runner.state.measurement is not None
        assert {one.at_once for one in runner.state.measurement.levels} == set(TAKES)
    finally:
        await pool.stop()


async def test_a_press_waits_for_one_look_not_for_the_full_run_sift_started(
    temp_db: Database, job_queue: JobQueue
) -> None:
    holds, first = Holds(), FirstBenchmark()
    pressed: list[float] = []

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

    async def scan(_context: JobContext) -> None:
        pressed.append(time.monotonic())

    register_handler(
        BENCHMARK, measure, name="Benchmarking this device", alone=True, exclusive=True
    )
    register_handler(WORK, scan, name="Reading a stand-in folder")
    quiet = WhenQuiet(
        runner=cast(Any, holds), queue=job_queue, first=first, kinds=list, since_input=lambda: None
    )
    stop = asyncio.Event()
    pool = WorkerPool(job_queue, concurrency=2, poll_interval=0.02, watchdog=False)
    await pool.start()
    looking = asyncio.create_task(quiet.keep_looking(stop, every=0.05))
    try:
        await asyncio.wait_for(holds.measuring.wait(), 10)
        asked = time.monotonic()
        await job_queue.enqueue(WORK, requested_by="admin-1")
        await _until(lambda: bool(pressed))
        assert pressed[0] - asked < 2, pressed[0] - asked
        assert _run(first).said == f"{benchmark.ASKED_FOR} {benchmark.AGAIN}"
    finally:
        stop.set()
        holds.release.set()
        await looking
        await pool.stop()


#: Run A's GPU previews, each model and the combined run, each as one stage at its own length.
GPU, COMBINED = 112.0, 80.0
MODEL_STAGES = {"Faces": 203.0, "Smart Search": 240.0, "Watermarks": 8.0}


@pytest.mark.parametrize("slower", [1.0, 10.0], ids=["run A's times", "ten times as long"])
async def test_a_whole_run_ends_within_its_budget_however_long_its_stages_take(
    job_queue: JobQueue, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, slower: float
) -> None:
    _ladders_played(monkeypatch, slower, rest=False)
    monkeypatch.setattr(runner_module, "WHOLE_SECONDS", budget_module.WHOLE_SECONDS * SCALE)
    for module in (budget_module, runner_module, selftest):
        monkeypatch.setattr(module, "GRACE_SECONDS", budget_module.GRACE_SECONDS * SCALE)

    async def source(into: Path, _settings: object) -> Path:
        await _played(2.3 * slower)
        return into / "source.mp4"

    async def card(**_kwargs: object) -> measure_encoder.CardCurve:
        await _played(GPU * slower)  # deaf to its deadline: the backstop ends it
        return measure_encoder.CardCurve(encoder="h264_nvenc", decodes_on_card=True)

    async def model(one: Any, **_kwargs: object) -> measure_models.ModelCurve:
        await _played(MODEL_STAGES[one.name] * slower)
        return measure_models.ModelCurve(one.name, one.family, one.device)

    async def together(*_args: object, **_kwargs: object) -> measure_together.Together:
        await _played(COMBINED * slower)
        return measure_together.Together()

    async def passes() -> list[Any]:
        return [
            SimpleNamespace(name=name, family="identify", device="cpu", share_key=None)
            for name in MODEL_STAGES
        ]

    monkeypatch.setattr(measure_encoder, "build_source", source)
    monkeypatch.setattr(measure_encoder, "measure_card", card)
    monkeypatch.setattr(measure_models, "measure_pass", model)
    store = await a_store(tmp_path)
    runner = a_runner(
        tmp_path,
        store,
        machine=a_machine(24),
        on=(SHARE,),
        preview=cast(Any, object()),
        passes=passes,
        together=together,
    )

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
        asked = time.monotonic()
        bench = await ask_for_run(job_queue, requested_by="admin-1")
        assert bench is not None

        async def ended() -> bool:
            job = await job_queue.get(bench)
            return job is not None and job.state is JobState.DONE

        while not await ended():
            assert time.monotonic() - asked < 30
            await asyncio.sleep(0.01)
        took = time.monotonic() - asked
    finally:
        await pool.stop()

    # The queue's own work is real time, not played faster: a second of it at most.
    assert took <= budget_module.WHOLE_SECONDS * SCALE + 1.0, f"{took / SCALE:.0f} s played"
    measured = runner.state.measurement
    assert measured is not None and bool(measured.levels) is (slower == 1.0)
    cut = [note for note in runner.notes if note.startswith("To finish in time")]
    assert cut and runner.unsure, "what was cut short is said, and only suggested"
