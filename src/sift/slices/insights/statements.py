# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every sentence Insights says, as pieces from figures already read for the vault state."""

from __future__ import annotations

import calendar
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal
from urllib.parse import quote

from sift.kernel.access.sentences import Line, Piece, and_then, many, plural, said, thing

#: A Theater wall is one sitting.
SITTINGS_FLOOR = 10


DECISIONS_FLOOR = 5


#: Three of four is "75 percent" and means nothing.
SHARE_FLOOR = 10


NOT_ENOUGH = "Not enough yet to say."


NOTHING_YET = "Insights start once you have viewed a few things. Sift is keeping count from today."


NOTHING_SHARED = "Nothing is shared with you yet."


CAME_BACK = 3


#: A single day is compared with the average day over this many days before it.
USUAL_DAYS = 28


USUAL_FLOOR_DAYS = 7


ABOUT_OVER = 100_000


#: Theater is a place the files were, not a kind of file.
KINDS = ("video", "image", "gif")


THEATER = "theater"


KIND_WORDS: Mapping[str, tuple[str, str]] = {
    "video": ("video", "videos"),
    "image": ("picture", "pictures"),
    "gif": ("GIF", "GIFs"),
}


SPANS = ("day", "week", "month", "year", "all")


WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


WEEKDAYS_SHORT = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


@dataclass(frozen=True, slots=True)
class Period:
    """One stretch of this device's local days the page is about, and today."""

    span: str
    start: date
    end: date
    today: date

    @property
    def is_open(self) -> bool:
        """Whether the period is still happening: today is inside it."""
        return self.start <= self.today <= self.end

    @property
    def days_so_far(self) -> int:
        """How many of its days have happened, so a daily average does not count the future."""
        last = min(self.end, self.today)
        return max((last - self.start).days + 1, 0)

    def previous(self) -> Period | None:
        """The period of the same span just before this one, or None for "all", which has none."""
        if self.span == "all":
            return None
        return period_of(self.span, self.start - timedelta(days=1), self.today, None)


def period_of(span: str, at: date, today: date, first: date | None) -> Period:
    """The period of `span` holding `at`; "all" runs from the first recorded day to today."""
    if span == "day":
        return Period(span, at, at, today)
    if span == "week":
        start = at - timedelta(days=at.weekday())
        return Period(span, start, start + timedelta(days=6), today)
    if span == "month":
        last = calendar.monthrange(at.year, at.month)[1]
        return Period(span, at.replace(day=1), at.replace(day=last), today)
    if span == "year":
        return Period(span, date(at.year, 1, 1), date(at.year, 12, 31), today)
    if span == "all":
        return Period(span, min(first or today, today), today, today)
    raise ValueError(f"not a span: {span!r}")


def _day_words(day: date, today: date) -> str:
    """ "September 23", or "September 23, 2025" in another year."""
    words = f"{calendar.month_name[day.month]} {day.day}"
    return words if day.year == today.year else f"{words}, {day.year}"


def _month_words(start: date, today: date) -> str:
    """ "August", or "August 2025" in another year."""
    words = calendar.month_name[start.month]
    return words if start.year == today.year else f"{words} {start.year}"


def when(period: Period) -> str:
    """The period as a statement says it after a figure: "this month", "in August", "today"."""
    today = period.today
    if period.span == "day":
        if period.start == today:
            return "today"
        if period.start == today - timedelta(days=1):
            return "yesterday"
        return f"on {WEEKDAYS[period.start.weekday()]}, {_day_words(period.start, today)}"
    if period.span == "week":
        if period.is_open:
            return "this week"
        if period.end == today - timedelta(days=today.weekday() + 1):
            return "last week"
        return f"in the week of {_day_words(period.start, today)}"
    if period.span == "month":
        return "this month" if period.is_open else f"in {_month_words(period.start, today)}"
    if period.span == "year":
        return "this year" if period.is_open else f"in {period.start.year}"
    return "overall"


