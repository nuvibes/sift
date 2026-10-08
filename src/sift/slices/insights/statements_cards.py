# SPDX-License-Identifier: AGPL-3.0-or-later
"""The sentences of the recap cards that name a file, a session, the downloads or a wall's files,
held to `statements`' rules."""

from __future__ import annotations

from datetime import date

from sift.kernel.access.sentences import Line, said
from sift.slices.insights.statements import (
    KIND_WORDS,
    THEATER,
    WEEKDAYS,
    Clock,
    Moment,
    Named,
    Period,
    _day_words,
    _on,
    count,
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


def most_viewed_files(period: Period) -> Line:
    """ "The files you viewed most in 2026." The heading of the five drawn side by side."""
    return said(f"The files you viewed most {when(period)}.")


def _mostly(kind: str) -> str:
    return "Theater" if kind == THEATER else KIND_WORDS[kind][1]


def focus(first_month: str, first_kind: str, last_month: str, last_kind: str) -> Line:
    """ "Your focus shifted: January was mostly videos, December mostly pictures.", only where
    the kind with the most time changed, else "Much the same: mostly videos in January and in
    December." """
    if first_kind != last_kind:
        return said(
            f"Your focus shifted: {first_month} was mostly {_mostly(first_kind)}, "
            f"{last_month} mostly {_mostly(last_kind)}."
        )
    return said(
        f"Much the same: mostly {_mostly(first_kind)} in {first_month} and in {last_month}."
    )


def months_led(period: Period, person: Named, months: int, of: int) -> Line:
    """ "Ava Lin was the person you viewed most in 7 of 12 months in 2026." Over the months with
    any time, the top five people's month by month beside it."""
    return said(
        named(person),
        f" was the person you viewed most in {count(months)} of "
        f"{counted(of, 'month', 'months')} {when(period)}.",
    )


def days_viewed(period: Period, days: int, busiest: date) -> Line:
    """ "You viewed files on 214 days in 2026, the most on Saturday, March 14." """
    return said(
        f"You viewed files on {counted(days, 'day', 'days')} {when(period)}, the most on "
        f"{day_named(busiest, period.today)}."
    )


def day_named(day: date, today: date) -> str:
    """ "Saturday, March 14": the busiest day, under its figure on the closing card."""
    return f"{WEEKDAYS[day.weekday()]}, {_day_words(day, today)}"
