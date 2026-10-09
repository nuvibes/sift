# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build the application in order, and take it down in the reverse of that order."""

from __future__ import annotations

import asyncio

# The name only, for `sqlite3.Error`; nosemgrep must sit on the flagged line itself.
import sqlite3  # nosemgrep: sift-no-database-driver-outside-kernel
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import AsyncExitStack, asynccontextmanager, contextmanager, suppress
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI

import sift
from sift import client

# `log_settings` declares two log settings at import, applied once there is a database.
from sift.kernel import changes, landing, lanes, log_settings, media, wiring
from sift.kernel.access import users_that_may_gain
from sift.kernel.changes import ChangeBus
from sift.kernel.config import (
    RETIRED_VARIABLES,
    Settings,
    ensure_directories,
    get_settings,
    retired_variables_in_use,
)
from sift.kernel.content.backlog import stop_builds
from sift.kernel.db import DatabaseError, keep_the_log_folded, keep_the_statistics_current
from sift.kernel.diagnostics import boot_set_aside
from sift.kernel.hardware import HardwareReport
from sift.kernel.jobs import JobQueue, WorkerPool
from sift.kernel.jobs.clock import TaskClock
from sift.kernel.jobs.clock import install as install_task_clock
from sift.kernel.log import (
    LOG_FILENAME,
    apply_log_preferences,
    configure_logging,
    get_logger,
    set_stage_sink,
)
from sift.kernel.public_net import TOOL_PROXY
from sift.kernel.threads import close_serving_pool, close_shared_pool
from sift.kernel.wiring import provide
from sift.slices import (
    auth,
    importing,
    insights,
    loops,
    organize,
    performance,
    photo_sets,
    player,
    semantic,
    settings_hub,
    tasks,
    workbench,
)
from sift.wiring.backups import build_backup, build_libraries
from sift.wiring.built import Diagnostics, Downloads, Storage, Understanding
from sift.wiring.catalog import build_catalog, build_loops
from sift.wiring.catch_up import catch_up
from sift.wiring.diagnostics import start_diagnostics
from sift.wiring.downloads import build_download_handlers, build_downloads
from sift.wiring.file_actions import build_file_actions
from sift.wiring.imports import build_imports
from sift.wiring.machine import build_benchmark, build_machine, build_self_test, probe_again
from sift.wiring.playback import build_playback
from sift.wiring.preferences import build_preferences
from sift.wiring.products import build_products
from sift.wiring.reactions import build_settings_reactions
from sift.wiring.searching import build_search
from sift.wiring.sign_in import build_auth
from sift.wiring.staging import build_capture
from sift.wiring.storage import build_storage, open_the_queue
from sift.wiring.swapping import build_swap
from sift.wiring.tasks import build_tasks
from sift.wiring.understanding import build_understanding
from sift.wiring.watching import start_watching
from sift.wiring.work_ahead import build_work_ahead
from sift.wiring.workers import build_workers

log = get_logger(__name__)


#: Three seconds, the worker pool's beat: a change takes hold while the screen is open.
SETTINGS_APPLY_SECONDS = 3.0

#: With a desktop shell, no job competes with the window's first paint.
FIRST_SCREEN_SECONDS = 5.0
_FIRST_SCREEN_BEAT = 0.05


@contextmanager
def _step(name: str) -> Iterator[None]:
    began = time.perf_counter()
    yield
    log.info("boot.step", step=name, ms=round((time.perf_counter() - began) * 1000))


async def first_screen_or(
    bus: ChangeBus, limit: float, *, beat: float = _FIRST_SCREEN_BEAT
) -> bool:
    """Wait for somebody's first screen or `limit` seconds; True when a screen came first."""
    deadline = time.monotonic() + limit
    while bus.open_connections() == 0:
        left = deadline - time.monotonic()
        if left <= 0:
            return False
        await asyncio.sleep(min(beat, left))
    return True


async def keep_the_settings_applied(
    get_app: Callable[[str], Awaitable[Any]],
    segment_cache: player.SegmentCache,
    stop: asyncio.Event,
    *,
    backups: int = 5,
    interval: float = SETTINGS_APPLY_SECONDS,
) -> None:
    """Push the stored answers onto the running process on a timer; a failed read is skipped."""
    while not stop.is_set():
        with suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)
        if stop.is_set():
            return
        try:
            gigabytes = int(await get_app(player.CACHE_MAX_GB_KEY))
            if segment_cache.resize(gigabytes * 1024**3):
                log.info("player.cache.resized", gigabytes=gigabytes)
            apply_log_preferences(
                detailed=log_settings.detailed_from(await get_app(log_settings.DETAIL_KEY)),
                # The setting is the whole log's size; the handler gets one file's share of it.
                per_file_bytes=log_settings.per_file_bytes(
                    log_settings.keep_bytes_from(await get_app(log_settings.KEEP_MB_KEY)),
                    backups,
                ),
                hide_personal=log_settings.hide_personal_from(
                    await get_app(log_settings.HIDE_PERSONAL_KEY)
                ),
            )
        except (OSError, ValueError, TypeError, sqlite3.Error, DatabaseError):
            log.warning("settings.apply_failed", exc_info=True)


