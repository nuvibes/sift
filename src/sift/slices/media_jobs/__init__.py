# SPDX-License-Identifier: AGPL-3.0-or-later
"""Derivatives: what a file turns out to be, and everything the grid draws it with.

A library is a pile of files until something opens each one and works out what it is. That is
this feature: it probes, it builds the still the grid shows, the clip that plays under the
cursor, and the strip of frames the player's scrubber reads, all in the background, as jobs,
because it is slow and nobody should be made to watch it.

It also owns the screen that makes that background work visible. Everything else in Sift is
something a person asked for; this is the part that happens on its own, and the dashboard is
where it stops being mysterious.

Nothing built here is precious. Every derivative lives in the cache directory, never beside the
original, and deleting the lot costs nothing but the CPU to build them again.
"""

from __future__ import annotations

from sift.kernel.sampling import sample_frames, sprite_frames
from sift.slices.media_jobs.jobs import (
    BECAUSE_CONTAINER,
    BECAUSE_INTERLEAVE,
    FINGERPRINT_FILE,
    FINGERPRINT_FOR_STASH_BOXES,
    KEEP_PROBES,
    LOOP_THUMBNAIL,
    PICTURES,
    PREVIEW,
    PROBE,
    READ_UNREAD,
    REBUILD_PREVIEWS,
    REBUILD_THUMBNAILS,
    RECLASSIFY,
    REIDENTIFY,
    REMUX,
    SPRITE,
    THUMBNAIL,
    build_picture,
    fingerprint_one,
    picture_lack,
    picture_lacking_among,
    picture_switched_on,
    preview,
    preview_recipe,
    probe_is_coming,
    register_handlers,
    sprite,
    thumbnail,
)
from sift.slices.media_jobs.one_pass import (
    OnePassReader,
    fingerprint_frames,
    picture_frames,
    sprite_tile_frames,
)
from sift.slices.media_jobs.router import router

__all__ = [
    "BECAUSE_CONTAINER",
    "BECAUSE_INTERLEAVE",
    "FINGERPRINT_FILE",
    "FINGERPRINT_FOR_STASH_BOXES",
    "KEEP_PROBES",
    "LOOP_THUMBNAIL",
    "PICTURES",
    "PREVIEW",
    "PROBE",
    "READ_UNREAD",
    "REBUILD_PREVIEWS",
    "REBUILD_THUMBNAILS",
    "RECLASSIFY",
    "REIDENTIFY",
    "REMUX",
    "SPRITE",
    "THUMBNAIL",
    "OnePassReader",
    "build_picture",
    "fingerprint_frames",
    "fingerprint_one",
    "picture_frames",
    "picture_lack",
    "picture_lacking_among",
    "picture_switched_on",
    "preview",
    "preview_recipe",
    "probe_is_coming",
    "register_handlers",
    "router",
    "sample_frames",
    "sprite",
    "sprite_frames",
    "sprite_tile_frames",
    "thumbnail",
]
