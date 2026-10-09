# SPDX-License-Identifier: AGPL-3.0-or-later
"""Running a downloader tool and reading what it left behind.

Nothing here logs: a tool's output is where a URL leaks, so the caller scrubs it first."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from sift.kernel.subprocess import OnLine, SubprocessError
from sift.kernel.subprocess import run as _run
from sift.slices.download.sources.tuning import SUBPROCESS_TIMEOUT_SECONDS

__all__ = ["TOOL_TEMP", "OnLine", "SubprocessError", "SubprocessResult", "output_files", "run"]

#: The tool's temporary directory: a killed one-file build leaves its unpacked runtime behind, and
#: here it goes with the workspace.
TOOL_TEMP = ".sift-tool-temp"


@dataclass(frozen=True, slots=True)
class SubprocessResult:
    """What a finished tool left behind: its exit status and the two streams, decoded to text."""

    returncode: int
    stdout: str
    stderr: str


async def run(
    argv: list[str],
    *,
    time_limit: float = SUBPROCESS_TIMEOUT_SECONDS,
    on_line: OnLine | None = None,
    into: Path | None = None,
) -> SubprocessResult:
    """Run a downloader to completion; `SubprocessError` only when it is missing or timed out."""
    extra = None
    if into is not None:
        scratch = into / TOOL_TEMP
        await asyncio.to_thread(scratch.mkdir, parents=True, exist_ok=True)
        extra = {"TMP": str(scratch), "TEMP": str(scratch), "TMPDIR": str(scratch)}
    result = await _run(argv, time_limit=time_limit, on_line=on_line, extra_env=extra)
    return SubprocessResult(
        returncode=result.returncode,
        stdout=result.stdout.decode("utf-8", "replace"),
        stderr=result.stderr.decode("utf-8", "replace"),
    )


def output_files(directory: Path) -> list[Path]:
    """Every media file a tool wrote into its output directory, read back rather than parsed."""
    return sorted(
        path
        for path in directory.rglob("*")
        if path.is_file() and not _is_sidecar(path) and not _is_tool_temp(directory, path)
    )


def _is_tool_temp(directory: Path, path: Path) -> bool:
    return path.relative_to(directory).parts[0] == TOOL_TEMP


#: Half-written files a pause leaves on purpose; read as media they would quarantine the download.
_UNFINISHED = frozenset({".part", ".ytdl"})


def _is_sidecar(path: Path) -> bool:
    suffix = path.suffix.lower()
    return suffix == ".json" or suffix in _UNFINISHED or path.name.endswith(".info.json")
