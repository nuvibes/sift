# SPDX-License-Identifier: AGPL-3.0-or-later
"""The watermark jobs: read one file, and fetch the models; both do nothing while switched off."""

from __future__ import annotations

import asyncio
from contextlib import suppress

from sift.kernel.jobs import JobContext, register_handler
from sift.kernel.jobs.families import Family
from sift.kernel.log import get_logger
from sift.slices.watermarks.service import WatermarkService

log = get_logger(__name__)

WATERMARK_READ = "watermark_read"
WATERMARK_FETCH_MODELS = "watermark_fetch_models"

#: Per second, not per chunk, which would hold the write lock for the whole download.
_PROGRESS_TICK = 1.0


async def read_one(context: JobContext, *, service: WatermarkService) -> None:
    if not await service.enabled():
        log.info("watermarks.job.skipped", job=WATERMARK_READ, reason="switched off")
        return
    asset_id = str(context.payload["asset_id"])
    await service.read_asset(asset_id)
    await context.set_progress(1.0)


async def fetch_models(context: JobContext, *, service: WatermarkService) -> None:
    """Download the models; a ticker turns the callback counts into progress and cancellation."""
    if not await service.enabled():
        log.info("watermarks.job.skipped", job=WATERMARK_FETCH_MODELS, reason="switched off")
        return

    latest = [0, 0]
    stop = False

    def note(written: int, total: int) -> bool:
        latest[0], latest[1] = written, total
        return not stop

    async def report() -> None:
        nonlocal stop
        while True:
            await asyncio.sleep(_PROGRESS_TICK)
            written, total = latest
            if total > 0:
                await context.set_progress(min(written / total, 1.0))
            try:
                await context.raise_if_canceled()
            except BaseException:
                # A flag, not a cancel, so the partial file is resumed next time.
                stop = True
                raise

    ticker = asyncio.create_task(report())
    try:
        again = bool(context.payload.get("again"))
        installed = await service.install_models(progress=note, force=again)
    finally:
        ticker.cancel()
        with suppress(asyncio.CancelledError):
            await ticker

    await context.set_progress(1.0)
    log.info("watermarks.models.job_finished", installed=len(installed))


def register_handlers(*, service: WatermarkService) -> None:
    register_handler(
        WATERMARK_READ,
        lambda context: read_one(context, service=service),
        name="Reading a watermark",
        # One Identify row with the face pass, with its own count line.
        family=Family.IDENTIFY,
        counts="files read for watermarks",
        by_itself=True,
    )
    register_handler(
        WATERMARK_FETCH_MODELS,
        lambda context: fetch_models(context, service=service),
        name="Downloading the watermark models",
        alone=True,
    )
