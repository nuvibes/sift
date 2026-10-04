# SPDX-License-Identifier: AGPL-3.0-or-later
"""Describing the library, as background jobs.

Three of them, separate because they cost wildly different amounts and are asked for by different
things.

**Describing one file** is the expensive one: it reads the file. It runs at background priority, so
whoever is watching something right now always wins.

**There is no sweep here.** The WALK is not this feature's: what "describe my library" means for
somebody who has just switched this on is a Generate run, which reads each file once for every
product that is missing from it and queues `describe` among them; a second walk of the same
library, on its own schedule, would read every file twice. (A job that ran until the library was
done would also hold a worker for hours and report no progress anybody could read.) What is left
here is the per-file work and the model download.

**Fetching the models** is the odd one out: the only job here that touches the network, and the one
thing that has to happen before either of the others can do anything. A job rather than a request
because it takes minutes, because the dashboard already draws a bar for every job, and because
cancelling a job is already something somebody can do.

Every one of them checks the switch first and does nothing at all when it is off. A job queued
before somebody switched the feature off finds it off and stops.

**The job types are declared here**, in the feature that owns them, rather than added to a shared
list, the same way recognition does it. A central register of everything every feature can queue
is a file every feature has to edit, and it is how two features end up knowing about each other.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from contextlib import suppress

from sift.kernel.jobs import JobContext, register_handler
from sift.kernel.jobs.families import Family
from sift.kernel.ledger import Actor
from sift.kernel.log import get_logger
from sift.slices.semantic.service import SemanticService
from sift.slices.semantic.whole_picture import WholePicture

log = get_logger(__name__)

SEMANTIC_DESCRIBE = "semantic_describe"
SEMANTIC_FETCH_MODELS = "semantic_fetch_models"
#: Delete index, a batch per write, so nothing else waits on it.
SEMANTIC_FORGET = "semantic_forget"
#: The tile pass: every HEIF still whose description was read from one tile of it, described again
#: from the whole picture. Asked for by a start while any wait (`WholePicture.tile_pass_owed`).
SEMANTIC_WHOLE_PICTURE = "semantic_whole_picture"

#: How often a download's progress is published, in seconds.
#:
#: Once a second rather than once a chunk. A chunk is a fraction of a megabyte, so a write per
#: chunk would hold the write lock for the length of a large model's download, competing with
#: exactly the work somebody started this to be able to do.
_PROGRESS_TICK = 1.0


async def describe(
    context: JobContext, *, service: SemanticService, settles_into: Sequence[str] = ()
) -> None:
    """Describe one file, and ask for the whole-library passes that read descriptions once it
    has written some (`settles_into`), the way every other file's work asks after itself."""
    if not await service.enabled():
        log.info("semantic.job.skipped", job=SEMANTIC_DESCRIBE, reason="switched off")
        return

    asset_id = str(context.payload["asset_id"])
    frames = await service.describe_asset(asset_id)
    if frames:
        await context.queue.settle_into(settles_into)
    await context.set_progress(1.0)
    log.info("semantic.described", asset_id=asset_id, frames=frames)


async def tile_pass(context: JobContext, *, service: SemanticService, tiles: WholePicture) -> None:
    """Describe again, from the whole picture, every HEIF still whose description came from one
    tile.

    Each description is taken back first and the file handed to the describing job, the one every
    arriving file goes through, so the tile's description goes and the whole picture's comes. A
    file described again is newer than its copy and leaves the list, so the pass ends
    (`WholePicture.read_from_a_tile`). Sift's own act: nobody pressed it, and its run is Sift's on
    History.
    """
    if not await service.enabled():
        log.info("semantic.job.skipped", job=SEMANTIC_WHOLE_PICTURE, reason="switched off")
        return
    after = ""
    queued = 0
    while True:
        page, after = await tiles.read_from_a_tile(after=after)
        for asset_id in page:
            await tiles.take_back(asset_id)
            # The file alone, as an arriving file's description is asked for, so a start that
            # finds the same files still waiting collapses onto the ones already queued for them.
            await context.enqueue_child(SEMANTIC_DESCRIBE, {"asset_id": asset_id}, dedupe=True)
        queued += len(page)
        if not after:
            break
    await context.set_progress(1.0)
    photos = "photo" if queued == 1 else "photos"
    await context.set_note(
        f"{queued:,} {photos} queued to describe again from the whole picture."
        if queued
        else "Every photo was already described from the whole picture."
    )
    log.info("semantic.tile_pass.queued", files=queued)


