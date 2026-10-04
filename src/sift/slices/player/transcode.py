# SPDX-License-Identifier: AGPL-3.0-or-later
"""Building the ffmpeg command for one segment, and running it.

Everything here is a pure function returning `list[str]`, matching the rule `kernel.media` sets:
ffmpeg is invoked with an argument list, never a shell string, and the arguments are assembled
somewhere that can be tested without spawning anything.

The spawning itself is `kernel.media.run`, shared with the thumbnail pipeline rather than
reimplemented: it already owns the kill, the reaping, and the translation of a non-zero exit into
`FFmpegError`. What is *not* shared is the time limit, which this passes for itself: see
`tuning.SEGMENT_TIMEOUT_SECONDS`.

## The one-character mistake that costs ten seconds

`-ss` before `-i` seeks the input: ffmpeg jumps to the nearest keyframe and starts decoding there.
`-ss` after `-i` seeks the output: ffmpeg decodes the file **from the beginning** and throws away
everything before the offset.

Both produce correct video. That is what makes it dangerous. At 5% into a 30-minute file the wrong
way already costs **about five times the right one**, growing linearly with the offset: minutes by
the middle of the file. Nothing about the output looks wrong, no test that checks the video would
notice, and a refactor that tidies the argument order reintroduces it silently. There is a
regression test asserting that a deep-offset segment costs about what an offset-zero one does, and
that test exists solely because of this.
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
    """The argv for one segment of fragmented MP4.

    Fragmented rather than ordinary MP4 because an ordinary one writes its index at the end of the
    file, so a player cannot start reading until the whole segment exists. The `+dash` flag set
    here produces a segment a browser can begin decoding as the bytes arrive.
    """
    argv = [settings.ffmpeg_path, *_BASE, *spec.decode]

    #  BEFORE `-i`. See the module docstring. This is the whole reason seeking is fast.
    if spec.start_seconds > 0:
        argv += ["-ss", f"{spec.start_seconds:.3f}"]

    # The source is used as it was handed over, never `.resolve()`d here, which would make an argv
    # builder touch the disk: milliseconds (tens at worst) on an SMB library against a twentieth
    # of one locally, on the loop, once per segment and twice whenever the card refuses and the
    # processor takes over. `PlayerService.build` resolves it once, on a thread, and hands the
    # answer down. A builder of arguments does no I/O.
    argv += ["-i", str(spec.source), "-t", f"{spec.duration_seconds:.3f}"]

    argv += _encode_args(spec)

    argv += [
        "-f",
        "mp4",
        # `empty_moov+default_base_moof` is what makes this a *fragment* rather than a standalone
        # file, and `dash` keeps the fragment layout to the one every HLS and DASH client expects.
        "-movflags",
        "empty_moov+default_base_moof+dash",
        # Each segment is timestamped from its own start rather than from the file's, so the
        # player stitches them into one continuous timeline instead of seeing every segment claim
        # to begin at zero.
        "-output_ts_offset",
        f"{spec.start_seconds:.3f}",
        "-muxdelay",
        "0",
        str(spec.destination),
    ]
    return argv


#: Top-level MP4 boxes that belong to the *initialisation* segment rather than to a media one.
#:
#: `ftyp` names the format and `moov` describes the tracks: codec, resolution, timescale. They
#: are identical for every segment of a given stream, which is exactly why HLS carries them once in
#: an init segment (`EXT-X-MAP`) instead of repeating them thousands of times.
#: The box the media half begins at. The cut is made *at* this box rather than after a list of
#: recognized header boxes, and the difference is not cosmetic: `-movflags +dash` emits `sidx`
#: boxes between `moov` and `moof`, so a rule written as "everything I recognize is header" already
#: meets a box it does not recognize before the media starts, and keeps working only by accident.
#: Naming the boundary directly means a `free`, `skip` or `sidx` box appearing before it is simply
#: carried along, which is what the spec allows.
_MEDIA_BOX = b"moof"

#: What a usable initialisation segment has to contain. Without `moov` there is no track
#: description at all, and `EXT-X-MAP` would point at something no player can use.
_REQUIRED_INIT_BOX = b"moov"


def split_fragment(raw: bytes) -> tuple[bytes, bytes]:
    """Split one self-initialising fMP4 into (init, media).

    ffmpeg, asked for a standalone fragmented MP4, writes a complete little file: `ftyp`, then
    `moov` describing the tracks, then the `moof`/`mdat` pairs holding the frames. That plays on
    its own but it is not what HLS wants: the spec expects the track description once, in an init
    segment, and media segments that carry frames only.

    Serving the self-initialising form would work in some players and fail in others (Safari's
    native HLS wants `EXT-X-MAP`), and would repeat the `moov` in every single segment. So the
    boxes are walked once and cut at the first `moof`. Everything before it is the header, which is
    byte-identical for every segment of a stream; everything from it is this segment's frames.

    Box walking rather than a library: an MP4 box is a 4-byte big-endian length and a 4-byte name,
    and reading that is a great deal less risk than a dependency that parses untrusted media.

    Raises `FFmpegError` when the header half turns out to have no `moov` in it. That should not
    happen; if it ever does, the alternative is publishing an `EXT-X-MAP` pointing at a file with
    no track description: a player that silently shows nothing, and nothing in the log to say
    why, which is the worst of the available outcomes.
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
            # Zero means "to the end of the file"; anything under eight is malformed. Either way
            # there is no media box after this, and refusing to advance is what stops a truncated
            # render (a full disk mid-write) from looping forever.
            break
        at += size
    # No media box at all: the whole thing is header. True for a zero-frame render, and the honest
    # answer rather than a guess at where media would have been.
    return raw, b""


