# SPDX-License-Identifier: AGPL-3.0-or-later
"""The audio fingerprint of a file's whole sound track, read by ffmpeg in a launch of its own.

A window scores as random against a whole-track print, so the whole track is always read."""

from __future__ import annotations

import asyncio
import struct
import tempfile
from dataclasses import dataclass
from pathlib import Path

from sift.kernel import media, subprocess
from sift.kernel.config import Settings
from sift.kernel.log import get_logger, timing_hook

log = get_logger(__name__)

#: Stored per row, so a later algorithm is a different row, never a reinterpretation.
ALGORITHM = 1

#: Raw little-endian integers: only the Chromaprint library reads the compressed form.
FP_FORMAT_RAW = "0"

#: A constant, since the ledger and the music card's price both read this name.
READ_STAGE = "chromaprint.read"

#: Fixed by the algorithm: 11,025 Hz, a value every 1,365 samples.
_SAMPLE_RATE = 11_025
_SAMPLES_PER_VALUE = 1_365

#: Generous: a tight limit would refuse long files on slow shares.
TIME_LIMIT_SECONDS = 1800.0

#: Read once per process; stored per row so another Chromaprint build can be told apart.
_TOOL: str | None = None


@dataclass(frozen=True, slots=True)
class Fingerprint:
    """One file's audio fingerprint, and what made it."""

    algorithm: int
    tool: str
    #: Worked out from the values themselves, not the container's duration.
    duration_ms: int
    #: Always zero: the whole track is read.
    offset_ms: int
    values: tuple[int, ...]

    @property
    def blob(self) -> bytes:
        """The values as they are stored: raw little-endian 32-bit integers, and nothing else."""
        return struct.pack(f"<{len(self.values)}I", *self.values)

    @property
    def empty(self) -> bool:
        """Whether this says there is no audio to fingerprint. A real answer, not a failure."""
        return not self.values


def parse(raw: bytes) -> tuple[int, ...]:
    """The integers in a raw fingerprint; a ragged length is a truncated read and refused."""
    if len(raw) % 4:
        raise ValueError(f"a raw fingerprint is a whole number of 32-bit values, not {len(raw)}")
    return struct.unpack(f"<{len(raw) // 4}I", raw)


def covers_ms(values: int) -> int:
    """How much audio this many values cover, in milliseconds. See `_SAMPLES_PER_VALUE`."""
    return round(values * _SAMPLES_PER_VALUE * 1000 / _SAMPLE_RATE)


async def tool_version(settings: Settings) -> str:
    """The first line of `ffmpeg -version`, read once per process and kept."""
    global _TOOL
    if _TOOL is None:
        try:
            said = await media.run(
                [settings.ffmpeg_path, "-version"], time_limit=30.0, capture=True
            )
        except media.FFmpegError as exc:
            # Recorded rather than raised: the tool may still read a file.
            log.warning("chromaprint.no_version", reason=str(exc))
            _TOOL = "ffmpeg"
        else:
            first = said.decode("utf-8", "replace").splitlines()
            _TOOL = first[0].strip() if first else "ffmpeg"
    return _TOOL


def forget_tool_version() -> None:
    """Drop the remembered version line. For a test that changes what the tool answers."""
    global _TOOL
    _TOOL = None


def args(source: Path, into: Path, *, settings: Settings) -> list[str]:
    """The one launch: the first audio stream whole, no video, as raw values into a file."""
    return [
        settings.ffmpeg_path,
        *media.BASE_FLAGS,
        "-i",
        str(source),
        "-vn",
        "-map",
        "0:a:0",
        "-f",
        "chromaprint",
        "-fp_format",
        FP_FORMAT_RAW,
        "-algorithm",
        str(ALGORITHM),
        str(into),
    ]


async def read_whole_track(source: Path, *, settings: Settings) -> Fingerprint:
    """Fingerprint a file's whole audio in the storage's lane; empty where it has none."""
    tool = await tool_version(settings)
    # Not the job's workspace: this file is read and thrown away within the call.
    with (
        timing_hook(READ_STAGE),
        tempfile.TemporaryDirectory(prefix="sift-chromaprint-") as workspace,
    ):
        raw = Path(workspace) / "fingerprint.raw"
        try:
            await media.run(
                args(source, raw, settings=settings),
                time_limit=TIME_LIMIT_SECONDS,
                priority=subprocess.Priority.BACKGROUND,
                reads=source,
            )
            written = await asyncio.to_thread(raw.read_bytes)
        except (media.FFmpegError, OSError) as exc:
            log.info("chromaprint.no_audio", path=str(source), reason=str(exc))
            return empty(tool)
        try:
            values = parse(written)
        except ValueError as exc:
            log.warning("chromaprint.unreadable", path=str(source), reason=str(exc))
            return empty(tool)
    return Fingerprint(
        algorithm=ALGORITHM,
        tool=tool,
        duration_ms=covers_ms(len(values)),
        offset_ms=0,
        values=values,
    )


def empty(tool: str) -> Fingerprint:
    """The answer for a file with no audio: looked at, nothing to record, never asked again."""
    return Fingerprint(algorithm=ALGORITHM, tool=tool, duration_ms=0, offset_ms=0, values=())
