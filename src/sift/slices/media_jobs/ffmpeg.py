# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every ffmpeg and ffprobe command Sift runs, and the one place they are built.

Two rules hold this module together.

The first is that ffmpeg is always a subprocess with an argument list, never a shell string and
never a Python binding. A shell string means quoting a filename that came from somewhere else,
and a filename is exactly the thing that will one day contain a quote; a binding means the whole
decoder shares this process, and a decoder handed a malformed file is the last thing that should
be able to take the server down with it.

The second is that the arguments are built by functions that return a list and touch nothing, so
what Sift asks ffmpeg to do can be read in a test rather than inferred from a log. That matters
most for the hardware paths, which are the ones nobody can check on a machine that has no GPU.
"""

from __future__ import annotations

import json
import zlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sift.kernel import media
from sift.kernel.config import Settings
from sift.kernel.log import get_logger

# The shared half lives in the kernel, because `player` needs the same encoder choice and the same
# runner and a slice may not import another slice. What stays here is what only this slice builds:
# thumbnails, previews, sprite tiles and the perceptual-hash frame.
from sift.kernel.media import (
    Encoder,
    FFmpegError,
    background_flags,
    background_threads,
    choose_encoder,
    render_node,
    seconds,
)
from sift.kernel.media import (
    run as _run,
)
from sift.kernel.media import (
    run_json as _run_json,
)
from sift.kernel.numbers import as_int
from sift.kernel.places import strip_places
from sift.kernel.sampling import Piece
from sift.kernel.subprocess import Priority
from sift.slices.media_jobs import tuning

log = get_logger(__name__)

__all__ = [
    "Encoder",
    "FFmpegError",
    "Probed",
    "all_frames_args",
    "all_tiles_args",
    "choose_encoder",
    "frame_args",
    "parse_probe",
    "preview_args",
    "probe_args",
    "probe_body",
    "probe_tool",
    "read_kept_probe",
    "render_node",
    "run",
    "run_json",
    "sprite_grid",
    "still_args",
    "strip_places",
    "thumbnail_args",
    "tile_args",
]


@dataclass(frozen=True, slots=True)
class Probed:
    """What ffprobe said a file is.

    Every field is optional because ffprobe reports what it can and a file is under no obligation
    to be well-formed. A missing duration is a real answer for a stream with no timeline in it,
    not a failure (see the sampler, which treats it as such).

    The container is deliberately absent. ffprobe cannot answer that question: it reports every
    format that *could* demux the file ("mov,mp4,m4a,3gp,3g2,mj2" for anything ISO-based), so
    reading a container out of it means picking one arbitrarily, and picking the first labels every
    MP4 in the library "mov". The ingress gate has already identified the container structurally,
    and it is the one allowlist that says which containers exist. That answer is used instead.
    """

    width: int | None
    height: int | None
    duration_ms: int | None
    fps: float | None
    vcodec: str | None
    acodec: str | None

    color_transfer: str | None = None
    """The transfer characteristics of the picture, as ffprobe names them: `bt709` for nearly
    everything, `smpte2084` (PQ) or `arib-std-b67` (HLG) for an HDR file. None where the stream
    does not say, which is most 8-bit files and is read as ordinary."""

    bit_depth: int | None = None
    """How many bits each colour sample carries: 8 for most files, 10 for HDR and for the higher
    profiles of AV1 and HEVC.

    Read from the stream where ffprobe states it, and worked out from the pixel format where it does
    not: a format is named for what it holds, so `yuv420p10le` says ten in its own name. Both, rather
    than either, because which one is present depends on the encoder."""

    audio_channels: int | None = None
    """How many channels the first audio track carries: 1 for mono, 2 for stereo, 6 for 5.1.

    Stored, unlike the picture's own duration below, because it cannot be worked out afterwards
    from anything else on the row and the only way to fill it in later is to read every file in
    the library again. None where the file has no sound, and None where ffprobe does not say."""

    audio_sample_rate: int | None = None
    """How many samples a second that track carries: 44,100 and 48,000 for nearly everything.

    ffprobe reports it as a string; it is kept as a number so it can be compared and banded."""

    video_duration_ms: int | None = None
    """How long the PICTURE runs, which is not always how long the file runs.

    `duration_ms` above is the container's answer (how long the thing plays), and that is the
    right answer for a scrubber, for the running time on a card, and for deciding whether two files
    are the same recording. This is the video stream's own, and it is the right answer for the one
    question the container cannot settle: how much picture is there to cut from.

    They usually differ by nothing, or by a trailing audio frame outliving the last picture. Rarely
    the picture stops seconds before the sound, and a sampler reading the container's length would
    then sample past the last frame.

    Stored since content v22 as `assets.video_duration_ms`, and read by every sampler through
    `sampling.picture_span`."""

    duration_seconds: float | None = None
    """The same running time, unrounded, for the one caller that cannot use the rounded one.

    Everything in Sift measures time in whole milliseconds, and this is the exception. The
    fingerprint the public stash-boxes share divides the running time into twenty-five moments, and
    it agrees with other people's software only if it divides the same number they did. Rounding to
    the millisecond first moves that number about one time in a thousand, which would produce a
    fingerprint that is wrong in a way nothing downstream could detect."""

    picture_stream: int = 0
    """Which of the file's video streams is the picture that moves, counted among the video
    streams only: the `K` of ffmpeg's `v:K`. Zero for nearly every file, which has one.

    Not stored: it is a fact about how to READ the file, asked by the one command that names a
    stream in a filter graph (see `preview_args`). An animated AVIF written by ffmpeg carries its
    still cover as video stream 0 and its frames as video stream 1, and a graph fed `[0:v]` is fed
    the cover: a hover clip of one frame held for a second."""


# --- Probing ------------------------------------------------------------------------------


def probe_args(path: Path, *, settings: Settings, still: bool = False) -> list[str]:
    """Ask ffprobe what a file is.

    `-count_frames` is deliberately not here. It is the honest way to learn how many frames a file
    has, and it costs a full decode of the whole file to do it: minutes, on a long video, for a
    number nothing reads.

    No `-show_entries` either: `-show_format` already returns the container's own tags in the JSON
    (title, encoder, and the place a camera wrote). The whole answer is kept per file, so what this
    asks for is what a later question will have to work from.

    A `still` is also asked for its first frame. A photograph's note saying which way up it goes
    (its EXIF orientation) is read by the decoder rather than the demuxer, so it is on the frame and
    absent from the stream, and the size recorded has to be the size every picture is drawn at
    (see `_quarter_turned`). One frame of one picture: a few milliseconds more, even on a large
    JPEG. A moving file's turn is on its stream and costs nothing extra to read.
    """
    return [
        settings.ffprobe_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-print_format",
        "json",
        "-show_streams",
        "-show_format",
        *(("-show_frames", "-read_intervals", "%+#1") if still else ()),
        # Absolute, so a filename beginning `-` is a path and not an ffprobe option, the same
        # reason verify_decodable resolves before it probes.
        str(path.resolve()),
    ]


def parse_probe(payload: dict[str, Any]) -> Probed:
    """Read ffprobe's JSON. Returns what it could work out and does not raise on a gap.

    Whether the file is usable at all is not decided here: the ingress gate has already asked
    that question and answered it. This only reports.
    """
    streams = payload.get("streams")
    streams = [s for s in streams if isinstance(s, dict)] if isinstance(streams, list) else []
    video = media.the_moving_picture(streams)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = payload.get("format")
    container = fmt if isinstance(fmt, dict) else {}

    width = _int(video.get("width")) if video else None
    height = _int(video.get("height")) if video else None
    if video and _quarter_turned(video, _first_frame_of(payload, video)):
        width, height = height, width

    picture = _duration_seconds(video.get("duration")) if video else None
    seconds = _duration_seconds(container.get("duration"))
    if seconds is None:
        # A Matroska stream often carries no duration at container level. The video stream's own
        # is less reliable but it is an answer, and the alternative is calling a file that plays
        # perfectly well duration-less.
        seconds = picture

    return Probed(
        width=width,
        height=height,
        duration_ms=None if seconds is None else round(seconds * 1000),
        fps=_frame_rate(video) if video else None,
        vcodec=video.get("codec_name") if video else None,
        acodec=audio.get("codec_name") if audio else None,
        bit_depth=_bit_depth(video) if video else None,
        color_transfer=_transfer(video) if video else None,
        # The first audio track's shape. The first and not a survey of all of them: a file with
        # two languages has two tracks of the same shape, and the question every reader asks is
        # what this file sounds like rather than what each track is.
        audio_channels=_int(audio.get("channels")) if audio else None,
        audio_sample_rate=_int(audio.get("sample_rate")) if audio else None,
        video_duration_ms=None if picture is None else round(picture * 1000),
        duration_seconds=seconds,
        picture_stream=media.position_among_pictures(streams, video),
    )


# `strip_places` is the kernel's (`sift.kernel.places`): the one list of the words that make a key
# a place, shared with the door every copy Sift makes or sends goes through, so the answer kept per
# file and the file handed out can never come to disagree about what a place is. Named in this
# module's exports as well, where every reader of a stored answer already looks for it.


def probe_body(payload: dict[str, Any]) -> bytes:
    """The tool's whole answer, stripped of places and compressed, ready to be stored.

    Compressed because it is kept per file and a library is hundreds of thousands of them: the
    answer is repetitive JSON, which is the case this compresses best.

    Sorted keys, so the same reading of the same file produces the same bytes: a row that is
    rewritten on every probe with a differently-ordered but identical answer is a row that looks
    like it changed.
    """
    text = json.dumps(strip_places(payload), sort_keys=True, separators=(",", ":"))
    return zlib.compress(text.encode("utf-8"))


def read_kept_probe(body: bytes) -> dict[str, Any]:
    """A kept answer, back as it was stored. The other half of `probe_body`, so nothing anywhere
    else has to know what the compression was.

    A body that will not decompress raises `ValueError`, like one that will not parse, so a caller
    catches one thing and never has to name the compression to do it."""
    try:
        text = zlib.decompress(body)
    except zlib.error as exc:
        raise ValueError(f"a kept reading that does not decompress: {exc}") from exc
    loaded = json.loads(text.decode("utf-8"))
    return loaded if isinstance(loaded, dict) else {}


#: What the tool says it is, once per process per path.
#:
#: Which build read a file is part of what the answer means (ffprobe's fields come and go
#: between versions), and it is the same string for every file, so asking per file would be a
#: subprocess launch per file to learn something that cannot have changed.
_TOOL_VERSIONS: dict[str, str] = {}


async def probe_tool(*, settings: Settings) -> str:
    """The version line of the ffprobe that is about to read a file.

    Its own words, not a parsed number: what is wanted is something a person can compare against
    a build they have, and every attempt to normalise it is a chance to lose the part that
    mattered. Empty when the tool will not say, which is not worth failing a probe over.
    """
    cached = _TOOL_VERSIONS.get(settings.ffprobe_path)
    if cached is not None:
        return cached
    try:
        said = await run([settings.ffprobe_path, "-version"], capture=True)
    except (FFmpegError, OSError) as error:
        log.info("probe.tool_unknown", reason=str(error))
        said = b""
    line = said.decode("utf-8", "replace").strip().splitlines()
    version = line[0].strip() if line else ""
    _TOOL_VERSIONS[settings.ffprobe_path] = version
    return version


def _first_frame_of(payload: dict[str, Any], video: dict[str, Any]) -> dict[str, Any] | None:
    """The first frame ffprobe decoded from this stream, when it was asked for one (a still).

    Matched by stream, because a HEIF can carry a thumbnail or a depth map beside the photograph
    and each of them answers with a first frame of its own.
    """
    frames = payload.get("frames")
    if not isinstance(frames, list):
        return None
    for frame in frames:
        if isinstance(frame, dict) and frame.get("stream_index") == video.get("index"):
            return frame
    return None


def _quarter_turned(video: dict[str, Any], frame: dict[str, Any] | None) -> bool:
    """Whether the picture is drawn a quarter turn from how it is stored, so its width is its height.

    Read off the turn ffmpeg itself applies, because ffmpeg draws every picture Sift makes (the
    thumbnail, the cover, the frames recognition reads) and turns each one by it unasked: a
    photograph's is on its decoded frame, a phone's video and an AVIF carry theirs on the stream.
    The tag itself is not read: where ffmpeg finds a note it does not act on (the note on a PNG),
    reading it would record a size no picture Sift makes is drawn at. A mirror alone changes nothing
    here; a mirror with a quarter turn reports that turn.
    """
    for holder in (frame, video):
        for side in (holder or {}).get("side_data_list") or []:
            angle = side.get("rotation") if isinstance(side, dict) else None
            if isinstance(angle, int | float) and not isinstance(angle, bool):
                return round(abs(angle)) % 180 == 90
    return False


def _transfer(video: dict[str, Any]) -> str | None:
    """The transfer characteristics, lowercased, or None where the stream does not state them.

    The name matters more than the depth: a ten-bit file is not HDR because it is ten-bit, and an
    encode that forces 8-bit 4:2:0 onto a PQ or HLG picture without mapping it is a grey, washed
    picture with no sentence. This is the fact the tone map keys on.
    """
    value = video.get("color_transfer")
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip().lower()


def _bit_depth(video: dict[str, Any]) -> int | None:
    """Bits per colour sample, from whichever of the two places states it.

    `bits_per_raw_sample` is the direct answer and is often simply absent. The pixel format is
    always there and names its own depth (`yuv420p10le` is ten, `yuv444p12le` is twelve), and a
    format with no number in it is eight, which is what the ordinary ones are. Anything that answers
    neither is left unknown rather than guessed at.
    """
    stated = _int(video.get("bits_per_raw_sample"))
    if stated and 1 <= stated <= 64:
        return stated

    pixels = video.get("pix_fmt")
    if not isinstance(pixels, str) or not pixels:
        return None
    digits = "".join(ch for ch in pixels if ch.isdigit())
    # The leading digits belong to the subsampling (the 420 of yuv420p10le), so the depth is
    # whatever follows them, and a format with nothing after them carries eight.
    for known in ("400", "410", "411", "420", "422", "440", "444"):
        if digits.startswith(known):
            rest = digits[len(known) :]
            if not rest:
                return 8
            # Converted rather than trusted to be convertible: the characters were kept by
            # `isdigit()`, which admits digits `int()` refuses, and a probe reporting an unusual
            # pixel format should leave the depth unknown rather than raise inside a scan.
            depth = as_int(rest)
            return depth if depth is not None and 1 <= depth <= 64 else None
    return 8 if digits == "" else None


def _frame_rate(video: dict[str, Any]) -> float | None:
    """Frames per second, from ffprobe's two rational strings.

    `avg_frame_rate` first, because it is the file's real average and that is what a transcode
    actually has to keep up with. `r_frame_rate` is the nominal base rate: for a variable-rate
    file it reports the highest rate the timebase can express, which on some phone recordings is
    wildly above anything the file contains, and projecting cost from it would call a perfectly
    ordinary clip unplayable.

    Both arrive as `numerator/denominator`, and both report an unknown rate as `0/0`.
    """
    for key in ("avg_frame_rate", "r_frame_rate"):
        raw = video.get(key)
        if not isinstance(raw, str) or "/" not in raw:
            continue
        numerator, _, denominator = raw.partition("/")
        try:
            top, bottom = float(numerator), float(denominator)
        except (TypeError, ValueError):
            continue
        if top > 0 and bottom > 0:
            return top / bottom
    return None


def _duration_seconds(raw: object) -> float | None:
    if not isinstance(raw, str | int | float):
        return None
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return None
    # ffprobe reports N/A as the string, and a live stream as a negative. Neither is a duration.
    return seconds if seconds > 0 else None


def _int(raw: object) -> int | None:
    if not isinstance(raw, str | int | float):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


# --- Frames -------------------------------------------------------------------------------


def _seek(timestamp_ms: int) -> list[str]:
    """An input seek, or nothing at all when the moment asked for is the beginning.

    `-ss 0` is not the no-op it reads as. A photograph is decoded through the image2 demuxer,
    which presents it as a video one frame long: 0.04 s at the assumed 25 fps. An input seek to 0
    on that lands ON the only frame's
    timestamp rather than before it, the frame is discarded as already passed, and ffmpeg exits 0
    having written nothing:

        Output file is empty, nothing was encoded (check -ss / -t / -frames parameters if used)

    Zero exit, no output. So the thumbnail job would fail on the missing file rather than on
    ffmpeg, and the perceptual hash (which reads frame zero for a still) would silently come back
    empty, leaving every photograph with no fingerprint and therefore no duplicate detection. One
    cause, two symptoms, neither of which points at a seek.

    Asking for the beginning is asking for no seek, so that is what this sends. Every other moment
    keeps the input seek, which is what makes sampling a two-hour video affordable (see the callers).
    """
    return [] if timestamp_ms <= 0 else ["-ss", seconds(timestamp_ms)]


def frame_args(
    path: Path,
    timestamp_ms: int,
    *,
    size: int,
    settings: Settings,
) -> list[str]:
    """One frame, at one moment, as a raw grayscale square on stdout.

    This is what the perceptual hash reads, so the picture is deliberately destroyed on the way
    out: colour dropped, aspect ratio ignored, squashed to a small square. That is the hash's
    first step and ffmpeg does it far faster than anything reading pixels in Python could.

    `-ss` before `-i` is what makes this affordable on a long file. After `-i`, ffmpeg decodes
    from the start and throws away everything before the timestamp: thirty of those on a two
    hour video is thirty full decodes. Before `-i`, it seeks, and it is still exact: it lands on
    the preceding keyframe and decodes forward to the frame asked for.

    Except at zero, where there is nothing to seek to and asking anyway loses the frame (see
    `_seek`).

    Composed from `hash_frame_moment` and `hash_frame_filter`, which are what the fingerprint
    really reads through: thirty moments of one file go to the kernel as one process, and this is
    the one-moment form of the same command (the same seek, the same filter, the same output),
    kept so the two cannot be built from different pieces.
    """
    return media.raw_frame_args(
        path,
        hash_frame_moment(timestamp_ms),
        filters=hash_frame_filter(size),
        pixel_format=HASH_PIXEL_FORMAT,
        settings=settings,
    )


#: What the fingerprint reads: one grey byte per pixel.
HASH_PIXEL_FORMAT = "gray"


def hash_frame_moment(timestamp_ms: int) -> media.Moment:
    """Where a fingerprint frame is taken from: a seek, or nothing at the very beginning."""
    return media.Moment(seek=tuple(_seek(timestamp_ms)))


def hash_frame_filter(size: int) -> str:
    """How a fingerprint frame is shaped: squashed to a small square, bilinear. The hash's first
    step, done by ffmpeg because it has to touch the pixels anyway."""
    return f"scale={size}:{size}:flags=bilinear"


def stash_box_still_args(
    path: Path,
    at_seconds: float,
    *,
    width: int,
    settings: Settings,
) -> list[str]:
    """One still for the fingerprint the public stash-boxes share, as an uncompressed bitmap.

    Every part of this is fixed by having to agree with software Sift did not write, and none of it
    may be tidied:

    * `-ss` always goes BEFORE `-i`, including at zero. That is the opposite of `frame_args` next
      door, which drops the seek at zero because a photograph read through the image demuxer loses
      its only frame that way. A video does not have that problem, and moving the seek would change
      which frame comes back on a file whose first keyframe is not at zero.
    * The width is fixed and the height follows, rounded to an even number.
    * The scaler's flags are left unstated, so it uses its own default. Naming one here (even the
      one that looks obviously better) produces different pixels and therefore a different
      fingerprint from everybody else's.
    * Uncompressed, so the answer does not depend on which JPEG encoder happens to be installed.

    The moment arrives in SECONDS, as a fraction, which is the one place in Sift that does not
    take milliseconds, and the exception is deliberate. The twenty-five moments are worked out by
    dividing the running time, so they land on values like 18.060000000000002, and rounding that to
    the nearest millisecond writes a different number on the command line. It would almost always
    be the same frame. "Almost always" is not what a fingerprint that has to match other people's
    exactly can be built on.
    """
    return [
        settings.ffmpeg_path,
        *background_flags(settings),
        *stash_box_moment(at_seconds).seek,
        "-i",
        str(path),
        "-frames:v",
        "1",
        "-vf",
        stash_box_filter(width),
        *STASH_BOX_OUTPUT,
        "-f",
        "rawvideo",
        "pipe:1",
    ]


#: How a stash-box still is encoded: an uncompressed bitmap, so the bytes do not depend on which
#: JPEG encoder happens to be installed. The twenty-five stills of one video are written as files
#: from one process; this is the same encoder the one-still command above names.
STASH_BOX_OUTPUT: tuple[str, ...] = ("-c:v", "bmp")


def stash_box_moment(at_seconds: float) -> media.Moment:
    """Where a stash-box still is taken from. Always a seek, even at zero (see the command above
    for why that is the opposite of the fingerprint's rule)."""
    return media.Moment(seek=("-ss", stash_box_seconds(at_seconds)))


def stash_box_filter(width: int) -> str:
    """How a still is shaped: the fixed width, the height following, no scaler flags named."""
    return f"scale={width}:-2"


def stash_box_seconds(value: float) -> str:
    """A moment as the seconds string the other implementations print.

    The shortest decimal that reads back as exactly the same number, with a whole number carrying
    no decimal point. `seconds()` next door is not reused: that one formats for a person reading a
    log, and this one formats for two programs agreeing.
    """
    if value == int(value):
        return str(int(value))
    return repr(value)


def all_frames_args(path: Path, *, size: int, settings: Settings, stream: int = 0) -> list[str]:
    """Every frame of a file, each a raw grayscale square on stdout, in a single decode.

    The fingerprint reads thirty frames. Taken as thirty separate `-ss` seeks that is thirty decodes
    for a format that cannot seek cheaply: a GIF is delta-coded and has no keyframes, so each seek
    re-decodes from the very start, and thirty of those over a slow mount take a GIF's fingerprint
    from seconds to minutes. This reads the whole thing once instead; the caller picks its thirty
    frames out of the stream. Every frame comes out at `size`x`size` bytes, in play order, so the
    stream splits cleanly into frames with no per-frame markers to parse.

    Only worth it for a short, all-intra file. A feature-length video has hundreds of thousands of
    frames and seeks cheaply between its keyframes, so it keeps the per-frame seek (see the caller).

    `stream` is which video stream moves (`media.moving_stream_of`): an animated AVIF holds a still
    cover first and its frames second, and ffmpeg's own choice is the cover, one frame. The first
    stream is named by nothing, so a GIF's command names no stream.
    """
    return [
        settings.ffmpeg_path,
        *background_flags(settings),
        "-i",
        str(path),
        *(("-map", f"0:v:{stream}") if stream else ()),
        "-vf",
        f"scale={size}:{size}:flags=bilinear",
        "-pix_fmt",
        "gray",
        "-f",
        "rawvideo",
        "pipe:1",
    ]


# --- Derivatives --------------------------------------------------------------------------


def still_args(
    source: Path,
    destination: Path,
    *,
    timestamp_ms: int,
    height: int | None = None,
    width: int | None = None,
    quality: int,
    settings: Settings,
) -> list[str]:
    """One frame, written to an image file. What both a thumbnail and a sprite tile are.

    One function for both, because they are the same command with different numbers, and two
    copies of it would be two places for the seek to be got wrong. Exactly one of `height` and
    `width` is given: the other follows from the source's shape.

    `-2` rather than `-1` on the free axis keeps the aspect ratio and rounds to an even number.
    An odd dimension is something several encoders refuse outright.

    `min(N,ih)` never scales a small picture up. A 100px source made into a 480px thumbnail is a
    bigger file that looks worse than the original, which is the opposite of the job.
    """
    return [
        settings.ffmpeg_path,
        *background_flags(settings),
        *_seek(timestamp_ms),
        "-i",
        str(source),
        "-frames:v",
        "1",
        "-vf",
        still_filter(height=height, width=width),
        *still_output(quality),
        str(destination),
    ]


def still_filter(*, height: int | None = None, width: int | None = None) -> str:
    """How a still is shaped. Exactly one of `height` and `width` is given; the other follows from
    the source's shape. See `still_args` for why `-2` and why `min(N, i?)`."""
    if (height is None) == (width is None):
        raise ValueError("a still is sized by height or by width, not both and not neither")
    return f"scale=-2:min({height}\\,ih)" if height else f"scale=min({width}\\,iw):-2"


def still_output(quality: int) -> tuple[str, ...]:
    """How a still is encoded: a JPEG at `quality` on ffmpeg's scale, where 2 is best."""
    return ("-q:v", str(quality))


def all_tiles_args(
    source: Path,
    pattern: Path,
    *,
    width: int,
    quality: int,
    settings: Settings,
) -> list[str]:
    """Every frame of a file, each a JPEG at `width`, written to the numbered `pattern`, in one
    decode. The GIF twin of `still_args`: a GIF re-decodes from the start on every seek, so reading
    its sprite tiles as one seek per tile is a decode per tile; this reads them all in one go and
    the caller keeps the ones it wants. Same width and quality a tile would get from `still_args`,
    so a frame it writes is the tile a seek to that frame would have written.
    """
    return [
        settings.ffmpeg_path,
        *background_flags(settings),
        "-i",
        str(source),
        "-vf",
        f"scale=min({width}\\,iw):-2",
        "-q:v",
        str(quality),
        str(pattern),
    ]


def thumbnail_args(
    source: Path,
    destination: Path,
    *,
    timestamp_ms: int,
    settings: Settings,
) -> list[str]:
    """The still the grid draws. A still at the tile's size."""
    return still_args(
        source,
        destination,
        timestamp_ms=timestamp_ms,
        height=tuning.THUMBNAIL_HEIGHT,
        quality=tuning.THUMBNAIL_QUALITY,
        settings=settings,
    )


def preview_args(
    source: Path,
    destination: Path,
    *,
    pieces: Sequence[Piece],
    encoder: Encoder,
    device: str | None = None,
    decode: Sequence[str] = (),
    stream: int = 0,
    settings: Settings,
) -> list[str]:
    """The hover clip: moments from across the file, joined into one small silent loop.

    `pieces` says which moments and how long each runs (`kernel.sampling.preview_segments`
    decides); one piece is the plain one-input command.

    **One process, not one per piece.** Each moment is its own input with its own seek before its
    `-i`, so ffmpeg jumps to each moment rather than decoding everything in front of it, and the
    filter graph joins them: one decode pipeline, one encode, no temporary files. `-an` drops the
    audio, which nothing plays on a hover; `+faststart` puts the index first so a browser can play
    the clip before it has all of it.

    `decode` puts the DECODING on the graphics card; it is an input option, so it goes before
    every `-i` (`kernel.media.decode_flags` decides what it holds). `stream` is which video stream
    moves (`Probed.picture_stream`): in a filter graph `[0:v]` is the FIRST video stream, which
    for an animated AVIF is its still cover. Zero leaves the ordinary command as it is.
    """
    if not pieces:
        raise ValueError("a preview needs at least one moment to be cut from")
    if stream < 0:
        raise ValueError("a video stream is counted from zero")
    picked = f"v:{stream}" if stream else "v"
    hardware, video = _preview_encoder(encoder, device)
    graph, last = _preview_graph(pieces, picked, _preview_shape(encoder), encoder)

    return [
        settings.ffmpeg_path,
        *background_flags(settings),
        *hardware,
        *_preview_inputs(source, pieces, decode),
        "-filter_complex",
        graph,
        "-map",
        last,
        "-an",
        *video,
        # In the output position, which is the one that reaches the encoder; before -i it caps
        # only the decoder. Both together hold an encode to its share of the machine.
        "-threads",
        str(background_threads(settings)),
        "-movflags",
        "+faststart",
        str(destination),
    ]


def _preview_shape(encoder: Encoder) -> str:
    """The filters every piece passes through: its rate, its size, its pixel aspect and format."""
    # Both axes forced even: `-2` rounds the free width, and the height (the ceiling or the
    # source's own, whichever is smaller) is rounded by hand, because x264 refuses an odd one.
    height = f"trunc(min({tuning.PREVIEW_HEIGHT}\\,ih)/2)*2"

    # 4:2:0 eight-bit, forced in the graph, for every encoder that takes an ordinary frame:
    # H.264 in anything else will not play in Safari, and `h264_nvenc` refuses a 10-bit input.
    # In the graph because it is the half both paths share; `format` converts only when needed.
    # VAAPI's frames become nv12 on the way to the card, which is already eight-bit 4:2:0.
    pixels_stage = "" if encoder is Encoder.VAAPI else ",format=yuv420p"
    # `setsar` because concat refuses inputs whose pixel aspect disagrees, and a file can change it.
    return f"fps={tuning.PREVIEW_FPS},scale=-2:{height},setsar=1{pixels_stage}"


def _preview_encoder(encoder: Encoder, device: str | None) -> tuple[list[str], list[str]]:
    """The hardware flags and the encoder's own, for the clip's quality on this encoder."""
    hardware: list[str] = []
    video: list[str] = ["-c:v", encoder.value]

    if encoder is Encoder.CPU:
        video += ["-preset", "veryfast", "-crf", str(tuning.PREVIEW_CRF)]
    elif encoder is Encoder.NVENC:
        video += ["-preset", "p4", "-rc", "vbr", "-cq", str(tuning.PREVIEW_CRF)]
    elif encoder is Encoder.QSV:
        video += ["-global_quality", str(tuning.PREVIEW_CRF)]
    else:
        if not device:
            raise ValueError("VAAPI needs a render node, and none was given")
        hardware = ["-vaapi_device", device]
        video += ["-qp", str(tuning.PREVIEW_CRF)]
    return hardware, video


def _preview_inputs(source: Path, pieces: Sequence[Piece], decode: Sequence[str]) -> list[str]:
    """One seeked input per piece."""
    inputs: list[str] = []
    for piece in pieces:
        # `-ss` before `-i` seeks; `-t` after it bounds what is read. `_seek` sends nothing at all
        # for the beginning, because `-ss 0` is not the no-op it reads as (see its own note).
        inputs += [
            *decode,
            *_seek(piece.start_ms),
            "-t",
            seconds(piece.length_ms),
            "-i",
            str(source),
        ]
    return inputs


def _preview_graph(
    pieces: Sequence[Piece], picked: str, shaped: str, encoder: Encoder
) -> tuple[str, str]:
    """The filter graph that shapes and joins the pieces, and the label of what it ends on."""
    graph = ";".join(f"[{n}:{picked}]{shaped}[p{n}]" for n in range(len(pieces)))
    joined = "".join(f"[p{n}]" for n in range(len(pieces)))
    if len(pieces) > 1:
        graph += f";{joined}concat=n={len(pieces)}:v=1:a=0[cut]"
        last = "[cut]"
    else:
        last = joined

    if encoder is Encoder.VAAPI:
        # VAAPI encodes from a frame on the GPU, so the graph uploads it: once, at the very end,
        # so concat never joins frames from several hardware contexts.
        graph += f";{last}format=nv12,hwupload[out]"
        last = "[out]"
    return graph, last


def tile_args(
    pattern: str,
    destination: Path,
    *,
    columns: int,
    rows: int,
    settings: Settings,
) -> list[str]:
    """Stitch a numbered run of stills into one sheet.

    The sprite is built in two passes (seek out each frame, then tile the results) rather than
    in one pass with a select filter over the whole file. The one-pass version reads every frame
    of the source to keep thirty of them, which on a feature-length video is minutes of decoding to
    build a scrubber strip. Seeking costs thirty seeks.
    """
    return [
        settings.ffmpeg_path,
        *background_flags(settings),
        "-start_number",
        "0",
        "-i",
        pattern,
        "-vf",
        f"tile={columns}x{rows}",
        "-frames:v",
        "1",
        "-q:v",
        str(tuning.SPRITE_QUALITY),
        str(destination),
    ]


def sprite_grid(frame_count: int) -> tuple[int, int]:
    """The shape of the sheet holding this many tiles: columns, then rows."""
    columns = min(frame_count, tuning.SPRITE_COLUMNS)
    rows = -(-frame_count // columns) if columns else 0
    return columns, rows


# --- Running ------------------------------------------------------------------------------


async def run(argv: list[str], *, capture: bool = False, reads: Path | None = None) -> bytes:
    """Run a tool with this slice's time limit and below everything else on the machine.

    `reads` is the library file the tool opens, when it opens one: the read takes a place in that
    storage's lane first, so a network share is never asked to serve more seeking readers than it
    can. See `kernel.lanes`.

    Two things are decided here rather than at each call, and they are the same decision seen twice.

    The limit is ten minutes, because these are background jobs: a sprite sheet for a three-hour
    video is slow and is supposed to be. The player passes its own, far tighter, because somebody is
    watching a spinner while it runs.

    And the priority is the low one, for the same reason: everything this slice spawns is generated
    work nobody has asked for yet. A thumbnail arriving a minute later costs nothing; the same
    minute of processor and disk taken away from playback is a stutter somebody sees. It is set
    once, at the slice's only runner, rather than at each of the four argument builders: one of
    those would eventually be added without it, and the omission would look like nothing at all.
    """
    return await _run(
        argv,
        time_limit=tuning.SUBPROCESS_TIMEOUT_SECONDS,
        capture=capture,
        priority=Priority.BACKGROUND,
        reads=reads,
    )


async def run_json(argv: list[str], *, reads: Path | None = None) -> dict[str, Any]:
    """The inspection pass, at the same limit and the same low priority as everything else here.

    It is short and it is still background: nothing is on screen waiting for a file's dimensions,
    and the pass runs over every file in a library in one go when one is first pointed at Sift.
    """
    return await _run_json(
        argv,
        time_limit=tuning.SUBPROCESS_TIMEOUT_SECONDS,
        priority=Priority.BACKGROUND,
        reads=reads,
    )


def remux_args(source: Path, destination: Path, *, settings: Settings) -> list[str]:
    """Copy every stream into a new container so the audio sits beside the video again.

    A stream copy: no decoder, no encoder, nothing re-compressed, nothing lost. What changes is
    where the packets sit: ffmpeg interleaves as it writes, which is the whole repair.

    `-map 0` is load-bearing. Without it ffmpeg keeps one stream per type and silently drops extra
    audio tracks, subtitles and chapters; the output would play and the loss would not be visible
    until somebody went looking for a track that used to be there.

    `+faststart` moves the index to the front as well. It does not fix this fault (moving the
    index on its own makes it about three times worse), but it costs a second pass over a file
    that has just been written and saves a round trip when playback starts.

    Built from `background_flags` like every other job-driven launch here, so it carries the
    `-max_alloc` ceiling: a repair reads a file whose container is already known to be odd, the
    worst place to let ffmpeg size a buffer from what the header claims. The thread cap that comes
    with it is near-free, since a stream copy decodes and encodes nothing.
    """
    return [
        settings.ffmpeg_path,
        *background_flags(settings),
        "-i",
        str(source),
        "-map",
        "0",
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(destination),
    ]
