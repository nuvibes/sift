# SPDX-License-Identifier: AGPL-3.0-or-later
"""Building the ffmpeg command for one segment, as an argument list, and running it.

`-ss` goes before `-i` so ffmpeg seeks the input; after it, ffmpeg decodes from the start and
discards, which is correct video at many times the cost. A regression test holds it.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path

from sift.kernel.config import Settings
from sift.kernel.log import get_logger, timing_hook
from sift.kernel.media import HDR_TO_SDR, Encoder, FFmpegError
from sift.kernel.media import run as run_tool
from sift.slices.player import tuning

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SegmentSpec:
    """Everything needed to produce one segment, and nothing about where it will be stored."""

    source: Path
    destination: Path
    start_seconds: float
    duration_seconds: float
    scale_height: int | None = None
    encoder: Encoder = Encoder.CPU
    device: str | None = None

    decode: tuple[str, ...] = ()
    """Flags that put the DECODING on the graphics card, or nothing where there is none to use.

    `kernel.media.decode_flags` decides this once at start-up and carries the measurements; a
    segment only has to place it. Worth having here more than anywhere else in Sift: this is the
    one encode somebody is actually waiting on, and it is reached precisely when the browser cannot
    play a file itself, which is to say for HEVC, AV1 and 10-bit sources, the codecs whose decode
    is most expensive. On a 10-bit HEVC file it roughly halves the processor time a segment costs,
    at the same wall clock."""

    hdr: bool = False
    """Whether the source picture is HDR (PQ or HLG), so the encode maps it to SDR first.

    The compat encode forces 8-bit 4:2:0, which every browser plays; forced onto an HDR picture
    with nothing in front of it, the result is grey and washed out: the values are kept and
    the scale they were on is dropped. `HDR_TO_SDR` at the head of the filter chain brings the
    picture onto the ordinary scale before the scale and the encoder see it. The policy decides
    this from the transfer characteristics probing read; see `Asset.is_hdr`.
    """

    level: str | None = None
    """The H.264 level to encode at, as ffmpeg spells it ("4.1"). None leaves it to the encoder.

    Worked out from the picture this segment will actually carry (see `policy.h264_level`), and
    it is the SAME function that tells the master playlist what to declare. That is the whole
    reason it arrives from outside rather than being decided here: a level the stream is encoded
    at and a level the playlist advertises have to be one number, and two places computing it is
    two places to drift.
    """


def segment_args(spec: SegmentSpec, *, settings: Settings) -> list[str]:
    """The argv for one segment of fragmented MP4, decodable as the bytes arrive."""
    argv = [settings.ffmpeg_path, *_BASE, *spec.decode]

    # Before `-i`: see the module docstring.
    if spec.start_seconds > 0:
        argv += ["-ss", f"{spec.start_seconds:.3f}"]

    # Used as handed over, never resolved here: an argv builder does no I/O.
    argv += ["-i", str(spec.source), "-t", f"{spec.duration_seconds:.3f}"]

    argv += _encode_args(spec)

    argv += [
        "-f",
        "mp4",
        # A fragment in the layout every HLS and DASH client expects.
        "-movflags",
        "empty_moov+default_base_moof+dash",
        # Timestamped from its own start, so the player stitches one continuous timeline.
        "-output_ts_offset",
        f"{spec.start_seconds:.3f}",
        "-muxdelay",
        "0",
        str(spec.destination),
    ]
    return argv


#: Where the media half begins: the cut is at this box, so any box before it rides along as header.
_MEDIA_BOX = b"moof"

#: Without `moov` an init segment has no track description.
_REQUIRED_INIT_BOX = b"moov"


def split_fragment(raw: bytes) -> tuple[bytes, bytes]:
    """Split one self-initialising fMP4 into (init, media) at the first `moof`, as HLS wants.

    Raises `FFmpegError` when the header half has no `moov`.
    """
    at = 0
    total = len(raw)
    while at + 8 <= total:
        size = int.from_bytes(raw[at : at + 4], "big")
        name = raw[at + 4 : at + 8]
        if name == _MEDIA_BOX:
            init = raw[:at]
            if _REQUIRED_INIT_BOX not in init:
                raise FFmpegError("the rendered fragment carries no track description")
            return init, raw[at:]
        if size < 8:
            # Zero or malformed: no media box follows, and not advancing stops a truncated loop.
            break
        at += size
    # No media box: a zero-frame render is all header.
    return raw, b""


def _encode_args(spec: SegmentSpec) -> list[str]:
    """The compat encode: H.264 and AAC, which every browser plays, tuned to start quickly."""
    argv: list[str] = []

    filters: list[str] = []
    if spec.hdr:
        # First, so the scale and the encoder get an ordinary picture.
        filters.append(HDR_TO_SDR)
    if spec.scale_height is not None:
        # `-2` keeps the aspect at an even width; `min(h,ih)` never scales up.
        filters.append(f"scale=-2:min({spec.scale_height}\\,ih)")

    if spec.encoder is Encoder.VAAPI:
        if spec.device is None:
            raise ValueError("VAAPI needs a render node, and none was given")
        argv += ["-vaapi_device", spec.device]
        filters.append("format=nv12")
        filters.append("hwupload")

    if filters:
        argv += ["-vf", ",".join(filters)]

    argv += ["-c:v", spec.encoder.value]

    # 8-bit 4:2:0 on every encoder handed software frames (NVENC refuses 4:2:2 and 10-bit with an
    # unhelpful error); VAAPI converts in its own filter chain instead.
    if spec.encoder is not Encoder.VAAPI:
        argv += ["-pix_fmt", "yuv420p"]

    # High profile and a fitting level on every encoder, since the master playlist declares them.
    argv += ["-profile:v", "high"]
    if spec.level is not None:
        argv += ["-level", spec.level]

    argv += _quality_args(spec)
    # A keyframe at each segment boundary, so a seek lands on a clean picture.
    # at a keyframe, and a player seeking to one gets a grey mess until the next one arrives.
    frames_per_segment = tuning.SEGMENT_SECONDS
    argv += [
        "-force_key_frames",
        f"expr:gte(t,n_forced*{frames_per_segment:g})",
        "-sc_threshold",
        "0",
    ]

    argv += ["-c:a", "aac", "-b:a", tuning.COMPAT_AUDIO_BITRATE, "-ac", "2"]
    return argv


def _quality_args(spec: SegmentSpec) -> list[str]:
    """Each encoder's spelling of quality; one with no arm gets the CPU's, which always works."""
    argv: list[str] = []
    match spec.encoder:
        case Encoder.NVENC:
            argv += ["-preset", "p4", "-rc", "vbr", "-cq", str(tuning.COMPAT_CRF)]
        case Encoder.QSV:
            argv += ["-global_quality", str(tuning.COMPAT_CRF)]
        case Encoder.VAAPI:
            argv += ["-qp", str(tuning.COMPAT_CRF)]
        case _:
            argv += ["-preset", tuning.COMPAT_PRESET, "-crf", str(tuning.COMPAT_CRF)]
    return argv


_BASE = (
    "-hide_banner",
    "-loglevel",
    "error",
    "-nostdin",
    "-y",
    "-max_alloc",
    str(1 << 30),
)


async def render(argv: list[str], destination: Path, *, stage: str) -> float:
    """Run one ffmpeg invocation to a temporary file, then move it into place. Returns seconds taken.

    Written aside and moved, because a file at the destination is served immediately.
    """
    await asyncio.to_thread(destination.parent.mkdir, parents=True, exist_ok=True)
    partial = destination.with_name(f".{destination.name}.partial")
    argv = [*argv[:-1], str(partial)]

    started = time.monotonic()
    try:
        with timing_hook(f"player.render.{stage}"):
            # A spinner time budget, not the thumbnail pipeline's hang guard.
            await run_tool(argv, time_limit=tuning.SEGMENT_TIMEOUT_SECONDS)
        elapsed = time.monotonic() - started
        await asyncio.to_thread(partial.replace, destination)
    finally:
        await asyncio.to_thread(partial.unlink, missing_ok=True)

    log.info("player.segment_rendered", stage=stage, seconds=round(elapsed, 3))
    return elapsed


def realtime_ratio(*, elapsed_seconds: float, video_seconds: float) -> float | None:
    """Seconds of video produced per second of work. None when it cannot be told."""
    if elapsed_seconds <= 0 or video_seconds <= 0:
        return None
    return video_seconds / elapsed_seconds
