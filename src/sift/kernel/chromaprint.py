# SPDX-License-Identifier: AGPL-3.0-or-later
"""The audio fingerprint of a file, read once, whole.

Chromaprint is the fingerprint AcoustID is built on: a chroma feature of the sound, 32 bits every
eighth of a second, which survives a re-encode, a change of loudness and other sound mixed over
the music. ffmpeg carries it as a muxer, so reading one needs no new tool, no audio library and no
model: one launch, `-f chromaprint`, and the numbers come back.

## The whole track, never a window

Chromaprint's own command-line tool stops at the first two minutes and AcoustID's service is built
around whole music files. Neither convention fits what is being fingerprinted here: thirty seconds
taken from five minutes into a track match a whole-track fingerprint at a bit error rate of 0.011
and score as random (0.472) against a fingerprint of that track's first two minutes. Eight
seconds from the middle behave the same way. So a window is not a cheaper version of the same
answer, it is a different and worse one, and this reads every file's whole sound track, which is
NOT every byte of the file. On MP4, with the video stream dropped the demuxer skips it, so the
launch below reads a few per cent of the bytes (1% to 50% per file), in a few seconds for a
three-minute video. A second read with the bytes already cached takes the same time, so the cost is
the audio DECODE, not the share. A container that interleaves the two in shared blocks (MKV, WebM,
AVI) may read nearly whole; that is unmeasured.

## Its own launch, and why it is not part of the frame decode

The pass that hashes pictures seeks to thirty moments and decodes one frame at each. Audio is
interleaved through the whole file, so reading it is sequential across the file's length: fewer
bytes than the whole (the video is skipped) but every part of it. Putting both in one process would
make the picture pass walk the whole file too, which is the expensive half of what it carefully
avoids. Two launches, each doing what it is good at.

## What comes back, and what a row of it means

`-fp_format 0` is the RAW form: plain little-endian 32-bit integers, one every eighth of a second,
with nothing around them. The compressed form is smaller and can only be read back by the
Chromaprint library itself, which is inside ffmpeg's DLLs and is not reachable from here, and
ffmpeg only ever writes fingerprints, it never reads one. Anything that later compares two of these
needs the integers, so the integers are what is stored.

The first two and a half seconds of any file yield nothing at all: the algorithm needs that much
sound before it can say anything. A 12-second tone gives 75 values, which is 9.4 seconds of
coverage, so how much audio a fingerprint COVERS is a property of the fingerprint
and is worked out from its own length rather than taken from the container's duration. The two are
different numbers and only one of them describes the blob.
"""

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

#: Chromaprint's algorithm number. 1 is ffmpeg's default and the one AcoustID's own tool uses; it
#: is stored per row so a later algorithm is a different row rather than a silent reinterpretation
#: of the numbers already kept.
ALGORITHM = 1

#: Raw 32-bit integers, little-endian. See the module docstring for why not the compressed form.
FP_FORMAT_RAW = "0"

#: The name one read is timed under. The ledger files it in each run's stages, and the music
#: card's price is the average of this device's recent ones, so it is a name two places read,
#: and a constant so a rename cannot quietly turn the price into a guess.
READ_STAGE = "chromaprint.read"

#: How long one value covers, in milliseconds.
#:
#: Fixed by the algorithm rather than chosen here: Chromaprint reads at 11,025 Hz in frames of 4,096
#: samples overlapping by two thirds, so a value arrives every 1,365 samples: 4,744 values (18,976
#: bytes) for 590 seconds of audio.
_SAMPLE_RATE = 11_025
_SAMPLES_PER_VALUE = 1_365

#: How long one file may take. Generous on purpose: this decodes the whole sound track of a file
#: that may be on a network share (a fraction of an MP4's bytes, possibly nearly all of an MKV's,
#: see the module docstring), and the lane in front of it already keeps the share from being asked
#: for too many together. A limit tight enough to matter would refuse long files on slow shares,
#: which is the one case this is most worth having.
TIME_LIMIT_SECONDS = 1800.0

#: The version line of the tool that made a fingerprint, read once per process.
#:
#: Once, because it costs a launch and cannot change while Sift is running: the binary is beside
#: the application. Stored on every row so a fingerprint made by a build with a different
#: Chromaprint in it can be told apart later without re-reading every file to find out.
_TOOL: str | None = None


@dataclass(frozen=True, slots=True)
class Fingerprint:
    """One file's audio fingerprint, and what made it."""

    algorithm: int
    tool: str
    #: How much audio the values cover, in milliseconds. Worked out from the values themselves.
    #: See the module docstring. Zero when there is nothing to cover.
    duration_ms: int
    #: Where in the file the fingerprint starts. Always zero here: the whole track is read.
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
    """The little-endian 32-bit integers in a raw Chromaprint fingerprint.

    A length that is not a multiple of four is a truncated read rather than a short fingerprint
    (ffmpeg writes whole values), so the remainder is refused rather than quietly dropped. Dropping
    it would hand back a fingerprint that is one value shorter than it should be, which compares
    against other fingerprints perfectly happily and is wrong.
    """
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
            # A tool that will not answer its own version is still a tool that may read a file, so
            # this is recorded rather than raised: the row says what is known about what made it.
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
    """The one launch: every byte of the file's first audio stream, as raw values, into a file.

    `-vn` because the picture is not wanted and decoding it would be the whole cost of the pass
    again. `-map 0:a:0` names the first audio stream, so a file carrying a commentary track as well
    is fingerprinted by its main sound rather than by whichever stream ffmpeg would have chosen.
    There is no `-t` and no `-ss`: the whole track is the answer, and a guard beside this refuses a
    launch that grows one.
    """
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
    """Fingerprint every second of a file's audio. An empty fingerprint where there is none.

    In the storage's lane, because this reads across the whole length of the file (its sound
    track, not its picture) and a share asked for several together delivers less to each. At
    background priority, because nobody is waiting for it. Timed as `READ_STAGE`, a stage of the
    run's record (`Ledger.stage`).

    A file ffmpeg cannot read audio from comes back EMPTY rather than raising, and that is the
    answer rather than a swallowed failure: the pass has looked, there is nothing to record, and a
    row saying so is what stops the file being offered again for the rest of the library's life.
    The reason is logged, so a file with no fingerprint can still say why.
    """
    tool = await tool_version(settings)
    # A temporary directory rather than the job's workspace: a workspace is for what must survive
    # one call, and this file is read and thrown away inside it.
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
    """The answer for a file with no audio in it: looked at, nothing to record, never asked again."""
    return Fingerprint(algorithm=ALGORITHM, tool=tool, duration_ms=0, offset_ms=0, values=())
