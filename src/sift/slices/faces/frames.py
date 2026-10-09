# SPDX-License-Identifier: AGPL-3.0-or-later
"""Getting the pictures out of a file, which is most of what a face pass costs.

**Reading the frames is about six sevenths of the whole pass**, against about an eighth for
finding and recognizing the faces in them put together. So what happens
here is worth several times more than any tuning of the models, and it is worth measuring rather
than reasoning about.

**It asks for the moments it wants, not for whole frames.** Video does not store every frame
outright; it stores a complete picture every few seconds and, in between, only what changed.
Asking for complete pictures only saves no time over a mixed set of real files (and is slower
on the longest) while costing most of the moments asked for, because several nearby moments come
back as the same complete picture. See `frame_args`.

**It reduces the picture on the way out**, inside the decoder rather than after a much larger one
has been copied through a pipe. A frame arrives at most `FRAME_LONG_SIDE` (1280) pixels on its
long side; the detector itself works at 640 square, on a further reduction of its own. Reducing
further than `FRAME_LONG_SIDE` already does is not worth trying: halving it was measured and
changed the time by nothing at all, because the cost is seeking and reconstructing rather than
scaling.

**It reads a GIF once.** A GIF stores every frame as a change from the one before, so
seeking to the tenth moment means replaying the first nine. Thirty seeks is thirty replays. One
read produces every frame, and the ones wanted are picked out of it.

The decoder is a separate process throughout, handed an argument list, never a shell string and
never a library sharing this process. Malformed media is the thing most likely to be pointed at
Sift, and a decoder is the last thing that should be able to take the server down with it.
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
from sift.kernel.subprocess import Priority, SubprocessError
from sift.kernel.subprocess import capture as subprocess_capture
from sift.slices.faces import tuning
from sift.slices.faces.models import Box

log = get_logger(__name__)

#: How long a single frame extraction may take. Generous: a seek in a large file on a network share
#: is slow and still working. This is a guard against a wedged decoder, not a budget.
_FRAME_TIMEOUT = 120.0

#: How long reading every frame of a GIF may take.
_WHOLE_FILE_TIMEOUT = 300.0


@dataclass(frozen=True, slots=True)
class Frame:
    """One picture, and when in the file it came from."""

    pixels: np.ndarray
    timestamp_ms: int


def output_size(
    width: int, height: int, long_side: int = tuning.FRAME_LONG_SIDE
) -> tuple[int, int]:
    """The size a frame is reduced to, keeping its shape and both sides even.

    Both sides even because several decoders refuse an odd dimension outright, and a face pass that
    worked on every file except the ones whose height happened to be odd would be a very confusing
    bug to be handed.
    """
    longest = max(width, height)
    if longest <= long_side:
        return max(2, width - width % 2), max(2, height - height % 2)
    ratio = long_side / longest
    return max(2, int(width * ratio) // 2 * 2), max(2, int(height * ratio) // 2 * 2)


def source_size(width: int, height: int) -> tuple[int, int]:
    """The file's own size, both sides even: the picture a face is cut from. See `windows`."""
    return output_size(width, height, max(2, width, height))


def enlargement(width: int, height: int) -> tuple[float, float]:
    """How many of the file's own pixels one pixel of the reduced frame stands for, across and down.

    `(1.0, 1.0)` for a file no larger than the reduced frame, which is most of what this ever says
    for anything but photographs and high-definition video. Two numbers rather than one because
    each side is rounded to an even number separately.
    """
    working = output_size(width, height)
    own = source_size(width, height)
    return own[0] / working[0], own[1] / working[1]


def reach(box: Box, width: int, height: int) -> tuple[int, int, int, int]:
    """The piece of a picture `width` by `height` to cut round a face: left, top, right, bottom.

    Held inside the picture, so where the face is at the edge the piece stops at the same edge the
    picture does, which is what makes a square warped out of the piece the square that would
    have been warped out of the whole picture. See `tuning.SOURCE_REACH`.
    """
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
    """The piece of `picture` round a face, as a COPY.

    A copy on purpose: a slice of an array keeps the whole of it alive, and the whole of it is a
    full-size frame (eleven megabytes at 1440p and twenty-five at 4K), which is exactly what
    reading pieces rather than frames exists not to hold.
    """
    height, width = picture.shape[:2]
    left, top, right, bottom = reach(box, width, height)
    return Window(pixels=picture[top:bottom, left:right].copy(), left=left, top=top)


