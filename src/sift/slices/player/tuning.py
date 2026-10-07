# SPDX-License-Identifier: AGPL-3.0-or-later
"""The player's numbers, in one place, each with the reason it is that number.

They come from measuring real media; a constant whose reason is not written down is one nobody can
safely change.
"""

from __future__ import annotations

#: Seconds of video per segment: cost grows linearly with length and the per-run overhead is tiny,
#: so the shortest worth taking is best; below two, playlist churn dominates.
SEGMENT_SECONDS = 2.0

#: The most one segment's transcode may run. A time budget, unlike `SUBPROCESS_TIMEOUT_SECONDS`:
#: somebody watches a spinner, and a segment later than this is not worth having.
SEGMENT_TIMEOUT_SECONDS = 30.0

#: How often a request waiting on a segment asks whether its job finished: the queue's one-second
#: idle poll would spend the latency budget noticing; this is one primary-key read.
JOB_POLL_SECONDS = 0.05

#: The realtime ratio below which a file is not offered for ordinary streaming: slower than realtime
#: stalls continuously, which first-segment latency cannot see; 1.2 keeps a small margin.
MIN_REALTIME_RATIO = 1.2

#: Bytes per read when serving a range: direct play's whole cost. A megabyte, because per-read
#: overhead on a mount crossing a filesystem boundary makes small chunks several times slower; held
#: only while a chunk is in flight.
STREAM_CHUNK_BYTES = 1024 * 1024

#: The most one response commits to for an open range (`bytes=0-`): a player takes a buffer's worth
#: and hangs up, and a shorter answer is legal and asked again. Several seconds of a high-bitrate
#: file.
MAX_OPEN_RANGE_BYTES = 8 * 1024 * 1024

#: The compat encode: `veryfast` meets the latency promise at 1080p and below, and slower would
#: spend the headroom modest hardware needs.
COMPAT_PRESET = "veryfast"
COMPAT_CRF = 23
COMPAT_AUDIO_BITRATE = "128k"

#: Segments are fragmented MP4 on both the copied and transcoded paths: MPEG-TS cannot carry AV1,
#: which would force every AV1 file through a transcode, and one format keeps one segment pipeline.
SEGMENT_SUFFIX = ".m4s"
SEGMENT_MIME = "video/mp4"
INIT_SEGMENT_NAME = "init.mp4"


# --- The ladder ---------------------------------------------------------------------------------

#: The heights a quality menu offers, largest first, each about half the pixels of the one above
#: (the spacing a person can see). Filtered per file to those strictly below the source, so no rung
#: is an upscale.
LADDER_HEIGHTS: tuple[int, ...] = (2160, 1440, 1080, 720, 480, 360)

#: Rough bits per second of each rung for the playlist's required `BANDWIDTH`: the encode is
#: constant-quality, so it only needs to be ordered and in the right region for hls.js to compare.
LADDER_BITRATES: dict[int, int] = {
    2160: 16_000_000,
    1440: 10_000_000,
    1080: 5_000_000,
    720: 2_800_000,
    480: 1_400_000,
    360: 800_000,
}

#: One second of the source, when offered beside the rungs: read from the file where it says; this
#: is the fallback for one never probed.
FALLBACK_SOURCE_BITRATE = 20_000_000


# --- What each encoder can actually do ------------------------------------------------------

#: Where an encoder's throughput estimate starts, in pixels per second, before real work is seen:
#: conservative (an older card is several times slower than a current one), since optimism means a
#: video pausing for ever. Learned within a segment or two.
STARTING_ENCODER_PIXELS_PER_SECOND: dict[str, float] = {
    "h264_nvenc": 400_000_000.0,
    "h264_qsv": 300_000_000.0,
    "h264_vaapi": 250_000_000.0,
}

#: How much of the newest segment's measurement to believe: one noisy sample moves the estimate a
#: little, and a genuinely slow machine moves it all the way within a dozen.
OBSERVATION_WEIGHT = 0.2

#: Segments at the same time on a hardware encoder, whose throughput stays flat with parallel jobs
#: (a CPU's falls, so its limit is one). Three, not four: the card has Sift's other picture work,
#: and a quality switch needs two.
HARDWARE_SEGMENT_JOBS = 3

#: The shortest encode time the estimate learns from: below it the time is start-up and rounding,
#: and dividing by it gives the optimistic number that stutters.
SHORTEST_USABLE_ENCODE_SECONDS = 0.05

#: The shortest piece worth its own segment: a copied file's last segment can be a few frames, a
#: whole request for nothing, so the tail joins the segment before it.
SHORTEST_TAIL_SECONDS = 1.0

#: Files whose keyframe maps stay in memory: each is kilobytes, so the cap only stops a long session
#: growing without limit.
REMEMBERED_KEYFRAME_MAPS = 64
