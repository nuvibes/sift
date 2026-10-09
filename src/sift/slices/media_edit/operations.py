# SPDX-License-Identifier: AGPL-3.0-or-later
"""What an edit is: the verbs, what each copy is called, and the exact ffmpeg command behind it.
The scratch file has no extension, so every builder passes `-f`; a zero seek is left out."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from sift.kernel.config import Settings
from sift.kernel.ingress import ALLOWED_MEDIA, Kind
from sift.kernel.media import background_flags, background_threads, seconds
from sift.slices.media_edit.tuning import (
    AVIF_CRF,
    AVIF_SPEED_PRESET,
    GIF_FPS,
    GIF_SHORT_EDGE,
    WEBP_QUALITY,
)

__all__ = [
    "GIF_FORMATS",
    "MOVING_FORMATS",
    "QUARTER_TURNS",
    "SEVERAL",
    "STILL_FORMATS",
    "Operation",
    "Turn",
    "crop_filter",
    "cut_args",
    "gif_args",
    "moving_format_for",
    "resize_filter",
    "scale_filter",
    "still_args",
    "still_format_for",
    "turn_filter",
]


class Operation(StrEnum):
    """The verbs the provenance row records. Trim and clip are one cut; gif changes the format."""

    CROP = "crop"
    RESIZE = "resize"
    ROTATE = "rotate"
    TRIM = "trim"
    CLIP = "clip"
    GIF = "gif"


ON_STILLS = frozenset({Operation.CROP, Operation.RESIZE, Operation.ROTATE})
ON_MOVING = frozenset({Operation.TRIM, Operation.CLIP, Operation.GIF})


class Turn(StrEnum):
    """Which way round the picture goes: quarter turns, and the two mirrors."""

    RIGHT = "right"
    LEFT = "left"
    HALF = "half"
    MIRROR = "mirror"
    FLIP = "flip"


_TURN_FILTER = {
    # ffmpeg's `transpose=1` is clockwise.
    Turn.RIGHT: "transpose=1",
    Turn.LEFT: "transpose=2",
    # Not `hflip,vflip`, which reads as a mirror.
    Turn.HALF: "transpose=2,transpose=2",
    Turn.MIRROR: "hflip",
    Turn.FLIP: "vflip",
}

#: The turns that swap width and height, stated once for everything that reports a size.
QUARTER_TURNS = frozenset({Turn.RIGHT, Turn.LEFT})


@dataclass(frozen=True, slots=True)
class StillFormat:
    """How a photograph is written back out: what to call it, and what to hand ffmpeg."""

    extension: str
    args: tuple[str, ...]
    #: Said on screen before it runs.
    lossy: bool


# `-update 1` makes the image muxer write one file to the exact path, not a numbered pattern.
_IMAGE_FILE = ("-f", "image2", "-update", "1")

_JPEG = StillFormat(extension="jpg", args=("-c:v", "mjpeg", "-q:v", "2", *_IMAGE_FILE), lossy=True)
_PNG = StillFormat(extension="png", args=("-c:v", "png", *_IMAGE_FILE), lossy=False)
_WEBP = StillFormat(
    extension="webp", args=("-c:v", "libwebp", "-quality", "90", "-f", "webp"), lossy=True
)
# `-cpu-used 6`: about twice JPEG's time for a sixth of the size; 8 is no faster.
_AVIF = StillFormat(
    extension="avif",
    args=(
        "-c:v",
        "libaom-av1",
        "-still-picture",
        "1",
        "-cpu-used",
        "6",
        "-crf",
        "30",
        "-f",
        "avif",
    ),
    lossy=True,
)

#: Each still format's writer, keyed by the allowlist's name. HEIC becomes JPEG: the shipped ffmpeg
#: cannot write HEIF.
STILL_FORMATS: dict[str, StillFormat] = {
    "jpeg": _JPEG,
    "png": _PNG,
    "webp": _WEBP,
    "heic": _JPEG,
    "avif": _AVIF,
}


@dataclass(frozen=True, slots=True)
class MovingFormat:
    """How a cut is written back out, in the source's own container, with its own exact-cut
    encoders."""

    extension: str
    #: A `.mkv` is written by the matroska muxer.
    muxer: str
    #: Index at the front so playback can start early.
    faststart: bool
    exact_video: tuple[str, ...]
    #: A `.webm` will not hold AAC.
    exact_audio: tuple[str, ...]


#: Near-transparent: a cut asks for the right seconds, not a smaller file.
_EXACT_H264 = ("-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p")

#: Rebuilt on the exact path only: copied audio frames would overrun the mark by tens of
#: milliseconds.
_EXACT_AAC = ("-c:a", "aac", "-b:a", "160k")
_EXACT_OPUS = ("-c:a", "libopus", "-b:a", "160k")

#: VP9 for the family H.264 cannot go in; slower, but an exact cut is short.
_EXACT_VP9 = (
    "-c:v",
    "libvpx-vp9",
    "-b:v",
    "0",
    "-crf",
    "24",
    "-cpu-used",
    "4",
    "-row-mt",
    "1",
    "-pix_fmt",
    "yuv420p",
)

MOVING_FORMATS: dict[str, MovingFormat] = {
    "mp4": MovingFormat(
        extension="mp4",
        muxer="mp4",
        faststart=True,
        exact_video=_EXACT_H264,
        exact_audio=_EXACT_AAC,
    ),
    "mov": MovingFormat(
        extension="mov",
        muxer="mov",
        faststart=True,
        exact_video=_EXACT_H264,
        exact_audio=_EXACT_AAC,
    ),
    "mkv": MovingFormat(
        extension="mkv",
        muxer="matroska",
        faststart=False,
        exact_video=_EXACT_H264,
        exact_audio=_EXACT_AAC,
    ),
    "webm": MovingFormat(
        extension="webm",
        muxer="webm",
        faststart=False,
        exact_video=_EXACT_VP9,
        exact_audio=_EXACT_OPUS,
    ),
}


def _by_mime(kind: Kind) -> dict[str, str]:
    """The allowlist's name for each media type of one kind, by the mime the ingress gate set."""
    return {media.mime: media.name for media in ALLOWED_MEDIA if media.kind is kind}


