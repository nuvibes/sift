# SPDX-License-Identifier: AGPL-3.0-or-later
"""The arm's-length subprocess runner: it runs a tool, times it out, and reads what it left."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from sift.slices.download.sources import subproc
from sift.slices.download.sources.subproc import SubprocessError


async def test_a_tool_that_runs_returns_its_streams_and_status() -> None:
    result = await subproc.run(
        [sys.executable, "-c", "import sys; sys.stdout.write('out'); sys.stderr.write('err')"]
    )
    assert result.returncode == 0
    assert result.stdout == "out"
    assert result.stderr == "err"


async def test_a_non_zero_exit_is_reported_not_raised() -> None:
    result = await subproc.run([sys.executable, "-c", "import sys; sys.exit(3)"])
    assert result.returncode == 3


async def test_a_tool_that_hangs_is_killed() -> None:
    with pytest.raises(SubprocessError, match="too long"):
        await subproc.run([sys.executable, "-c", "import time; time.sleep(30)"], time_limit=0.2)


async def test_a_missing_tool_raises_rather_than_returning() -> None:
    with pytest.raises(SubprocessError, match="could not run"):
        await subproc.run(["/definitely/not/a/real/binary/anywhere"])


def test_output_files_reads_media_and_skips_sidecars(tmp_path: Path) -> None:
    (tmp_path / "a.mp4").write_bytes(b"x")
    nested = tmp_path / "sub"
    nested.mkdir()
    (nested / "b.jpg").write_bytes(b"y")
    (tmp_path / "meta.json").write_text("{}")
    (tmp_path / "a.info.json").write_text("{}")

    files = subproc.output_files(tmp_path)

    assert [path.name for path in files] == ["a.mp4", "b.jpg"]


async def test_a_cancelled_run_kills_the_tool() -> None:
    task = asyncio.create_task(subproc.run([sys.executable, "-c", "import time; time.sleep(30)"]))
    await asyncio.sleep(0.1)  # let it spawn and reach the wait on the process
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_a_tools_temporary_files_go_inside_the_download_and_are_never_read_as_media(
    tmp_path: Path,
) -> None:
    """A one-file build unpacks a 24 MB runtime into the temporary directory and a
    killed one never removes it; pointed into the download's own directory, it goes with the
    workspace. And what lands there is a runtime, not media, so it is never read back."""
    shown = await subproc.run(
        [
            sys.executable,
            "-c",
            "import os, sys, tempfile; sys.stdout.write(os.environ['TMP'] + '|'"
            " + os.environ['TEMP'] + '|' + tempfile.gettempdir())",
        ],
        into=tmp_path,
    )
    scratch = tmp_path / subproc.TOOL_TEMP
    assert shown.stdout.split("|") == [str(scratch)] * 3
    (scratch / "_MEI1234").mkdir()
    (scratch / "_MEI1234" / "python313.dll").write_bytes(b"x")
    (tmp_path / "clip.mp4").write_bytes(b"x")

    assert [path.name for path in subproc.output_files(tmp_path)] == ["clip.mp4"]
