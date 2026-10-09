# SPDX-License-Identifier: AGPL-3.0-or-later
"""The player's numbers."""

from __future__ import annotations

#: Seconds per segment: the shortest worth taking, as below two the playlist churn dominates.
SEGMENT_SECONDS = 2.0

SEGMENT_TIMEOUT_SECONDS = 30.0

JOB_POLL_SECONDS = 0.05

#: Below this realtime ratio a file stalls continuously, so it is not offered for streaming.
MIN_REALTIME_RATIO = 1.2

STREAM_CHUNK_BYTES = 1024 * 1024

#: The most one response commits to for an open range: players take a buffer's worth and hang up.
MAX_OPEN_RANGE_BYTES = 8 * 1024 * 1024

COMPAT_PRESET = "veryfast"
COMPAT_CRF = 23
COMPAT_AUDIO_BITRATE = "128k"

#: Fragmented MP4 on every path: MPEG-TS cannot carry AV1.
SEGMENT_SUFFIX = ".m4s"
SEGMENT_MIME = "video/mp4"
INIT_SEGMENT_NAME = "init.mp4"


#: Quality menu heights, about half the pixels apart, filtered per file so none is an upscale.
LADDER_HEIGHTS: tuple[int, ...] = (2160, 1440, 1080, 720, 480, 360)

#: Rough `BANDWIDTH` per rung: the encode is constant-quality, so order is all hls.js needs.
LADDER_BITRATES: dict[int, int] = {
    2160: 16_000_000,
    1440: 10_000_000,
    1080: 5_000_000,
    720: 2_800_000,
    480: 1_400_000,
    360: 800_000,
}

FALLBACK_SOURCE_BITRATE = 20_000_000


#: Starting encoder throughput in pixels per second: conservative, since optimism stalls video.
STARTING_ENCODER_PIXELS_PER_SECOND: dict[str, float] = {
    "h264_nvenc": 400_000_000.0,
    "h264_qsv": 300_000_000.0,
    "h264_vaapi": 250_000_000.0,
}

OBSERVATION_WEIGHT = 0.2

#: Hardware segments together: a card's throughput is flat to four, and it has other work.
HARDWARE_SEGMENT_JOBS = 3

SHORTEST_USABLE_ENCODE_SECONDS = 0.05

#: A copied file's tail shorter than this joins the segment before it.
SHORTEST_TAIL_SECONDS = 1.0

REMEMBERED_KEYFRAME_MAPS = 64