def named_period(period: Period) -> str:
    """The period as a noun: "September", "this week", "2025"."""
    today = period.today
    if period.span == "day":
        if period.start == today:
            return "today"
        if period.start == today - timedelta(days=1):
            return "yesterday"
        return _day_words(period.start, today)
    if period.span == "week":
        if period.is_open:
            return "this week"
        if period.end == today - timedelta(days=today.weekday() + 1):
            return "last week"
        return f"the week of {_day_words(period.start, today)}"
    if period.span == "month":
        return _month_words(period.start, today)
    if period.span == "year":
        return str(period.start.year)
    return "this"


def _nearest(numerator: int, denominator: int) -> int:
    """Rounded half up in integers, so no float decides which way a figure rounds."""
    return (2 * numerator + denominator) // (2 * denominator)


def count(value: int) -> str:
    """A count as a person says it: "7", "1,240", and past 100,000 "about 101,000"."""
    if value > ABOUT_OVER:
        return f"about {many(_nearest(value, 1000) * 1000)}"
    return many(value)


def counted(value: int, one: str, more: str) -> str:
    """ "1 video" or "312 videos": the kernel's one plural rule, with Insights' own rounding."""
    return plural(value, one, more, number=count)


def duration(ms: int) -> str:
    """A length of time as a person says it, never with a decimal."""
    # Under a day the figure beside it keeps its minutes, so whole hours there say "about".
    minutes = _nearest(max(ms, 0), 60_000)
    if minutes < 1:
        return "less than a minute"
    if minutes < 60:
        return counted(minutes, "minute", "minutes")
    if minutes < 120:
        rest = minutes - 60
        return "1 hour" if rest == 0 else f"1 hour {counted(rest, 'minute', 'minutes')}"
    hours = counted(_nearest(ms, 3_600_000), "hour", "hours")
    return f"about {hours}" if minutes < 24 * 60 and minutes % 60 else hours