def pieces_of(picture: np.ndarray | None, boxes: Sequence[Box]) -> list[Window | None]:
    """Every wanted piece of one frame, cut on a THREAD by the caller: each cut copies a face-sized
    window out of a full-size frame, and a run of them over a 4K frame is milliseconds of
    memory traffic that the event loop has no business waiting through."""
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

    **Not complete pictures only.** Reconstructing only whole frames saves nothing over decoding
    forward to an exact moment (the same seconds, and slower on the longest files), and complete
    pictures are seconds apart, so several nearby moments come back as the very same picture: ten
    moments on a five-second clip would produce three, and a deeper look would be no deeper.

    So the moments are read as asked for: a deeper look is actually deeper, and a face is cut from
    the moment it was found at rather than from whichever whole picture happened to be nearest.

    Seeking to zero is not a no-op and is skipped. A still image is presented as a video one frame
    long, and a seek to zero on it lands *on* that frame's moment rather than before it: the frame
    counts as already past, nothing is written, and the decoder exits reporting success.
    """
    return media.raw_frame_args(
        path,
        moment_at(timestamp_ms),
        filters=frame_filter(width, height),
        pixel_format=FRAME_PIXELS,
        settings=settings,
    )


#: How many moments one process reads. A launch per run rather than per moment, and a run short
#: enough that the time limit, which is checked between moments, never waits long on one read.
MOMENTS_PER_READ = 8

FRAME_PIXELS = "rgb24"


def moment_at(timestamp_ms: int) -> media.Moment:
    """Where a moment is taken from: a seek, or nothing at all at the beginning. See `frame_args`
    for why seeking to zero loses the frame of a still."""
    return media.Moment(seek=() if timestamp_ms <= 0 else ("-ss", media.seconds(timestamp_ms)))


def frame_filter(width: int, height: int) -> str:
    """How a moment is shaped: reduced to the working size, the scaler's own default flags."""
    return f"scale={width}:{height}"


def frame_requests(facts: media.FileFacts, *, density: float) -> list[media.FrameRequest]:
    """What a pass over this video would ask the kernel for, so a Build can read it once for
    everything. The same ladder, size and filter `Reader.stream` asks with; the test beside it
    proves the two agree. Only a video: a still and a GIF are read their own way."""
    if facts.media_type != "video":
        return []
    out_width, out_height = output_size(facts.width, facts.height)
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
    """Every frame of a file, in order, from one read. For GIFs, which cannot be seeked.

    `stream` is which video stream moves (`media.moving_stream_of`): an animated AVIF holds a
    still cover first and its frames second, and ffmpeg's own choice is the cover, one frame.
    The first stream is named by nothing, so a GIF's command names no stream.
    """
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
    """Cut a stream of raw colour bytes into pictures.

    There are no markers between them and none are needed: every frame is exactly the same number
    of bytes, because the size was fixed when it was asked for. A trailing part-frame (which is
    what a decoder killed mid-write leaves) is dropped rather than reshaped into a picture whose
    bottom half is whatever came next.
    """
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
    """Which of a GIF's frames to keep, spread evenly across the whole of it.

    Indexes rather than the frames themselves, so the caller can keep the timing that goes with
    them. Evenly spread rather than the first N: a GIF's first thirty frames are usually the
    same moment, and its point is what happens later.
    """
    if len(frames) <= wanted:
        return list(range(len(frames)))
    step = (len(frames) - 1) / (wanted - 1) if wanted > 1 else 0
    return sorted({round(index * step) for index in range(wanted)})


