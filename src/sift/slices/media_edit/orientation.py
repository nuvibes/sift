# SPDX-License-Identifier: AGPL-3.0-or-later
"""The camera's note saying which way up a photograph goes, read so the editor works on what is
seen.
The turn leads the filter chain with ffmpeg's own turn off, so the copy comes out upright."""

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

#: A ceiling on something going wrong; it decodes one frame.
READ_TIMEOUT_SECONDS = 20


@dataclass(frozen=True, slots=True)
class Orientation:
    """Which way the stored picture goes to be the one seen: mirror first, then turn clockwise."""

    quarter_turns: int = 0
    mirrored: bool = False

    def filters(self) -> tuple[str, ...]:
        """Filters for the FRONT of the chain, so the copy is cut where aimed and written upright."""
        chain: list[str] = []
        if self.mirrored:
            chain.append("hflip")
        # Written out so a three-quarter turn is one pass, not three.
        if self.quarter_turns == 1:
            chain.append("transpose=1")
        elif self.quarter_turns == 2:
            chain.append("transpose=2,transpose=2")
        elif self.quarter_turns == 3:
            chain.append("transpose=2")
        return tuple(chain)


UPRIGHT = Orientation()

#: The note's eight values; a front camera's picture is often mirrored.
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

#: A plain turn given as an angle: the way a player would turn it, so clockwise is negative.
_BY_ANGLE = {
    -90: Orientation(quarter_turns=1),
    90: Orientation(quarter_turns=3),
    180: Orientation(quarter_turns=2),
    -180: Orientation(quarter_turns=2),
}


def orientation_args(path: Path, *, settings: Settings) -> list[str]:
    """Ask for the first frame's own description and nothing else."""
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
    """The note, out of what probing said: the tag wins, the angle is the fallback, else upright."""
    frames = payload.get("frames") or []
    if not frames or not isinstance(frames[0], dict):
        return UPRIGHT
    frame = frames[0]

    tag = (frame.get("tags") or {}).get("Orientation")
    try:
        # ffprobe pads it with spaces.
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
    """The note on this file, or upright when there is none or reading fails, so editing never
    stops."""
    try:
        answer = await run_json(
            orientation_args(path, settings=settings),
            time_limit=READ_TIMEOUT_SECONDS,
            # Somebody is waiting with the editor open.
            priority=Priority.NORMAL,
        )
    except FFmpegError as failure:
        log.info("edit.orientation_unreadable", path=path.name, reason=str(failure)[:200])
        return UPRIGHT
    return understand(answer)
