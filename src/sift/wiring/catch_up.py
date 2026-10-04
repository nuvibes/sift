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
    # Downloads whose worker was killed while they were running. A killed worker runs no attempt,
    # so nothing else can settle the ledger row, and it would read as "1 downloading" for ever.
    # Every such row was left by a process that has already stopped, so this moment is the only
    # one at which the answer changes.
    settled = await fetches.settle_orphans()
    if settled:
        log.info("download.interrupted_by_a_restart", how_many=settled)

    # Staged imports whose job is long gone. A drop or a paste whose every attempt failed keeps its
    # bytes for as long as the queue keeps the job; past that, nothing can ask for them.
    swept = await asyncio.to_thread(capture.sweep_staging, data_dir)
    if swept:
        log.info("capture.staging_swept", removed=swept)


async def _files_owed_a_read(content: ContentStore, queue: JobQueue) -> None:
    # Files still identified by the whole-file digest are brought forward to the sampled identity.
    # A row whose only copy is on a drive that is away waits for it, and until it is brought
    # forward an arriving file of its size is digested both ways.
    if await content.legacy_identities_remain():
        await queue.enqueue_when_settled(media_jobs.REIDENTIFY, priority=BACKGROUND_PRIORITY)

    # Files a scan took in and never read (a scan cut off by a stop hands out no reads for them,
    # and a rescan skips them as unchanged) are read now: the scan's own floor, the read and the
    # picture, and nothing past it. What the switches build is the Build's to offer.
    if await content.unread_count():
        await queue.enqueue_when_settled(media_jobs.READ_UNREAD, priority=BACKGROUND_PRIORITY)


async def _recognition_owed(
    queue: JobQueue,
    hub: settings_hub.SettingsService,
    recognition: faces.FaceService,
    tiles: semantic.WholePicture,
) -> None:
    # Faces described by a model other than the one set are measured again from their stored
    # pictures. A family changed and then the process stopped leaves files nothing would otherwise
    # come back for, and until it does they are out of matching and grouping.
    if await hub.get_app(faces.ENABLED_KEY) and await recognition.measured_by_another_model():
        await queue.enqueue_when_settled(faces.FACE_REMEASURE, priority=BACKGROUND_PRIORITY)

    # Files whose scan refused a face for size that the floor set now would accept are looked at
    # again, and only those: a lowered floor changes nothing else a scan found. The pass ends
    # itself (a file looked at again leaves the list), so on a caught-up library this is one read.
    if await hub.get_app(faces.ENABLED_KEY) and await recognition.floor_pass_owed():
        await queue.enqueue_when_settled(faces.FACE_FLOOR, priority=BACKGROUND_PRIORITY)

    # The one face in a file a stash-box put somebody on, where nothing has been asked about it:
    # the box's question (`faces.service_box`). Asked for by the box's own filing from then on,
    # and by a file's scan; this is for what was filed or scanned before either asked.
    if await hub.get_app(faces.ENABLED_KEY) and await recognition.box_questions_owed():
        await queue.enqueue_when_settled(faces.FACE_BOX_QUESTIONS, priority=BACKGROUND_PRIORITY)

    # HEIF photographs whose faces were read from one tile of the grid, before the HEIF door read
    # the whole picture, are looked at again from it. The pass ends itself (a file looked at
    # again is newer than its copy), so on a caught-up library this is a walk of the HEIF stills.
    if await hub.get_app(faces.ENABLED_KEY) and await recognition.tile_pass_owed():
        await queue.enqueue_when_settled(faces.FACE_WHOLE_PICTURE, priority=BACKGROUND_PRIORITY)

    # The same for Smart Search: HEIF photographs described from one tile are described again
    # from the whole picture, and the pass ends itself the same way (a file described again is
    # newer than its copy).
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
    # Files with no stream rows yet, or rows an older reading wrote, are read again: one ffprobe per
    # file, no decode and no seeking, at background priority because nobody is waiting for it. A
    # release that reads a new stream field raises the reading's version, and this is what brings
    # an existing library's rows up to it without another pass over every file.
    if await content.assets_lacking_probe_rows(1):
        await queue.enqueue_when_settled(media_jobs.KEEP_PROBES, priority=BACKGROUND_PRIORITY)

    # Sets Sift made that fell under a raised floor are dissolved in the background: nothing else
    # ever looks back at a set.
    if await sets.under_floor(photo_sets.MIN_PICTURES):
        await queue.enqueue_when_settled(
            photo_sets.DISSOLVE_UNDER_FLOOR, priority=BACKGROUND_PRIORITY
        )

    # Read again what each file IS where an older generation of the ingress classifier typed it.
    # Only the rows a change can reach are below the line (see `ingress.CLASSIFIER_VERSION`).
    if await content.unclassified(1):
        await queue.enqueue_when_settled(media_jobs.RECLASSIFY, priority=BACKGROUND_PRIORITY)

    # Rebuild the hover clips of a library whose previews were built to a different shape, because
    # somebody changed the setting or because a release changed what a preview is. Asked here
    # rather than when the setting is written, because that write happens in a request and this
    # reads the whole derivatives table; on a restart where the answer is no it is one indexed count.
    shape = sampler.preview_shape(str(await hub.get_app(performance.PREVIEW_SHAPE_KEY)))
    if await content.previews_of_another_recipe_count(media_jobs.preview_recipe(shape)):
        await queue.enqueue_when_settled(media_jobs.REBUILD_PREVIEWS, priority=BACKGROUND_PRIORITY)

    # Build the stills for marks saved before a mark had a picture of its own, so two moments cut
    # from one video are not two identical tiles. No scan fixes it: a scan looks at files and a mark
    # is not one. One sweep job rather than one per mark, so thousands of marks are one row.
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
    """Work a start owes the library that no scan can do.

    Each is asked for at boot rather than by a scan, because a scan skips a file whose path, size
    and mtime are unchanged, so an already-indexed library would never be looked at again.

    Each is also asked for only when there is something to do. A job queued unconditionally is a job
    on the dashboard after every restart, for a library where the answer is already known, and the
    dashboard is where somebody looks to see whether anything is happening.
    """
    await _what_a_stop_left(fetches, data_dir)
    # The defaults this release's update changed, said once on History for whoever never chose.
    await hub.tell_changed_defaults(app_version())
    await _files_owed_a_read(content, queue)
    await _recognition_owed(queue, hub, recognition, tiles)
    # What the segment cache wrote before this boot, taken back under its cap. Off the loop: a
    # directory of a few hundred files is a few hundred stats.
    await asyncio.to_thread(segments.reload)

    await _rows_owed(content, queue, hub, sets, marks)
