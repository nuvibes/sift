# SPDX-License-Identifier: AGPL-3.0-or-later
"""Cut the two crops of a frame a watermark is drawn in, in one decoder read of the file."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from sift.kernel import lanes, media
from sift.kernel.config import Settings
from sift.kernel.ml import pictures
from sift.kernel.subprocess import Priority, SubprocessError
from sift.kernel.subprocess import capture as subprocess_capture

#: Fractions of the frame (left, top, right, bottom): the lower band finds nearly every mark.
CROPS: tuple[tuple[str, float, float, float, float], ...] = (
    ("band", 0.0, 0.88, 1.0, 1.0),
    ("corner", 0.60, 0.80, 1.0, 1.0),
)

#: A quarter in, since a clip often opens black.
FRACTION = 0.25

#: Twice the finder's longest side; past this nothing more is read.
MAX_WIDTH = 1920

#: Generous: a seek on a network share is slow and still working.
READ_TIMEOUT = 120.0

#: The crops as raw colour bytes.
PIXELS = "rgb24"


@dataclass(frozen=True, slots=True)
class Piece:
    name: str
    pixels: np.ndarray


def _even(value: int) -> int:
    """Several decoders refuse an odd side."""
    return max(2, value - value % 2)


def _plan(width: int, height: int) -> list[tuple[str, int, int, int, int, int, int]]:
    plan = []
    for name, left, top, right, bottom in CROPS:
        x, y = int(left * width), int(top * height)
        cut_width = _even(max(8, int(right * width) - x))
        cut_height = _even(max(8, int(bottom * height) - y))
        out_width = _even(min(2 * cut_width, max(MAX_WIDTH, cut_width)))
        out_height = _even(round(cut_height * out_width / cut_width))
        plan.append((name, cut_width, cut_height, x, y, out_width, out_height))
    return plan


def filtergraph(width: int, height: int) -> str:
    """One graph that cuts both crops out of a frame, scales each, and stacks them padded right."""
    plan = _plan(width, height)
    widest = max(out_width for *_, out_width, _ in plan)
    parts = [f"split={len(plan)}" + "".join(f"[in{at}]" for at in range(len(plan)))]
    for at, (_, cut_width, cut_height, x, y, out_width, out_height) in enumerate(plan):
        parts.append(
            f"[in{at}]crop={cut_width}:{cut_height}:{x}:{y},"
            f"scale={out_width}:{out_height},"
            f"pad={widest}:{out_height}:0:0[out{at}]"
        )
    parts.append("".join(f"[out{at}]" for at in range(len(plan))) + f"vstack=inputs={len(plan)}")
    return ";".join(parts)


def stacked_size(width: int, height: int) -> tuple[int, int]:
    plan = _plan(width, height)
    return (
        max(out_width for *_, out_width, _ in plan),
        sum(out_height for *_, out_height in plan),
    )


def unstack(raw: bytes, width: int, height: int) -> list[Piece]:
    """The decoder's picture cut back into its crops; a short read gives nothing."""
    across, down = stacked_size(width, height)
    if len(raw) < across * down * 3:
        return []
    frame = np.frombuffer(raw, dtype=np.uint8, count=across * down * 3).reshape(down, across, 3)
    pieces = []
    at = 0
    for name, _, _, _, _, out_width, out_height in _plan(width, height):
        pieces.append(Piece(name=name, pixels=frame[at : at + out_height, :out_width]))
        at += out_height
    return pieces


def moment_of(media_type: str, duration_ms: int | None) -> media.Moment:
    """Where the frame is taken from; a still or GIF is never seeked, which would write nothing."""
    if media_type != "video" or not duration_ms:
        return media.Moment(seek=())
    return media.Moment(seek=("-ss", media.seconds(int(duration_ms * FRACTION))))


def request(media_type: str, width: int, height: int, duration_ms: int | None) -> media.RawFrames:
    """The one frame this pass reads, as an ask a task's one decode can answer."""
    across, down = stacked_size(width, height)
    return media.RawFrames(
        moments=(moment_of(media_type, duration_ms),),
        filters=filtergraph(width, height),
        pixel_format=PIXELS,
        frame_bytes=across * down * 3,
    )


async def frame_requests(facts: media.FileFacts) -> list[media.FrameRequest]:
    """What reading this file's mark asks for, so one task can read the file once."""
    if facts.media_type not in ("image", "video") or not facts.width or not facts.height:
        return []
    return [request(facts.media_type, facts.width, facts.height, facts.duration_ms)]


async def read(
    path: Path,
    *,
    media_type: str,
    width: int,
    height: int,
    duration_ms: int | None,
    settings: Settings,
    priority: Priority = Priority.BACKGROUND,
) -> list[Piece]:
    """The crops of one file's frame, or nothing where it could not be read; `FFmpegError` where
    the decoder refused it."""
    asked = request(media_type, width, height, duration_ms)
    # A frame the task already decoded for every pass is not decoded again.
    held = pictures.held(
        path, filters=asked.filters, pixel_format=asked.pixel_format, moment=asked.moments[0]
    )
    if held is not None:
        return unstack(held, width, height)
    argv = media.raw_frame_args(
        path, asked.moments[0], filters=asked.filters, pixel_format=PIXELS, settings=settings
    )
    try:
        # Through the storage lane, like every library read, so a share is not overloaded.
        async with lanes.reading(path):
            raw = await subprocess_capture(argv, time_limit=READ_TIMEOUT, priority=priority)
    except SubprocessError as error:
        raise media.FFmpegError(str(error)) from error
    return unstack(raw, width, height)
