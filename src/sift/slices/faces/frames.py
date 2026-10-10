# SPDX-License-Identifier: AGPL-3.0-or-later
"""Getting the pictures out of a file, which is most of what a face pass costs.

It asks for the moments it wants, reduces in the decoder (`FRAME_LONG_SIDE`), reads a GIF once,
and runs the decoder as a separate process with an argument list, never inside the server.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncGenerator, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from sift.kernel import lanes, media, sampling
from sift.kernel.config import Settings
from sift.kernel.log import get_logger
from sift.kernel.ml import pictures
from sift.kernel.subprocess import Priority, SubprocessError
from sift.kernel.subprocess import capture as subprocess_capture
from sift.slices.faces import tuning
from sift.slices.faces.models import Box

log = get_logger(__name__)

#: A guard against a wedged decoder, not a budget: a seek on a network share is slow.
_FRAME_TIMEOUT = 120.0

_WHOLE_FILE_TIMEOUT = 300.0


@dataclass(frozen=True, slots=True)
class Frame:
    """One picture, and when in the file it came from."""

    pixels: np.ndarray
    timestamp_ms: int


def output_size(
    width: int, height: int, long_side: int = tuning.FRAME_LONG_SIDE
) -> tuple[int, int]:
    """The size a frame is reduced to, keeping its shape; both sides even, as decoders require."""
    longest = max(width, height)
    if longest <= long_side:
        return max(2, width - width % 2), max(2, height - height % 2)
    ratio = long_side / longest
    return max(2, int(width * ratio) // 2 * 2), max(2, int(height * ratio) // 2 * 2)


def source_size(width: int, height: int) -> tuple[int, int]:
    """The file's own size, both sides even: the picture a face is cut from. See `windows`."""
    return output_size(width, height, max(2, width, height))


def enlargement(width: int, height: int) -> tuple[float, float]:
    """How many of the file's pixels one pixel of the reduced frame stands for, across and down."""
    working = output_size(width, height)
    own = source_size(width, height)
    return own[0] / working[0], own[1] / working[1]


def reach(box: Box, width: int, height: int) -> tuple[int, int, int, int]:
    """The piece of a `width` by `height` picture to cut round a face, held inside the picture
    so it matches a square cut from the whole: left, top, right, bottom."""
    centre_x, centre_y = box.centre()
    half = box.long_side * tuning.SOURCE_REACH
    left = max(0, int(centre_x - half))
    top = max(0, int(centre_y - half))
    right = min(width, int(np.ceil(centre_x + half)))
    bottom = min(height, int(np.ceil(centre_y + half)))
    return left, top, max(left + 1, right), max(top + 1, bottom)


@dataclass(frozen=True, slots=True)
class Window:
    """A piece of one moment at the file's own size, and where it sits in that moment."""

    pixels: np.ndarray
    left: int
    top: int


def cut(picture: np.ndarray, box: Box) -> Window:
    """The piece of `picture` round a face, as a copy, so the full frame is not kept alive."""
    height, width = picture.shape[:2]
    left, top, right, bottom = reach(box, width, height)
    return Window(pixels=picture[top:bottom, left:right].copy(), left=left, top=top)


def pieces_of(picture: np.ndarray | None, boxes: Sequence[Box]) -> list[Window | None]:
    """Every wanted piece of one frame; the caller runs it on a thread."""
    return [None if picture is None else cut(picture, box) for box in boxes]


def frame_args(
    path: Path,
    timestamp_ms: int,
    *,
    width: int,
    height: int,
    settings: Settings,
) -> list[str]:
    """One picture at a given moment, as raw colour bytes on the output.

    Exact moments, not complete pictures only, which would repeat; zero is not sought, or a still
    loses its only frame.
    """
    return media.raw_frame_args(
        path,
        moment_at(timestamp_ms),
        filters=frame_filter(width, height),
        pixel_format=FRAME_PIXELS,
        settings=settings,
    )


#: Moments one process reads: few enough that the time limit never waits long on one read.
MOMENTS_PER_READ = 8

FRAME_PIXELS = "rgb24"


