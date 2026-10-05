# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a page of the log looks like on the wire."""

from __future__ import annotations

from pydantic import Field

from sift.kernel.wire import Wire


class LogLine(Wire):
    """One record, already taken apart so a screen does not have to read JSON.

    `raw` is kept beside the parsed fields rather than instead of them, because the fields a record
    carries are whatever the line that wrote it passed (there is no fixed set) and the ones that
    matter on a bad day are usually the ones nothing thought to name.
    """

    #: When it was written, as the logger wrote it. Absent on a line that is not one of Sift's.
    at: str | None = None
    #: `info`, `warning`, `error`, whatever the record says. Absent likewise.
    level: str | None = None
    #: The short name of what happened, which is what a reader scans down.
    event: str | None = None
    #: The whole line, always. A line that would not parse has only this.
    raw: str


class LogPage(Wire):
    """The end of the log, oldest first, and where it came from."""

    lines: list[LogLine] = Field(default=[])
    #: Where the file is, so somebody who wants the whole thing knows where to look.
    path: str
    #: How big it is now, so the size cap on the settings screen means something.
    size_bytes: int = 0
    #: False when there is no log file at all: the setting may be off, or nothing has been written
    #: yet. An empty page and an absent file are different answers and a screen should say which.
    present: bool = False
    #: How much of the log was looked through to find these lines. Only interesting when the page
    #: was filtered: a level or a search that matches rarely reads far, and this says how far.
    searched_bytes: int = 0
    #: Whether the read reached the oldest line kept, so a filtered page with fewer lines than
    #: asked for can say "that is all of them" or "the older part was not searched", which are
    #: different answers. See `tail.SEARCH_BUDGET`.
    whole: bool = True
