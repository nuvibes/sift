# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build the application in order, and take it down in the reverse of that order.

Boot order matters and is deliberate: settings, then directories, then the database, then the
worker pool, then the routes. Nothing accepts a request until everything it depends on is proven to
work: a failure at boot is loud and fixable, and the same failure on the first request is a mystery.

The order is the signatures, not a comment between two statements. Start-up is a run of named
steps, each taking what it needs and handing back what the next ones need. A step that has to come
after another says so by taking its result, so moving a line that must not move stops the build
rather than producing a subtly wrong application. What each step builds is published where it is
built, through the one door in `kernel.wiring`.
"""

from __future__ import annotations

import asyncio

# The NAME only, never a connection. `sqlite3.Error` is what the settings converger catches so
# a failed read is skipped rather than killing the task that carries both settings for the life
# of the process. The rule exists because a connection opened outside the kernel misses the
# pragmas and the single-writer lock; nothing here opens one.
#
# The suppression has to be on the IMPORT LINE: semgrep honours `nosemgrep` on the line it
# flags or the one directly above, and a reason written five lines up is not read at all.
import sqlite3  # nosemgrep: sift-no-database-driver-outside-kernel
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI

import sift

# `log_settings` also declares two settings about the log at import, the way a slice declares its
# own. The logger is configured before there is a database to ask, so these are what it becomes
# afterwards: see `kernel.log.apply_log_preferences`.
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
from sift.kernel.db import DatabaseError, keep_the_log_folded, keep_the_statistics_current
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
from sift.wiring.machine import build_benchmark, build_machine, build_self_test
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


#: How often the two process-wide settings are re-read. Three seconds, the same beat the worker pool
#: converges on, and for the same reason: a change made on a settings screen should take hold while
#: somebody is still looking at the screen, and neither read costs anything worth counting.
SETTINGS_APPLY_SECONDS = 3.0


async def keep_the_settings_applied(
    get_app: Callable[[str], Awaitable[Any]],
    segment_cache: player.SegmentCache,
    stop: asyncio.Event,
    *,
    backups: int = 5,
    interval: float = SETTINGS_APPLY_SECONDS,
) -> None:
    """Push the stored answers onto the running process, on a timer, until told to stop.

    ## Why a timer at all, when everything else reads a setting where it needs it

    Because neither of these can be asked at the moment it matters. The segment cache is capped
    inside the single-slot transcode lock, which is synchronous and cannot await a database read;
    the log is written from every corner of the application, including code that has no idea a
    database exists. Both are objects that hold a number, so the number is pushed to them.

    ## Why the two share a timer and nothing else

    Each step reads its own setting and applies it to its own object. What they share is the beat.
    They are deliberately not folded into the worker pool's own reconfigure poll, which converges on
    a different question and would then be answering two.

    A read that fails is skipped rather than fatal: the process keeps whatever it had, which is the
    boot configuration or the last answer that arrived, and the next beat tries again. A background
    task that dies takes both settings with it for the life of the process. The kernel's own
    refusal is one of the failures: a restore closes the database and opens the restored one, and a
    beat that lands in that window is told the database is not open. Uncaught, a restore under a
    running app would kill this loop for good, and the shutdown that later awaited the dead task
    would raise in its place.
    """
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
                # The setting is what the WHOLE log may take. The handler is given one file's
                # share of it; `log_backups` is what says how many files there will be.
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
    """A connection the other end reset while it was being closed is the ordinary end, not an error.

    Without this, every run logs two tracebacks at ERROR, from asyncio and not from Sift.

    What happens is Windows-shaped. `_ProactorBasePipeTransport._call_connection_lost` shuts the
    socket down after the peer has gone, the shutdown raises `ConnectionResetError` (WinError
    10054), and there is no `await` to receive it, so it reaches the loop's default handler, which
    logs a full traceback at ERROR. A browser tab closing mid-response does it, and so does the
    server being stopped while anything is connected, which is every ordinary shutdown.

    It is the same judgement `_close` in the live slice already makes, one layer down: there is
    nobody left to talk to, and that is not a fault. The difference is that a transport callback has
    no caller to catch it, so the only place to say so is here.

    **Deliberately narrow, and this is the part that matters.** It refuses exactly one exception
    type, and only when the loop's own message names the callback that raises it. Everything else
    goes to the handler that was already there, whatever that is. A blanket handler would have
    swallowed the next real one silently, which is a far worse trade than two ugly lines a run.
    """
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
    """ALL FOUR, before the database closes.

    One left running keeps its task, wakes on its next tick after the database has closed, asks
    for a connection and raises "the database is not open". Nobody retrieves that exception, so
    asyncio prints `Task exception was never retrieved` while the log handler is still installed,
    and the last thing in the log of an orderly shutdown is an error.
    """
    await watches.watchdog.stop()
    await watches.threads.stop()
    await watches.reads.stop()
    await watches.backlog.stop()


def _let_go_of_the_process_wide_parts() -> None:
    """What a step installs for the whole process rather than hands to somebody. Each is a no-op
    for a part that was never opened, so this is safe after a start that failed at any step."""
    # The thread pools go after the database, because the database steps off the loop onto them
    # until it is closed, and a pool that has been shut down does not slow down, it raises.
    close_serving_pool()
    close_shared_pool()
    # The download tools' proxy runs on a thread of its own; a daemon, so this is the orderly end
    # of its listeners rather than a condition of exit.
    TOOL_PROXY.close()
    lanes.install(None)
    landing.install(None)
    set_stage_sink(None)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build the application, in order, and take it down in the reverse of that order.

    Every part that has to be let go of is handed to `teardown` the moment it exists, so a start
    that fails part way lets go of exactly what it had built. The database's connections run on
    threads the interpreter waits for, so a failed start that left them open could never end.
    """
    async with AsyncExitStack() as teardown:
        await start_up(app, teardown)
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
    # ONE of these for the whole process, because there is one graphics card. The preview builder
    # and the player both hold it, so a fault either of them meets is a fault the other stops paying
    # for, and a card that refuses repeatedly is given up on once rather than re-attempted per file
    # for the life of the library. Made here rather than inside either feature for the ordinary
    # reason: this is the only place that knows about both of them.
    accelerator = media.Accelerator(hardware)
    provide(app, wiring.ACCELERATOR, accelerator)
    store = await build_storage(app, settings, hardware, teardown)
    return settings, hardware, accelerator, store