def _encode_args(spec: SegmentSpec) -> list[str]:
    """The compat encode: H.264 and AAC, which every browser on every site can play.

    One encode, whichever rung of the ladder it is asked for at, tuned for "starts quickly and
    keeps up", not for archival quality.
    """
    argv: list[str] = []

    filters: list[str] = []
    if spec.hdr:
        # First, so the scale below works on an ordinary picture and the encoder is handed one.
        filters.append(HDR_TO_SDR)
    if spec.scale_height is not None:
        # `-2` keeps the aspect ratio and rounds the width to an even number, which H.264's
        # chroma subsampling requires. `min(h,ih)` means a file already shorter than the ceiling
        # is left alone rather than being scaled *up* into a bigger, slower, blurrier encode.
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

    # 8-bit 4:2:0, on every encoder that is handed software frames.
    #
    # ON EVERY ENCODER, NOT THE CPU ARM ALONE: a ProRes file decodes to `yuv422p10le`, NVENC cannot
    # ingest that, and the failure is `CreateInputBuffer failed: invalid param (8)`, which names
    # no pixel format, reads like a bad width or height, and reaches the person watching as a video
    # that never starts. 10-bit HEVC and anything 4:2:2 or 4:4:4 fail the same way.
    #
    # Every browser Sift targets decodes 8-bit 4:2:0 and this is the compatibility encode, so
    # there is nothing lost by saying it once for all of them.
    #
    # VAAPI is the exception and must not have it: its frames are converted and uploaded by the
    # filter chain below (`format=nv12,hwupload`), and a second opinion here conflicts with that.
    if spec.encoder is not Encoder.VAAPI:
        argv += ["-pix_fmt", "yuv420p"]

    # High profile and a level that fits the picture, on EVERY encoder rather than only the
    # software one. Hardware encoders would pick their own from what the device can do, but a
    # master playlist *declares* what the stream is, and a declaration a browser reads and believes
    # has to be true of every rung, whoever encoded it.
    #
    # NVENC honours both rather than quietly ignoring them: asked for `high` at level 4.1 it
    # produces a stream ffprobe reports as `High` / `41`.
    argv += ["-profile:v", "high"]
    if spec.level is not None:
        argv += ["-level", spec.level]

    # Each encoder spells quality differently, and handing one another's flag means it fails to
    # open. The CPU case is last and is the catch-all rather than a named arm, because it is the
    # answer for most machines Sift runs on and because an encoder nobody added an arm for should
    # land on the path that always works rather than on no path at all.
    match spec.encoder:
        case Encoder.NVENC:
            argv += ["-preset", "p4", "-rc", "vbr", "-cq", str(tuning.COMPAT_CRF)]
        case Encoder.QSV:
            argv += ["-global_quality", str(tuning.COMPAT_CRF)]
        case Encoder.VAAPI:
            argv += ["-qp", str(tuning.COMPAT_CRF)]
        case _:
            argv += ["-preset", tuning.COMPAT_PRESET, "-crf", str(tuning.COMPAT_CRF)]

    # Closed GOP with a keyframe at the segment boundary. Without this the segments do not start
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

    Written aside and moved rather than written directly, because the destination is a cache key:
    the moment a file exists at that path, another request will serve it. A partially-written
    segment at the real path is a truncated video handed to a player. `Path.replace` is atomic
    within a filesystem, so a reader sees either nothing or the whole thing.

    Every stage is timed. First-segment latency is the number the diagnostics engine will be asked
    about when somebody reports that playback is slow to start.
    """
    await asyncio.to_thread(destination.parent.mkdir, parents=True, exist_ok=True)
    partial = destination.with_name(f".{destination.name}.partial")
    argv = [*argv[:-1], str(partial)]

    started = time.monotonic()
    try:
        with timing_hook(f"player.render.{stage}"):
            # This slice's own time limit, not the thumbnail pipeline's ten-minute hang guard.
            # That one is right for a background sprite sheet and wrong here, where somebody is
            # watching a spinner: a segment that has not arrived in thirty seconds is not going to
            # be wanted by the time it does.
            await run_tool(argv, time_limit=tuning.SEGMENT_TIMEOUT_SECONDS)
        elapsed = time.monotonic() - started
        await asyncio.to_thread(partial.replace, destination)
    finally:
        await asyncio.to_thread(partial.unlink, missing_ok=True)

    log.info("player.segment_rendered", stage=stage, seconds=round(elapsed, 3))
    return elapsed


def realtime_ratio(*, elapsed_seconds: float, video_seconds: float) -> float | None:
    """Seconds of video produced per second of work. None when it cannot be told.

    This is the *measured* counterpart to `policy.projected_realtime`, and it is the one that is
    actually true. The projection decides whether to offer a file at all; this says what happened,
    and it is what the diagnostics engine reports and what a future tuning pass should calibrate
    against.
    """
    if elapsed_seconds <= 0 or video_seconds <= 0:
        return None
    return video_seconds / elapsed_seconds
