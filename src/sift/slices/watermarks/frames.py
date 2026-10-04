# SPDX-License-Identifier: AGPL-3.0-or-later
"""Getting the two corners of a frame a watermark is drawn in, in one read of the file.

**Where the mark is.** Of ten crops of a frame (four corners and a lower band, each also searched
inverted), the lower band alone finds nearly every mark, and the band with the bottom-right corner
finds all but a very few. The top corners find nothing the band does not, and searching the
inverted copy adds almost nothing. So this reads two crops and not ten, at about a fifth of the
cost.

**The decoder does the shaping, because it is opening the file anyway.** One process reads the
file once, cuts both crops out of the frame, scales each to the size the finder will be handed,
stacks them into one picture and writes it down a pipe. Nothing here decodes a frame twice, and
nothing resizes one, which is what makes this affordable without an imaging library.

**Neither crop is ever enlarged past the point the finder can use.** The finder is given at most
960 pixels on its longest side, so a crop enlarged beyond twice that is pixels nobody reads. The
crop is enlarged to twice its own width, capped at 1920, and a crop already wider than that is
passed at its own size rather than reduced: the reader downstream wants every real pixel of the
letters, and reducing them to enlarge them again is the one thing that would lose detail.

**One frame per video, a quarter of the way in.** A watermark is a property of the copy and is
drawn over the whole of it, so a second frame nearly always says exactly what the first did. A
quarter of the way in rather than the start, because the start of a clip is often black.

**So the black opening second is already never read: above four seconds, and that bound is
exact.** A quarter of the way in is a second in at four seconds and further in at anything longer,
so no separate first-second skip is needed for all but the shortest clips. Under four seconds the
quarter point IS inside the first second, and it is left that way on purpose: a fixed one-second
seek would OVERSHOOT a clip shorter than that, ffmpeg would write nothing, exit reporting success,
and the file would be recorded as carrying no mark, silently, and for ever, because a file that
was looked at is not looked at again. A frame that might be black is a worse read; a seek past the
end is a wrong answer nobody can see. The durable shape if this ever matters is a floor that is also
capped inside the clip, and it needs a measurement of how many short clips a real library holds
before it is worth the second rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from sift.kernel import lanes, media
from sift.kernel.config import Settings
from sift.kernel.subprocess import Priority
from sift.kernel.subprocess import capture as subprocess_capture

#: The two crops, as fractions of the frame: left, top, right, bottom.
#:
#: The band reaches a little further up than a corner would and runs the whole width, because a
#: mark is drawn along the bottom as often as into a corner. The corner overlaps it and reaches
#: higher still, which is what the second crop is for: most of what it adds is a mark sitting
#: just above the band rather than a mark the band could not read.
CROPS: tuple[tuple[str, float, float, float, float], ...] = (
    ("band", 0.0, 0.88, 1.0, 1.0),
    ("corner", 0.60, 0.80, 1.0, 1.0),
)

#: How far into a video the frame is taken from.
FRACTION = 0.25

#: The widest a crop is enlarged to. Twice the finder's own longest side: past this the finder is
#: reducing again and the reader has all the letters it is going to get.
MAX_WIDTH = 1920

#: How long one file's read may take. Generous rather than a budget: a seek into a large file on a
#: network share is slow and still working.
READ_TIMEOUT = 120.0


@dataclass(frozen=True, slots=True)
class Piece:
    """One crop of a frame, at the size it will be read at."""

    name: str
    pixels: np.ndarray


def _even(value: int) -> int:
    """A side a decoder will accept without rounding it itself. Several refuse an odd one."""
    return max(2, value - value % 2)


def _plan(width: int, height: int) -> list[tuple[str, int, int, int, int, int, int]]:
    """Each crop as its place in the frame and the size it comes out at."""
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
    """One graph that cuts both crops out of a frame, scales each, and stacks them.

    Padded to a common width before stacking because a stack is one rectangle; the padding is
    black, sits to the right of both crops, and is cut off again the moment the bytes arrive.
    """
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
    """How wide and how tall the one picture the decoder writes is."""
    plan = _plan(width, height)
    return (
        max(out_width for *_, out_width, _ in plan),
        sum(out_height for *_, out_height in plan),
    )


def unstack(raw: bytes, width: int, height: int) -> list[Piece]:
    """The decoder's one picture, cut back into the crops it was made of.

    A short read gives nothing rather than a picture whose bottom half is missing, which is what
    a decoder killed part way through writing leaves, and it would be read as a crop with no text
    in it rather than as a file that was not read.
    """
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
    """Where in the file the frame is taken from.

    A still and a GIF are read from the beginning and not seeked into. A seek to zero is not
    free and is not a no-op: a still is presented as a video one frame long, and seeking to zero
    lands past that frame, so nothing is written and the decoder exits reporting success. An
    GIF stores each frame as a change from the one before, so seeking into one replays every
    frame up to it for a picture that says what the first one said.
    """
    if media_type != "video" or not duration_ms:
        return media.Moment(seek=())
    return media.Moment(seek=("-ss", media.seconds(int(duration_ms * FRACTION))))


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
    """The crops of one file's frame, or nothing where it could not be read.

    Background priority by definition: whoever is watching something right now matters more than a
    mark that will be read either way.
    """
    argv = media.raw_frame_args(
        path,
        moment_of(media_type, duration_ms),
        filters=filtergraph(width, height),
        pixel_format="rgb24",
        settings=settings,
    )
    # THROUGH THE STORAGE LANE, like every other read of a library file. Opened straight through
    # the subprocess seam, it would be a reader nobody was counting on a network share: the share's
    # places are handed out to keep a seeking reader count the share can serve, and a pass outside
    # that count is the one thing the rule cannot protect against. It costs a dictionary lookup on
    # a local disk (see `kernel.lanes.reading`).
    async with lanes.reading(path):
        raw = await subprocess_capture(argv, time_limit=READ_TIMEOUT, priority=priority)
    return unstack(raw, width, height)