_STILL_NAME_BY_MIME = _by_mime(Kind.IMAGE)
_MOVING_NAME_BY_MIME = _by_mime(Kind.VIDEO)


def _check_every_format_has_somewhere_to_go() -> None:
    """Refuse to start if an allowed format has no writer here. A raise, since asserts vanish under
    -O."""
    missing = (set(_STILL_NAME_BY_MIME.values()) - STILL_FORMATS.keys()) | (
        set(_MOVING_NAME_BY_MIME.values()) - MOVING_FORMATS.keys()
    )
    if missing:
        raise RuntimeError(f"the editor has nowhere to save: {', '.join(sorted(missing))}")


_check_every_format_has_somewhere_to_go()


def still_format_for(mime: str | None) -> StillFormat | None:
    """How to write this photograph back out, or None when Sift cannot."""
    name = _STILL_NAME_BY_MIME.get(mime or "")
    return STILL_FORMATS.get(name) if name else None


def moving_format_for(mime: str | None) -> MovingFormat | None:
    """Which container a cut of this file lands in, or None when it cannot be a stream copy (GIF,
    animated WebP)."""
    name = _MOVING_NAME_BY_MIME.get(mime or "")
    return MOVING_FORMATS.get(name) if name else None


def stamp(milliseconds: int) -> str:
    """A moment written the way somebody would say it: `1m30s`, `2h5m`, `45s`, `0s`."""
    total = max(0, milliseconds) // 1000
    hours, rest = divmod(total, 3600)
    minutes, second = divmod(rest, 60)
    parts = [
        f"{value}{unit}" for value, unit in ((hours, "h"), (minutes, "m"), (second, "s")) if value
    ]
    return "".join(parts) or "0s"


def suffix(
    operation: Operation,
    *,
    turn: Turn | None = None,
    width: int | None = None,
    height: int | None = None,
    start_ms: int | None = None,
    gif_format: str | None = None,
) -> str:
    """What is added to the original's name, distinct enough that two different edits never collide."""
    if operation is Operation.CROP:
        return f"-cropped-{width}x{height}"
    if operation is Operation.RESIZE:
        return f"-{width}px"
    if operation is Operation.ROTATE:
        return _TURN_SUFFIX[turn] if turn else "-rotated"
    if operation is Operation.TRIM:
        return "-trimmed"
    if operation is Operation.GIF:
        # The suffix is the format, not the verb, and the moment keeps two GIFs of one video apart.
        if gif_format is None:
            raise ValueError("a GIF is named after the format it is written in")
        return f"-{gif_format}-from-{stamp(start_ms or 0)}"
    return f"-from-{stamp(start_ms or 0)}"


#: A mirror is not named as a rotation.
_TURN_SUFFIX = {
    Turn.RIGHT: "-rotated-right",
    Turn.LEFT: "-rotated-left",
    Turn.HALF: "-rotated-180",
    Turn.MIRROR: "-mirrored",
    Turn.FLIP: "-flipped",
}

#: The suffix for a copy made by several operations together.
SEVERAL = "-edited"


def output_filename(source_name: str | None, *, suffix_text: str, extension: str) -> str:
    """The original's name, what was done to it, and the produced file's own extension."""
    stem = (source_name or "file").rsplit(".", 1)[0] or "file"
    return f"{stem}{suffix_text}.{extension}"


def still_args(
    source: Path,
    destination: Path,
    *,
    filters: Sequence[str],
    fmt: StillFormat,
    settings: Settings,
) -> list[str]:
    """One frame through one filter chain to one file, however many operations the chain holds.
    `-map 0:v:0` takes the first picture; `-noautorotate` because the chain already applies the
    turn."""
    return [
        settings.ffmpeg_path,
        *background_flags(settings),
        "-noautorotate",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-an",
        "-vf",
        # An empty `-vf` is a parse error; an upright picture asked for no change has an empty
        # chain.
        ",".join(filters) or "null",
        "-frames:v",
        "1",
        *fmt.args,
        str(destination),
    ]