def moment_at(timestamp_ms: int) -> media.Moment:
    """Where a moment is taken from: a seek, or nothing at the beginning (`frame_args`)."""
    return media.Moment(seek=() if timestamp_ms <= 0 else ("-ss", media.seconds(timestamp_ms)))


def frame_filter(width: int, height: int) -> str:
    """How a moment is shaped: reduced to the working size, the scaler's own default flags."""
    return f"scale={width}:{height}"


def frame_requests(facts: media.FileFacts, *, density: float) -> list[media.FrameRequest]:
    """What a pass over this file asks for, so one task reads it once: a video's moments, or a
    still's working frame and the closer look at its own size (`Reader.windows`)."""
    out_width, out_height = output_size(facts.width, facts.height)
    if facts.media_type == "image":
        own_width, own_height = source_size(facts.width, facts.height)
        sizes = dict.fromkeys(((out_width, out_height), (own_width, own_height)))
        return [
            media.RawFrames(
                moments=(pictures.STILL,),
                filters=frame_filter(width, height),
                pixel_format=FRAME_PIXELS,
                frame_bytes=width * height * 3,
            )
            for width, height in sizes
        ]
    if facts.media_type != "video":
        return []
    return [
        media.RawFrames(
            moments=tuple(
                moment_at(at) for at in sampling.face_frames(facts.duration_ms, density=density)
            ),
            filters=frame_filter(out_width, out_height),
            pixel_format=FRAME_PIXELS,
            frame_bytes=out_width * out_height * 3,
        )
    ]


def all_frames_args(
    path: Path, *, width: int, height: int, settings: Settings, stream: int = 0
) -> list[str]:
    """Every frame of a file in order from one read, for GIFs; `stream` names the moving one."""
    return [
        settings.ffmpeg_path,
        *media.background_flags(settings),
        "-i",
        str(path),
        *(("-map", f"0:v:{stream}") if stream else ()),
        "-vf",
        f"scale={width}:{height}",
        "-pix_fmt",
        "rgb24",
        "-f",
        "rawvideo",
        "pipe:1",
    ]


def split(raw: bytes, width: int, height: int) -> list[np.ndarray]:
    """Cut a stream of raw colour bytes into equal pictures, dropping a trailing part-frame."""
    stride = width * height * 3
    if stride <= 0:
        return []
    count = len(raw) // stride
    return [
        np.frombuffer(raw, dtype=np.uint8, count=stride, offset=index * stride).reshape(
            height, width, 3
        )
        for index in range(count)
    ]


def thin(frames: Sequence[np.ndarray], wanted: int) -> list[int]:
    """Which of a GIF's frames to keep, by index, spread evenly across the whole of it."""
    if len(frames) <= wanted:
        return list(range(len(frames)))
    step = (len(frames) - 1) / (wanted - 1) if wanted > 1 else 0
    return sorted({round(index * step) for index in range(wanted)})


