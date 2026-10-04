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
    """The one failure that leaves no evidence.

    Sift is a single loop, so anything holding it freezes the whole application, and a frozen loop
    logs nothing, which looks exactly like a quiet patch. The watchdog watches from outside the loop
    and takes a stack by itself, so the next one is diagnosable without anybody happening to be
    looking. It costs one wake-up a second.

    The thread watch is the other half of the same question, and the one the watchdog is blind to.
    Work is kept off the loop by handing it to a thread, and there is a finite number of threads to
    hand it to, so a full pool is a real way for the application to stop working, and it presents
    as the opposite of a held loop: the interface stays responsive while video stutters.
    """
    watchdog = LoopWatchdog()
    watchdog.start()
    threads = ThreadPoolWatch()
    threads.start()
    # And the third finite thing, which the two above are blind to: when it fills, both of them
    # read healthy, and tell the truth, while every request waits the better part of a minute. It
    # is handed the borrowing itself rather than the database, so it measures the door every
    # request goes through and knows nothing else about it.
    reads = ReadPoolWatch(database.read, holding=lambda: database.sweeping)
    reads.start()
    # And the fourth, which the three above are all blind to in the same way: they measure waiting
    # for a resource, and a loop buried in work that each returns promptly waits for nothing. It
    # can read healthy on every one of them (nothing held, no pool full, no connection queued)
    # while the application is unusable, because a request making hundreds of small round trips is
    # slow without ever queueing.
    backlog = LoopBacklogWatch()
    backlog.start()
    # And the other half of every measurement here: what the time actually went on. A thing costing
    # four milliseconds that runs two thousand times is eight seconds and looks innocent one line
    # at a time, which is precisely the shape none of the watches can see.
    slowest = SlowestWork()
    # And the one reading here that is not a stopwatch. Everything above answers "was it slow",
    # which is only ever true once something else is busy; this answers "how much did it move",
    # which is true before anyone notices and is what the slowness is made of.
    widest = WidestReads()
    set_loop_backlog(lambda: backlog.latest_seconds)
    set_work_sink(slowest.record)
    set_rows_sink(widest.record)
    # And the one resource none of the above can see: the storage a file is read from. A network
    # share that has stopped coping reads as a healthy loop, a healthy pool and healthy readers,
    # with every job slow. Every read of a library file takes a place in its storage's lane; the
    # lanes are installed here so a read anywhere in the process goes through them.
    storage_lanes = StorageLanes()
    lanes.install(storage_lanes)
    provide(app, wiring.LANES, storage_lanes)
    provide(app, wiring.WATCHDOG, watchdog)
    provide(app, wiring.THREADS, threads)
    provide(app, wiring.READS, reads)
    provide(app, wiring.BACKLOG, backlog)
    provide(app, wiring.SLOWEST, slowest)
    provide(app, wiring.WIDEST, widest)
    # And the answer to it, for the one path where a queue is not merely slower but looks like a
    # bug: the reads behind a video get threads a sweep has no way to reach. Opened before anything
    # can serve a request, so no request ever falls back to the shared pool.
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