def _quieten_a_reset_at_teardown() -> None:
    """A peer reset during close is the ordinary end on Windows; nothing else is caught."""
    ordinary = "_call_connection_lost"

    def handler(loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        failure = context.get("exception")
        if isinstance(failure, ConnectionResetError) and ordinary in context.get("message", ""):
            return
        loop.default_exception_handler(context)

    asyncio.get_running_loop().set_exception_handler(handler)


async def _end_loop(stop: asyncio.Event, task: asyncio.Task[Any], *, cancel: bool = True) -> None:
    """Ask one of the process's own loops to stop, and wait until it has."""
    stop.set()
    if cancel:
        task.cancel()
    with suppress(asyncio.CancelledError):
        await task


async def _stop_the_watches(watches: Diagnostics) -> None:
    """Stop all four loops before the database closes, or each errors on its next tick."""
    await watches.watchdog.stop()
    await watches.threads.stop()
    await watches.reads.stop()
    await watches.backlog.stop()


def _let_go_of_the_process_wide_parts() -> None:
    """What a step installs for the whole process; safe after a start that failed at any step."""
    # The thread pools go after the database, which steps onto them until it is closed.
    close_serving_pool()
    close_shared_pool()
    TOOL_PROXY.close()
    lanes.install(None)
    landing.install(None)
    set_stage_sink(None)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build the application in order; each part is handed to `teardown` the moment it exists."""
    async with AsyncExitStack() as teardown:
        await start_up(app, teardown)
        with boot_set_aside():
            yield
        log.info("boot.shutdown")


@dataclass(frozen=True)
class _Built:
    """What the features hand on to the steps that start the work."""

    queue: JobQueue
    marks: loops.LoopService
    master_keys: auth.MasterKeyStore
    hub: settings_hub.SettingsService
    downloads: Downloads
    segment_cache: player.SegmentCache
    understanding: Understanding


async def _ground(
    app: FastAPI, teardown: AsyncExitStack
) -> tuple[Settings, HardwareReport, media.Accelerator, Storage]:
    settings: Settings = get_settings()
    configure_logging(
        settings.log_level,
        redact_personal=not settings.log_unredacted,
        log_file=settings.data_dir / LOG_FILENAME,
        max_bytes=settings.log_max_bytes,
        backups=settings.log_backups,
    )
    _quieten_a_reset_at_teardown()
    ensure_directories(settings)
    provide(app, wiring.SETTINGS, settings)

    hardware = await build_machine(app, settings)
    # One for the process, as there is one graphics card: a fault either holder meets, both stop
    # paying.
    accelerator = media.Accelerator(hardware)
    provide(app, wiring.ACCELERATOR, accelerator)
    store = await build_storage(app, settings, hardware, teardown)
    # After everything built below has stopped and before the database closes.
    teardown.push_async_callback(stop_builds)
    return settings, hardware, accelerator, store


async def _build_features(
    app: FastAPI,
    teardown: AsyncExitStack,
    settings: Settings,
    hardware: HardwareReport,
    accelerator: media.Accelerator,
    store: Storage,
) -> _Built:
    # What runs as a file lands in staging (`kernel/landing.py`); with nothing installed, it is
    # inert.
    landing.install(store.database)
    library_service = build_catalog(app, store)
    queue = await open_the_queue(store)
    marks = build_loops(app, store, queue)

    # The unwrapped master keys, in memory while a user is logged in; built before the worker pool.
    master_keys = auth.MasterKeyStore()

    hub = build_preferences(app, store, settings.release_feed_url)
    # Before the import handlers, whose gate reads it per file.
    provide(app, importing.ROOT_PREFS, importing.RootPreferences(store.database))
    downloads = build_downloads(app, store, queue, master_keys)
    # The tunnel clients are child processes; left running they keep their loopback ports.
    teardown.push_async_callback(downloads.tunnels.stop_all)
    # A tunnel program an antivirus took while Sift was not running is in the log from the start.
    await downloads.tunnels.check_client()
    build_imports(app, settings, hardware, store, library_service, hub, queue, accelerator)
    build_download_handlers(app, settings, store, downloads, hub)
    segment_cache = build_playback(app, settings, hardware, queue, accelerator)
    # The record every decision writes into, built before anything that writes one.
    workbench_store = workbench.Store(store.database)
    provide(app, wiring.RECORDER, workbench_store)
    build_file_actions(app, settings, store, queue, hub, workbench_store)
    build_self_test(app, settings, hardware, store, hub)
    understanding = build_understanding(app, settings, hardware, store, hub, workbench_store, queue)
    build_benchmark(app, store, queue, hub, understanding.workbench)
    # A batch rename's receipt is taken back through the organizer built with the file actions.
    understanding.workbench.register_reverser(
        organize.BatchRenameReceipts(
            wiring.part_of_app(app, organize.ORGANIZER),
            touched=wiring.part_of_app(app, wiring.REINDEXER).touched_many,
        )
    )
    products = build_products(app, settings, hardware, store, hub, queue, understanding)
    build_work_ahead(app, store, queue, products, understanding)
    await build_search(app, store, queue)
    return _Built(queue, marks, master_keys, hub, downloads, segment_cache, understanding)


async def _before_the_workers(
    app: FastAPI,
    teardown: AsyncExitStack,
    settings: Settings,
    hardware: HardwareReport,
    accelerator: media.Accelerator,
    store: Storage,
    built: _Built,
) -> tuple[WorkerPool, tasks.TasksService, TaskClock]:
    """Every handler a worker could claim, registered before the pool starts after ready."""
    pool = await build_workers(
        app, store, built.queue, hardware, built.hub, built.master_keys, accelerator
    )
    await build_backup(app, settings, store, built.hub, pool, built.queue)
    await build_libraries(app, settings, store)
    build_capture(app, settings, store, built.queue)
    build_auth(
        app,
        settings,
        hardware,
        store,
        built.queue,
        built.hub,
        built.master_keys,
        built.downloads.tunnels,
    )
    task_service, task_clock = await build_tasks(app, store, built.queue, built.hub)
    teardown.callback(install_task_clock, None)
    await build_swap(
        app, settings, store, built.queue, built.hub, built.downloads, built.understanding
    )
    return pool, task_service, task_clock


async def _after_the_workers(
    app: FastAPI,
    teardown: AsyncExitStack,
    store: Storage,
    built: _Built,
    task_service: tasks.TasksService,
    task_clock: TaskClock,
) -> None:
    watcher = await start_watching(app, store, built.queue, built.hub)
    teardown.push_async_callback(watcher.stop)
    build_settings_reactions(
        app, store, built.queue, built.hub, watcher, built.understanding, task_clock
    )
    staying_awake = asyncio.Event()
    staying_awake_task = asyncio.create_task(
        task_service.keep_awake(staying_awake), name="tasks.keep_awake"
    )
    # Before the workers stop, so the power request is withdrawn on the loop's way out.
    teardown.push_async_callback(_end_loop, staying_awake, staying_awake_task, cancel=False)
    quiet_stop = asyncio.Event()
    quiet_task = asyncio.create_task(
        wiring.part_of_app(app, performance.WHEN_QUIET).keep_looking(quiet_stop),
        name="performance.when_quiet",
    )
    teardown.push_async_callback(_end_loop, quiet_stop, quiet_task)
    watches = start_diagnostics(app, store.database)
    teardown.push_async_callback(_stop_the_watches, watches)


def _listen_for_changes(app: FastAPI, teardown: AsyncExitStack) -> None:
    # Who is connected and what they wait to hear: built last, torn down first.
    bus = ChangeBus()
    provide(app, wiring.CHANGES, bus)
    changes.listens(bus)
    teardown.callback(changes.listens, None)
    # And how an arriving file finds who it could reach, handed down from the permission layer.
    changes.resolves_arrivals(users_that_may_gain)


def _keep_the_database(teardown: AsyncExitStack, store: Storage) -> None:
    # The write-ahead log folded on a timer: a busy pool of readers never lets it start over.
    folding = asyncio.Event()
    folding_task = asyncio.create_task(
        keep_the_log_folded(store.database, folding), name="db.log_keeper"
    )
    teardown.push_async_callback(_end_loop, folding, folding_task)
    # And the planner's table sizes, refreshed daily for an install left open for days.
    statistics_stop = asyncio.Event()
    statistics_task = asyncio.create_task(
        keep_the_statistics_current(store.database, statistics_stop), name="db.statistics_keeper"
    )
    teardown.push_async_callback(_end_loop, statistics_stop, statistics_task)


def _keep_the_days_added_up(teardown: AsyncExitStack, store: Storage, queue: JobQueue) -> None:
    # And each finished day of each User added up for Insights, giving way to pressed work.
    insights_stop = asyncio.Event()
    insights_task = asyncio.create_task(
        insights.keep_the_days_added_up(
            store.database,
            insights_stop,
            somebody_waiting=queue.somebody_waiting,
            # Once a User is up to date: the recaps just closed, then the achievements of the day.
            after_day=(
                lambda user_id, _day: insights.recaps.make_due(
                    store.database, user_id, insights.store.local_today(), content=store.content
                ),
                lambda user_id, day: insights.path.make_due(
                    store.database, user_id, day, content=store.content
                ),
            ),
        ),
        name="insights.rollup",
    )
    teardown.push_async_callback(_end_loop, insights_stop, insights_task)


def _keep_the_settings(teardown: AsyncExitStack, settings: Settings, built: _Built) -> None:
    # Two stored answers pushed onto the process on one timer: cache size and log detail.
    settling = asyncio.Event()
    settling_task = asyncio.create_task(
        keep_the_settings_applied(
            built.hub.get_app, built.segment_cache, settling, backups=settings.log_backups
        ),
        name="settings.applier",
    )
    teardown.push_async_callback(_end_loop, settling, settling_task)


async def start_up(app: FastAPI, teardown: AsyncExitStack) -> None:
    """Build every part in order, handing each one's undoing to `teardown` as it is built."""
    # First in, so last out: after the database, whatever step below opened these.
    teardown.callback(_let_go_of_the_process_wide_parts)
    # The client's file list, walked beside the steps rather than on the first page's request.
    listing = asyncio.create_task(client.files(), name="client.files")
    teardown.push_async_callback(_end_task, listing)
    with _step("ground"):
        settings, hardware, accelerator, store = await _ground(app, teardown)
    with _step("features"):
        built = await _build_features(app, teardown, settings, hardware, accelerator, store)
    with _step("services"):
        pool, task_service, task_clock = await _before_the_workers(
            app, teardown, settings, hardware, accelerator, store, built
        )
    # Handed over before the start, so a failed start still stops the workers it began.
    teardown.push_async_callback(pool.stop)
    with _step("watching"):
        await _after_the_workers(app, teardown, store, built, task_service, task_clock)
    _listen_for_changes(app, teardown)
    # Numeric thread pools are pinned to one thread at import; reported, as it is otherwise unseen.
    log.info(
        "numeric.threads.pinned",
        libraries=len(sift.NUMERIC_THREAD_LIMITS),
        replaced=sorted(sift.NUMERIC_THREADS_REPLACED),
    )
    _keep_the_database(teardown, store)
    _keep_the_days_added_up(teardown, store, built.queue)
    _keep_the_settings(teardown, settings, built)
    # A variable Sift no longer reads, said once at start.
    for name in retired_variables_in_use():
        log.warning("config.retired", variable=name, why=RETIRED_VARIABLES[name])
    await listing
    log.info("boot.ready", port=settings.port)
    resuming = asyncio.create_task(
        _after_ready(
            app,
            settings,
            hardware,
            store,
            built,
            pool,
            hold=FIRST_SCREEN_SECONDS if settings.stop_on_stdin_eof else 0.0,
        ),
        name="boot.after_ready",
    )
    # Before the workers stop, so a shutdown during the hold never starts them on the way out.
    teardown.push_async_callback(_end_task, resuming)


async def _end_task(task: asyncio.Task[Any]) -> None:
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


async def _after_ready(
    app: FastAPI,
    settings: Settings,
    hardware: HardwareReport,
    store: Storage,
    built: _Built,
    pool: WorkerPool,
    *,
    hold: float,
) -> None:
    """What a request never waits for: the owed work, then the workers after the first screen."""
    ready = time.monotonic()
    try:
        with _step("catch_up"):
            await catch_up(
                store.content,
                built.queue,
                built.marks,
                built.downloads.service,
                built.hub,
                settings.data_dir,
                built.understanding.faces,
                wiring.part_of_app(app, player.SEGMENT_CACHE),
                wiring.part_of_app(app, photo_sets.SERVICE),
                wiring.part_of_app(app, semantic.WHOLE_PICTURE),
            )
    except Exception:
        # Said, and the workers start anyway: without them no work at all would run.
        log.exception("boot.catch_up_failed")
    drawn = False
    left = hold - (time.monotonic() - ready)
    if left > 0:
        drawn = await first_screen_or(wiring.part_of_app(app, wiring.CHANGES), left)
    # THE WORKERS START HERE, after every handler is registered: see `build_workers`.
    await pool.start()
    log.info(
        "jobs.resumed",
        after_ready_ms=round((time.monotonic() - ready) * 1000),
        first_screen=drawn,
    )
    try:
        await probe_again(settings, hardware)
    except Exception:
        log.exception("hardware.reprobe_failed")
