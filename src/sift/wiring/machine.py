# SPDX-License-Identifier: AGPL-3.0-or-later
"""What this machine is and can do: the hardware probe, and the self-test that measures it."""

from __future__ import annotations

import asyncio
import contextlib
from functools import partial
from pathlib import Path

from fastapi import FastAPI

from sift.kernel import sampling, wiring
from sift.kernel.config import Settings
from sift.kernel.content.mounts import storage_of
from sift.kernel.db import check_sqlite_capabilities
from sift.kernel.hardware import HardwareReport, probe
from sift.kernel.jobs import JobContext, JobQueue, JobSwitchedOff, register_handler
from sift.kernel.jobs.families import Family
from sift.kernel.log import get_logger
from sift.kernel.ml import accel
from sift.kernel.ml.child import ChildRunner
from sift.kernel.subprocess import set_machine_memory
from sift.kernel.wiring import provide
from sift.kernel.workbench import Workbench
from sift.slices import library_roots, performance, settings_hub
from sift.slices.faces import settings as face_settings
from sift.slices.faces import tuning as face_tuning
from sift.slices.faces import weights as face_weights
from sift.slices.faces.crop import CHIP_SIZE
from sift.slices.faces.runner import ChildRunner as FaceRunner
from sift.slices.media_jobs.ffmpeg import preview_args
from sift.slices.performance import benchmark, measure_together
from sift.slices.performance.measure_models import RECOGNITION_SHARE_KEY, Ask, ModelPass
from sift.slices.performance.selftest import StorageToMeasure
from sift.slices.semantic import settings as search_settings
from sift.slices.semantic import weights as search_weights
from sift.slices.semantic.embed import FEATURE as SEARCH_FEATURE
from sift.slices.semantic.embed import FRAME_SIZE
from sift.slices.watermarks import settings as mark_settings
from sift.slices.watermarks import weights as mark_weights
from sift.slices.watermarks.reader import FEATURE as MARK_FEATURE
from sift.wiring.built import Storage

log = get_logger(__name__)


async def build_machine(app: FastAPI, settings: Settings) -> HardwareReport:
    """What this machine can do, before anything depends on the answer.

    The SQLite check comes first of all: a library that cannot do what the schema needs should stop
    the boot while there is still nothing on disk to be sorry about.

    The hardware is looked at once. The result sizes the worker pool and the read pool, is
    served at /health, and logs at startup any accelerator that was asked for but is not here.
    """
    provide(app, wiring.SQLITE, check_sqlite_capabilities())
    # The graphics-card runtime goes in front before anything can import one: the first build of
    # that library loaded wins for the life of the process, and here nothing has run yet to race it.
    accelerated = accel.enable(settings)
    log.info("boot.accelerator", enabled=accelerated)
    hardware = await probe(settings)
    # Every background tool is held to its share of this machine's memory from here on.
    set_machine_memory(hardware.total_ram_bytes)
    provide(app, wiring.HARDWARE, hardware)
    return hardware


def build_self_test(
    app: FastAPI,
    settings: Settings,
    hardware: HardwareReport,
    store: Storage,
    hub: settings_hub.SettingsService,
) -> None:
    """The self-test's runner, before the Build's products, which hold it. The watches and the
    queue it reads come later in the boot, so they are looked up when read."""

    async def ask(requested_by: str | None) -> str | None:
        queue = wiring.part_of_app(app, wiring.QUEUE)
        return await benchmark.ask_for_run(queue, requested_by=requested_by)

    def stalls() -> tuple[int, int]:
        threads = wiring.part_of_app(app, wiring.THREADS)
        return wiring.part_of_app(app, wiring.WATCHDOG).held_count, threads.full_count

    runner = performance.SelfTestRunner(
        settings=settings,
        hardware=hardware,
        worst_lag=lambda: wiring.part_of_app(app, wiring.WATCHDOG).worst_lag_seconds,
        worst_wait=lambda: wiring.part_of_app(app, wiring.THREADS).worst_wait_seconds,
        current=partial(performance.current_settings, hub),
        storages=partial(performance.storages_to_measure, store.library),
        rates=performance.RatesStore(store.database),
        ask=ask,
        preview=preview_args,
        passes=partial(model_passes, settings, hardware, hub),
        together=measure_together.run,
        fell_behind=lambda: sum(stalls()),
        stalls=stalls,
    )
    provide(app, performance.SELF_TEST_RUNNER, runner)
    provide(app, performance.selftest.SELF_TEST, runner.state)


