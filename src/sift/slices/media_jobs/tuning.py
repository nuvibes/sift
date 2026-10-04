# SPDX-License-Identifier: AGPL-3.0-or-later
"""The numbers the derivatives are built to.

None is a setting: they describe what a thumbnail is, and a preview's height, length and quality are
only right together, which a config file cannot assert. All are rebuildable: change one, delete the
cache, and it fills back in at the new value.
"""

from __future__ import annotations

from sift.kernel import mp4

# --- Thumbnails ---------------------------------------------------------------------------

#: Tall enough for the largest a tile is drawn, no taller. Rows are justified, so height is the
#: fixed axis and width follows the source.
THUMBNAIL_HEIGHT = 480

#: JPEG quality on ffmpeg's mjpeg scale (2 best, 31 worst): where the file stops looking better and
#: keeps getting bigger.
THUMBNAIL_QUALITY = 5

# --- Hover previews -----------------------------------------------------------------------

# How long a preview runs and how often it cuts are `kernel.sampling.PREVIEW_SHAPES`, shared with
# the settings screen that offers them; only what the encoder decides is here.

#: The thumbnail's height, by requirement: a preview is drawn INSTEAD of the thumbnail in the same
#: rectangle, so a smaller one softens the tile the moment somebody looks closely.
PREVIEW_HEIGHT = THUMBNAIL_HEIGHT

#: Frames per second: below about twelve, motion reads as a slideshow.
PREVIEW_FPS = 12

#: x264's quality scale (lower is better, 23 its default): higher looks worse than the thumbnail
#: beside it, and this lands inside the band below.
PREVIEW_CRF = 23

#: What a preview may weigh per second of clip, a band the numbers above are chosen to land in (a
#: test asserts it): above it the clip arrives too late, below it is not worth showing. Per second
#: so it holds at any preview length.
PREVIEW_MIN_BYTES_PER_SECOND = 6_600
PREVIEW_MAX_BYTES_PER_SECOND = 167_000

#: What a clip costs before its length counts (the container and the first whole frame), so a
#: half-second clip is not held to a per-second ceiling. No per-cut term: the pieces are joined
#: before encoding, so a cut adds almost nothing.
PREVIEW_OVERHEAD_BYTES = 100_000

# --- Trickplay sprites --------------------------------------------------------------------

#: One tile of the scrub sheet, in pixels: read at a glance while dragging, so sized for
#: recognition.
SPRITE_TILE_WIDTH = 160

#: Tiles per row: a squarer sheet decodes faster than a very wide one.
SPRITE_COLUMNS = 6

#: JPEG quality for the sheet, lower than a thumbnail's because no tile is seen large.
SPRITE_QUALITY = 8

# --- Perceptual hashing -------------------------------------------------------------------

# Not here: `HASH_FRAME_SIZE` and `HASH_CORNER_SIZE` live beside the arithmetic in
# `sift.kernel.content.perceptual`, because they shape a STORED fingerprint, and changing one
# silently stops matching every fingerprint taken before it.

# --- Subprocesses -------------------------------------------------------------------------

#: The longest one ffmpeg or ffprobe run may take before it is killed: a spinning decoder would hold
#: a worker for good. Generous enough for a long video over a network on one core; a guard against a
#: hang, not a budget.
SUBPROCESS_TIMEOUT_SECONDS = 600.0

# --- Repairing a badly interleaved file ---------------------------------------------------

#: How far a file may store its audio from its video before it is repaired: the kernel's constant,
#: which the browse screen compares against too.
MAX_INTERLEAVE_GAP_BYTES = mp4.NEEDS_REPAIR_BYTES

#: Videos one stash-box fingerprint pass reads before asking for another: each is decoded at
#: twenty-five points, so fifty is a job of minutes somebody lets finish.
FINGERPRINT_BATCH = 50

#: Empty pages in a row the fingerprint pass steps past before it stops. Stepping past lets
#: unreadable files at the head of the oldest-first list stop holding up the rest; the ceiling keeps
#: a library whose drive is away from being walked end to end for nothing.
FINGERPRINT_SKIP_PAGES = 20

#: Files one re-identifying pass brings forward per job: a minute or two over a share, few enough
#: jobs for the Activity screen, small enough to cancel.
REIDENTIFY_BATCH = 200

#: Never-read files the pass at start asks for at a time: a page of ids and a queue write each; the
#: reads are the jobs it hands out.
UNREAD_BATCH = 500

#: Files one stamp pass (reclassify, kept-reading) reads per job: the work per file is small, and
#: the settle delay between batches sets the rate, so the batch decides how much is done in it.
STAMP_BATCH = 250

#: How long those passes wait between batches: short, since they work through what was already
#: there; not zero, so a drive that has gone away costs seconds rather than a spin.
STAMP_SETTLE_SECONDS = 5
