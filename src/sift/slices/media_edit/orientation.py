# SPDX-License-Identifier: AGPL-3.0-or-later
"""The note a camera leaves saying which way up a photograph goes, read for the editor's own use.

A camera's sensor is fixed in the body, so a picture taken with the body held upright is recorded
lying on its side with a note attached: turn me a quarter before showing this. Browsers obey the
note without being asked, and so does ffmpeg, which turns every picture Sift draws from the file by
it; the size probing records is the size after the turn.

This module exists because of what a person does in an editor: they drag a rectangle over the
picture they can SEE. A rectangle aimed at the drawn picture and then cut out of the stored one
takes the wrong part, and takes it silently: there is no error, just a photograph cropped somewhere
nobody chose.

So the editor works in what is on screen from end to end. The note is read here and the turn is
put at the front of the filter chain, with ffmpeg's own turn switched off for that one command
(`operations.still_args`), so that what ffmpeg cuts is the picture the person was looking at and it
is turned once. The copy that comes out is upright, carries no note of its own, and needs none.

**This is the editor's own correction and nothing wider.** Nothing here changes what is recorded at
import or what a thumbnail looks like, and no file already on the disk is altered. Read `WHY THE
COPY IS UPRIGHT` on `Orientation.filters` for what that costs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sift.kernel.config import Settings
from sift.kernel.log import get_logger
from sift.kernel.media import FFmpegError, run_json
from sift.kernel.subprocess import Priority

log = get_logger(__name__)

__all__ = ["UPRIGHT", "Orientation", "orientation_args", "read_orientation", "understand"]

#: How long the reader is allowed to take. It decodes one frame of one picture, so this is a ceiling
#: on something going wrong rather than a budget: a photograph that takes ten seconds to read a
#: single frame from is a photograph the editor is not going to be able to work on anyway.
READ_TIMEOUT_SECONDS = 20


@dataclass(frozen=True, slots=True)
class Orientation:
    """Which way the stored picture has to be put to be the picture somebody sees.

    Two parts, in this order: mirror first, then turn clockwise. That is the order the note itself
    is defined in, and the order the filters below are written in: swapping them gives the wrong
    answer for four of the eight cases and the right one for the other four, which is the worst
    possible way for it to be wrong.
    """

    #: Quarter turns clockwise, 0 to 3.
    quarter_turns: int = 0
    #: Whether the picture is mirrored left to right before that turn.
    mirrored: bool = False

    def filters(self) -> tuple[str, ...]:
        """What to put at the FRONT of the chain so everything after it works on what is seen.

        WHY THE COPY IS UPRIGHT. The alternative was to aim correctly and still write the copy the
        way the source was stored, note and all. That was rejected: the copy would be cut in the
        right place and then displayed on its side by everything that reads the note, which is the
        same silent wrongness moved one step along and harder to spot, because by then the person
        has seen a correct preview.

        It costs one thing, and it is worth saying plainly: the copy is a re-drawn picture rather
        than the same picture turned. The editor already re-draws every still it produces and
        already says on screen when that costs a generation of quality, so this adds no new cost:
        it only means the pixels come out arranged the way the note asked for instead of the way
        they were stored.
        """
        chain: list[str] = []
        if self.mirrored:
            chain.append("hflip")
        # `transpose=1` is a quarter clockwise. Written out rather than looped over, because a loop
        # producing "transpose=1,transpose=1,transpose=1" for a three-quarter turn is three passes
        # over the frame to say one thing.
        if self.quarter_turns == 1:
            chain.append("transpose=1")
        elif self.quarter_turns == 2:
            chain.append("transpose=2,transpose=2")
        elif self.quarter_turns == 3:
            chain.append("transpose=2")
        return tuple(chain)


#: A picture stored the way it is meant to be seen, which is most of them.
UPRIGHT = Orientation()

#: The eight values the note can take, as the file format numbers them. The mirrored four are as
#: real as the others: a picture taken with a front-facing camera routinely carries one.
_BY_EXIF_TAG = {
    1: Orientation(),
    2: Orientation(mirrored=True),
    3: Orientation(quarter_turns=2),
    4: Orientation(quarter_turns=2, mirrored=True),
    5: Orientation(quarter_turns=1, mirrored=True),
    6: Orientation(quarter_turns=1),
    7: Orientation(quarter_turns=3, mirrored=True),
    8: Orientation(quarter_turns=3),
}

#: What a plain turn with no mirror looks like when it arrives as an angle instead of a tag. The
#: angle is stated the way a player would have to turn the picture, which is why it is negative for
#: a clockwise quarter.
_BY_ANGLE = {
    -90: Orientation(quarter_turns=1),
    90: Orientation(quarter_turns=3),
    180: Orientation(quarter_turns=2),
    -180: Orientation(quarter_turns=2),
}


def orientation_args(path: Path, *, settings: Settings) -> list[str]:
    """Ask for the first frame's own description and nothing else.

    One frame, because the note is on the picture and reading further is reading the whole file to
    answer a question the first frame has already answered.
    """
    return [
        settings.ffprobe_path,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-read_intervals",
        "%+#1",
        "-show_frames",
        "-of",
        "json",
        str(path),
    ]


def understand(payload: dict[str, Any]) -> Orientation:
    """The note, out of what probing said. Upright when it said nothing about one.

    Two places carry it and they are read in this order. The **tag** is the note itself and is the
    only one of the two that can say a picture is mirrored, so it wins wherever it is present. The
    **angle** is what probing works out from that tag, or from a container that states a turn
    another way; it is a fallback rather than a second opinion, and it cannot express a mirror.

    Anything unreadable, absent or outside the eight values is upright, which is what the rest of
    Sift assumes today, so a format whose note this cannot find behaves exactly as it does now.
    """
    frames = payload.get("frames") or []
    if not frames or not isinstance(frames[0], dict):
        return UPRIGHT
    frame = frames[0]

    tag = (frame.get("tags") or {}).get("Orientation")
    try:
        # It arrives space-padded out of ffprobe, which is why it is not read as a number
        # directly: `int(" 6")` is fine and `int("")` is not, and both turn up.
        found = _BY_EXIF_TAG.get(int(str(tag).strip()))
    except (TypeError, ValueError):
        found = None
    if found is not None:
        return found

    for side_data in frame.get("side_data_list") or []:
        if not isinstance(side_data, dict):
            continue
        angle = side_data.get("rotation")
        if isinstance(angle, (int, float)):
            return _BY_ANGLE.get(int(angle), UPRIGHT)
    return UPRIGHT


async def read_orientation(path: Path, *, settings: Settings) -> Orientation:
    """The note on this file, or upright when there is none and when anything goes wrong.

    A picture Sift cannot read a note from is treated as stored the way it is seen, because that is
    what every other part of Sift already assumes about every file. A failure here must not stop
    somebody editing: it makes the editor no better than it is today on that one file, and refusing
    would make it worse on files that were never affected.
    """
    try:
        answer = await run_json(
            orientation_args(path, settings=settings),
            time_limit=READ_TIMEOUT_SECONDS,
            # Normal rather than background: somebody has the editor open in front of them and
            # nothing is drawn until this comes back.
            priority=Priority.NORMAL,
        )
    except FFmpegError as failure:
        log.info("edit.orientation_unreadable", path=path.name, reason=str(failure)[:200])
        return UPRIGHT
    return understand(answer)
