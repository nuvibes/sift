# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading the moments of one file, at the size the model reads.

Two decisions here are not obvious.

**The decoder is launched from a thread, never from the event loop.** Starting a process out of a
large Python process is not free, and the ordinary async way to do it runs the launch *on* the loop,
which stops the whole application while it happens. One launch is a blink; a pass over a long video
is thirty of them, several files at once, and together they can hold the loop for minutes with
nothing reporting it but an application that has stopped answering. So every launch goes through the
kernel's threaded helper, at background priority: whoever is watching something right now matters
more than an index that will finish either way.

**A seek to zero is skipped rather than passed.** A still picture is presented to the decoder as a
video one frame long, and seeking to zero on it lands *on* that frame's moment rather than before
it: the frame counts as already past, nothing is written, and the decoder exits reporting success.
Every photo would get no description at all, with a green suite, because the version of the
decoder a machine happens to have behaves differently from the one that ships.

The frames are **squashed** to the square rather than cropped to it. That is what the model's own
published preparation does, and matching it is not cosmetic: a model shown pictures framed
differently from its training returns numbers that are confidently wrong rather than an error.
"""

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

#: How long one moment may take to read before it is abandoned. Generous: this is a guard against a
#: decoder that has hung, not a budget for a slow disk.
_FRAME_TIMEOUT = 60.0

#: How long reading a whole GIF in one pass may take. A GIF cannot be seeked cheaply,
#: so it is read once from end to end, which is more work in one call than a single seek.
_WHOLE_FILE_TIMEOUT = 120.0


@dataclass(frozen=True, slots=True)
class Moment:
    """One picture and where in the file it came from."""

    pixels: np.ndarray
    at_ms: int


def moments_for(duration_ms: int) -> tuple[int, ...]:
    """Which moments of a file of this length to describe.

    The kernel's ladder, unchanged and deliberately not a second one: a short clip gets a frame a
    second, a long video gets thirty spread across it, and every feature that looks inside a video
    asks the same question and gets the same answer. There is a guard test asserting only one
    ladder exists in the tree.

    Thirty frames on a two-hour video is one every four minutes, which is coarse for jumping to a
    moment. That is a known limit of the shared ladder rather than something to fix privately here:
    a rung for this feature is a change to the ladder, made with everything else that reads it.
    """
    return sampling.sample_frames(duration_ms)


#: How a picture is shaped for the model: squashed to its square, bilinear. Named on the filter
#: rather than as a global `-sws_flags`, so the one-moment command and the many-moment one say it
#: the same way; the output is byte-identical to the global form.
FRAME_FILTER = f"scale={FRAME_SIZE}:{FRAME_SIZE}:flags=bilinear"
FRAME_PIXELS = "rgb24"
_FRAME_BYTES = FRAME_SIZE * FRAME_SIZE * 3


def moment_at(at_ms: int) -> media.Moment:
    """Where a moment is taken from: a seek, or nothing at all at the beginning. Seeking to zero
    is not a no-op: a still presented as a one-frame video loses its frame to it."""
    return media.Moment(seek=() if at_ms <= 0 else ("-ss", media.seconds(at_ms)))


async def frame_requests(facts: media.FileFacts) -> list[media.FrameRequest]:
    """What describing this video would ask the kernel for, so a Build can read it once for
    everything. The same ladder, filter and pixels `Reader._video` asks with; the test beside it
    proves the two agree. Only a video: a still and a GIF are read their own way."""
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
    """One moment, squashed to the square the model reads. The one-moment form of what `_video`
    reads thirty of through one process; built from the same pieces so the two cannot differ."""
    return media.raw_frame_args(
        path, moment_at(at_ms), filters=FRAME_FILTER, pixel_format=FRAME_PIXELS, settings=settings
    )


def all_frames_args(path: Path, *, settings: Settings, stream: int = 0) -> list[str]:
    """Every frame of a file, in order, from one read. For GIFs, which cannot be seeked.

    `stream` is which video stream moves (`media.moving_stream_of`): an animated AVIF holds a
    still cover first and its frames second, and ffmpeg's own choice is the cover, one frame.
    The first stream is named by nothing, so a GIF's command is the plain one.
    """
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
    """Cut a stream of raw colour bytes into whole pictures.

    There are no markers between them and none are needed: every picture is exactly the same
    number of bytes. A trailing part-picture is dropped rather than padded: a decoder killed at
    its time limit leaves one, and half a picture described as a whole one is numbers about
    nothing.
    """
    stride = FRAME_SIZE * FRAME_SIZE * 3
    whole = len(raw) // stride
    return [
        np.frombuffer(raw, dtype=np.uint8, count=stride, offset=index * stride).reshape(
            FRAME_SIZE, FRAME_SIZE, 3
        )
        for index in range(whole)
    ]


def thin(count: int, wanted: int) -> list[int]:
    """Which of a GIF's frames to keep, spread evenly across the whole of it.

    Evenly spread rather than the first few: a GIF's opening frames are usually the same
    moment, and its point is what happens later.
    """
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
        """Every moment worth describing in one file.

        A still is one picture. A GIF is read once from end to end, because seeking one is
        more expensive than decoding all of it. A video is seeked to each moment of the ladder.
        """
        if media_type == "image":
            return [Moment(pixels=picture, at_ms=0) for picture in await self._single(path, 0)]
        if media_type == "gif":
            return await self._gif(path, duration_ms)
        return await self._video(path, duration_ms)

    async def _video(self, path: Path, duration_ms: int) -> list[Moment]:
        """Every moment of the ladder from ONE process, in order.

        One process per moment would be thirty opens and seeks of the same file, which over a
        network share is thirty round trips before a single picture is described. The kernel
        takes every moment as its own seeked input and hands the pictures back in order; a moment
        that read nothing is None in its place, and a chunk that came back short is read a moment
        at a time so nothing is mislabelled with the wrong second.
        """
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
                # One unreadable moment is not an unreadable file. A truncated tail is common and
                # everything before it is perfectly good, so it is recorded and the pass carries on
                # with what it has.
                log.warning("semantic.frame.unreadable", at_ms=at_ms)
                continue
            for picture in split(raw):
                # Two nearby moments in a file with few complete pictures decode to the very same
                # picture. Compared exactly rather than approximately: these are the same bytes or
                # they are not, and a "nearly the same" test would throw away a real cut between
                # two static shots.
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
        # A GIF carries no reliable per-frame timing here, and inventing one would put a
        # made-up second on screen. The position in the sequence is the honest answer, and it is
        # what a viewer would scrub to: the same choice the face pass makes for the same reason.
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
