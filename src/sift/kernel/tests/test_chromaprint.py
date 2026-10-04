# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading a file's audio fingerprint, against the real tool.

The launch is checked as a string of arguments AND run for real on a tone this test generates.
Both, because they answer different questions: the arguments are where the whole-track rule lives
and a mutation of them is invisible to anything that only looks at the numbers, while the run is
the only thing that says the arguments are ones ffmpeg accepts at all.
"""

from __future__ import annotations

import struct
import subprocess
from pathlib import Path

import pytest

from sift.kernel import chromaprint, media
from sift.kernel.config import Settings

pytestmark = [pytest.mark.integration]


def _tone(settings: Settings, into: Path, *, seconds: int = 12) -> Path:
    """A file of pure tone, made by the same ffmpeg that will read it."""
    made = into / "tone.m4a"
    subprocess.run(
        [
            settings.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={seconds}",
            "-c:a",
            "aac",
            str(made),
        ],
        check=True,
        capture_output=True,
    )
    return made


def _silent_picture(settings: Settings, into: Path) -> Path:
    """A picture, which has no audio stream at all for `-map 0:a:0` to find."""
    made = into / "still.png"
    subprocess.run(
        [
            settings.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=64x64:d=1",
            "-frames:v",
            "1",
            str(made),
        ],
        check=True,
        capture_output=True,
    )
    return made


def test_the_launch_is_the_whole_track(tmp_path: Path) -> None:
    """No `-t` and no `-ss`: the whole-track rule.

    Thirty seconds from five minutes into a track match a whole-track fingerprint and score as
    random against a fingerprint of that track's first two minutes, so a window is a different
    answer rather than a cheaper one. A `-t` added here would be invisible in every other test:
    the fingerprint would still parse, still store and still compare, against the wrong thing.
    """
    settings = Settings()
    argv = chromaprint.args(Path("in.mp4"), Path("out.raw"), settings=settings)
    assert "-t" not in argv
    assert "-ss" not in argv
    assert argv[-1] == "out.raw"
    assert "-vn" in argv
    assert argv[argv.index("-map") + 1] == "0:a:0"
    assert argv[argv.index("-f") + 1] == "chromaprint"
    assert argv[argv.index("-fp_format") + 1] == chromaprint.FP_FORMAT_RAW
    assert argv[argv.index("-algorithm") + 1] == str(chromaprint.ALGORITHM)


def test_the_values_are_little_endian_32_bit_integers() -> None:
    raw = struct.pack("<3I", 1, 2, 4_000_000_000)
    assert chromaprint.parse(raw) == (1, 2, 4_000_000_000)


def test_a_truncated_read_is_refused_rather_than_rounded_down() -> None:
    """Three and a half values is a read that went wrong, and dropping the remainder would hand
    back a fingerprint one value short, which compares against anything perfectly happily."""
    with pytest.raises(ValueError, match="whole number"):
        chromaprint.parse(struct.pack("<3I", 1, 2, 3) + b"\x00")


def test_how_much_audio_the_values_cover() -> None:
    """Chromaprint answers eight times a second, so the coverage is the length divided by that."""
    assert chromaprint.covers_ms(0) == 0
    assert 7_900 <= chromaprint.covers_ms(64) <= 8_000


@pytest.mark.asyncio
async def test_a_real_file_is_read_whole(tmp_path: Path) -> None:
    """The launch runs, the numbers come back, and they cover the tone less its lead-in.

    Chromaprint says nothing about the first two and a half seconds of any file (it needs that
    much sound before it can answer), so twelve seconds of tone comes back as about nine.
    """
    settings = Settings()
    chromaprint.forget_tool_version()
    made = _tone(settings, tmp_path)
    fingerprint = await chromaprint.read_whole_track(made, settings=settings)

    assert not fingerprint.empty
    assert fingerprint.algorithm == chromaprint.ALGORITHM
    assert fingerprint.offset_ms == 0
    assert fingerprint.tool.startswith("ffmpeg version")
    assert len(fingerprint.blob) == len(fingerprint.values) * 4
    assert 8_000 <= fingerprint.duration_ms <= 12_000


@pytest.mark.asyncio
async def test_a_file_with_no_audio_comes_back_empty_rather_than_raising(tmp_path: Path) -> None:
    """ffmpeg refuses `-map 0:a:0` outright on a file with no audio stream. That is an ANSWER:
    there is nothing to record, and the empty row it becomes is what stops the file being offered
    again for the rest of the library's life."""
    settings = Settings()
    fingerprint = await chromaprint.read_whole_track(
        _silent_picture(settings, tmp_path), settings=settings
    )
    assert fingerprint.empty
    assert fingerprint.duration_ms == 0
    assert fingerprint.blob == b""


@pytest.mark.asyncio
async def test_a_file_that_is_not_there_comes_back_empty(tmp_path: Path) -> None:
    settings = Settings()
    fingerprint = await chromaprint.read_whole_track(tmp_path / "nothing.mp4", settings=settings)
    assert fingerprint.empty


@pytest.mark.asyncio
async def test_the_version_line_is_read_once(tmp_path: Path) -> None:
    """It costs a launch and cannot change while Sift is running, so it is read once and kept."""
    settings = Settings()
    chromaprint.forget_tool_version()
    first = await chromaprint.tool_version(settings)
    assert first.startswith("ffmpeg version")
    broken = Settings(ffmpeg_path=str(tmp_path / "not-a-tool"))
    assert await chromaprint.tool_version(broken) == first


@pytest.mark.asyncio
async def test_a_tool_that_will_not_answer_its_version_is_still_a_tool(tmp_path: Path) -> None:
    chromaprint.forget_tool_version()
    broken = Settings(ffmpeg_path=str(tmp_path / "not-a-tool"))
    assert await chromaprint.tool_version(broken) == "ffmpeg"
    chromaprint.forget_tool_version()


@pytest.mark.asyncio
async def test_unreadable_bytes_come_back_empty(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A fingerprint file of a length that is not a whole number of values is a read that went
    wrong, and the honest answer is the empty one rather than a fingerprint with a hole in it."""
    settings = Settings()
    # Warmed first: the patched runner below writes to the last argument, and `-version` is not a
    # path anybody wants a file at.
    await chromaprint.tool_version(settings)

    async def ran(argv: list[str], **kwargs: object) -> bytes:
        Path(argv[-1]).write_bytes(b"\x01\x02\x03")
        return b""

    monkeypatch.setattr(media, "run", ran)
    fingerprint = await chromaprint.read_whole_track(tmp_path / "x.mp4", settings=settings)
    assert fingerprint.empty
