# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading the library's watermarks, as background jobs.

Two of them, separate because they cost wildly different amounts and are asked for by different
things.

**Reading one file** is the one that costs: it opens the file. It runs at background priority, so
whoever is watching something right now always wins.

**There is no sweep here.** The WALK is not this feature's: what "read the library" means is the
Build's Identify run for this one product, which reads each file once for everything it lacks. A
second walk of the same library, on its own schedule, would read every file twice. The watermark
task's Run now is that run; what is left here is the per-file work and the model download.

**Fetching the models** is the odd one out: the only job here that touches the network, and the one
thing that has to happen before the other can do anything.

Both check the switch first and do nothing at all when it is off. A job queued before somebody
switched the feature off finds it off and stops.
"""

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

#: How often a download's progress is published, in seconds. Once a second rather than once a
#: chunk: a write per chunk would hold the write lock for the length of the download.
_PROGRESS_TICK = 1.0


async def read_one(context: JobContext, *, service: WatermarkService) -> None:
    """Read one file's watermark."""
    if not await service.enabled():
        log.info("watermarks.job.skipped", job=WATERMARK_READ, reason="switched off")
        return
    asset_id = str(context.payload["asset_id"])
    await service.read_asset(asset_id)
    await context.set_progress(1.0)


async def fetch_models(context: JobContext, *, service: WatermarkService) -> None:
    """Download the models this install needs.

    The seam between the two halves is the awkward part and it is worth naming. Reporting progress
    is asynchronous (it writes to the queue) and the callback the transfer offers is an
    ordinary function called once per chunk, which cannot wait for anything. So the callback does
    the only two things it can do without waiting: it writes the latest count into a variable, and
    it reads a flag saying whether to stop. A ticker beside the transfer turns those into a
    progress row and a cancellation, on its own schedule rather than on the network's.
    """
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
                # Setting the flag rather than cancelling the transfer: the reader stops asking for
                # the next chunk and leaves a partial file behind, so the next attempt resumes from
                # where this one stopped.
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
        # The same family the face pass is in. What both answer is "who is this file from", which
        # is one thing a person waits for and should be one ROW on the screen rather than two:
        # one estimate, one press. Its count is its own line inside that row, though: one figure
        # over both passes would count the library twice and describe neither.
        family=Family.IDENTIFY,
        counts="files read for watermarks",
        by_itself=True,
    )
    register_handler(
        WATERMARK_FETCH_MODELS,
        lambda context: fetch_models(context, service=service),
        name="Downloading the watermark models",
    )
