# SPDX-License-Identifier: AGPL-3.0-or-later
"""Running a downloader tool for this slice, and reading what it left behind.

The spawning, timing out and killing is the kernel's shared subprocess runner: the one place every
tool in Sift is started, so a cancelled download kills its tool rather than leaving it fetching in
the background. This wraps that for the download slice: it runs a downloader with the slice's time
budget and hands back what it wrote as text, and it reads the files a tool dropped into its output
directory.

Nothing here logs. A tool's output is where a URL leaks, so what to do with it is the caller's
decision, made after the output has been through the slice's scrubber.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from sift.kernel.subprocess import OnLine, SubprocessError
from sift.kernel.subprocess import run as _run
from sift.slices.download.sources.tuning import SUBPROCESS_TIMEOUT_SECONDS

__all__ = ["TOOL_TEMP", "OnLine", "SubprocessError", "SubprocessResult", "output_files", "run"]

#: The folder inside a download's output directory that its tool is given as its temporary
#: directory, and that is never read back as media.
#:
#: Because the publishers' one-file builds of both tools are launchers: each unpacks a whole Python
#: runtime (about 24 MB for gallery-dl) into a `_MEI...` folder under the temporary directory
#: and deletes it on the way out. A tool that is killed never gets to the way out, so every
#: cancelled, paused or timed-out download would leave one of those folders in the machine's
#: temporary directory. Pointed in here, it is inside the download's workspace, and goes when the
#: workspace goes.
#:
#: Inside the output directory rather than beside it so this module, which already decides what in
#: that directory is media, is the one place that knows the folder is there. The name starts with a
#: dot and says whose it is, so no tool writing a gallery can produce it by accident.
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
    """Run a downloader to completion and return its result, its streams decoded to text.

    Raises `SubprocessError` when the tool will not run at all: it is missing, or it timed out and
    was killed. A tool that ran and reported a failure comes back as a non-zero exit code for the
    caller to interpret, not as an error.

    `on_line` is told each line the tool writes to its output as it writes it, for a caller watching
    a download report on itself. It sees the OUTPUT stream only; the error stream, which is where a
    failure is read from, is untouched by it.

    `into` is the directory the tool writes into, when it has one. The tool's TEMPORARY files then
    go in a folder inside it rather than in the machine's own temporary directory (see
    `TOOL_TEMP`), so they are swept with the download's workspace however the run ended.
    """
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
    """Every file a tool wrote into its output directory, newest listing order aside.

    The tool is told to write into a directory of its own, and whatever it produced is read back
    from there rather than parsed out of its chatter on stdout, which is brittle and, being the
    place a path is printed, the thing being kept out of the logs. Metadata sidecars the tools drop
    (`.json`, `.info.json`) are not media and are skipped, and so is a download the tool has not
    finished writing.
    """
    return sorted(
        path
        for path in directory.rglob("*")
        if path.is_file() and not _is_sidecar(path) and not _is_tool_temp(directory, path)
    )


def _is_tool_temp(directory: Path, path: Path) -> bool:
    """Whether a file is in the tool's own temporary folder: a runtime it unpacked, not media."""
    return path.relative_to(directory).parts[0] == TOOL_TEMP


#: What a tool calls a file it has not finished. yt-dlp writes `<name>.part` while it fetches and
#: renames it when it is whole, and keeps `<name>.ytdl` beside it to know where to pick up from;
#: gallery-dl uses the same `.part` suffix.
#:
#: Skipped because they are not files the download produced, and because a pause leaves them
#: behind on purpose: the workspace survives so the next run can continue them, and everything in
#: it is otherwise read back as media and handed to the import gate. A half-written video is not
#: media: the gate would refuse it and the whole download would settle as quarantined, which is
#: the one ending a pause must never be able to cause.
_UNFINISHED = frozenset({".part", ".ytdl"})


def _is_sidecar(path: Path) -> bool:
    suffix = path.suffix.lower()
    return suffix == ".json" or suffix in _UNFINISHED or path.name.endswith(".info.json")
