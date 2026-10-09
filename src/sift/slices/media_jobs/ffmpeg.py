# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every ffmpeg and ffprobe command this slice runs, built as argument lists in one place.

A subprocess with a list, never a shell string or a binding, and builders that touch nothing.
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

# The shared half is the kernel's, since `player` needs it too; this slice's own builders stay here.
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
    """What ffprobe said a file is; every field optional, and no container (the ingress gate's)."""

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


def probe_args(path: Path, *, settings: Settings, still: bool = False) -> list[str]:
    """Ask ffprobe what a file is, without a frame count (a full decode); a still is also asked its
    first frame, where its EXIF turn is."""
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
        # Absolute, so a name beginning `-` is a path, not an option.
        str(path.resolve()),
    ]


def parse_probe(payload: dict[str, Any]) -> Probed:
    """Read ffprobe's JSON. Returns what it could work out and does not raise on a gap."""
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
        # A Matroska stream often has no container duration; the video stream's will do.
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
        # The first audio track's shape: what the file sounds like.
        audio_channels=_int(audio.get("channels")) if audio else None,
        audio_sample_rate=_int(audio.get("sample_rate")) if audio else None,
        video_duration_ms=None if picture is None else round(picture * 1000),
        duration_seconds=seconds,
        picture_stream=media.position_among_pictures(streams, video),
    )


# `strip_places` is the kernel's (`sift.kernel.places`), re-exported for readers of a stored answer.


def probe_body(payload: dict[str, Any]) -> bytes:
    """The tool's whole answer, stripped of places and compressed, keys sorted for stable bytes."""
    text = json.dumps(strip_places(payload), sort_keys=True, separators=(",", ":"))
    return zlib.compress(text.encode("utf-8"))


def read_kept_probe(body: bytes) -> dict[str, Any]:
    """A kept answer, back as it was stored; anything unreadable raises `ValueError`."""
    try:
        text = zlib.decompress(body)
    except zlib.error as exc:
        raise ValueError(f"a kept reading that does not decompress: {exc}") from exc
    loaded = json.loads(text.decode("utf-8"))
    return loaded if isinstance(loaded, dict) else {}


#: What the tool says it is, once per process per path.
_TOOL_VERSIONS: dict[str, str] = {}


async def probe_tool(*, settings: Settings) -> str:
    """The version line of the ffprobe about to read a file, in its own words; empty if unsaid."""
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
    """The first frame ffprobe decoded from this stream when asked for one (a still), by stream."""
    frames = payload.get("frames")
    if not isinstance(frames, list):
        return None
    for frame in frames:
        if isinstance(frame, dict) and frame.get("stream_index") == video.get("index"):
            return frame
    return None


def _quarter_turned(video: dict[str, Any], frame: dict[str, Any] | None) -> bool:
    """Whether the picture is drawn a quarter turn from how it is stored, by ffmpeg's own turn."""
    for holder in (frame, video):
        for side in (holder or {}).get("side_data_list") or []:
            angle = side.get("rotation") if isinstance(side, dict) else None
            if isinstance(angle, int | float) and not isinstance(angle, bool):
                return round(abs(angle)) % 180 == 90
    return False


def _transfer(video: dict[str, Any]) -> str | None:
    """The transfer characteristics, lowercased, or None where the stream does not state them."""
    value = video.get("color_transfer")
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip().lower()


def _bit_depth(video: dict[str, Any]) -> int | None:
    """Bits per colour sample, from `bits_per_raw_sample` or the pixel format's name."""
    stated = _int(video.get("bits_per_raw_sample"))
    if stated and 1 <= stated <= 64:
        return stated

    pixels = video.get("pix_fmt")
    if not isinstance(pixels, str) or not pixels:
        return None
    digits = "".join(ch for ch in pixels if ch.isdigit())
    # The leading digits are the subsampling; any digits after them are the depth.
    for known in ("400", "410", "411", "420", "422", "440", "444"):
        if digits.startswith(known):
            rest = digits[len(known) :]
            if not rest:
                return 8
            # `isdigit()` admits digits `int()` refuses: unknown rather than a raise inside a scan.
            depth = as_int(rest)
            return depth if depth is not None and 1 <= depth <= 64 else None
    return 8 if digits == "" else None


def _frame_rate(video: dict[str, Any]) -> float | None:
    """Frames per second, `avg_frame_rate` first (`r_frame_rate` can be high); `0/0` is none."""
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
    # N/A arrives as a string and a live stream as a negative.
    return seconds if seconds > 0 else None


