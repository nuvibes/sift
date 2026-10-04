# SPDX-License-Identifier: AGPL-3.0-or-later
"""What this machine is and can do: the hardware probe, and the self-test that measures it."""

from __future__ import annotations

import contextlib
from functools import partial

from fastapi import FastAPI

from sift.kernel import wiring
from sift.kernel.config import Settings
from sift.kernel.db import check_sqlite_capabilities
from sift.kernel.hardware import HardwareReport, probe
from sift.kernel.jobs import JobContext, JobQueue, JobSwitchedOff, register_handler
from sift.kernel.log import get_logger
from sift.kernel.ml import accel
from sift.kernel.subprocess import set_machine_memory
from sift.kernel.wiring import provide
from sift.kernel.workbench import Workbench
from sift.slices import library_roots, performance, settings_hub
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
    # THE GRAPHICS-CARD RUNTIME IS PUT IN FRONT BEFORE ANYTHING CAN IMPORT ONE, and this line is
    # the guarantee rather than a tidy-up.
    #
    # Two builds of that library cannot both be loaded, and the first one in wins for the life of
    # the process. Every import of it goes through one function that does this first, but that is a
    # property of the code as it is today: a module that grows its own `import onnxruntime` line is
    # correct in isolation, and is a way for a face job running before anybody opened a settings
    # screen to decide the card cannot be used, for good, on a machine where it works. The symptom
    # is a screen refusing a card it can see, and nothing anywhere saying why.
    #
    # Done here it cannot be raced: nothing has run yet. It costs a `sys.path` insert and a few
    # directory lookups, imports nothing, and does nothing at all where no runtime is installed.
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
    """The self-test's runner: what the Performance screen starts and the Build waits for.

    Built before the Build's products because the registry holds it, and before the diagnostics
    because it only reads them while a run is going. The two watches it reads are looked up then
    rather than now (they are provided later in the boot), and a run cannot start until the
    boot is over and a request or a job asks for one.
    """
    runner = performance.SelfTestRunner(
        settings=settings,
        hardware=hardware,
        worst_lag=lambda: wiring.part_of_app(app, wiring.WATCHDOG).worst_lag_seconds,
        worst_wait=lambda: wiring.part_of_app(app, wiring.THREADS).worst_wait_seconds,
        current=partial(performance.current_settings, hub),
        storages=partial(performance.storages_to_measure, store.library),
        rates=performance.RatesStore(store.database),
    )
    provide(app, performance.SELF_TEST_RUNNER, runner)
    provide(app, performance.selftest.SELF_TEST, runner.state)


def build_benchmark(
    app: FastAPI,
    store: Storage,
    queue: JobQueue,
    hub: settings_hub.SettingsService,
    workbench: Workbench,
) -> None:
    """The benchmark Sift runs by itself on the first library folder, and everything around it.

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

    async def scan(root_id: str, requested_by: str | None) -> None:
        # The scan the folder's own add asked for, named for the person who added it.
        with contextlib.suppress(JobSwitchedOff):
            await queue.enqueue(
                library_roots.SCAN, {"root_id": root_id}, dedupe=True, requested_by=requested_by
            )

    async def measure_and_set(context: JobContext) -> None:
        await performance.run_benchmark(
            context,
            runner=runner,
            first=first,
            saves=hub,
            current=partial(performance.current_settings, hub),
            notify=notify,
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
        performance.FirstFolder(runner=runner, queue=queue, roots=roots, first=first),
    )
