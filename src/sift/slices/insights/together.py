# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Alongside layer: two of a period's measures that rose and fell together, and the rank
correlation that decides it, in integers.

The figures are ranked (a tie takes the average of the ranks it spans), and the coefficient is the
Pearson correlation of the ranks. Ranks are kept doubled so a tie's half rank stays a whole number,
and whether a coefficient reaches a bar is decided by comparing squares of integers, so no float
ever decides whether a sentence is said.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from fractions import Fraction

from sift.kernel.access.sentences import Line, Piece, said
from sift.slices.insights.statements import (
    THEATER,
    Named,
    Period,
    counted,
    named,
)


def doubled_ranks(values: Sequence[int]) -> list[int]:
    """Each value's rank from 1, doubled; a run of equal values takes the average of its ranks."""
    order = sorted(range(len(values)), key=lambda at: values[at])
    ranks = [0] * len(values)
    start = 0
    while start < len(order):
        end = start
        while end + 1 < len(order) and values[order[end + 1]] == values[order[start]]:
            end += 1
        # Ranks start+1 to end+1, averaged and doubled: (start + 1) + (end + 1).
        for at in order[start : end + 1]:
            ranks[at] = start + end + 2
        start = end + 1
    return ranks


@dataclass(frozen=True, slots=True)
class Together:
    """The coefficient's parts over `n` pairs: rho = covariance / sqrt(spread_x * spread_y)."""

    n: int
    covariance: int
    spread_x: int
    spread_y: int

    @property
    def rho(self) -> float:
        """The coefficient, for ranking two of them; never for deciding one."""
        return self.covariance / math.sqrt(self.spread_x * self.spread_y)

    def at_least(self, bar: Fraction) -> bool:
        """Whether rho >= bar, for a bar above zero, decided in integers."""
        if self.covariance <= 0:
            return False
        left = self.covariance**2 * bar.denominator**2
        return left >= bar.numerator**2 * self.spread_x * self.spread_y


def spearman(xs: Sequence[int], ys: Sequence[int]) -> Together | None:
    """The rank correlation of two equally long runs, or None where either never moves."""
    if len(xs) != len(ys):
        raise ValueError("two runs of different lengths")
    n = len(xs)
    rx, ry = doubled_ranks(xs), doubled_ranks(ys)
    sx, sy = sum(rx), sum(ry)
    covariance = n * sum(a * b for a, b in zip(rx, ry, strict=True)) - sx * sy
    spread_x = n * sum(a * a for a in rx) - sx * sx
    spread_y = n * sum(b * b for b in ry) - sy * sy
    if n < 2 or spread_x == 0 or spread_y == 0:
        return None
    return Together(n, covariance, spread_x, spread_y)


#: The fewest periods a pair is read over, counting only those where both measures were above zero.
TOGETHER_FLOOR = 8


#: The weakest rank correlation said.
TOGETHER_AT = Fraction(3, 5)


#: The most such sentences a block says.
TOGETHER_MOST = 3


#: What each measure is, as the clause after "the weeks": its (metric, key) is the router's and
#: the recaps' (`MEASURES`); `person` is the top person of the span, named when said.
TOGETHER_WORDS: Mapping[str, str] = {
    "theater": "you viewed Theater most",
    "starred": "you starred the most files",
    "viewed": "you viewed the most",
    "added": "the most files arrived",
    "sessions": "you had the most sessions",
    "decided": "you answered the most questions on Organize",
    "o": "you pressed O the most",
    "person": "you viewed {} most",
}


#: Each measure's figure: the metric and key a day's rows hold it under. `person`'s key is the
#: span's top person, filled in by the caller.
MEASURES: Mapping[str, tuple[str, str]] = {
    "theater": ("viewed_ms:kind", THEATER),
    "starred": ("starred", ""),
    "viewed": ("viewed_ms", ""),
    "added": ("files_added", ""),
    "sessions": ("sittings", ""),
    "decided": ("decided", ""),
    "o": ("o", ""),
    "person": ("viewed_ms:person", ""),
}


#: The pairs tried, in this order on a tie.
PAIRS: tuple[tuple[str, str], ...] = (
    ("theater", "starred"),
    ("viewed", "added"),
    ("sessions", "decided"),
    ("viewed", "o"),
    ("person", "starred"),
)


