# SPDX-License-Identifier: AGPL-3.0-or-later
"""The watches on every way the application can stop being usable."""

from __future__ import annotations

from fastapi import FastAPI

from sift.kernel import lanes, wiring
from sift.kernel.db import Database
from sift.kernel.diagnostics import (
    LoopBacklogWatch,
    LoopWatchdog,
    ReadPoolWatch,
    SlowestWork,
    ThreadPoolWatch,
    WidestReads,
    install_stack_dumper,
)
from sift.kernel.lanes import StorageLanes
from sift.kernel.log import set_loop_backlog, set_rows_sink, set_work_sink
from sift.kernel.threads import open_serving_pool
from sift.kernel.wiring import provide
from sift.wiring.built import Diagnostics


def start_diagnostics(app: FastAPI, database: Database) -> Diagnostics:
    """The watches on a held loop and a full thread pool: a frozen loop logs nothing by itself."""
    watchdog = LoopWatchdog()
    watchdog.start()
    threads = ThreadPoolWatch()
    threads.start()
    # A full connection pool, which both watches above read as healthy.
    reads = ReadPoolWatch(database.read, holding=lambda: database.sweeping)
    reads.start()
    # A loop buried in prompt small work, which waits on nothing the watches above can see.
    backlog = LoopBacklogWatch()
    backlog.start()
    # What the time went on: thousands of small costs look innocent one line at a time.
    slowest = SlowestWork()
    # How much moved, not how slow: true before anyone notices.
    widest = WidestReads()
    set_loop_backlog(lambda: backlog.latest_seconds)
    set_work_sink(slowest.record)
    set_rows_sink(widest.record)
    # The storage a file is read from: a share that stops coping reads healthy everywhere else.
    storage_lanes = StorageLanes()
    lanes.install(storage_lanes)
    provide(app, wiring.LANES, storage_lanes)
    provide(app, wiring.WATCHDOG, watchdog)
    provide(app, wiring.THREADS, threads)
    provide(app, wiring.READS, reads)
    provide(app, wiring.BACKLOG, backlog)
    provide(app, wiring.SLOWEST, slowest)
    provide(app, wiring.WIDEST, widest)
    # Threads a sweep cannot reach for the reads behind a video, opened before any request.
    open_serving_pool()
    install_stack_dumper()
    return Diagnostics(
        watchdog=watchdog,
        threads=threads,
        reads=reads,
        backlog=backlog,
        slowest=slowest,
        widest=widest,
    )
