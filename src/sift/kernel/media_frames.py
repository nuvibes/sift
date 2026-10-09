# SPDX-License-Identifier: AGPL-3.0-or-later
"""A file's frames: the moments asked of it, its picture and clock, and the shape it is read in."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class Moment:
    """One picture to take from a file: the input options that place it, `-ss 1.500` or nothing."""

    seek: tuple[str, ...]


#: What one seeked input holds, in pictures of the file's size, as measured and rounded up.
PICTURES_PER_INPUT = 16


PICTURES_PER_THREAD = 3


@dataclass(frozen=True, slots=True)
class Picture:
    """The size of a file's decoded picture: what each of its decoders holds several of."""

    width: int
    height: int
    bytes_per_pixel: float


def input_bytes(picture: Picture, *, threads: int) -> int:
    """What one seeked input of a file with this picture holds while it decodes."""
    pictures = PICTURES_PER_INPUT + PICTURES_PER_THREAD * max(1, threads)
    return int(picture.width * picture.height * picture.bytes_per_pixel * pictures)


def moments_in_memory(picture: Picture, *, threads: int, budget: int) -> int:
    """How many seeked inputs of this picture one process may hold inside `budget` bytes."""
    return max(1, budget // max(1, input_bytes(picture, threads=threads)))


def picture_from(stream: dict[str, Any]) -> Picture | None:
    """The picture ffprobe describes for a video stream, or None where it does not say."""
    width, height = stream.get("width"), stream.get("height")
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        return None
    layout = str(stream.get("pix_fmt") or "")
    deep = any(mark in layout for mark in ("p9", "p10", "p12", "p14", "p16", "le", "be"))
    if "444" in layout or layout.startswith(("rgb", "bgr", "gbr", "argb", "abgr")):
        samples = 3.0
    elif "422" in layout:
        samples = 2.0
    else:
        samples = 1.5
    return Picture(width=width, height=height, bytes_per_pixel=samples * (2 if deep else 1))


def the_moving_picture(streams: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    """The video stream that is the file: not cover art, and the one that runs the longest.

    An animated AVIF's still cover comes first and unmarked; ties keep the first, as before.
    """
    pictures = [one for one in streams if one.get("codec_type") == "video"]
    if len(pictures) <= 1:
        return pictures[0] if pictures else None

    # Cover art is dropped, not ranked: a poster can state a longer duration than the film.
    moving = [one for one in pictures if not _is_attached_picture(one)] or pictures
    if len(moving) == 1:
        return moving[0]

    def runs_for(stream: dict[str, Any]) -> tuple[int, float]:
        return (_whole(stream.get("nb_frames")) or 0, _positive(stream.get("duration")) or 0.0)

    return max(moving, key=runs_for)


def position_among_pictures(streams: Sequence[dict[str, Any]], video: dict[str, Any] | None) -> int:
    """Where the chosen picture sits among the file's video streams: the `K` of `v:K`."""
    pictures = [one for one in streams if one.get("codec_type") == "video"]
    for position, one in enumerate(pictures):
        if one is video:
            return position
    return 0


def _is_attached_picture(stream: dict[str, Any]) -> bool:
    """Whether ffprobe marked this stream as cover art rather than as content."""
    disposition = stream.get("disposition")
    return isinstance(disposition, dict) and bool(disposition.get("attached_pic"))


def _whole(raw: object) -> int | None:
    if not isinstance(raw, str | int | float) or isinstance(raw, bool):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _positive(raw: object) -> float | None:
    if not isinstance(raw, str | int | float) or isinstance(raw, bool):
        return None
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return None
    return seconds if seconds > 0 else None


@dataclass(frozen=True, slots=True)
class FrameClock:
    """How a file's frames are timed, so a frame is selected by ticks exactly as a seek stops."""

    numerator: int
    denominator: int
    start_us: int
    """Where the file's own timeline starts, in microseconds: the container's start time."""

    def ticks(self, microseconds: int) -> int:
        """Microseconds in this stream's ticks, rounded as the tool rounds them."""
        unit = self.numerator * 1_000_000
        whole = (abs(microseconds) * self.denominator + unit // 2) // unit
        return -whole if microseconds < 0 else whole

    def at(self, microseconds: int) -> int:
        """The tick a seek to this moment stops at, counted from the file's start."""
        return self.ticks(microseconds + self.start_us) - self.ticks(self.start_us)


def clock_from(stream: dict[str, Any] | None, start_time: object) -> FrameClock | None:
    """The clock ffprobe describes for a stream and its file, or None where it does not say."""
    if stream is None:
        return None
    numerator, _, denominator = str(stream.get("time_base") or "").partition("/")
    if not numerator.isdigit() or not denominator.isdigit():
        return None
    if int(numerator) <= 0 or int(denominator) <= 0:
        return None
    start = microseconds_of(str(start_time)) if start_time is not None else None
    return FrameClock(int(numerator), int(denominator), start or 0)


@dataclass(frozen=True, slots=True)
class RawFrames:
    """One consumer's ask for raw pixels: what `raw_moments` takes, less the source."""

    moments: tuple[Moment, ...]
    filters: str
    pixel_format: str
    frame_bytes: int


@dataclass(frozen=True, slots=True)
class FrameFiles:
    """One consumer's ask for image files: what `moments_to_files` takes, less the destination."""

    moments: tuple[Moment, ...]
    filters: str
    suffix: str
    output: tuple[str, ...]


FrameRequest = RawFrames | FrameFiles


def microseconds_of(text: str) -> int | None:
    """A seek's seconds as the tool reads them: whole microseconds, further digits dropped."""
    negative = text.startswith("-")
    whole, _, fraction = (text[1:] if negative else text).partition(".")
    if not whole.isdigit() or (fraction and not fraction.isdigit()):
        return None
    value = int(whole) * 1_000_000 + int((fraction + "000000")[:6])
    return -value if negative else value


_RawKey = tuple[Path, str, str]


_FilesKey = tuple[Path, str, str, tuple[str, ...]]


@dataclass
class PreparedFrames:
    """What one decode produced, keyed the way each consumer asks; a file is handed out once."""

    _raw: dict[_RawKey, dict[tuple[str, ...], bytes | None]] = field(default_factory=dict)
    _files: dict[_FilesKey, dict[tuple[str, ...], Path | None]] = field(default_factory=dict)

    def put_raw(self, source: Path, request: RawFrames, frames: Sequence[bytes | None]) -> None:
        held = self._raw.setdefault((source, request.filters, request.pixel_format), {})
        for moment, frame in zip(request.moments, frames, strict=True):
            held[moment.seek] = frame

    def put_files(self, source: Path, request: FrameFiles, files: Sequence[Path | None]) -> None:
        held = self._files.setdefault(
            (source, request.filters, request.suffix, tuple(request.output)), {}
        )
        for moment, made in zip(request.moments, files, strict=True):
            held[moment.seek] = made

    def raw(
        self, source: Path, moments: Sequence[Moment], *, filters: str, pixel_format: str
    ) -> list[bytes | None] | None:
        """The frames for these moments, or None where any of them was not prepared."""
        held = self._raw.get((source, filters, pixel_format))
        if held is None or any(moment.seek not in held for moment in moments):
            return None
        return [held[moment.seek] for moment in moments]

    def files(
        self,
        source: Path,
        moments: Sequence[Moment],
        *,
        filters: str,
        suffix: str,
        output: Sequence[str],
    ) -> list[Path | None] | None:
        """The files for these moments, taken out of the store; None where any was not prepared."""
        held = self._files.get((source, filters, suffix, tuple(output)))
        if held is None or any(moment.seek not in held for moment in moments):
            return None
        return [held.pop(moment.seek) for moment in moments]

    @property
    def count(self) -> int:
        return sum(len(one) for one in self._raw.values()) + sum(
            len(one) for one in self._files.values()
        )

    def holds(self, source: Path, request: FrameRequest) -> bool:
        """Whether every moment of this ask is here, so the ask would launch nothing."""
        if isinstance(request, RawFrames):
            held: dict[tuple[str, ...], Any] | None = self._raw.get(
                (source, request.filters, request.pixel_format)
            )
        else:
            held = self._files.get((source, request.filters, request.suffix, tuple(request.output)))
        return held is not None and all(moment.seek in held for moment in request.moments)

    def merged(self, other: PreparedFrames) -> PreparedFrames:
        """A store holding both; `other` wins where both hold a moment."""
        both = PreparedFrames()
        for mine in (self, other):
            for raw_key, frames in mine._raw.items():
                both._raw.setdefault(raw_key, {}).update(frames)
            for files_key, files in mine._files.items():
                both._files.setdefault(files_key, {}).update(files)
        return both


# --- Which shape to read a file in ----------------------------------------------------------------


class ReadShape(StrEnum):
    """How a Build reads one file's moments."""

    SEEK = "seek"
    """Each moment as its own seeked input: today's form, and the only one for a long file."""
    DECODE_ONCE = "decode_once"
    """The whole file decoded once, every moment kept as it passes."""


@dataclass(frozen=True, slots=True)
class StorageRead:
    """What the storage a file sits on measured, when it is on another machine."""

    megabytes_per_second: float
    seek_seconds: float


@dataclass(frozen=True, slots=True)
class ReadRates:
    """This machine's measured rates, the way the rule reads them. All at the self-test's 720p."""

    decode_fps: float
    """Frames of 720p one task decodes a second."""
    seek_seconds: float
    """What one moment costs by seeking, within a process that seeks many: the seek and the
    decode from the keyframe before it, on a local disk."""
    storage: StorageRead | None = None
    """The share's own numbers, when the file is on one. None for a local disk."""


#: The pixels the self-test's clip has; a file's cost scales by its pixels against these.
REFERENCE_PIXELS = 1280 * 720


#: What one seeked moment costs, in frames of the same file decoded, by codec, as measured.
FRAMES_PER_SEEK: dict[str, int] = {
    "h264": 50,
    "hevc": 115,
    "vp9": 200,
    "av1": 160,
    "vp8": 35,
    "prores": 27,
}


FRAMES_PER_SEEK_UNKNOWN = FRAMES_PER_SEEK["h264"]


def frames_per_seek(codec: str | None) -> int:
    """What one seeked moment of a file in this codec costs, in its own frames decoded."""
    return FRAMES_PER_SEEK.get(codec or "", FRAMES_PER_SEEK_UNKNOWN)


def choose_read_shape(
    *,
    moments: int,
    duration_seconds: float,
    fps: float,
    width: int,
    height: int,
    size_bytes: int,
    codec: str | None = None,
    rates: ReadRates | None,
) -> ReadShape:
    """Seek or decode once: the file's frames against moments times `frames_per_seek`; on a
    share both sides are priced in seconds from what the share measured."""
    if moments <= 0 or duration_seconds <= 0:
        return ReadShape.SEEK
    frames = duration_seconds * (fps if fps > 0 else 30.0)
    decoding = frames
    seeking = float(moments * frames_per_seek(codec))
    if rates is not None and rates.storage is not None and rates.decode_fps > 0:
        scale = max(1.0, (width * height) / REFERENCE_PIXELS) if width and height else 1.0
        per_frame = scale / rates.decode_fps
        decoding *= per_frame
        seeking = seeking * per_frame + moments * rates.storage.seek_seconds
        if rates.storage.megabytes_per_second > 0:
            decoding = max(decoding, size_bytes / 1_000_000 / rates.storage.megabytes_per_second)
    return ReadShape.DECODE_ONCE if decoding < seeking else ReadShape.SEEK


@dataclass(frozen=True, slots=True)
class FileFacts:
    """What a product needs to know about a file to say which moments it would ask for."""

    asset_id: str
    path: Path
    media_type: str
    duration_ms: int
    width: int
    height: int
    fps: float
    size_bytes: int
    #: How long the picture runs, as the row stores it.
    video_duration_ms: int | None = None
    #: The picture's codec, which prices a seek into the file (`frames_per_seek`).
    vcodec: str | None = None


def seconds(milliseconds: int) -> str:
    """Milliseconds as the decimal seconds ffmpeg's time options take."""
    return f"{milliseconds / 1000:.3f}"


def select_expression(moment: Moment, clock: FrameClock) -> str | None:
    """The `select` that keeps exactly the frame this moment's seek stops at, or None to seek it."""
    if moment.seek == ():
        return "eq(n\\,0)"
    if len(moment.seek) != 2 or moment.seek[0] != "-ss":
        return None
    microseconds = microseconds_of(moment.seek[1])
    if microseconds is None:
        return None
    tick = clock.at(microseconds)
    return f"gte(pts\\,{tick})*(isnan(prev_pts)+lt(prev_pts\\,{tick}))"


def _selectable(moment: Moment) -> bool:
    """Whether the decode-once form takes this moment: a seek written the way it can read."""
    if moment.seek == ():
        return True
    return (
        len(moment.seek) == 2
        and moment.seek[0] == "-ss"
        and microseconds_of(moment.seek[1]) is not None
    )


def _output_of(workspace: Path, r: int, m: int, request: FrameRequest) -> Path:
    suffix = request.suffix if isinstance(request, FrameFiles) else ".raw"
    return workspace / f"{r:02d}-{m:04d}{suffix}"
