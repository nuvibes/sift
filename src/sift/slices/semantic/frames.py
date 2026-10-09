# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading the moments of one file at the model's size, launched from a thread, never the loop."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from sift.kernel import lanes, media, sampling
from sift.kernel.config import Settings
from sift.kernel.log import get_logger
from sift.kernel.subprocess import Priority, SubprocessError
from sift.kernel.subprocess import capture as subprocess_capture
from sift.slices.semantic.embed import FRAME_SIZE

log = get_logger(__name__)

#: A guard against a hung decoder, not a budget for a slow disk.
_FRAME_TIMEOUT = 60.0

_WHOLE_FILE_TIMEOUT = 120.0


@dataclass(frozen=True, slots=True)
class Moment:
    """One picture and where in the file it came from."""

    pixels: np.ndarray
    at_ms: int


def moments_for(duration_ms: int) -> tuple[int, ...]:
    """Which moments of a file of this length to describe: the kernel's one ladder."""
    return sampling.sample_frames(duration_ms)


FRAME_FILTER = f"scale={FRAME_SIZE}:{FRAME_SIZE}:flags=bilinear"
FRAME_PIXELS = "rgb24"
_FRAME_BYTES = FRAME_SIZE * FRAME_SIZE * 3


def moment_at(at_ms: int) -> media.Moment:
    """Where a moment is taken from; a seek to zero would lose a still's only frame."""
    return media.Moment(seek=() if at_ms <= 0 else ("-ss", media.seconds(at_ms)))


async def frame_requests(facts: media.FileFacts) -> list[media.FrameRequest]:
    """What describing this video would ask the kernel for, so a Build can read it once."""
    if facts.media_type != "video":
        return []
    return [
        media.RawFrames(
            moments=tuple(moment_at(at) for at in moments_for(facts.duration_ms)),
            filters=FRAME_FILTER,
            pixel_format=FRAME_PIXELS,
            frame_bytes=_FRAME_BYTES,
        )
    ]


def frame_args(path: Path, at_ms: int, *, settings: Settings) -> list[str]:
    """One moment, squashed to the square the model reads, built from `_video`'s pieces."""
    return media.raw_frame_args(
        path, moment_at(at_ms), filters=FRAME_FILTER, pixel_format=FRAME_PIXELS, settings=settings
    )


def all_frames_args(path: Path, *, settings: Settings, stream: int = 0) -> list[str]:
    """Every frame of a file, in order, from one read, for GIFs, which cannot be seeked."""
    return [
        settings.ffmpeg_path,
        *media.background_flags(settings),
        "-i",
        str(path),
        *(("-map", f"0:v:{stream}") if stream else ()),
        "-sws_flags",
        "bilinear",
        "-vf",
        f"scale={FRAME_SIZE}:{FRAME_SIZE}",
        "-pix_fmt",
        "rgb24",
        "-f",
        "rawvideo",
        "pipe:1",
    ]


def split(raw: bytes) -> list[np.ndarray]:
    """Cut a stream of raw colour bytes into whole pictures, dropping a trailing part."""
    stride = FRAME_SIZE * FRAME_SIZE * 3
    whole = len(raw) // stride
    return [
        np.frombuffer(raw, dtype=np.uint8, count=stride, offset=index * stride).reshape(
            FRAME_SIZE, FRAME_SIZE, 3
        )
        for index in range(whole)
    ]


def thin(count: int, wanted: int) -> list[int]:
    """Which of a GIF's frames to keep, spread evenly across the whole of it."""
    if count <= wanted:
        return list(range(count))
    step = (count - 1) / (wanted - 1) if wanted > 1 else 0
    return sorted({round(index * step) for index in range(wanted)})


class Reader:
    """Reads the moments of one file, in whichever way that kind of file is cheapest to read."""

    def __init__(self, settings: Settings, *, priority: Priority = Priority.BACKGROUND) -> None:
        self._settings = settings
        self._priority = priority

    async def read(self, path: Path, *, media_type: str, duration_ms: int) -> list[Moment]:
        """Every moment worth describing in one file."""
        if media_type == "image":
            return [Moment(pixels=picture, at_ms=0) for picture in await self._single(path, 0)]
        if media_type == "gif":
            return await self._gif(path, duration_ms)
        return await self._video(path, duration_ms)

    async def _video(self, path: Path, duration_ms: int) -> list[Moment]:
        """Every moment of the ladder from one process, in order, None where one read nothing."""
        wanted = moments_for(duration_ms)
        pictures = await media.raw_moments(
            path,
            [moment_at(at_ms) for at_ms in wanted],
            filters=FRAME_FILTER,
            pixel_format=FRAME_PIXELS,
            frame_bytes=_FRAME_BYTES,
            settings=self._settings,
            time_limit=_WHOLE_FILE_TIMEOUT,
            priority=self._priority,
        )
        moments: list[Moment] = []
        previous: np.ndarray | None = None
        for at_ms, raw in zip(wanted, pictures, strict=True):
            if raw is None:
                # One unreadable moment is not an unreadable file: the pass carries on.
                log.warning("semantic.frame.unreadable", at_ms=at_ms)
                continue
            for picture in split(raw):
                # Exact: a near test would lose a real cut between two static shots.
                if previous is not None and np.array_equal(previous, picture):
                    continue
                previous = picture
                moments.append(Moment(pixels=picture, at_ms=at_ms))
        return moments

    async def _gif(self, path: Path, duration_ms: int) -> list[Moment]:
        stream = await media.moving_stream_of(
            path, settings=self._settings, priority=self._priority
        )
        raw = await self._capture(
            all_frames_args(path, settings=self._settings, stream=stream),
            _WHOLE_FILE_TIMEOUT,
            path=path,
        )
        pictures = split(raw)
        if not pictures:
            return []
        wanted = max(1, len(moments_for(duration_ms)))
        # Its position in the sequence, since a GIF carries no reliable per-frame timing here.
        return [
            Moment(pixels=pictures[index], at_ms=index) for index in thin(len(pictures), wanted)
        ]

    async def _single(self, path: Path, at_ms: int) -> list[np.ndarray]:
        argv = frame_args(path, at_ms, settings=self._settings)
        return split(await self._capture(argv, _FRAME_TIMEOUT, path=path))

    async def _capture(self, argv: list[str], time_limit: float, *, path: Path) -> bytes:
        try:
            async with lanes.reading(path):
                return await subprocess_capture(
                    argv, time_limit=time_limit, priority=self._priority
                )
        except SubprocessError as error:
            raise media.FFmpegError(str(error)) from error