async def _build_features(
    app: FastAPI,
    teardown: AsyncExitStack,
    settings: Settings,
    hardware: HardwareReport,
    accelerator: media.Accelerator,
    store: Storage,
) -> _Built:
    # What runs for a file the moment it lands in staging, while its bytes are still on the local
    # disk: see `kernel/landing.py`. Installed here, once, because the import pipeline is handed
    # a job's context and no database; with nothing installed a landing does nothing, so a build
    # that forgot this line is inert rather than quietly wrong.
    landing.install(store.database)
    library_service = build_catalog(app, store)
    queue = await open_the_queue(store)
    marks = build_loops(app, store, queue)

    # The unwrapped master keys live here, in memory, for as long as a user is logged in. Built
    # before the worker pool because the pool's system-secrets seam reads keys out of it: a
    # background download that needs a saved site login gets an admin's key from this store, or
    # None, which is the signal to wait for a login rather than to fail.
    master_keys = auth.MasterKeyStore()

    hub = build_preferences(app, store, settings.release_feed_url)
    # What each library folder answers differently about the work below. Provided BEFORE the import
    # handlers, because the gate they are built with reads it per file.
    provide(app, importing.ROOT_PREFS, importing.RootPreferences(store.database))
    downloads = build_downloads(app, store, queue, master_keys)
    # The tunnel clients are child processes of this one. Left running they would outlive the
    # server and keep holding their loopback ports, so the next start could not bind them.
    teardown.push_async_callback(downloads.tunnels.stop_all)
    # A tunnel program an antivirus took while Sift was not running is in the log from the start.
    await downloads.tunnels.check_client()
    build_imports(app, settings, hardware, store, library_service, hub, queue, accelerator)
    build_download_handlers(app, settings, store, downloads, hub)
    segment_cache = build_playback(app, settings, hardware, queue, accelerator)
    # The record every decision writes into, built before the first thing that writes one. Two
    # areas take receipts (the workbench's own queues, and duplicate review, which is built with
    # the file actions because it needs the deleter), so it cannot be made inside the workbench
    # and handed round from there.
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
    """Every handler a worker could claim work for, registered before the pool starts."""
    pool = await build_workers(
        app, store, built.queue, hardware, built.hub, built.master_keys, accelerator
    )
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
    # Before the workers stop, so a shutdown never leaves the power request standing. Not
    # cancelled: told to stop, the loop withdraws the request on its way out.
    teardown.push_async_callback(_end_loop, staying_awake, staying_awake_task, cancel=False)
    watches = start_diagnostics(app, store.database)
    teardown.push_async_callback(_stop_the_watches, watches)


