# SPDX-License-Identifier: AGPL-3.0-or-later
"""The sentences of the recap cards that name a file, a session, the downloads or a wall's files,
held to `statements`' rules."""

from __future__ import annotations

from sift.kernel.access.sentences import Line, said
from sift.slices.insights.statements import (
    Clock,
    Moment,
    Named,
    Period,
    _on,
    counted,
    duration,
    named,
    once,
    when,
)


def most_viewed_file(period: Period, file: Named, views: int) -> Line:
    """ "The file you viewed most this month was harbour.mp4: 14 views." Views outside Theater."""
    return said(
        f"The file you viewed most {when(period)} was ",
        named(file),
        f": {counted(views, 'view', 'views')}.",
    )


def first_and_last(
    period: Period, first: Named, began: Moment, last: Named, ended: Moment, hours: Clock
) -> Line:
    """ "Your first file today was A, at 9:10 AM, and your last B, at 11:40 PM.", and over a longer
    period the day as well: "..., on Monday at 9:10 AM, ...". The time a file was opened and the
    time the last one closed, on the reader's clock."""
    late = ended.minute >= 24 * 60
    return said(
        f"Your first file {when(period)} was ",
        named(first),
        f",{_on(period, began, always=False)} at {began.clock(hours)}, and your last ",
        named(last),
        f",{_on(period, ended, always=late)} at {ended.clock(hours)}.",
    )


def new_favourite(period: Period, file: Named, views: int) -> Line:
    """ "A new favorite this month: A, viewed 5 times on the first day you opened it." A file never
    viewed before that day and viewed three times or more on it."""
    return said(
        f"A new favorite {when(period)}: ",
        named(file),
        f", viewed {once(views)} on the first day you opened it.",
    )


def rediscovered(period: Period, file: Named, days: int) -> Line:
    """ "Welcome back to A, viewed again this month after 214 days." The longest time away."""
    return said(
        "Welcome back to ",
        named(file),
        f", viewed again {when(period)} after {counted(days, 'day', 'days')}.",
    )


def longest_session(period: Period, ms: int, pages: int) -> Line:
    """ "Your longest visit this month ran 2 hours from opening Sift to closing it, across 34
    pages." Its span, which holds a window left open as well, so never said as time spent."""
    head = f"Your longest visit {when(period)} ran {duration(ms)} from opening Sift to closing it"
    if pages <= 0:
        return said(f"{head}.")
    return said(f"{head}, across {counted(pages, 'page', 'pages')}.")


def downloaded(period: Period, files: int) -> Line | None:
    """ "Sift finished 40 downloads for you this month." A download is a press, so it is yours."""
    if files <= 0:
        return None
    return said(f"Sift finished {counted(files, 'download', 'downloads')} for you {when(period)}.")


def theater_showed(period: Period, files: int) -> Line | None:
    """ "Theater showed you 1,240 files this month.", counted once a day: a wall's files are
    named apart from the files you opened, which are counted outside Theater."""
    if files <= 0:
        return None
    return said(f"Theater showed you {counted(files, 'file', 'files')} {when(period)}.")
