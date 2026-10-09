# SPDX-License-Identifier: AGPL-3.0-or-later
"""The last few lines of a file, read backwards in blocks from its end, never the whole file."""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

#: How many lines one request may ask for. A screenful is tens; this is generous and is a bound.
MOST_LINES = 500

#: Truncated rather than dropped: a line that is too long is usually the interesting one.
LINE_CAP = 4000

#: Sixty-four kilobytes holds a few hundred ordinary lines, so most requests are one read.
_BLOCK = 64 * 1024


def tail_of(path: Path, lines: int) -> list[str]:
    """The last `lines` complete lines of one file, oldest first; nothing if it is not there."""
    return newest_matching([path], lines).lines


@dataclass(frozen=True, slots=True)
class Tail:
    """What a backwards read found, and how much of the log it had to look through to find it."""

    #: The lines kept, oldest first.
    lines: list[str]
    #: How many bytes were read to find them. The cost, and what a screen says it searched.
    read_bytes: int
    #: Whether the read reached the oldest line, so fewer lines than asked means there are no more.
    whole: bool


#: How much one filtered read may look through: a filter can match nothing (see `whole`).
SEARCH_BUDGET = 32 * 1024 * 1024


def newest_matching(
    paths: Sequence[Path],
    lines: int,
    keep: Callable[[str], bool] | None = None,
    *,
    budget: int | None = None,
) -> Tail:
    """The newest `lines` lines `keep` accepts, across `paths` (newest file first), oldest first.

    Read from the end in blocks, at most `budget` bytes (then `whole=False`); a file that cannot
    be read is an empty answer, never a raise on the screen that is looking for what went wrong.
    """
    wanted = max(0, min(lines, MOST_LINES))
    if wanted == 0:
        return Tail(lines=[], read_bytes=0, whole=True)
    reading = _Reading(wanted, keep, budget)
    for path in paths:
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size == 0 or not path.is_file():
            continue
        found = reading.backwards(path, size)
        if found is not None:
            return found
    return reading.done(whole=True)


class _Reading:
    """One tail read: the lines kept so far, newest first, and the bytes spent."""

    def __init__(self, wanted: int, keep: Callable[[str], bool] | None, budget: int | None) -> None:
        self.wanted = wanted
        self.keep = keep
        self.budget = budget
        self.kept: list[str] = []
        self.spent = 0

    def take(self, piece: bytes) -> bool:
        """Judge one whole line. True once there are enough."""
        text = piece.decode("utf-8", errors="replace").rstrip("\r")
        if not text.strip():
            return False
        text = text[:LINE_CAP]
        if self.keep is None or self.keep(text):
            self.kept.append(text)
        return len(self.kept) >= self.wanted

    def done(self, whole: bool) -> Tail:
        return Tail(lines=self.kept[::-1], read_bytes=self.spent, whole=whole)

    def backwards(self, path: Path, size: int) -> Tail | None:
        """Read one file from its end; the answer once it is settled, else None for the next."""
        at = size
        carry = b""
        try:
            with path.open("rb") as handle:
                while at > 0:
                    if self.budget is not None and self.spent >= self.budget:
                        return self.done(whole=False)
                    step = min(_BLOCK, at)
                    at -= step
                    handle.seek(at, os.SEEK_SET)
                    held = handle.read(step)
                    self.spent += len(held)
                    pieces = (held + carry).split(b"\n")
                    # The first piece is only whole if this read reached the start of the file.
                    carry = pieces[0]
                    for piece in reversed(pieces[1:]):
                        if self.take(piece):
                            return self.done(whole=False)
                    if len(carry) > LINE_CAP * 4:
                        # Reading backwards prepends to a line, so its front is kept and shown.
                        carry = carry[: LINE_CAP * 4]
        except OSError:
            return Tail(lines=[], read_bytes=self.spent, whole=False)
        if self.take(carry):
            return self.done(whole=False)
        return None