def _listen_for_changes(app: FastAPI, teardown: AsyncExitStack) -> None:
    # Who is connected, and what each of them is waiting to be told. Built last of the working
    # parts and torn down first, because everything else is what produces the announcements: a bus
    # listening before there is a database to change would be listening to nothing, and one still
    # listening after the connections have gone would be collecting for nobody.
    #
    # Two steps rather than one, and they are not the same step. Publishing it is how the route
    # that holds a connection open finds it. Telling the module to listen is how nineteen writes
    # spread across the permission layer, the content layer and six features reach it without a
    # live-update parameter on the signature of everything that changes what somebody may see.
    bus = ChangeBus()
    provide(app, wiring.CHANGES, bus)
    changes.listens(bus)
    # Before everything built ahead of it goes, so nothing shutting down announces into a bus
    # whose connections have gone.
    teardown.callback(changes.listens, None)
    # And how a file arriving works out who it could reach. Handed down rather than imported,
    # because the answer is read off the grant table, which only the permission layer may name,
    # and that layer is built on top of the content layer the announcement comes from.
    changes.resolves_arrivals(users_that_may_gain)


def _keep_the_database(teardown: AsyncExitStack, store: Storage) -> None:
    # The write-ahead log folds back into the database on a timer. SQLite does the copying itself,
    # but the step that lets it start the log over needs a moment with no reader in it, and a pool
    # of readers under continuous background work never has one, so on a busy install the log
    # only grows, to nearly the size of the database.
    folding = asyncio.Event()
    folding_task = asyncio.create_task(
        keep_the_log_folded(store.database, folding), name="db.log_keeper"
    )
    teardown.push_async_callback(_end_loop, folding, folding_task)
    # And the other thing about the database that only gets worse while nothing asks: what the
    # query planner believes about the size of each table. The boot has just refreshed it and the
    # end of every whole-library pass refreshes it again; this is the outer bound, for the install
    # left open for days with somebody filing things by hand and no pass ever finishing.
    #
    # Its own timer rather than a step on the folding one: they are a day and five minutes apart,
    # and folding a log every day or re-analyzing every five minutes would each be wrong.
    statistics_stop = asyncio.Event()
    statistics_task = asyncio.create_task(
        keep_the_statistics_current(store.database, statistics_stop), name="db.statistics_keeper"
    )
    teardown.push_async_callback(_end_loop, statistics_stop, statistics_task)