async def model_passes(
    settings: Settings, hardware: HardwareReport, hub: settings_hub.SettingsService
) -> list[ModelPass]:
    """Each model pass as it runs on this device: its set models, on its set device."""
    frames = sampling.MAX_FRAMES
    side = face_tuning.DETECTOR_INPUT
    detector, recognizer = face_weights.pairing(str(await hub.get_app(face_settings.MODEL_KEY)))
    face_device = str(await hub.get_app(face_settings.DEVICE_KEY))
    pictures, _, _ = search_weights.working_set(str(await hub.get_app(search_settings.MODEL_KEY)))
    search_device = str(await hub.get_app(search_settings.DEVICE_KEY))
    search_store = search_weights.store(settings)
    finder, reader = mark_weights.working_set()
    mark_device = str(await hub.get_app(mark_settings.DEVICE_KEY))
    mark_store = mark_weights.store(settings)
    return [
        ModelPass(
            name="Faces",
            family=Family.IDENTIFY.value,
            device=face_device,
            installed=face_weights.store(settings).installed,
            runner=lambda: FaceRunner(settings, hardware, device=face_device),
            asks=(
                Ask(detector, (1, 3, side, side), frames),
                Ask(recognizer, (frames, 3, CHIP_SIZE, CHIP_SIZE)),
            ),
            picture=(face_tuning.FRAME_LONG_SIDE, face_tuning.FRAME_LONG_SIDE * 9 // 16),
            share_key=RECOGNITION_SHARE_KEY,
        ),
        ModelPass(
            name="Smart Search",
            family=Family.SEMANTIC.value,
            device=search_device,
            installed=search_store.installed,
            runner=lambda: ChildRunner(
                search_store, hardware, device=search_device, feature=SEARCH_FEATURE
            ),
            asks=(Ask(pictures, (1, 3, FRAME_SIZE, FRAME_SIZE), frames),),
            picture=(FRAME_SIZE, FRAME_SIZE),
        ),
        ModelPass(
            name="Watermarks",
            family=Family.IDENTIFY.value,
            device=mark_device,
            installed=mark_store.installed,
            runner=lambda: ChildRunner(
                mark_store, hardware, device=mark_device, feature=MARK_FEATURE
            ),
            asks=(
                Ask(finder, (1, 3, 64, 960)),
                Ask(finder, (1, 3, 288, 960)),
                Ask(reader, (2, 3, 48, 384)),
            ),
            picture=(1920, 1080),
            moments=1,
        ),
    ]


def build_benchmark(
    app: FastAPI,
    store: Storage,
    queue: JobQueue,
    hub: settings_hub.SettingsService,
    workbench: Workbench,
) -> None:
    """The benchmark job, and what Sift runs by itself when a library folder is added.

    The reaction to a folder added (`wiring.ON_FOLDER_ADDED`), the job that measures and sets,
    the folder's scan queued once that job has settled, and the Undo that History offers for what it
    set. Built after the self-test's runner, which it drives, and before the workers start, which
    claim it. See `performance.benchmark`.
    """
    runner = wiring.part_of_app(app, performance.SELF_TEST_RUNNER)
    first = performance.FirstBenchmark()
    provide(app, performance.FIRST_BENCHMARK, first)

    async def notify(changed: set[str]) -> None:
        # The reactions are assembled after the workers start, so they are looked up when a value
        # is set rather than now.
        react = wiring.part_of_app_or_none(app, wiring.ON_SETTINGS_CHANGED)
        if react is not None:
            await react(changed)

    async def roots() -> int:
        return len(await store.library.roots())

    async def storage(root_id: str) -> StorageToMeasure | None:
        root = await store.library.get_root(root_id)
        if root is None:
            return None
        where = await asyncio.to_thread(storage_of, Path(root.abs_path))
        return StorageToMeasure(
            storage=where.key, label=root.name, remote=where.remote, roots=(Path(root.abs_path),)
        )

    async def scan(root_id: str, requested_by: str | None) -> None:
        # The scan the folder's own add asked for, named for the person who added it.
        with contextlib.suppress(JobSwitchedOff):
            await library_roots.queue_scan(queue, {"root_id": root_id}, requested_by=requested_by)

    async def measure_and_set(context: JobContext) -> None:
        await performance.run_benchmark(
            context,
            runner=runner,
            first=first,
            saves=hub,
            current=partial(performance.current_settings, hub),
            notify=notify,
            storage_of=storage,
        )

    register_handler(
        performance.BENCHMARK,
        measure_and_set,
        name=performance.BENCHMARK_NAME,
        alone=True,
        exclusive=True,
    )
    queue.listen_for_settled(
        performance.BENCHMARK, performance.ThenScan(queue=queue, first=first, scan=scan)
    )
    workbench.register_reverser(performance.BenchmarkReceipts(hub, notify))
    provide(
        app,
        wiring.ON_FOLDER_ADDED,
        performance.FirstFolder(
            runner=runner, queue=queue, roots=roots, first=first, storage=storage
        ),
    )
