# SPDX-License-Identifier: AGPL-3.0-or-later
"""Playback: the segment cache, the player that fills it, and the screens a phone can command."""

from __future__ import annotations

from fastapi import FastAPI

from sift.kernel import media
from sift.kernel.config import Settings
from sift.kernel.hardware import HardwareReport
from sift.kernel.jobs import JobQueue
from sift.kernel.wiring import provide
from sift.slices import media_jobs, player, remote


def build_playback(
    app: FastAPI,
    settings: Settings,
    hardware: HardwareReport,
    queue: JobQueue,
    accelerator: media.Accelerator,
) -> player.SegmentCache:
    """Playback.

    Hands the cache back as well as publishing it. The lifespan holds a cap on it that follows a
    setting, and reaching for it through the part bag instead would be a second way to get one
    object, which fails at start-up on any app that does not build playback.

    The cache is bounded by a byte cap rather than a count, because what fills a disk is bytes: a
    thousand small segments and one enormous one are the same number of files. Published as well as
    held inside the service, because the eviction it does is the kind of thing only an end-to-end
    test can watch: segments have to be really produced and really evicted.
    """
    segment_cache = player.SegmentCache(
        settings.transcode_cache_dir, max_bytes=settings.transcode_cache_max_bytes
    )
    service = player.PlayerService(
        segment_cache,
        queue,
        settings=settings,
        accelerator=accelerator,
        device=media_jobs.ffmpeg.render_node(),
        cpu_count=hardware.cpu_count,
    )
    provide(app, player.SEGMENT_CACHE, segment_cache)
    provide(app, player.SERVICE, service)
    # The screens offered to a phone as a remote. Nothing to build them from: they are what the open
    # players say about themselves, held for as long as they keep saying it.
    provide(app, remote.SCREENS, remote.Screens())
    player.register_handlers(service=service)
    return segment_cache
