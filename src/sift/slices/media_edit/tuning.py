# SPDX-License-Identifier: AGPL-3.0-or-later
"""The numbers compressing and editing a file are built on."""

from __future__ import annotations

from dataclasses import dataclass

#: The smallest output height; a smaller source keeps its own size, never scaled up.
MINIMUM_HEIGHT = 720


#: Bits per pixel per frame at the best and worst quality offered: a rough size model.
HIGHEST_BITS_PER_PIXEL = 0.12
LOWEST_BITS_PER_PIXEL = 0.025

CONTAINER_OVERHEAD = 0.02

#: Sound is copied, so a typical stereo rate erring high is assumed rather than read.
ASSUMED_AUDIO_BITS_PER_SECOND = 128_000

LOW_FRAME_RATE = 30.0


@dataclass(frozen=True, slots=True)
class Rung:
    """One attempt: a quality, a picture size, and a frame rate."""

    crf: int
    height: int | None
    fps: float | None
    bits_per_pixel: float


#: Best first: quality, then picture size, then frame rate.
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

# Plays anywhere: the container and codec every browser, phone and television decodes.

COMPATIBLE_CONTAINER = "mp4"

COMPATIBLE_VCODEC = "h264"

#: Sound that is copied untouched; any other is converted, and the panel says so first.
COMPATIBLE_ACODECS = frozenset({"aac", "mp3", "ac3"})

FALLBACK_ACODEC = "aac"
FALLBACK_AUDIO_BITRATE = "160k"

MAX_ATTEMPTS = 4


SAMPLE_SECONDS = 6.0

SAMPLE_AT_FRACTION = 0.25


#: Free space kept beside the expected output: a full media disk fails every writer.
FREE_SPACE_HEADROOM_BYTES = 2 * 1024 * 1024 * 1024

SUBPROCESS_TIMEOUT_SECONDS = 3600.0

MAX_BULK_ASSETS = 500

MAX_EDIT_STEPS = 8


#: Longest EXACT cut: its near-lossless re-encode is for short pieces. A trim is never bounded.
LONGEST_EXACT_CUT_MS = 300_000

#: Per format: a GIF stores every frame whole; WebP and AVIF compress between frames.
LONGEST_GIF_MS: dict[str, int] = {"gif": 15_000, "webp": 60_000, "avif": 60_000}

GIF_FPS = 15

#: The short edge, so a portrait clip gets as many pixels as a landscape one.
GIF_SHORT_EDGE = 480

WEBP_QUALITY = 80

#: SVT-AV1 quality, 0-63, LOWER better.
AVIF_CRF = 35

AVIF_SPEED_PRESET = 6
