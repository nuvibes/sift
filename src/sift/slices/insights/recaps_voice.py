# SPDX-License-Identifier: AGPL-3.0-or-later
"""A recap card's voice: a headline of a few words and a line or two of context over its figure.

The card's statement stays as its definition. The voice is what the card leads with: each kind has
several phrasings (`recaps_voice_lines`), each a story told only when the figures tell it. A card
says a story its figures tell before a phrasing that fits any figures, and where several fit the
period chooses between them, so the same recap always reads the same way.

The only comparison is with the reader's own past: a day against the same weekday of the four
weeks before it, a longer period against the one before, read from the recipe and with the hidden
part taken out like every other figure.
"""

from __future__ import annotations

import re
import zlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from fractions import Fraction
from statistics import median
from typing import TYPE_CHECKING

from sift.kernel.access.sentences import Line, Piece, capitalized, said
from sift.slices.insights import statements
from sift.slices.insights.recaps_models import Recipe
from sift.slices.insights.recaps_periods import USUAL, Period, PeriodKind, _parts, source
from sift.slices.insights.recaps_voice_lines import FIRST, LINES, Phrasing
from sift.slices.insights.statements import Named

if TYPE_CHECKING:
    from sift.slices.insights.recaps_cards import Said

Slot = str | Piece | Line

_MINUTE = 60_000
_HOUR = 60 * _MINUTE
_FILM = 90 * _MINUTE

#: How long a name may run and still sit in a headline.
NAME_FITS = 24

#: The days before a day it is compared with: the same weekday, this many weeks back.
USUAL_WEEKS = 4

#: The fewest of those days that make a usual.
USUAL_FEWEST = 2


@dataclass(frozen=True, slots=True)
class Voice:
    """What a card leads with, and which phrasing said it."""

    headline: Line
    context: Line
    phrasing: str


@dataclass(frozen=True, slots=True)
class Facts:
    """What a card's phrasings may say: the words for each slot, and the stories that are true."""

    slots: Mapping[str, Slot]
    stories: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class Card:
    """One card as its voice reads it."""

    kind: str
    words: Said
    figures: Mapping[str, int]
    recipe: Recipe
    period: Period
    today: date

    def get(self, metric: str, key: str = "", *, before: bool = False) -> int:
        return self.figures.get(source(metric, key, before=before), 0)

    def has(self, metric: str, key: str = "", *, before: bool = False) -> bool:
        return source(metric, key, before=before) in self.recipe.sources

    @property
    def said_on(self) -> statements.Period:
        return self.period.said_on(self.today)

    @property
    def is_day(self) -> bool:
        return self.period.kind is PeriodKind.DAY


# --- the numbers as a person says them -----------------------------------------------------


def _nearest(value: int, unit: int) -> int:
    return (2 * max(value, 0) + unit) // (2 * unit)


def spoken(ms: int) -> str:
    """A length said the way a person rounds it: "40 minutes", "an hour and a half", "3 hours".
    Under an hour it is the minutes the card's figure shows."""
    minutes = _nearest(ms, _MINUTE)
    if minutes < 1:
        return "under a minute"
    if minutes < 60:
        return statements.counted(minutes, "minute", "minutes")
    if minutes < 75:
        return "an hour"
    if minutes < 105:
        return "an hour and a half"
    return statements.counted(max(_nearest(ms, _HOUR), 2), "hour", "hours")


_FILMS = {1: "half a film", 2: "a film", 3: "a film and a half", 4: "two films"}


def spoken_short(ms: int) -> str:
    """A length as a headline says it, in one unit: "85 minutes", "3 hours"."""
    minutes = _nearest(ms, _MINUTE)
    if minutes < 120:
        return spoken(ms) if minutes < 60 else f"{minutes} minutes"
    return spoken(ms)