class Reader:
    """Reads pictures out of one file, in whichever way that kind of file is cheapest to read."""

    def __init__(self, settings: Settings, *, priority: Priority = Priority.BACKGROUND) -> None:
        self._settings = settings
        # Background work by definition: whoever is watching something right now matters more than
        # a face index that will finish either way.
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
        """The pictures for one file, one at a time, at the moments asked for.

        One at a time rather than all together because a caller that can stop reading is what lets
        the time limit stop a pass part of the way through a long file, leaving the rest for a
        later pass to carry on from.
        """
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
        # A handful of moments per process rather than one. One process per moment would be sixty
        # opens and seeks of the same file for a deep pass; one process for ALL of them would
        # leave the time limit waiting on a single read of the whole plan. So the moments go in
        # runs: a pass the limit stops has read at most one run past it, and a pass that reads to
        # the end has paid a launch per run rather than per moment.
        for start in range(0, len(timestamps), MOMENTS_PER_READ):
            run = timestamps[start : start + MOMENTS_PER_READ]
            pictures = await media.raw_moments(
                path,
                [moment_at(timestamp_ms) for timestamp_ms in run],
                filters=frame_filter(out_width, out_height),
                pixel_format=FRAME_PIXELS,
                frame_bytes=out_width * out_height * 3,
                settings=self._settings,
                time_limit=_FRAME_TIMEOUT * len(run),
                priority=self._priority,
            )
            for timestamp_ms, raw in zip(run, pictures, strict=True):
                if raw is None:
                    # One unreadable moment is not an unreadable file: a truncated tail is common
                    # and everything before it is perfectly good. Recorded, and the pass carries
                    # on with what it has, which is exactly what the coverage figure is for.
                    log.warning("faces.frame.unreadable", timestamp_ms=timestamp_ms)
                    continue
                for picture in split(raw, out_width, out_height):
                    # Two nearby moments in a file with few complete pictures come back as the
                    # very same picture. An exact comparison rather than a similarity one: these
                    # are the same decoded bytes or they are not, and a "nearly the same" test
                    # would throw away a genuine cut between two static shots.
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
        """Pieces of the file at its OWN size, round faces already found in the reduced frame.

        `wanted` is a moment and a box in the file's own pixels, and the answer is in the same
        order; None where the moment could not be read again, which the caller treats as "use the
        reduced frame", never as "no face".

        **Why this exists.** Finding a face needs nothing like the file's size (the detector reads
        a 640 square), so the pass reads reduced frames and that is right. Cutting a face out and
        measuring it is different: the recognizer reads a 112 square, and a face that is 120 pixels
        in a 1440p file is 60 in the reduced frame. Cut from there it is refused as too small, or
        stretched and described from detail that Sift itself threw away: every face in a 2560x1440
        clip can be refused that way while none would be at its own size.

        Only the moments that hold a face about to be described are read (at most two per
        appearance), and in runs no heavier in bytes than one ordinary run of reduced frames, so
        the most this holds at a time is what `stream` already does. A GIF is not read again:
        it cannot be seeked, and a second read of every frame for two of them is not worth it, so
        it answers None throughout and its faces are cut from the frames the pass already has.
        """
        if not wanted or media_type == "gif":
            return [None] * len(wanted)
        own_width, own_height = source_size(width, height)
        own_bytes = own_width * own_height * 3
        if media_type == "image":
            pictures = await self._pictures(
                frame_args(path, 0, width=own_width, height=own_height, settings=self._settings),
                own_width,
                own_height,
                _FRAME_TIMEOUT,
                path=path,
            )
            picture = pictures[0] if pictures else None
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
        # Back into the order asked for: each moment's pieces were cut in `wanted`'s order.
        position = {timestamp_ms: 0 for timestamp_ms in answer}
        ordered: list[Window | None] = []
        for timestamp_ms, _ in wanted:
            ordered.append(answer[timestamp_ms][position[timestamp_ms]])
            position[timestamp_ms] += 1
        return ordered

    async def _single(self, path: Path, timestamp_ms: int, width: int, height: int) -> list[Frame]:
        argv = frame_args(
            path,
            timestamp_ms,
            width=width,
            height=height,
            settings=self._settings,
        )
        pictures = await self._pictures(argv, width, height, _FRAME_TIMEOUT, path=path)
        return [Frame(pixels=picture, timestamp_ms=timestamp_ms) for picture in pictures]

    async def _pictures(
        self, argv: list[str], width: int, height: int, time_limit: float, *, path: Path
    ) -> list[np.ndarray]:
        """Read a decoder's raw output as whole pictures.

        **The capturing call rather than the ordinary one because the output is not capped there.**
        A frame here is 1280 across and raw, so a single one is nearly three megabytes and every
        frame of a GIF is hundreds. The ordinary call keeps the first sixteen megabytes and
        discards the rest, and `split` below would turn a truncated read into a shorter list of
        perfectly valid pictures: a file quietly examined in part, with nothing saying so.

        Nothing is lost by not streaming. This collects every picture before returning one, so it
        never wanted the pieces as they arrived; what it wanted was all of the bytes.
        """
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
        pictures = await self._pictures(argv, width, height, _WHOLE_FILE_TIMEOUT, path=path)
        if not pictures:
            return []
        keep = thin(pictures, max(1, wanted))
        # A GIF carries no reliable per-frame timing here, and inventing one would put a
        # made-up second on screen next to somebody's name. The position in the sequence is the
        # honest answer, and it is what a viewer would scrub to.
        return [Frame(pixels=pictures[index], timestamp_ms=index) for index in keep]


