# SPDX-License-Identifier: AGPL-3.0-or-later
"""The numbers compressing and editing a file are built on.

The target sizes are settings, since they answer what the place somebody sends a file to will
accept, which changes. These describe what an encode is, and are only right in relation to each
other.
"""

from __future__ import annotations

from dataclasses import dataclass

# --- the floor ------------------------------------------------------------------------------

#: The smallest picture Sift produces, in scan lines: below it a copy is not worth sending, so a
#: target reachable only under it is reported unreachable with the smallest that is. A floor on the
#: output: a smaller source is compressed at its own size, never scaled up.
MINIMUM_HEIGHT = 720

# --- estimating a size before spending four minutes finding out -------------------------------

#: Bits per pixel per frame at the best and worst quality offered: the whole size model, rough and
#: used only to choose what to attempt; the encode decides. Below the lowest the picture is mush, so
#: a target needing it does not fit.
HIGHEST_BITS_PER_PIXEL = 0.12
LOWEST_BITS_PER_PIXEL = 0.025

#: A container's cost over its streams, as a share: ignored, every estimate reads under, the
#: direction that makes a target look reachable.
CONTAINER_OVERHEAD = 0.02

#: What sound is assumed to cost: it is copied, never re-encoded, and reading each file's rate would
#: open every file to draw a panel, so a typical stereo rate erring high leaves room.
ASSUMED_AUDIO_BITS_PER_SECOND = 128_000

#: The frame rate the lowest rungs cap at, never raising a slower file: the last thing tried and the
#: first visibly wrong, so only at the floor.
LOW_FRAME_RATE = 30.0

# --- what a rung is -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Rung:
    """One attempt: a quality, a picture size, and a frame rate.

    Walking down the ladder spends quality before picture size: a softer frame at full size reads
    as compressed, a sharp one at a quarter of the size reads as the wrong video.
    """

    #: x264's constant-quality scale, where lower is better. What the encoder is actually given.
    crf: int
    #: The tallest the output may be, or None for "whatever the source is".
    height: int | None
    #: The fastest the output may run, or None for "whatever the source does".
    fps: float | None
    #: What this step is expected to cost, in bits per pixel per frame. Drives the estimate.
    bits_per_pixel: float


#: The ladder, best first: quality steps at full size, then the picture comes down, then at the
#: floor the frame rate. The last two go below the floor on purpose: the floor filters this table,
#: so what it costs stays visible and testable.
RUNGS: tuple[Rung, ...] = (
    Rung(crf=20, height=None, fps=None, bits_per_pixel=HIGHEST_BITS_PER_PIXEL),
    Rung(crf=24, height=None, fps=None, bits_per_pixel=0.08),
    Rung(crf=28, height=None, fps=None, bits_per_pixel=0.055),
    Rung(crf=30, height=1080, fps=None, bits_per_pixel=0.05),
    Rung(crf=30, height=MINIMUM_HEIGHT, fps=None, bits_per_pixel=0.045),
    Rung(crf=34, height=MINIMUM_HEIGHT, fps=None, bits_per_pixel=0.032),
    Rung(crf=34, height=MINIMUM_HEIGHT, fps=LOW_FRAME_RATE, bits_per_pixel=LOWEST_BITS_PER_PIXEL),
    Rung(crf=34, height=480, fps=LOW_FRAME_RATE, bits_per_pixel=0.02),
    Rung(crf=36, height=360, fps=LOW_FRAME_RATE, bits_per_pixel=0.015),
)

# --- what "plays anywhere" means --------------------------------------------------------------
#
# Deliberately not named after any service. What is wanted is the pair that every browser, phone
# and television decodes, and that pair does not change when a particular site changes its rules.

#: The container a compatibility conversion produces.
COMPATIBLE_CONTAINER = "mp4"

#: The video codec it produces. Sift only ever encodes this one. See the kernel's encoder list.
COMPATIBLE_VCODEC = "h264"

#: Sound that travels in that container and is copied untouched. Any other sound cannot be
#: rewrapped, so for this target alone it is converted, and the panel says so before the work
#: starts.
COMPATIBLE_ACODECS = frozenset({"aac", "mp3", "ac3"})

#: What the sound is converted to in that one case, and at what rate.
FALLBACK_ACODEC = "aac"
FALLBACK_AUDIO_BITRATE = "160k"

#: The most encodes one file is given before the best so far is kept: each is a full pass, and four
#: finds the best of the rungs by halving.
MAX_ATTEMPTS = 4

# --- the sample -----------------------------------------------------------------------------

#: Seconds a sample encode covers: long enough to judge motion, short enough to wait for.
SAMPLE_SECONDS = 6.0

#: Where the sample is taken, as a share of the running time: the start is often a title card or a
#: fade, which says nothing about the rest.
SAMPLE_AT_FRACTION = 0.25

# --- guards ---------------------------------------------------------------------------------

#: Room that must stay free beside the expected output before an encode starts: filling a media disk
#: fails everything writing to it at the same time.
FREE_SPACE_HEADROOM_BYTES = 2 * 1024 * 1024 * 1024

#: How long one encode may run before it is killed: a whole file at real quality on a slow disk, far
#: longer than a derivative job.
SUBPROCESS_TIMEOUT_SECONDS = 3600.0

#: The most files one request may compress: the same limit tags, People and collections use.
MAX_BULK_ASSETS = 500

#: The most operations one Save may carry: far more than the panel produces, and a bound on an
#: arbitrary caller, since each step is another pass over a decoded frame.
MAX_EDIT_STEPS = 8

# --- how long a piece may be ------------------------------------------------------------------

#: The longest stretch an EXACT cut takes: its near-lossless re-encode is chosen for short pieces,
#: and a whole film would be hours. Wider than the editor's own menu, because a marked Loop is
#: whatever somebody marked. A trim copies packets and is never bounded by this.
LONGEST_EXACT_CUT_MS = 300_000

#: The longest GIF built, per format: a GIF stores every frame whole, so its size grows with
#: frame count, while WebP and AVIF compress between frames and cost far less. Still ceilings: a
#: thing to paste somewhere is short.
LONGEST_GIF_MS: dict[str, int] = {"gif": 15_000, "webp": 60_000, "avif": 60_000}

#: Frames a second a GIF is built at: reads as motion, and a GIF's size doubles exactly with
#: the rate.
GIF_FPS = 15

#: A GIF's SHORT edge in pixels: a width cap would give a portrait clip a third of a
#: landscape one's pixels.
GIF_SHORT_EDGE = 480

#: libwebp's quality, 0-100, higher better.
WEBP_QUALITY = 80

#: SVT-AV1's quality dial, 0-63, LOWER better: deliberately not named like `WEBP_QUALITY`, so a
#: copy-paste cannot invert a rung.
AVIF_CRF = 35

#: SVT-AV1's speed dial, 0 slowest to 13 fastest: 6 buys most of the size for a fraction of the
#: slower presets' time, and faster saves little.
AVIF_SPEED_PRESET = 6