class Reader:
    """Reads pictures out of one file, in whichever way that kind of file is cheapest to read."""

    def __init__(self, settings: Settings, *, priority: Priority = Priority.BACKGROUND) -> None:
        self._settings = settings
        # Background work: whoever is watching something matters more.
        self._priority = priority

    async def stream(
        self,
        path: Path,
        *,
        media_type: str,
        width: int,
        height: int,
        timestamps: Sequence[int],
        long_side: int = tuning.FRAME_LONG_SIDE,
    ) -> AsyncGenerator[Frame]:
        """The pictures for one file, one at a time, so the time limit can stop part-way."""
        out_width, out_height = output_size(width, height, long_side)
        if media_type == "gif":
            for frame in await self._gif(path, out_width, out_height, len(timestamps)):
                yield frame
            return
        if media_type == "image":
            for frame in await self._single(path, 0, out_width, out_height):
                yield frame
            return

        previous: np.ndarray | None = None
        # A handful of moments per process: fewer launches, and the time limit never waits long.
        for start in range(0, len(timestamps), MOMENTS_PER_READ):
            run = timestamps[start : start + MOMENTS_PER_READ]
            raws = await media.raw_moments(
                path,
                [moment_at(timestamp_ms) for timestamp_ms in run],
                filters=frame_filter(out_width, out_height),
                pixel_format=FRAME_PIXELS,
                frame_bytes=out_width * out_height * 3,
                settings=self._settings,
                time_limit=_FRAME_TIMEOUT * len(run),
                priority=self._priority,
            )
            for timestamp_ms, raw in zip(run, raws, strict=True):
                if raw is None:
                    # One unreadable moment is not an unreadable file; the coverage says so.
                    log.warning("faces.frame.unreadable", timestamp_ms=timestamp_ms)
                    continue
                for picture in split(raw, out_width, out_height):
                    # Nearby moments can decode to the very same picture; compared exactly.
                    if previous is not None and np.array_equal(previous, picture):
                        continue
                    previous = picture
                    yield Frame(pixels=picture, timestamp_ms=timestamp_ms)

    async def windows(
        self,
        path: Path,
        *,
        media_type: str,
        width: int,
        height: int,
        wanted: Sequence[tuple[int, Box]],
    ) -> list[Window | None]:
        """Pieces of the file at its own size round faces found in the reduced frame, in order;
        None where a moment would not read again (and throughout for a GIF).

        Faces are measured at the file's own size, or a large file's faces fall under the floor.
        """
        if not wanted or media_type == "gif":
            return [None] * len(wanted)
        own_width, own_height = source_size(width, height)
        own_bytes = own_width * own_height * 3
        if media_type == "image":
            whole = await self._single(path, 0, own_width, own_height)
            picture = whole[0].pixels if whole else None
            return await asyncio.to_thread(pieces_of, picture, [box for _, box in wanted])

        work_width, work_height = output_size(width, height)
        per_read = max(1, MOMENTS_PER_READ * work_width * work_height * 3 // own_bytes)
        moments = sorted({timestamp_ms for timestamp_ms, _ in wanted})
        answer: dict[int, list[Window | None]] = {}
        for start in range(0, len(moments), per_read):
            run = moments[start : start + per_read]
            raws = await media.raw_moments(
                path,
                [moment_at(timestamp_ms) for timestamp_ms in run],
                filters=frame_filter(own_width, own_height),
                pixel_format=FRAME_PIXELS,
                frame_bytes=own_bytes,
                settings=self._settings,
                time_limit=_FRAME_TIMEOUT * len(run),
                priority=self._priority,
            )
            for timestamp_ms, raw in zip(run, raws, strict=True):
                split_up = [] if raw is None else split(raw, own_width, own_height)
                picture = split_up[0] if split_up else None
                if picture is None:
                    log.warning("faces.frame.unreadable_at_size", timestamp_ms=timestamp_ms)
                answer[timestamp_ms] = await asyncio.to_thread(
                    pieces_of, picture, [box for at, box in wanted if at == timestamp_ms]
                )
        # Back into the order asked for.
        position = {timestamp_ms: 0 for timestamp_ms in answer}
        ordered: list[Window | None] = []
        for timestamp_ms, _ in wanted:
            ordered.append(answer[timestamp_ms][position[timestamp_ms]])
            position[timestamp_ms] += 1
        return ordered

    async def _single(self, path: Path, timestamp_ms: int, width: int, height: int) -> list[Frame]:
        # A still the task already decoded for every pass is not decoded again.
        held = (
            pictures.held(path, filters=frame_filter(width, height), pixel_format=FRAME_PIXELS)
            if timestamp_ms <= 0
            else None
        )
        if held is not None:
            return [
                Frame(pixels=picture, timestamp_ms=timestamp_ms)
                for picture in split(held, width, height)
            ]
        argv = frame_args(
            path,
            timestamp_ms,
            width=width,
            height=height,
            settings=self._settings,
        )
        decoded = await self._pictures(argv, width, height, _FRAME_TIMEOUT, path=path)
        return [Frame(pixels=picture, timestamp_ms=timestamp_ms) for picture in decoded]

    async def _pictures(
        self, argv: list[str], width: int, height: int, time_limit: float, *, path: Path
    ) -> list[np.ndarray]:
        """Read a decoder's raw output as whole pictures, uncapped, so nothing is cut short."""
        try:
            async with lanes.reading(path):
                raw = await subprocess_capture(argv, time_limit=time_limit, priority=self._priority)
        except SubprocessError as error:
            raise media.FFmpegError(str(error)) from error
        return split(raw, width, height)

    async def _gif(self, path: Path, width: int, height: int, wanted: int) -> list[Frame]:
        stream = await media.moving_stream_of(
            path, settings=self._settings, priority=self._priority
        )
        argv = all_frames_args(
            path, width=width, height=height, settings=self._settings, stream=stream
        )
        every = await self._pictures(argv, width, height, _WHOLE_FILE_TIMEOUT, path=path)
        if not every:
            return []
        keep = thin(every, max(1, wanted))
        # A GIF's position in the sequence, not an invented time.
        return [Frame(pixels=every[index], timestamp_ms=index) for index in keep]


def long_side_scale(long_side: int) -> str:
    """The filter that brings a picture's longer side down to `long_side`, keeping its shape."""
    return f"scale='if(gte(iw,ih),min({long_side},iw),-2)':'if(gte(iw,ih),-2,min({long_side},ih))'"


#: The long side a reference picture is decoded to for finding its face.
REFERENCE_LONG_SIDE = 2048


def crop_of(share: tuple[float, float, float, float]) -> str:
    """The filter keeping one piece of a picture at its own size, its edges as shares."""
    left, top, right, bottom = share
    return f"crop=w=iw*{right - left:.6f}:h=ih*{bottom - top:.6f}:x=iw*{left:.6f}:y=ih*{top:.6f}"


async def decode_image(
    path: Path,
    settings: Settings,
    *,
    long_side: int = REFERENCE_LONG_SIDE,
    piece: tuple[float, float, float, float] | None = None,
) -> np.ndarray | None:
    """One picture from an image file for reference import, or one `piece` of it, decoded large
    in a separate process, as a PPM that carries its own size."""
    scale = long_side_scale(long_side)
    argv = [
        settings.ffmpeg_path,
        *media.background_flags(settings),
        "-i",
        str(path),
        "-frames:v",
        "1",
        "-vf",
        scale if piece is None else f"{crop_of(piece)},{scale}",
        "-f",
        "image2pipe",
        "-vcodec",
        "ppm",
        "pipe:1",
    ]
    try:
        # `reads=path` takes a place in the storage's lane (`kernel.media.run`).
        raw = await media.run(argv, time_limit=_FRAME_TIMEOUT, capture=True, reads=path)
    except media.FFmpegError as error:
        log.warning("faces.reference.unreadable", path=str(path), detail=str(error))
        return None
    return await asyncio.to_thread(_ppm_picture, raw)


#: A binary PPM's header, which carries the size, so the in-memory decode needs no probe.
_PPM_HEADER = re.compile(rb"\AP6\s+(\d+)\s+(\d+)\s+255\s")


async def decode_picture_bytes(
    blob: bytes, settings: Settings, *, long_side: int = REFERENCE_LONG_SIDE
) -> np.ndarray | None:
    """One picture from bytes a stranger's server sent, for a starter reference, or None.

    Piped to the decoder, never written to disk.
    """
    argv = [
        settings.ffmpeg_path,
        *media.background_flags(settings),
        "-i",
        "pipe:0",
        "-frames:v",
        "1",
        "-vf",
        long_side_scale(long_side),
        "-f",
        "image2pipe",
        "-vcodec",
        "ppm",
        "pipe:1",
    ]
    try:
        raw = await media.run(argv, time_limit=_FRAME_TIMEOUT, capture=True, stdin=blob)
    except media.FFmpegError as error:
        log.warning("faces.starter.unreadable", detail=str(error))
        return None
    return await asyncio.to_thread(_ppm_picture, raw)


def _ppm_picture(raw: bytes) -> np.ndarray | None:
    """One PPM frame as an array, or None where the bytes are not one; on a thread."""
    header = _PPM_HEADER.match(raw)
    if header is None:
        return None
    width, height = int(header.group(1)), int(header.group(2))
    body = raw[header.end() : header.end() + width * height * 3]
    if width <= 0 or height <= 0 or len(body) != width * height * 3:
        return None
    return np.frombuffer(body, dtype=np.uint8).reshape(height, width, 3).copy()
