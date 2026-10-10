# SPDX-License-Identifier: AGPL-3.0-or-later
"""A picture is never written onto a full disk: the job waits for room, its attempt handed back."""

from __future__ import annotations

from pathlib import Path

import pytest

from sift.kernel.jobs import held_for
from sift.kernel.jobs.retrying import ROOM_WAIT, WaitingForSpace
from sift.kernel.media import FFmpegError
from sift.slices.media_jobs import ffmpeg, shared


async def test_a_full_disk_holds_the_picture_before_the_tool_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran: list[list[str]] = []

    async def run(argv: list[str], *, reads: Path | None = None) -> bytes:
        ran.append(argv)
        return b""

    monkeypatch.setattr(ffmpeg, "run", run)
    monkeypatch.setattr(shared, "_free_bytes", lambda _folder: shared.ROOM_FLOOR - 1)

    with pytest.raises(WaitingForSpace, match="Waiting for space") as raised:
        await shared._render(["ffmpeg", "out.jpg"], tmp_path / "cache" / "out.jpg")

    assert ran == []
    assert held_for(raised.value) == ROOM_WAIT


async def test_a_disk_that_fills_partway_holds_the_picture_and_leaves_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "cache" / "out.jpg"

    async def run(argv: list[str], *, reads: Path | None = None) -> bytes:
        Path(argv[-1]).write_bytes(b"half")
        raise FFmpegError("ffmpeg.exe failed: [image2] Error writing: No space left on device")

    monkeypatch.setattr(ffmpeg, "run", run)

    with pytest.raises(WaitingForSpace):
        await shared._render(["ffmpeg", "out.jpg"], destination)

    assert list(destination.parent.iterdir()) == []


async def test_any_other_failure_of_the_tool_is_left_as_it_was(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run(argv: list[str], *, reads: Path | None = None) -> bytes:
        raise FFmpegError("ffmpeg.exe failed: Invalid argument")

    monkeypatch.setattr(ffmpeg, "run", run)

    with pytest.raises(FFmpegError, match="Invalid argument"):
        await shared._render(["ffmpeg", "out.jpg"], tmp_path / "out.jpg")
