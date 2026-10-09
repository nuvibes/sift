# SPDX-License-Identifier: AGPL-3.0-or-later
"""Describing the library, as background jobs: one file, the tile pass, and the model download."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from contextlib import suppress

from sift.kernel.jobs import JobContext, backs_off, register_handler
from sift.kernel.jobs.families import Family
from sift.kernel.ledger import Actor
from sift.kernel.log import get_logger
from sift.slices.semantic.service import SemanticService
from sift.slices.semantic.whole_picture import WholePicture

log = get_logger(__name__)

SEMANTIC_DESCRIBE = "semantic_describe"
SEMANTIC_FETCH_MODELS = "semantic_fetch_models"
SEMANTIC_FORGET = "semantic_forget"
#: The tile pass: HEIF stills described from one tile, described again from the whole picture.
SEMANTIC_WHOLE_PICTURE = "semantic_whole_picture"

#: Once a second rather than once a chunk, so the write lock is not held for the download.
_PROGRESS_TICK = 1.0


async def describe(
    context: JobContext, *, service: SemanticService, settles_into: Sequence[str] = ()
) -> None:
    """Describe one file, then ask for the passes that read descriptions (`settles_into`)."""
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
    """Describe again, from the whole picture, every HEIF still described from one tile."""
    if not await service.enabled():
        log.info("semantic.job.skipped", job=SEMANTIC_WHOLE_PICTURE, reason="switched off")
        return
    after = ""
    queued = 0
    while True:
        page, after = await tiles.read_from_a_tile(after=after)
        for asset_id in page:
            await tiles.take_back(asset_id)
            # The file alone, so a start finding the same files collapses onto the queued jobs.
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
    """Download the models; the callback only records, and a ticker reports and cancels."""
    if not await service.enabled():
        log.info("semantic.job.skipped", job=SEMANTIC_FETCH_MODELS, reason="switched off")
        return

    latest = [0, 0]
    # Which file of the set is being fetched, so three bars in a row do not read as retries.
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
                # Across the whole set, so the bar only ever goes forward.
                within = min(written / total, 1.0) if total else 0.0
                await context.set_progress(min((index + within) / of, 1.0))
                await context.set_note(f"{index + 1} of {of} - {role}")
            elif total > 0:
                await context.set_progress(min(written / total, 1.0))
            try:
                await context.raise_if_canceled()
            except BaseException:
                # A flag, not a cancel: a partial file resumes, where a torn write could not be
                # trusted.
                stop = True
                raise

    ticker = asyncio.create_task(report())
    try:
        # `again` is the repair path for a file that exists but refuses to load.
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
        alone=True,
    )
    backs_off(SEMANTIC_FETCH_MODELS)
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