def _int(raw: object) -> int | None:
    if not isinstance(raw, str | int | float):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _seek(timestamp_ms: int) -> list[str]:
    """An input seek, or nothing at the beginning: `-ss 0` on a still discards its only frame."""
    return [] if timestamp_ms <= 0 else ["-ss", seconds(timestamp_ms)]


def frame_args(
    path: Path,
    timestamp_ms: int,
    *,
    size: int,
    settings: Settings,
) -> list[str]:
    """One frame at one moment, a raw grayscale square on stdout: what the perceptual hash reads."""
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
    """How a fingerprint frame is shaped: squashed to a small square, bilinear."""
    return f"scale={size}:{size}:flags=bilinear"


def stash_box_still_args(
    path: Path,
    at_seconds: float,
    *,
    width: int,
    settings: Settings,
) -> list[str]:
    """One still for the fingerprint the public stash-boxes share, as an uncompressed bitmap.

    Every part must agree with other software: always seek first, even at zero; even height; the
    scaler's default flags; and the moment in seconds, since milliseconds would round differently.
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


#: How a stash-box still is encoded: uncompressed, so no JPEG encoder changes the bytes.
STASH_BOX_OUTPUT: tuple[str, ...] = ("-c:v", "bmp")


def stash_box_moment(at_seconds: float) -> media.Moment:
    """Where a stash-box still is taken from: always a seek, even at zero."""
    return media.Moment(seek=("-ss", stash_box_seconds(at_seconds)))


def stash_box_filter(width: int) -> str:
    """How a still is shaped: the fixed width, the height following, no scaler flags named."""
    return f"scale={width}:-2"


def stash_box_seconds(value: float) -> str:
    """A moment as the seconds string the other implementations print: shortest exact decimal."""
    if value == int(value):
        return str(int(value))
    return repr(value)


def all_frames_args(path: Path, *, size: int, settings: Settings, stream: int = 0) -> list[str]:
    """Every frame of a file, each a raw grayscale square on stdout, in a single decode.

    For a short all-intra file such as a GIF, where every seek re-decodes from the start.
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
    """One frame, written to an image file: a thumbnail or a sprite tile.

    Exactly one of `height` and `width` is given; `-2` keeps it even, `min(N,ih)` never upscales.
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
    """How a still is shaped; exactly one of `height` and `width` is given (see `still_args`)."""
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
    """Every frame of a GIF as numbered JPEG tiles in one decode: the twin of `still_args`."""
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

    Each piece is its own seeked input and the filter graph joins them; `stream` picks the moving
    video stream (an animated AVIF's first is its cover).
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
        # Output position, which reaches the encoder.
        "-threads",
        str(background_threads(settings)),
        "-movflags",
        "+faststart",
        str(destination),
    ]


def _preview_shape(encoder: Encoder) -> str:
    """The filters every piece passes through: its rate, its size, its pixel aspect and format."""
    # Both axes even, which x264 requires.
    height = f"trunc(min({tuning.PREVIEW_HEIGHT}\\,ih)/2)*2"

    # 4:2:0 eight-bit for every encoder taking an ordinary frame (Safari, NVENC); VAAPI's nv12 is.
    pixels_stage = "" if encoder is Encoder.VAAPI else ",format=yuv420p"
    # `setsar` because concat refuses inputs whose pixel aspect disagrees.
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
        # `-ss` before `-i` seeks; `-t` after it bounds what is read.
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
        # VAAPI uploads once, at the end, so concat never joins frames across hardware contexts.
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
    """Stitch a numbered run of stills into one sheet: thirty seeks, not a decode of the file."""
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


async def run(argv: list[str], *, capture: bool = False, reads: Path | None = None) -> bytes:
    """Run a tool with this slice's ten-minute limit and the low priority of background work.

    `reads` is the library file it opens, which takes a place in that storage's lane first.
    """
    return await _run(
        argv,
        time_limit=tuning.SUBPROCESS_TIMEOUT_SECONDS,
        capture=capture,
        priority=Priority.BACKGROUND,
        reads=reads,
    )


async def run_json(argv: list[str], *, reads: Path | None = None) -> dict[str, Any]:
    """The inspection pass, at the same limit and the same low priority as everything else here."""
    return await _run_json(
        argv,
        time_limit=tuning.SUBPROCESS_TIMEOUT_SECONDS,
        priority=Priority.BACKGROUND,
        reads=reads,
    )


def remux_args(source: Path, destination: Path, *, settings: Settings) -> list[str]:
    """Copy every stream into a new container so the audio sits beside the video again.

    `-map 0` keeps every track; `background_flags` carries the `-max_alloc` ceiling.
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
