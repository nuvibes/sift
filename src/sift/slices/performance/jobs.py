# SPDX-License-Identifier: AGPL-3.0-or-later
"""The one thing this slice does in the background: fetch the graphics-card runtime.

Queued rather than done in a request: it is about 1.3 GB, and a request held open that long times
out somewhere between the browser and here. As an ordinary job it reports progress, can be stopped
from the same control, and a stopped download resumes from the packages already fetched.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress

from sift.kernel.config import Settings
from sift.kernel.jobs import JobContext, backs_off, register_handler
from sift.kernel.log import get_logger
from sift.kernel.ml import accel

log = get_logger(__name__)

#: The kind, as the queue knows it.
ACCEL_INSTALL = "accel_install"

#: How often progress is published, in seconds. Often enough to look alive, rare enough that a
#: gigabyte of chunks does not become a gigabyte of database writes.
_PROGRESS_TICK = 0.5


async def install_accelerator(context: JobContext, *, settings: Settings) -> None:
    """Fetch and unpack the card's runtime, reporting as it goes.

    The seam between the two halves is the same one the model download has, and it is worth naming
    twice. Reporting progress is asynchronous (it writes to the queue) and the callback the
    transfer offers is an ordinary function called once per chunk that cannot wait for anything. So
    the callback does the only two things it can do without waiting: it writes the latest count into
    a variable and reads a flag saying whether to stop. A ticker beside the transfer turns those into
    a progress row and a cancellation, on its own schedule rather than on the network's.
    """
    latest = [0, accel.TOTAL_BYTES]
    stop = False

    def note(received: int, total: int) -> bool:
        latest[0], latest[1] = received, total
        return not stop

    async def report() -> None:
        nonlocal stop
        while True:
            await asyncio.sleep(_PROGRESS_TICK)
            received, total = latest
            if total > 0:
                await context.set_progress(min(received / total, 1.0))
                await context.set_note(f"{received // 1_000_000} MB of {total // 1_000_000} MB")
            try:
                await context.raise_if_canceled()
            except BaseException:
                # Setting the flag rather than tearing the transfer down: the reader stops asking
                # for the next chunk and leaves a partial file behind, so the next attempt resumes
                # from where this one stopped.
                stop = True
                raise

    ticker = asyncio.create_task(report())
    try:
        finished = await accel.install(settings, progress=note)
    finally:
        ticker.cancel()
        with suppress(asyncio.CancelledError):
            await ticker

    if not finished:
        log.info("performance.accel.stopped")
        return
    await context.set_progress(1.0)
    # Proved, not assumed, and proved HERE rather than leaving it to the first real use. A card that
    # cannot run a model is a fact somebody wants while they are still looking at the screen that
    # downloaded it, not hours later, in a job that quietly failed.
    problem = await accel.works(settings)
    if problem is not None:
        await context.set_note(problem)
        log.warning("performance.accel.installed_but_unusable", detail=problem)
        return
    log.info("performance.accel.ready")


def register_handlers(*, settings: Settings) -> None:
    register_handler(
        ACCEL_INSTALL,
        lambda context: install_accelerator(context, settings=settings),
        name="Downloading GPU support",
    )
    backs_off(ACCEL_INSTALL)
