# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every log of the library and of the app, redacted, in one archive somebody can share.

The desktop app runs it as `python -I -m sift.logbundle`, also when the backend won't start, so it
imports the standard library and the redaction rules and nothing that loads native code.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import zipfile
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import BinaryIO, NoReturn

from sift.kernel.redaction import redacted_line

#: The newest bytes kept of one place's logs: hours of use, and an archive of a few megabytes.
PLACE_BUDGET = 32 * 1024 * 1024

#: The longest line kept, so one runaway line cannot hold the whole budget in memory.
LINE_CAP = 64 * 1024

#: The most of a facts file read.
FACTS_CAP = 64 * 1024

#: A log and its rotations (`sift.log`, `sift.log.3`, `backend.1.log`), and nothing else there.
_LOG_NAME = re.compile(r"[\w.-]+\.log(?:\.\d+)?")


def log_files(folder: Path) -> list[Path]:
    """The log files directly in `folder`, newest first. None where it cannot be read."""
    try:
        found = [one for one in folder.iterdir() if _LOG_NAME.fullmatch(one.name) and one.is_file()]
        return sorted(found, key=lambda one: one.stat().st_mtime, reverse=True)
    except OSError:
        return []


def _lines(path: Path, keep: int) -> Iterator[str]:
    """The lines of the newest `keep` bytes of a file, the first one only if it is whole."""
    with path.open("rb") as handle:
        start = max(0, handle.seek(0, os.SEEK_END) - keep)
        handle.seek(start)
        if start:
            handle.readline()
        end = start + keep
        while handle.tell() < end:
            piece = handle.readline(LINE_CAP)
            if not piece:
                return
            # A line past the cap keeps its front; the rest is read past.
            tail = piece
            while len(tail) == LINE_CAP and not tail.endswith(b"\n"):
                tail = handle.readline(LINE_CAP)
            yield piece.decode("utf-8", errors="replace").rstrip("\r\n")


def write_archive(
    target: BinaryIO | Path, places: Sequence[tuple[str, Path]], facts: Path | None = None
) -> None:
    """Each place's logs under a folder of its name, newest first up to `PLACE_BUDGET`, every line
    redacted, with `contents.txt` saying what was kept of each."""
    said: list[str] = []
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, folder in places:
            files = log_files(folder)
            if not files:
                said.append(f"{name}: no logs in {redacted_line(str(folder))}")
            room = PLACE_BUDGET
            for one in files:
                try:
                    size = one.stat().st_size
                    keep = min(size, room)
                    if keep:
                        with archive.open(f"{name}/{one.name}", "w", force_zip64=True) as entry:
                            for line in _lines(one, keep):
                                entry.write((redacted_line(line) + "\n").encode("utf-8"))
                except OSError:
                    # Rotated away or held by another program since it was listed.
                    said.append(f"{name}/{one.name}: unreadable")
                    continue
                room -= keep
                said.append(f"{name}/{one.name}: {size} bytes, newest {keep} kept")
        if facts is not None:
            try:
                with facts.open("rb") as handle:
                    held = handle.read(FACTS_CAP).decode("utf-8", errors="replace")
                lines = (f"{redacted_line(one)}\n" for one in held.splitlines())
                archive.writestr("facts.txt", "".join(lines))
            except OSError:
                said.append("facts.txt: unreadable")
        archive.writestr("contents.txt", "".join(f"{one}\n" for one in said))


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        self.exit(2, f"{message}\n")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _Parser(prog="python -m sift.logbundle")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--app-logs", type=Path, required=True)
    parser.add_argument("--library-logs", type=Path)
    parser.add_argument("--facts", type=Path)
    asked = parser.parse_args(argv)
    places = [("app", asked.app_logs)]
    if asked.library_logs is not None:
        places.insert(0, ("library", asked.library_logs))
    # Written beside and moved into place, so a failure never leaves half an archive under the name.
    part = asked.out.with_name(asked.out.name + ".part")
    try:
        write_archive(part, places, asked.facts)
        os.replace(part, asked.out)  # nosemgrep: sift-no-file-removal-outside-delete-trash
    except OSError as error:
        part.unlink(missing_ok=True)  # nosemgrep: sift-no-file-removal-outside-delete-trash
        # The reason alone: the error's own text names the path.
        parser.exit(
            1, f"Couldn't write the log archive: {error.strerror or type(error).__name__}\n"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
