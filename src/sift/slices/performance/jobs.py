# SPDX-License-Identifier: AGPL-3.0-or-later
"""The one thing this slice does in the background: fetch the graphics-card runtime."""

from __future__ import annotations

import asyncio
from contextlib import suppress

from sift.kernel.config import Settings
from sift.kernel.jobs import JobContext, backs_off, register_handler
from sift.kernel.log import get_logger
from sift.kernel.ml import accel

log = get_logger(__name__)

ACCEL_INSTALL = "accel_install"

#: Rare enough that a gigabyte of chunks is not a gigabyte of database writes.
_PROGRESS_TICK = 0.5


async def install_accelerator(context: JobContext, *, settings: Settings) -> None:
    """Fetch and unpack the card's runtime; the callback records, a ticker reports and cancels."""
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
                # A flag rather than a teardown, so the next attempt resumes the partial file.
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
    # Proved here, while somebody is still looking at the screen that downloaded it.
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