def snap_to_even(left: int, top: int, width: int, height: int) -> tuple[int, int, int, int]:
    """The nearest rectangle ffmpeg can cut: rounded down onto the colour blocks, in this one place."""
    return left - left % 2, top - top % 2, width - width % 2, height - height % 2


def crop_filter(*, left: int, top: int, width: int, height: int) -> str:
    """Keep the rectangle, throw the rest away."""
    return f"crop={width}:{height}:{left}:{top}"


def resize_filter(*, width: int) -> str:
    """Make it this many pixels across, and let the height follow."""
    return f"scale={width}:-1"


def turn_filter(turn: Turn) -> str:
    """A quarter turn either way, a half turn, or a mirror across either line."""
    return _TURN_FILTER[turn]


def cut_args(
    source: Path,
    destination: Path,
    *,
    start_ms: int,
    duration_ms: int,
    fmt: MovingFormat,
    settings: Settings,
    exact: bool = False,
) -> list[str]:
    """A piece of the file: copied for a trim (start may be early), re-encoded for an exact clip.
    The seek goes before the input and the length after, so the length measures the output."""
    streams: tuple[str, ...] = (
        (*fmt.exact_video, *fmt.exact_audio, "-shortest")
        if exact
        # On a re-encode `make_zero` stretches the result to more than twice the mark.
        else ("-c:v", "copy", "-c:a", "copy", "-avoid_negative_ts", "make_zero")
    )
    return [
        settings.ffmpeg_path,
        *background_flags(settings),
        *(["-ss", seconds(start_ms)] if start_ms > 0 else []),
        "-i",
        str(source),
        "-t",
        seconds(duration_ms),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        *streams,
        *(["-threads", str(background_threads(settings))] if exact else []),
        *(["-movflags", "+faststart"] if fmt.faststart else []),
        "-f",
        fmt.muxer,
        str(destination),
    ]


#: The three formats a GIF can be written as; all animate (verified on a clip longer than a second).


@dataclass(frozen=True, slots=True)
class GifFormat:
    """How one GIF format is written; only GIF needs a palette graph."""

    extension: str
    muxer: str
    #: Empty for GIF, whose codec is the muxer.
    codec: tuple[str, ...]


#: Two-stage palette in one command. `[0:v]` is required: -filter_complex is not fed by -map.
_GIF_GRAPH = (
    "[0:v]fps={fps},{scale}:flags=lanczos,split[a][b];"
    "[a]palettegen=stats_mode=full[p];[b][p]paletteuse=dither=sierra2_4a[out]"
)

GIF_FORMATS: dict[str, GifFormat] = {
    "gif": GifFormat(extension="gif", muxer="gif", codec=()),
    "webp": GifFormat(
        extension="webp",
        muxer="webp",
        # This build has no `-compression_level`; the preset is the lever.
        codec=(
            "-c:v",
            "libwebp",
            "-lossless",
            "0",
            "-q:v",
            str(WEBP_QUALITY),
            "-preset",
            "picture",
        ),
    ),
    "avif": GifFormat(
        extension="avif",
        muxer="avif",
        # SVT-AV1: far faster than libaom. `yuv420p` pins 8-bit for every decoder.
        codec=(
            "-c:v",
            "libsvtav1",
            "-crf",
            str(AVIF_CRF),
            "-preset",
            str(AVIF_SPEED_PRESET),
            "-pix_fmt",
            "yuv420p",
        ),
    ),
}


def scale_filter(short_edge: int, *, landscape: bool) -> str:
    """Cap the SHORT edge so landscape and portrait clips get the same pixels; `-2` keeps the other
    even."""
    return f"scale=-2:{short_edge}" if landscape else f"scale={short_edge}:-2"


def gif_args(
    source: Path,
    destination: Path,
    *,
    start_ms: int,
    duration_ms: int,
    fmt: GifFormat,
    landscape: bool,
    settings: Settings,
) -> list[str]:
    """A stretch of a video as a looping GIF, rebuilt at its own rate and size, without sound."""
    scale = scale_filter(GIF_SHORT_EDGE, landscape=landscape)
    picture: list[str] = (
        ["-filter_complex", _GIF_GRAPH.format(fps=GIF_FPS, scale=scale), "-map", "[out]"]
        if not fmt.codec
        else ["-vf", f"fps={GIF_FPS},{scale}:flags=lanczos", *fmt.codec]
    )
    return [
        settings.ffmpeg_path,
        *background_flags(settings),
        *(["-ss", seconds(start_ms)] if start_ms > 0 else []),
        "-i",
        str(source),
        "-t",
        seconds(duration_ms),
        # Named, so a source with audio builds the same command as one without.
        "-an",
        *picture,
        "-loop",
        "0",
        "-threads",
        str(background_threads(settings)),
        "-f",
        fmt.muxer,
        str(destination),
    ]
