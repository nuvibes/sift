# SPDX-License-Identifier: AGPL-3.0-or-later
"""A recap's periods: a day, a week, a month or a year on this device's calendar, when each is
due, and how a figure is named in a recipe. See `recaps` for the design.
"""

from __future__ import annotations

import calendar
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum

from sift.slices.insights import statements

#: THE RECAP FLOOR: sittings in the period, every kind, a Theater session counting as one (the
#: rollup counts it that way). The same number as the page's floor, and one constant.
FLOOR_SITTINGS = statements.SITTINGS_FLOOR


#: How long a recap is announced for, in days from when it was made.
ANNOUNCED_FOR_DAYS = 7


class PeriodKind(StrEnum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    YEAR = "year"


#: The order a recap is announced in when several are fresh: the longest period first, so a
#: Monday's week is not shadowed by its Sunday.
LONGEST_FIRST = (PeriodKind.YEAR, PeriodKind.MONTH, PeriodKind.WEEK, PeriodKind.DAY)


#: How long each kind is announced for, in days from when it was made: a day's for one day.
ANNOUNCED_DAYS: Mapping[PeriodKind, int] = {
    PeriodKind.DAY: 1,
    PeriodKind.WEEK: ANNOUNCED_FOR_DAYS,
    PeriodKind.MONTH: ANNOUNCED_FOR_DAYS,
    PeriodKind.YEAR: ANNOUNCED_FOR_DAYS,
}


#: Each kind's switch (`__init__.py` registers them): off, no recap of that kind is created.
SETTING_KEYS: Mapping[PeriodKind, str] = {
    PeriodKind.DAY: "insights.recap_day",
    PeriodKind.WEEK: "insights.recap_week",
    PeriodKind.MONTH: "insights.recap_month",
    PeriodKind.YEAR: "insights.recap_year",
}


@dataclass(frozen=True, slots=True)
class Period:
    """A stretch of this device's local calendar: a day, a week (Monday to Sunday), a month, a
    year."""

    kind: PeriodKind
    first: date
    last: date
    #: The clock a time of day on its cards is said on: the reader's, set when a recap is opened
    #: for somebody (`opened`); the default where nobody is reading, as when a recap is made.
    hours: statements.Clock = "12"

    @property
    def key(self) -> str:
        """The `recaps.period` value: 'day:YYYY-MM-DD', 'week:2026-W39', 'month:2026-09',
        'year:2026'."""
        if self.kind is PeriodKind.DAY:
            return f"day:{self.first.isoformat()}"
        if self.kind is PeriodKind.WEEK:
            year, week, _ = self.first.isocalendar()
            return f"week:{year}-W{week:02d}"
        if self.kind is PeriodKind.MONTH:
            return f"month:{self.first.year}-{self.first.month:02d}"
        return f"year:{self.first.year}"

    def previous(self) -> Period:
        return period_of(self.kind, self.first - timedelta(days=1))

    def said_on(self, today: date) -> statements.Period:
        """The same days as the statement builders take them, seen from `today`."""
        return statements.period_of(self.kind.value, self.first, today, None)

    @property
    def span(self) -> str:
        """The days it covers: "September 22, 2026", "September 21 to 27, 2026", "September 2026",
        "2026"."""
        month = calendar.month_name
        if self.kind is PeriodKind.DAY:
            return f"{month[self.first.month]} {self.first.day}, {self.first.year}"
        if self.kind is PeriodKind.YEAR:
            return str(self.first.year)
        if self.kind is PeriodKind.MONTH:
            return f"{month[self.first.month]} {self.first.year}"
        start = f"{month[self.first.month]} {self.first.day}"
        if self.first.year != self.last.year:
            return (
                f"{start}, {self.first.year} to "
                f"{month[self.last.month]} {self.last.day}, {self.last.year}"
            )
        end = (
            str(self.last.day)
            if self.last.month == self.first.month
            else f"{month[self.last.month]} {self.last.day}"
        )
        return f"{start} to {end}, {self.last.year}"


def period_of(kind: PeriodKind, day: date) -> Period:
    """The period of this kind that `day` falls in. The bounds are the statement builders' own."""
    bounds = statements.period_of(kind.value, day, day, None)
    return Period(kind, bounds.start, bounds.end)


def period_from_key(key: str) -> Period | None:
    """The period a `recaps.period` value names, or None for one that is not a period (an
    achievement) or is not well formed."""
    kind, _, rest = key.partition(":")
    try:
        if kind == PeriodKind.DAY:
            return period_of(PeriodKind.DAY, date.fromisoformat(rest))
        if kind == PeriodKind.WEEK:
            year, _, week = rest.partition("-W")
            return period_of(PeriodKind.WEEK, date.fromisocalendar(int(year), int(week), 1))
        if kind == PeriodKind.MONTH:
            year, _, month = rest.partition("-")
            return period_of(PeriodKind.MONTH, date(int(year), int(month), 1))
        if kind == PeriodKind.YEAR:
            return period_of(PeriodKind.YEAR, date(int(rest), 1, 1))
    except ValueError:
        return None
    return None


def due(
    today: date, added_up_to: date | None, kinds: Iterable[PeriodKind] = tuple(PeriodKind)
) -> list[Period]:
    """THE DUE RULE: the periods whose recap may be made today, of the kinds asked for.

    For each kind, the period immediately before the one today falls in, once its last day has been
    added up: yesterday, last week, last month, last year. Nothing is due before anything has been
    added up. See the module's note on why the week before last is not made late.
    """
    if added_up_to is None:
        return []
    closed = [period_of(kind, today).previous() for kind in kinds]
    return [period for period in closed if period.last <= added_up_to]


def title_of(period: Period, today: date) -> str:
    """ "Your Tuesday", "Your week", "Your September", "Your 2026". "Your September 2025" in
    another year."""
    if period.kind is PeriodKind.DAY:
        return f"Your {statements.WEEKDAYS[period.first.weekday()]}"
    if period.kind is PeriodKind.WEEK:
        return "Your week"
    return f"Your {statements.named_period(period.said_on(today))}"


#: Where a source's figure was read: the period itself, the one before it (`prev:`), or the usual
#: day's `USUAL_DAYS` before a day (`usual:`, summed; the recipe keeps how many days it spans).
PREV = "prev"


USUAL = "usual"


def source(metric: str, key: str = "", *, before: bool = False, scope: str = "") -> str:
    """The name a figure is kept under in a recipe: `metric|key`, `prev:` for the period before,
    `usual:` for the days a day is compared with."""
    scope = PREV if before else scope
    return f"{scope + ':' if scope else ''}{metric}|{key}"


def _parts(name: str) -> tuple[str, str, str]:
    """(where it was read, the metric, the key) of a source name."""
    scope, colon, rest = name.partition(":")
    if not colon or scope not in (PREV, USUAL):
        scope, rest = "", name
    metric, _, key = rest.partition("|")
    return scope, metric, key


def _usual_window(period: Period) -> tuple[date, date]:
    """The days a day is compared with: the `USUAL_DAYS` before it."""
    return period.first - timedelta(days=statements.USUAL_DAYS), period.first - timedelta(days=1)
