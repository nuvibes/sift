# SPDX-License-Identifier: AGPL-3.0-or-later
"""The last few lines of a file, read without reading the file.

Sift's log may be a gigabyte: that is the default ceiling, and the setting goes to ten. Anything
that answers "what has it been doing lately" by reading the whole thing would hold a gigabyte in
memory to show a screenful, on the very machine somebody is asking because something is already
wrong with it.

So this seeks from the END and reads backwards in blocks until it has enough lines. What it costs is
the size of what is returned, whatever the size of the file.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

#: How many lines one request may ask for. A screenful is tens; this is generous and is a bound.
MOST_LINES = 500

#: How much of one line is kept. A log line is a JSON record and a pathological one (a stack
#: trace, a very long SQL statement at Detailed) can be enormous. Truncated rather than dropped,
#: because a line that is too long is usually the interesting one.
LINE_CAP = 4000

#: How much is read from the end at a time. Sixty-four kilobytes holds a few hundred ordinary lines,
#: so most requests are one read.
_BLOCK = 64 * 1024


def tail_of(path: Path, lines: int) -> list[str]:
    """The last `lines` complete lines of ONE file, oldest first. Nothing at all if it is not there.

    A missing file is an empty answer rather than an error, deliberately: no log file is an ordinary
    state (the setting that makes one may be off, or the application may have only just started)
    and it is not a fault to report.

    The same reader the filtered read below uses, asked to keep every line of one file and given no
    budget but the file itself: ONE way of reading a log backwards, so the screen's plain view and
    its filtered view cannot come to disagree about where a line starts.
    """
    return newest_matching([path], lines).lines


@dataclass(frozen=True, slots=True)
class Tail:
    """What a backwards read found, and how much of the log it had to look through to find it."""

    #: The lines kept, oldest first.
    lines: list[str]
    #: How many bytes were read to find them. The cost, and what a screen says it searched.
    read_bytes: int
    #: Whether the read reached the oldest line of the oldest file, so that a filtered view with
    #: fewer lines than asked for means "there are no more", and not "I stopped looking".
    whole: bool


#: How much of the log one filtered read may look through before it stops and says so.
#:
#: A level or a search can match nothing, and a read that kept going until it found enough would
#: then read the whole log (a gigabyte at the default ceiling) to answer "no errors". Thirty-two
#: megabytes is many hours of ordinary use (see `kernel/log_settings.DEFAULT_KEEP_MB`), which is
#: the window somebody opening the log after something went wrong is asking about. The answer
#: carries `whole`, so a screen can say the older
#: part was not searched rather than implying there was nothing in it.
SEARCH_BUDGET = 32 * 1024 * 1024


def newest_matching(
    paths: Sequence[Path],
    lines: int,
    keep: Callable[[str], bool] | None = None,
    *,
    budget: int | None = None,
) -> Tail:
    """The newest `lines` lines that `keep` accepts, across files newest first, oldest first.

    `paths` is the log and then its rotated copies in the order they were written BACKWARDS (the
    current file, `.1`, `.2`) because a line kept in `.1` is older than every line in the current
    file. A file that is not there is skipped: rotation leaves gaps, and a fresh install has none.

    Read from the END in blocks, so what it costs is what it had to look through rather than the
    size of the log. A block's first piece may be the tail of a line that started in the block
    before it, so it is CARRIED to the next read rather than judged: only a piece with a newline
    in front of it, or the very start of a file, is a whole line.

    `budget` bounds the bytes read. Unbounded is for a read with no filter, where the lines asked
    for are found within a few blocks by construction; a filter can match nothing, which is what
    the budget is for (see `SEARCH_BUDGET`). A read the budget stopped says `whole=False`.

    Any failure to READ a file that exists is an empty answer: a file rotated out
    from under the read, or a share that went away, is not a reason for a settings screen to raise
    while somebody is trying to find out what went wrong.
    """
    wanted = max(0, min(lines, MOST_LINES))
    if wanted == 0:
        return Tail(lines=[], read_bytes=0, whole=True)
    kept: list[str] = []  # newest first while reading; turned over at the end
    spent = 0

    def take(piece: bytes) -> bool:
        """Judge one whole line. True once there are enough."""
        text = piece.decode("utf-8", errors="replace").rstrip("\r")
        if not text.strip():
            return False
        text = text[:LINE_CAP]
        if keep is None or keep(text):
            kept.append(text)
        return len(kept) >= wanted

    def done(whole: bool) -> Tail:
        return Tail(lines=kept[::-1], read_bytes=spent, whole=whole)

    for path in paths:
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size == 0 or not path.is_file():
            continue
        at = size
        carry = b""
        try:
            with path.open("rb") as handle:
                while at > 0:
                    if budget is not None and spent >= budget:
                        return done(whole=False)
                    step = min(_BLOCK, at)
                    at -= step
                    handle.seek(at, os.SEEK_SET)
                    held = handle.read(step)
                    spent += len(held)
                    pieces = (held + carry).split(b"\n")
                    # The first piece is only whole if this read reached the start of the file.
                    carry = pieces[0]
                    for piece in reversed(pieces[1:]):
                        if take(piece):
                            return done(whole=False)
                    if len(carry) > LINE_CAP * 4:
                        # A line far past what can be shown. Reading backwards PREPENDS to it, so
                        # its front is always the earliest part read, and the front is what is
                        # shown (`LINE_CAP` characters, at most four bytes each). The rest is
                        # dropped now rather than a whole stack trace being held in memory.
                        carry = carry[: LINE_CAP * 4]
        except OSError:
            return Tail(lines=[], read_bytes=spent, whole=False)
        if take(carry):
            return done(whole=False)
    return done(whole=True)