def _keep_the_days_added_up(teardown: AsyncExitStack, store: Storage, queue: JobQueue) -> None:
    # And the third thing nobody chooses a time for: each finished day of each User added up for
    # Insights, a piece at a time. A loop of the process like the two above, not a task: it has no
    # When anybody would set, and it takes no worker. Its adding-up gives way while work somebody
    # pressed is queued (asked on every piece rather than once); its re-split does not, because a
    # stale split is paid for by every reader (see `insights.rollup`).
    insights_stop = asyncio.Event()
    insights_task = asyncio.create_task(
        insights.keep_the_days_added_up(
            store.database,
            insights_stop,
            somebody_waiting=queue.somebody_waiting,
            # Once a User is up to date: the recaps of the periods just closed (judged against
            # the real today, which is what "just closed" means), then the achievements reached
            # by the day just added up. Each is called on its own; see `rollup.add_up_one_day`.
            after_day=(
                lambda user_id, _day: insights.recaps.make_due(
                    store.database, user_id, insights.store.local_today()
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
    # Two stored answers that are applied to the running PROCESS rather than read when they are
    # needed: how much disk the converted copies may take, and what the log records. Neither can be
    # asked at the moment it matters (the cache is capped inside a lock that cannot await, and a
    # log line is written from anywhere at all), so both are pushed onto their object on a timer.
    #
    # ONE TIMER, TWO ANSWERS, AND THEY SHARE NOTHING ELSE. Each step reads its own setting and
    # applies it to its own thing; what is shared is the beat, not the question. The worker pool
    # converges on the same principle (`build_workers`), and this is deliberately not folded into
    # that one: the pool's poll is about the pool.
    settling = asyncio.Event()
    settling_task = asyncio.create_task(
        keep_the_settings_applied(
            built.hub.get_app, built.segment_cache, settling, backups=settings.log_backups
        ),
        name="settings.applier",
    )
    teardown.push_async_callback(_end_loop, settling, settling_task)


async def start_up(app: FastAPI, teardown: AsyncExitStack) -> None:
    """Build every part in order, handing each one's undoing to `teardown` as it is built.

    Each step takes what it needs from the ones before it. Nothing here reads a part back off the
    application: what a step needs, it is handed. `teardown` undoes in the reverse of the order it
    was handed things, so a part is let go of before anything it was built on.
    """
    # First in, so last out: after the database, whatever step below opened these.
    teardown.callback(_let_go_of_the_process_wide_parts)
    settings, hardware, accelerator, store = await _ground(app, teardown)
    built = await _build_features(app, teardown, settings, hardware, accelerator, store)
    pool, task_service, task_clock = await _before_the_workers(
        app, teardown, settings, hardware, accelerator, store, built
    )
    # Handed over before the start, so a start that fails half way still stops the workers it
    # began. They stop before the database they write to closes.
    teardown.push_async_callback(pool.stop)
    # THE WORKERS START HERE, after every handler above is registered: see `build_workers`.
    await pool.start()
    await _after_the_workers(app, teardown, store, built, task_service, task_clock)
    _listen_for_changes(app, teardown)
    # The numeric thread pools are held to one thread at package import, because a process
    # holding one cannot reliably start another program. Reported rather than merely done: it is
    # invisible otherwise, and a value found already set is replaced rather than obeyed.
    log.info(
        "numeric.threads.pinned",
        libraries=len(sift.NUMERIC_THREAD_LIMITS),
        replaced=sorted(sift.NUMERIC_THREADS_REPLACED),
    )
    _keep_the_database(teardown, store)
    _keep_the_days_added_up(teardown, store, built.queue)
    _keep_the_settings(teardown, settings, built)
    # A variable somebody set that Sift no longer reads. Said once, at the one moment the person
    # who wrote it is looking, because the alternative is a line in a config file that is obeyed by
    # nothing and reports nothing.
    for name in retired_variables_in_use():
        log.warning("config.retired", variable=name, why=RETIRED_VARIABLES[name])
    log.info("boot.ready", port=settings.port)
