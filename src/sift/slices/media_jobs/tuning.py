# SPDX-License-Identifier: AGPL-3.0-or-later
"""The numbers the derivatives are built to; all rebuildable by clearing the cache."""

from __future__ import annotations

from sift.kernel import mp4

#: Tall enough for the largest tile; rows are justified, so height is the fixed axis.
THUMBNAIL_HEIGHT = 480

THUMBNAIL_QUALITY = 5


#: A preview is drawn instead of the thumbnail, in the same rectangle.
PREVIEW_HEIGHT = THUMBNAIL_HEIGHT

PREVIEW_FPS = 12

PREVIEW_CRF = 23

#: The weight band per second of clip the numbers above land in (a test asserts it).
PREVIEW_MIN_BYTES_PER_SECOND = 6_600
PREVIEW_MAX_BYTES_PER_SECOND = 167_000

#: What a clip costs before its length counts, so a short clip is not held to the band.
PREVIEW_OVERHEAD_BYTES = 100_000


SPRITE_TILE_WIDTH = 160

SPRITE_COLUMNS = 6

SPRITE_QUALITY = 8


#: A hang guard for one ffmpeg or ffprobe run, not a budget.
SUBPROCESS_TIMEOUT_SECONDS = 600.0


MAX_INTERLEAVE_GAP_BYTES = mp4.NEEDS_REPAIR_BYTES

FINGERPRINT_BATCH = 50

FINGERPRINT_SKIP_PAGES = 20

REIDENTIFY_BATCH = 200

UNREAD_BATCH = 500

STAMP_BATCH = 250

STAMP_SETTLE_SECONDS = 5