def duration_short(ms: int) -> str:
    """A length as a fact on a tile: the client's `sayDuration` rule, rounded before it is split."""
    ms = max(ms, 0)
    if ms < 60_000:
        return "under a minute"
    minutes = _nearest(ms, 60_000)
    if minutes < 60:
        return f"{minutes} min"
    if minutes < 24 * 60:
        return _pair(minutes // 60, "h", minutes % 60, "min")
    hours = _nearest(ms, 3_600_000)
    return _pair(hours // 24, "d", hours % 24, "h")


def _pair(big: int, big_unit: str, small: int, small_unit: str) -> str:
    """ "2 h 10 min", or "2 h" where the smaller unit comes to nothing."""
    return f"{big} {big_unit}" if small == 0 else f"{big} {big_unit} {small} {small_unit}"


_UNIT_NOUNS: Mapping[str, tuple[str, str]] = {
    "views": ("view", "views"),
    "times": ("time", "times"),
    "presses": ("press", "presses"),
    "files": ("file", "files"),
}


def figure_said(value: int, unit: str) -> str:
    """A figure as a tile says it; empty for a time of day and a size, worded by the screen."""
    if unit == "ms":
        return "0 min" if value <= 0 else duration_short(value)
    if unit in _UNIT_NOUNS:
        return counted(value, *_UNIT_NOUNS[unit])
    if unit == "count":
        return count(value)
    return ""


def shares_of(values: Sequence[int], whole: int) -> list[int]:
    """`values` as whole numbers adding up to exactly `whole`, by largest remainder."""
    total = sum(max(value, 0) for value in values)
    if total <= 0 or whole <= 0:
        return [0 for _ in values]
    floors = [max(value, 0) * whole // total for value in values]
    rests = [
        max(value, 0) * whole - floor * total for value, floor in zip(values, floors, strict=True)
    ]
    left = whole - sum(floors)
    for at in sorted(range(len(values)), key=lambda one: (-rests[one], one))[:left]:
        floors[at] += 1
    return floors


def _parts_of(total_ms: int, parts: Sequence[int]) -> list[str]:
    """The parts of a whole length, said so they add up to the whole as said."""
    minutes = _nearest(max(total_ms, 0), 60_000)
    if minutes >= 120:
        hours = shares_of(parts, _nearest(total_ms, 3_600_000))
        said_as: list[str] = []
        for h, ms in zip(hours, parts, strict=True):
            if h >= 2 or (h == 1 and duration(ms) == "1 hour"):
                # After "25 minutes of GIFs", a bare "3 in Theater" would read as minutes.
                bare = not said_as or said_as[-1][-1].isdigit()
                said_as.append(count(h) if bare else _hours(h))
            else:
                said_as.append(duration(ms))
        return said_as
    shared = shares_of(parts, minutes)
    if minutes < 60:
        return [count(one) if one > 0 else duration(0) for one in shared]
    return [_minutes(one) for one in shared]


def _hours(hours: int) -> str:
    """ "1 hour", "3 hours"."""
    return "1 hour" if hours == 1 else f"{count(hours)} hours"


def _minutes(minutes: int) -> str:
    """A whole number of minutes as `duration` says it: "45 minutes", "1 hour 5 minutes"."""
    return duration(minutes * 60_000)


#: The reader's `appearance.clock`, read by the route; nothing here reads a setting.
Clock = Literal["12", "24"]


def clock(minute: int, hours: Clock) -> str:
    """A minute of the day on the reader's clock, as `lib/shell/when.ts` writes it."""
    minute %= 24 * 60
    hour, past = divmod(minute, 60)
    if hours == "24":
        return f"{hour:02d}:{past:02d}"
    return f"{hour % 12 or 12}:{past:02d} {'AM' if hour < 12 else 'PM'}"


def hour_mark(hour: int, hours: Clock) -> str:
    """An hour alone under a chart's bar, "2 AM" or "02": a full time would hit its neighbours."""
    hour %= 24
    if hours == "24":
        return f"{hour:02d}"
    return f"{hour % 12 or 12} {'AM' if hour < 12 else 'PM'}"


def once(times: int) -> str:
    """ "once", "twice", "38 times"."""
    return {1: "once", 2: "twice"}.get(times, f"{count(times)} times")


@dataclass(frozen=True, slots=True)
class Named:
    """One thing a statement names: a History link kind, or `wall`, a saved Theater wall."""

    kind: str
    id: str
    name: str


#: A wall has no page of its own, so it opens on Theater.
_ADDRESS_OF: Mapping[str, str] = {"wall": "/theater?wall={id}"}


def named(one: Named) -> Piece:
    """One thing as the piece a statement places it with: a link, wherever it sits."""
    address = _ADDRESS_OF.get(one.kind)
    href = address.format(id=quote(one.id, safe="")) if address else None
    return thing(one.kind, one.id, one.name, href=href)


def plain(words: str) -> Line:
    """A statement that is only words: the floor's line, the empty library's, the guest's."""
    return said(words)


def viewed(period: Period, total_ms: int, parts: Mapping[str, int]) -> Line:
    """The headline: "You viewed 41 hours in August: 29 of videos, 9 of pictures, 3 of GIFs."."""
    whole = duration(total_ms)
    so_far = " so far" if period.is_open and period.span != "all" else ""
    shown = [(kind, ms) for kind, ms in _in_order(parts) if ms > 0]
    if len(shown) == 1:
        return said(f"You viewed {whole} {_of(shown[0][0])} {when(period)}{so_far}.")
    words = _parts_of(total_ms, [ms for _, ms in shown])
    pieces = ", ".join(
        f"{said_as} {_of(kind)}" for (kind, _), said_as in zip(shown, words, strict=True)
    )
    if not pieces:
        return said(f"You viewed {whole} {when(period)}{so_far}.")
    return said(f"You viewed {whole} {when(period)}{so_far}: {pieces}.")


def _in_order(parts: Mapping[str, int]) -> list[tuple[str, int]]:
    """The parts by kind in the order a sentence lists them: videos, pictures, GIFs, Theater."""
    return [(kind, parts.get(kind, 0)) for kind in (*KINDS, THEATER)]


def _of(kind: str) -> str:
    """What a part of time was spent on: "of videos", or "in Theater"."""
    return "in Theater" if kind == THEATER else f"of {KIND_WORDS[kind][1]}"


def compared(period: Period, this_ms: int, before: Period, before_ms: int) -> Line | None:
    """ "That's 12 hours fewer than July.", only between two closed periods."""
    if period.is_open or before.is_open:
        return None
    name = named_period(before)
    than = "the week before" if period.span == "week" else name
    if period.span == "day":
        than = "the day before"
    difference = this_ms - before_ms
    if duration(abs(difference)) == "less than a minute" or difference == 0:
        return said(f"That's the same as {than}.")
    more = "more" if difference > 0 else "fewer"
    return said(f"That's {duration(abs(difference))} {more} than {than}.")


def compared_with_usual(this_ms: int, usual_ms: int) -> Line:
    """ "That's 40 minutes more than your daily average.": one day against another is noise."""
    difference = this_ms - usual_ms
    if difference == 0 or duration(abs(difference)) == "less than a minute":
        return said("That's the same as your daily average.")
    more = "more" if difference > 0 else "less"
    return said(f"That's {duration(abs(difference))} {more} than your daily average.")


def biggest(period: Period, day: date, ms: int) -> Line | None:
    """ "Thursday was your biggest day: 5 hours.": the chart's tallest bar, in words."""
    if ms <= 0 or period.span == "day":
        return None
    if period.span == "week":
        return said(f"{WEEKDAYS[day.weekday()]} was your biggest day: {duration(ms)}.")
    if period.span == "month":
        return said(f"{_day_words(day, period.today)} was your biggest day: {duration(ms)}.")
    month = _month_words(day.replace(day=1), period.today)
    return said(f"{month} was your biggest month: {duration(ms)}.")


def busiest_hour(hour: int, ms: int, hours: Clock) -> Line | None:
    """ "Your most-viewed hour began at 10:00 PM: 40 minutes.", on the reader's clock."""
    if ms <= 0:
        return None
    return said(f"Your most-viewed hour began at {clock(hour * 60, hours)}: {duration(ms)}.")


def files_by_kind(files: Mapping[str, int], sittings: int) -> Line | None:
    """ "312 videos, 1,240 pictures and 96 GIFs, across 212 visits." """
    parts = [counted(files.get(kind, 0), *KIND_WORDS[kind]) for kind in KINDS if files.get(kind)]
    if not parts:
        return None
    joined = and_then(parts)
    comma = "," if len(parts) > 1 else ""
    return said(f"{joined}{comma} across {counted(sittings, 'visit', 'visits')}.")


def most_viewed_person(period: Period, person: Named, ms: int, files: Mapping[str, int]) -> Line:
    """ "Your most-viewed person this month was Ava Example: 6 hours, 38 videos." """
    parts = [duration(ms)]
    parts += [counted(files.get(kind, 0), *KIND_WORDS[kind]) for kind in KINDS if files.get(kind)]
    return said(
        f"Your most-viewed person {when(period)} was ", named(person), f": {and_then(parts)}."
    )


_THING_WORDS: Mapping[str, str] = {"site": "Site", "tag": "tag", "song": "song"}


def most_viewed(period: Period, one: Named, ms: int) -> Line:
    """ "Your most-viewed Site in September was Quillhouse: 5 hours.", and the same for a tag."""
    return said(
        f"Your most-viewed {_THING_WORDS.get(one.kind, one.kind)} {when(period)} was ",
        named(one),
        f": {duration(ms)}.",
    )


def most_viewed_people(period: Period) -> Line:
    """ "The people you viewed most in September." The heading of a ranked list of five."""
    return said(f"The people you viewed most {when(period)}.")


def theater(period: Period, total_ms: int, wall: Named | None, wall_ms: int, sessions: int) -> Line:
    """ "You spent 5 hours in Theater, mostly on your Saved Layout 'Nine up'.", or no time."""
    if total_ms <= 0:
        return said(f"You spent no time in Theater {when(period)}.")
    head = f"You spent {duration(total_ms)} in Theater"
    if wall is not None and sessions >= SHARE_FLOOR and 2 * wall_ms > total_ms:
        return said(f"{head}, mostly on your Saved Layout '", named(wall), "'.")
    return said(f"{head}.")


def came_back(files: int) -> Line | None:
    """ "14 files you came back to three times or more." """
    if files <= 0:
        return None
    return said(f"{counted(files, 'file', 'files')} you came back to three times or more.")


def three_times_or_more() -> Line:
    """What a file came back to is: the caption under that figure."""
    return said("Each opened three times or more.")


def photo_sets(looked_through: int) -> Line | None:
    """ "You looked through 36 Photo Sets." """
    if looked_through <= 0:
        return None
    return said(f"You looked through {counted(looked_through, 'Photo Set', 'Photo Sets')}.")


def songs(heard: int) -> Line | None:
    """ "You viewed files carrying 12 songs." """
    if heard <= 0:
        return None
    return said(f"You viewed files carrying {counted(heard, 'song', 'songs')}.")


def opened(period: Period, pickups: int) -> Line | None:
    """ "You opened Sift 38 times this week." """
    if pickups <= 0:
        return None
    return said(f"You opened Sift {once(pickups)} {when(period)}.")


def opened_first(pickups: int, person: Named, times: int) -> Line | None:
    """ "12 of those times, the first thing you opened was Ava Example." """
    if pickups < SHARE_FLOOR or times < 2 or times > pickups:
        return None
    lead = "Every time" if times == pickups else f"{count(times)} of those times"
    return said(f"{lead}, the first thing you opened was ", named(person), ".")


@dataclass(frozen=True, slots=True)
class Moment:
    """When a sitting started or finished; a finish past midnight keeps the day it began on."""

    day: date
    minute: int

    def clock(self, hours: Clock) -> str:
        return clock(self.minute, hours)

    def on(self) -> date:
        """The calendar day the clock time falls on: the next one for a finish past midnight."""
        return self.day + timedelta(days=self.minute // (24 * 60))


def _on(period: Period, moment: Moment, *, always: bool) -> str:
    """ " on Tuesday", " on September 23", or nothing for a day's own sittings."""
    day = moment.on()
    if period.span == "day" and not always:
        return ""
    if period.span in ("day", "week"):
        return f" on {WEEKDAYS[day.weekday()]}"
    return f" on {_day_words(day, period.today)}"


def earliest_and_latest(period: Period, earliest: Moment, latest: Moment, hours: Clock) -> Line:
    """ "Your earliest start was 7:10 AM on Tuesday; your latest finish 1:40 AM on Saturday." """
    late_day = latest.minute >= 24 * 60
    return said(
        f"Your earliest start was {earliest.clock(hours)}{_on(period, earliest, always=False)};"
        f" your latest finish {latest.clock(hours)}{_on(period, latest, always=late_day)}."
    )


def on_the_day(period: Period, moment: Moment) -> Line | None:
    """ "On Tuesday.": the day under an earliest start or a latest finish."""
    words = _on(period, moment, always=moment.minute >= 24 * 60).strip()
    if not words:
        return None
    return said(f"{words[:1].upper()}{words[1:]}.")


def busiest_weekday(period: Period, weekday: int, ms: int, total_ms: int) -> Line | None:
    """ "You viewed the most on Fridays: 12 hours.", over a week or longer."""
    if period.span == "day" or ms <= 0 or total_ms <= 0:
        return None
    day = WEEKDAYS[weekday] if period.span == "week" else f"{WEEKDAYS[weekday]}s"
    return said(f"You viewed the most on {day}: {duration(ms)}.")


def opinions(rated: int, starred: int) -> Line | None:
    """ "You rated 44 files and starred 19." No average: that is a judgement dressed as a figure."""
    if rated <= 0 and starred <= 0:
        return None
    if rated <= 0:
        return said(f"You starred {counted(starred, 'file', 'files')}.")
    if starred <= 0:
        return said(f"You rated {counted(rated, 'file', 'files')}.")
    return said(f"You rated {counted(rated, 'file', 'files')} and starred {count(starred)}.")


def o_count(period: Period, presses: int) -> Line | None:
    """ "You pressed O 12 times this month." """
    if presses <= 0:
        return None
    return said(f"You pressed O {once(presses)} {when(period)}.")


def organized(period: Period, decisions: int) -> Line | None:
    """ "You answered 120 questions on Organize this month." """
    if decisions <= 0:
        return None
    return said(
        f"You answered {counted(decisions, 'question', 'questions')} on Organize {when(period)}."
    )


def named_and_filed(faces_named: int, files_filed: int) -> Line | None:
    """ "You named 30 faces and filed 45 files." A part with nothing in it is left out."""
    parts = []
    if faces_named > 0:
        parts.append(f"named {counted(faces_named, 'face', 'faces')}")
    if files_filed > 0:
        parts.append(f"filed {counted(files_filed, 'file', 'files')}")
    if not parts:
        return None
    return said(f"You {and_then(parts)}.")


def arrived(period: Period, added: int) -> Line:
    """ "142 files were imported this month.", or "No files..."."""
    if added <= 0:
        return said(f"No files were imported {when(period)}.")
    return said(f"{counted(added, 'file', 'files')} were imported {when(period)}.")


def most_from(site: Named, files: int) -> Line | None:
    """ "Most came from Quillhouse: 40 files." The Site that brought the most files."""
    if files <= 0:
        return None
    return said("Most came from ", named(site), f": {counted(files, 'file', 'files')}.")


def deleted(removed: int) -> Line | None:
    """ "3 files were deleted." Passive, because the record counts deletions by anybody."""
    if removed <= 0:
        return None
    were = "was" if removed == 1 else "were"
    return said(f"{counted(removed, 'file', 'files')} {were} deleted.")


def worked(period: Period, total_ms: int) -> Line:
    """ "Sift worked on tasks for 14 hours this month.", never None, or the block says nothing."""
    if total_ms <= 0:
        return said(f"Sift spent no time on tasks {when(period)}.")
    return said(f"Sift worked on tasks for {duration(total_ms)} {when(period)}.")


def found_and_fingerprinted(faces_found: int, fingerprints: int) -> Line | None:
    """ "Sift found 1,204 faces and fingerprinted 3,000 files." """
    parts = []
    if faces_found > 0:
        parts.append(f"found {counted(faces_found, 'face', 'faces')}")
    if fingerprints > 0:
        parts.append(f"fingerprinted {counted(fingerprints, 'file', 'files')}")
    if not parts:
        return None
    return said(f"Sift {and_then(parts)}.")


def still_counting() -> Line:
    """ "Sift is still counting some earlier days.", so a figure that will grow is not a fact."""
    return said("Sift is still counting some earlier days.")


def some_hidden(period: Period) -> Line:
    """ "Some of September is hidden. Unlock to include it.", in placeholder mode only."""
    return said(f"Some of {named_period(period)} is hidden. Unlock to include it.")


def statements_of(lines: Sequence[Line | None]) -> list[Line]:
    """The lines a block says, in order, with the ones that had nothing to say left out."""
    return [line for line in lines if line]
