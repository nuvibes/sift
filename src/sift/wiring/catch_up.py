# SPDX-License-Identifier: AGPL-3.0-or-later
"""Work a start owes the library that no scan can do."""

from __future__ import annotations

import asyncio
from pathlib import Path

from sift.kernel import sampling as sampler
from sift.kernel.content import ContentStore
from sift.kernel.jobs import BACKGROUND_PRIORITY, JobQueue
from sift.kernel.log import get_logger
from sift.kernel.version import app_version
from sift.slices import (
    capture,
    download,
    faces,
    loops,
    media_jobs,
    performance,
    photo_sets,
    player,
    semantic,
    settings_hub,
)

log = get_logger(__name__)


async def _what_a_stop_left(fetches: download.DownloadService, data_dir: Path) -> None:
    # Downloads whose worker was killed mid-run: only this moment can settle their rows.
    settled = await fetches.settle_orphans()
    if settled:
        log.info("download.interrupted_by_a_restart", how_many=settled)

    # Staged imports whose job is long gone: nothing can ask for their bytes any more.
    swept = await asyncio.to_thread(capture.sweep_staging, data_dir)
    if swept:
        log.info("capture.staging_swept", removed=swept)


async def _files_owed_a_read(content: ContentStore, queue: JobQueue) -> None:
    # Files still on the whole-file digest, brought forward to the sampled identity.
    if await content.legacy_identities_remain():
        await queue.enqueue_when_settled(media_jobs.REIDENTIFY, priority=BACKGROUND_PRIORITY)

    # Files a cut-off scan took in and never read, read to the scan's own floor now.
    if await content.any_unread():
        await queue.enqueue_when_settled(media_jobs.READ_UNREAD, priority=BACKGROUND_PRIORITY)


async def _recognition_owed(
    queue: JobQueue,
    hub: settings_hub.SettingsService,
    recognition: faces.FaceService,
    tiles: semantic.WholePicture,
) -> None:
    # Faces described by another model, measured again from their stored pictures.
    if await hub.get_app(faces.ENABLED_KEY) and await recognition.measured_by_another_model():
        await queue.enqueue_when_settled(faces.FACE_REMEASURE, priority=BACKGROUND_PRIORITY)

    # Faces a scan refused for size that the floor now accepts; the pass ends itself.
    if await hub.get_app(faces.ENABLED_KEY) and await recognition.floor_pass_owed():
        await queue.enqueue_when_settled(faces.FACE_FLOOR, priority=BACKGROUND_PRIORITY)

    # The one face in a file a stash-box put somebody on, where nothing has asked about it.
    if await hub.get_app(faces.ENABLED_KEY) and await recognition.box_questions_owed():
        await queue.enqueue_when_settled(faces.FACE_BOX_QUESTIONS, priority=BACKGROUND_PRIORITY)

    # HEIF faces read from one tile, looked at again from the whole picture.
    if await hub.get_app(faces.ENABLED_KEY) and await recognition.tile_pass_owed():
        await queue.enqueue_when_settled(faces.FACE_WHOLE_PICTURE, priority=BACKGROUND_PRIORITY)

    # The same for Smart Search descriptions of HEIF photographs.
    if await hub.get_app(semantic.ENABLED_KEY) and await tiles.tile_pass_owed():
        await queue.enqueue_when_settled(
            semantic.SEMANTIC_WHOLE_PICTURE, priority=BACKGROUND_PRIORITY
        )


async def _rows_owed(
    content: ContentStore,
    queue: JobQueue,
    hub: settings_hub.SettingsService,
    sets: photo_sets.PhotoSetService,
    marks: loops.LoopService,
) -> None:
    # Files whose stream rows are missing or from an older reading, probed again.
    if await content.assets_lacking_probe_rows(1):
        await queue.enqueue_when_settled(media_jobs.KEEP_PROBES, priority=BACKGROUND_PRIORITY)

    # Sets Sift made under a raised floor, dissolved: nothing else looks back at a set.
    if await sets.under_floor(photo_sets.MIN_PICTURES):
        await queue.enqueue_when_settled(
            photo_sets.DISSOLVE_UNDER_FLOOR, priority=BACKGROUND_PRIORITY
        )

    # Files an older ingress classifier typed (see `ingress.CLASSIFIER_VERSION`).
    if await content.any_unclassified():
        await queue.enqueue_when_settled(media_jobs.RECLASSIFY, priority=BACKGROUND_PRIORITY)

    # Hover clips built to another shape, rebuilt; asked here since it reads the whole table.
    shape = sampler.preview_shape(str(await hub.get_app(performance.PREVIEW_SHAPE_KEY)))
    if await content.previews_of_another_recipe_count(media_jobs.preview_recipe(shape)):
        await queue.enqueue_when_settled(media_jobs.REBUILD_PREVIEWS, priority=BACKGROUND_PRIORITY)

    # Stills for marks saved before marks had their own picture, as one sweep job.
    if await marks.marks_without_still(1):
        await queue.enqueue_when_settled(loops.LOOP_STILLS, priority=BACKGROUND_PRIORITY)


async def catch_up(
    content: ContentStore,
    queue: JobQueue,
    marks: loops.LoopService,
    fetches: download.DownloadService,
    hub: settings_hub.SettingsService,
    data_dir: Path,
    recognition: faces.FaceService,
    segments: player.SegmentCache,
    sets: photo_sets.PhotoSetService,
    tiles: semantic.WholePicture,
) -> None:
    """Work a start owes the library that no scan can do, each queued only when owed."""
    await _what_a_stop_left(fetches, data_dir)
    # The defaults this release's update changed, said once on History for whoever never chose.
    await hub.tell_changed_defaults(app_version())
    await _files_owed_a_read(content, queue)
    await _recognition_owed(queue, hub, recognition, tiles)
    # What the segment cache wrote before this boot, trimmed under its cap, off the loop.
    await asyncio.to_thread(segments.reload)

    await _rows_owed(content, queue, hub, sets, marks)