async def fetch_models(context: JobContext, *, service: SemanticService) -> None:
    """Download the models this install is set to use.

    The awkward part is the seam between the two halves and it is worth naming. Reporting progress
    is asynchronous (it writes to the queue) and the callback the transfer offers is an
    ordinary function called once per chunk, which cannot wait for anything. So the callback does
    the only two things it can do without waiting: it writes the latest count into a variable, and
    it reads a flag saying whether to stop. A ticker beside the transfer turns those into a
    progress row and a cancellation, on its own schedule rather than on the network's.
    """
    if not await service.enabled():
        log.info("semantic.job.skipped", job=SEMANTIC_FETCH_MODELS, reason="switched off")
        return

    latest = [0, 0]
    # Which file is being fetched, how many there are, and what it is for. A working set is three
    # separate downloads of very different sizes, so the fraction below runs to one and back to zero
    # three times, which without this reads as a download that keeps failing and starting again.
    at = {"index": 0, "of": 0}
    role = ""
    stop = False

    def note(written: int, total: int) -> bool:
        latest[0], latest[1] = written, total
        return not stop

    def starting(index: int, of: int, what: str) -> None:
        nonlocal role
        at["index"], at["of"] = index, of
        role = what
        latest[0] = latest[1] = 0

    async def report() -> None:
        nonlocal stop
        while True:
            await asyncio.sleep(_PROGRESS_TICK)
            written, total = latest
            index, of = at["index"], at["of"]
            if of:
                # Across the whole set rather than within one file, so the bar only ever goes
                # forward. Each file counts for an equal share, which is wrong about the bytes and
                # right about what a bar is for: they are named beside it, so the share being even
                # is visible rather than a claim about size.
                within = min(written / total, 1.0) if total else 0.0
                await context.set_progress(min((index + within) / of, 1.0))
                await context.set_note(f"{index + 1} of {of} - {role}")
            elif total > 0:
                await context.set_progress(min(written / total, 1.0))
            try:
                await context.raise_if_canceled()
            except BaseException:
                # Setting the flag rather than cancelling the transfer: the reader stops asking for
                # the next chunk and leaves a partial file behind, so the next attempt resumes from
                # where this one stopped. Tearing the task down mid-write would leave a file whose
                # length nobody can trust.
                stop = True
                raise

    ticker = asyncio.create_task(report())
    try:
        # `again` is the repair path. A model file that exists but is not the one Sift expects
        # refuses to load and says so; without this the app has no way to act on that.
        again = bool(context.payload.get("again"))
        installed = await service.install_models(progress=note, force=again, on_file=starting)
    finally:
        ticker.cancel()
        with suppress(asyncio.CancelledError):
            await ticker

    await context.set_progress(1.0)
    log.info("semantic.models.job_finished", installed=len(installed))


def register_handlers(
    *, service: SemanticService, tiles: WholePicture, settles_into: Sequence[str] = ()
) -> None:
    register_handler(
        SEMANTIC_DESCRIBE,
        lambda context: describe(context, service=service, settles_into=settles_into),
        name="Generating Smart Search description",
        family=Family.SEMANTIC,
        counts="files described for Smart Search",
        by_itself=True,
    )
    register_handler(
        SEMANTIC_WHOLE_PICTURE,
        lambda context: tile_pass(context, service=service, tiles=tiles),
        name="Looking again at descriptions in photos read from one tile",
        family=Family.SEMANTIC,
        # One at a time: two copies would queue the same files twice.
        alone=True,
    )
    register_handler(
        SEMANTIC_FETCH_MODELS,
        lambda context: fetch_models(context, service=service),
        name="Downloading Smart Search model",
    )
    register_handler(
        SEMANTIC_FORGET,
        lambda context: forget(context, service=service),
        name="Deleting the Smart Search index",
        alone=True,
    )


async def forget(context: JobContext, *, service: SemanticService) -> None:
    """Delete the index for whoever pressed it. See `SemanticService.clear_index`."""
    await service.clear_index(Actor.user(context.pressed_by) if context.pressed_by else None)
    await context.set_progress(1.0)