def long_side_scale(long_side: int) -> str:
    """The filter that brings a picture's LONGER side down to `long_side`, keeping its shape.

    Both sides rather than the width alone. A tall portrait held only at its width stays thousands
    of pixels high, and its decoded bytes run past what a tool's captured answer may hold, so it
    arrives cut short and reads as no picture at all. Capped on both sides, the largest answer is
    a square of `long_side`, well inside that bound.
    """
    return f"scale='if(gte(iw,ih),min({long_side},iw),-2)':'if(gte(iw,ih),-2,min({long_side},ih))'"


#: The long side a reference picture is decoded to for finding its face.
REFERENCE_LONG_SIDE = 2048


def crop_of(share: tuple[float, float, float, float]) -> str:
    """The filter that keeps one piece of a picture at its own size: left, top, right and bottom
    as shares of the whole, so the piece is named without knowing the file's size."""
    left, top, right, bottom = share
    return f"crop=w=iw*{right - left:.6f}:h=ih*{bottom - top:.6f}:x=iw*{left:.6f}:y=ih*{top:.6f}"


async def decode_image(
    path: Path,
    settings: Settings,
    *,
    long_side: int = REFERENCE_LONG_SIDE,
    piece: tuple[float, float, float, float] | None = None,
) -> np.ndarray | None:
    """One picture from an image file, for reference import, or one `piece` of it at the file's
    own size (see `crop_of`).

    Larger than a video frame is reduced to, because a reference image is looked at only once and
    its landmarks are what everything about that person is aligned by. The decoder is still a
    separate process: these files arrive from a folder somebody was handed, which is exactly the
    case where a malformed one should not be parsed inside the server.

    Asked for a PPM, whose header carries the size the decoder actually produced, so nothing here
    predicts the scaled size and nothing can cut the bytes into rows of the wrong length.
    """
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
        # `reads=path` is what takes a place in the storage's lane (see `kernel.media.run`), so a
        # reference import does not read the share outside the count the lane keeps. A folder of
        # reference pictures is a burst of small reads from wherever somebody put them.
        raw = await media.run(argv, time_limit=_FRAME_TIMEOUT, capture=True, reads=path)
    except media.FFmpegError as error:
        log.warning("faces.reference.unreadable", path=str(path), detail=str(error))
        return None
    return await asyncio.to_thread(_ppm_picture, raw)


#: A binary PPM's header: magic, width, height, the largest value. What ffmpeg writes ahead of the
#: pixels when asked for `ppm`, and the reason the in-memory decode below needs no probe: the answer
#: carries its own size, where raw video carries none.
_PPM_HEADER = re.compile(rb"\AP6\s+(\d+)\s+(\d+)\s+255\s")


async def decode_picture_bytes(
    blob: bytes, settings: Settings, *, long_side: int = REFERENCE_LONG_SIDE
) -> np.ndarray | None:
    """One picture from bytes a stranger's server sent, for a starter reference. None if unreadable.

    `decode_image` for a picture that is not a file and must not become one: a stash-box's photo is
    fetched over the network, and like an uploaded cover (`CoverPictures.receive`) its bytes are
    piped into the decoder and never written under a name on the disk: there is no instant at
    which somebody else's bytes sit there for anything to open. The decoder is the same separate
    process, at the same size, so a starter is judged on the same picture a folder's would be.
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
    """One PPM frame as an array, or None where the bytes are not one. On a thread: the copy is
    the whole picture, megabytes the event loop should not stand behind."""
    header = _PPM_HEADER.match(raw)
    if header is None:
        return None
    width, height = int(header.group(1)), int(header.group(2))
    body = raw[header.end() : header.end() + width * height * 3]
    if width <= 0 or height <= 0 or len(body) != width * height * 3:
        return None
    return np.frombuffer(body, dtype=np.uint8).reshape(height, width, 3).copy()