#: The periods a span's alongside is read over, one and many.
TOGETHER_UNITS: Mapping[str, tuple[str, str]] = {
    "day": ("day", "days"),
    "week": ("week", "weeks"),
    "month": ("month", "months"),
}


@dataclass(frozen=True, slots=True)
class Pair:
    """Two measures found to rise and fall together over `n` periods, and how closely."""

    first: str
    second: str
    n: int
    rho: float


def together_pairs(series: Mapping[str, Sequence[int]]) -> list[Pair]:
    """The pairs said, strongest first: each read over the periods where both measures were above
    zero, at least `TOGETHER_FLOOR` of them, with a rank correlation of `TOGETHER_AT` or more. A
    measure missing from `series` is a pair not tried. `series` holds equally long runs, one figure
    per closed period."""
    found: list[Pair] = []
    for first, second in PAIRS:
        xs, ys = series.get(first), series.get(second)
        if xs is None or ys is None:
            continue
        both = [(x, y) for x, y in zip(xs, ys, strict=True) if x > 0 and y > 0]
        if len(both) < TOGETHER_FLOOR:
            continue
        rank = spearman([x for x, _ in both], [y for _, y in both])
        if rank is not None and rank.at_least(TOGETHER_AT):
            found.append(Pair(first, second, rank.n, rank.rho))
    found.sort(key=lambda one: -one.rho)
    return found[:TOGETHER_MOST]


def _together_clause(measure: str, person: Named | None) -> list[str | Piece]:
    words = TOGETHER_WORDS[measure]
    if measure != "person":
        return [words]
    if person is None:
        raise ValueError("a pair of the top person's time names the person")
    head, _, tail = words.partition("{}")
    return [head, named(person), tail]


def together(unit: str, pair: Pair, person: Named | None = None) -> Line:
    """ "Over 9 weeks, the weeks you viewed Theater most were the weeks you starred the most
    files." Two measures said as happening together, never one as the other's cause, with the
    sample the coefficient was read over."""
    one, more = TOGETHER_UNITS[unit]
    return said(
        f"Over {counted(pair.n, one, more)}, the {more} ",
        *_together_clause(pair.first, person),
        f" were the {more} ",
        *_together_clause(pair.second, person),
        ".",
    )


def closed_spans(period: Period) -> tuple[str, list[tuple[date, date]]]:
    """The closed periods the Alongside block reads a span over, and what they are called: the
    days of a week or a month, the whole weeks of a year, the whole months of everything. Only
    closed ones, and only whole ones inside the span: like with like."""
    yesterday = period.today - timedelta(days=1)
    spans: list[tuple[date, date]] = []
    if period.span in ("week", "month"):
        day = period.start
        while day <= min(period.end, yesterday):
            spans.append((day, day))
            day += timedelta(days=1)
        return "day", spans
    if period.span == "year":
        monday = period.start + timedelta(days=(7 - period.start.weekday()) % 7)
        while monday + timedelta(days=6) <= min(period.end, yesterday):
            spans.append((monday, monday + timedelta(days=6)))
            monday += timedelta(days=7)
        return "week", spans
    first = period.start if period.start.day == 1 else _next_month(period.start)
    while _next_month(first) - timedelta(days=1) <= yesterday:
        spans.append((first, _next_month(first) - timedelta(days=1)))
        first = _next_month(first)
    return "month", spans


def _next_month(day: date) -> date:
    """The first day of the month after `day`'s."""
    return (day.replace(day=28) + timedelta(days=4)).replace(day=1)


def series_of(
    daily: Mapping[str, Mapping[tuple[str, str], int]],
    spans: Sequence[tuple[date, date]],
    person: str | None,
) -> dict[str, list[int]]:
    """Each measure (`statements.MEASURES`) as one figure per span, from the rows a page or a
    recap already read; the top person's only where there is one."""
    out: dict[str, list[int]] = {}
    for measure, (metric, key) in MEASURES.items():
        if measure == "person":
            if person is None:
                continue
            key = person
        run: list[int] = []
        for first, last in spans:
            total = 0
            day = first
            while day <= last:
                total += daily.get(day.isoformat(), {}).get((metric, key), 0)
                day += timedelta(days=1)
            run.append(total)
        out[measure] = run
    return out