def films(ms: int) -> str | None:
    """A length as films of an hour and a half, to the nearest half: "a film and a half"."""
    halves = _nearest(ms, _FILM // 2)
    if halves < 1:
        return None
    return _FILMS.get(halves) or f"{statements.count(_nearest(ms, _FILM))} films"


_SHARES = (
    (Fraction(1, 10), "a tenth"),
    (Fraction(1, 5), "a fifth"),
    (Fraction(1, 4), "a quarter"),
    (Fraction(1, 3), "a third"),
    (Fraction(1, 2), "half"),
    (Fraction(2, 3), "two thirds"),
    (Fraction(3, 4), "three quarters"),
    (Fraction(9, 10), "nearly all"),
)


def share(part: int, whole: int) -> str | None:
    """`part` of `whole` as the nearest everyday fraction, or None under a twentieth."""
    if whole <= 0 or part <= 0 or Fraction(part, whole) < Fraction(1, 20):
        return None
    exact = min(Fraction(part, whole), Fraction(1))
    return min(_SHARES, key=lambda one: abs(one[0] - exact))[1]


_TIMES = {2: "twice", 3: "three times", 4: "four times", 5: "five times"}


def times(big: int, small: int) -> str | None:
    """How many times `small` goes into `big`, from twice up: "three times", "about 12 times"."""
    if small <= 0 or big < 2 * small:
        return None
    whole = _nearest(big, small)
    words = _TIMES.get(whole, f"{statements.count(whole)} times")
    return words if whole * small == big else f"about {words}"


def hour_said(hour: int, hours: statements.Clock) -> str:
    """An hour as a headline says it: "10 PM", "midnight", "noon", or "22:00"."""
    if hours == "24":
        return f"{hour:02d}:00"
    return {0: "midnight", 12: "noon"}.get(hour, statements.hour_mark(hour, hours))


def hour_bare(hour: int, hours: statements.Clock) -> str:
    """An hour as "closer to 9" says it: the number alone, or "21:00"."""
    return f"{hour:02d}:00" if hours == "24" else str(hour % 12 or 12)


def part_of_day(hour: int) -> str:
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    return "evening" if hour >= 17 else "night"


def per_day(value: int, period: Period, today: date) -> str | None:
    """`value` a day over the days of the period so far, from two days up."""
    days = (min(period.last, today) - period.first).days + 1
    return statements.count(_nearest(value, days)) if days > 1 and value >= days else None


def fits(name: str) -> bool:
    """Whether a name reads as words in a line: short, with no code, number or file type in it."""
    words = re.split(r"[\s_.-]+", name)
    return (
        0 < len(name) <= NAME_FITS
        and not re.search(r"[0-9a-fA-F]{6}|\d{4}|\.\w{2,4}$", name)
        and not any(len(re.findall(r"[a-z][A-Z]", word)) > 1 for word in words)
    )


def stem(name: str) -> str:
    """A file's name without its type: "harbor_lights_04"."""
    head, dot, _ = name.rpartition(".")
    return head if dot and head else name


# --- the period's own words ------------------------------------------------------------------


def period_slots(card: Card) -> dict[str, Slot]:
    """What every phrasing may say about the period: "Wednesday", "of the week", "last week"."""
    period, said_on = card.period, card.said_on
    weekday = statements.WEEKDAYS[period.first.weekday()]
    title = {PeriodKind.DAY: weekday, PeriodKind.WEEK: "week"}.get(
        period.kind, statements.named_period(said_on)
    )
    before = short_period(period.previous(), card.today, "before")
    return {
        "title": title,
        "noun": "year" if period.kind is PeriodKind.YEAR else title,
        "unit": period.kind.value,
        "when": short_period(period, card.today, "when"),
        "span": statements.named_period(said_on),
        "weekday": weekday,
        "than": f"your usual {weekday}" if card.is_day else before,
        "next": {
            PeriodKind.DAY: "tomorrow",
            PeriodKind.WEEK: "next week",
            PeriodKind.MONTH: "next month",
        }.get(period.kind, "next year"),
    }


#: What a period too far back to name in two words is called instead.
_THAT = {
    "when": {PeriodKind.DAY: "that day", PeriodKind.WEEK: "that week"},
    "before": {PeriodKind.DAY: "the day before", PeriodKind.WEEK: "the week before"},
}


def short_period(period: Period, today: date, role: str) -> str:
    """The period as a line names it, "yesterday", "last week", "in May", and one longer than two
    words as "that week" or "the week before", so a recap opened later stays short."""
    said_on = period.said_on(today)
    words = statements.when(said_on) if role == "when" else statements.named_period(said_on)
    return words if len(words.split()) <= 2 else _THAT[role].get(period.kind, words)


def usual(card: Card, metric: str, key: str = "") -> int | None:
    """The reader's own usual for a figure: a day's same weekday over the weeks before it (the
    middle of them), a longer period's period before. None where the recipe keeps neither."""
    if not card.is_day:
        return card.get(metric, key, before=True) if card.has(metric, key, before=True) else None
    days = [
        card.figures.get(name, 0)
        for name in card.recipe.sources
        if (parts := _parts(name))[0] == USUAL
        and parts[1] == metric
        and parts[2].rpartition("@")[0] == key
        and "@" in parts[2]
    ]
    return int(median(days)) if len(days) >= USUAL_FEWEST else None


def named_of(card: Card, kind: str) -> Named | None:
    one = next((one for one in card.recipe.named if one.kind == kind), None)
    return None if one is None else Named(kind, one.id, one.name)


def name_slots(prefix: str, one: Named) -> dict[str, Slot]:
    """A named thing as a slot, and as a headline slot only where its name fits there."""
    piece = statements.named(one)
    return {prefix: piece, f"{prefix}_head": piece} if fits(one.name) else {prefix: piece}


def compared_stories(now: int, then: int | None, stem: str = "") -> set[str]:
    """The stories a figure tells against the reader's usual: big, up, usual, easy, quiet."""
    if then is None or then <= 0:
        return set()
    if now >= 2 * then:
        story = "big"
    elif 5 * now >= 6 * then:
        story = "up"
    elif 5 * now >= 4 * then:
        story = "usual"
    elif 2 * now >= then:
        story = "easy"
    else:
        story = "quiet"
    return {stem + story}


# --- saying it -----------------------------------------------------------------------------

_SLOT = re.compile(r"\{(\w+)\}")


def slots_of(template: str) -> set[str]:
    return set(_SLOT.findall(template))


def render(template: str, slots: Mapping[str, Slot]) -> Line:
    """A template with its slots filled, each sentence opening on a capital."""
    parts: list[Slot] = []
    for at, run in enumerate(_SLOT.split(template)):
        value = slots[run] if at % 2 else run
        opens = not parts or (isinstance(parts[-1], str) and parts[-1].rstrip().endswith("."))
        if isinstance(value, str) and value and opens:
            value = value[:1].upper() + value[1:]
        parts.append(value)
    return capitalized(said(*parts))


def choose(kind: str, facts: Facts, period: Period) -> Phrasing | None:
    """The phrasing a card says: the reader against their own usual first, then any story its
    figures tell, then one that fits any figures; among several, the one the period picks."""
    fitting = [
        one
        for one in LINES[kind]
        if (not one.story or one.story in facts.stories)
        and slots_of(one.headline + one.context) <= set(facts.slots)
    ]
    told = [one for one in fitting if one.story] or fitting
    first = FIRST.get(kind, frozenset())
    told = [one for one in told if one.story in first] or told
    if not told:
        return None
    return told[zlib.crc32(f"{period.key}|{kind}".encode()) % len(told)]


def spoken_by(kind: str, facts: Facts | None, period: Period) -> Voice | None:
    if facts is None:
        return None
    chosen = choose(kind, facts, period)
    if chosen is None:
        return None
    return Voice(
        render(chosen.headline, facts.slots), render(chosen.context, facts.slots), chosen.id
    )


def every_phrasing() -> Sequence[tuple[str, Phrasing]]:
    """Every phrasing of every kind, for the checks that read them all."""
    return [(kind, one) for kind, lines in LINES.items() for one in lines]
