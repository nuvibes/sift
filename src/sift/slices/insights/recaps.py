# SPDX-License-Identifier: AGPL-3.0-or-later
"""Recaps: a week, a month or a year, frozen as a handful of cards the day after it closes.

## What is frozen, and what is decided when it is drawn

A recap is a snapshot of how a period looked when it closed. A cleanup in February does not
rewrite December: the figures a card reads are kept WHOLE in the card's recipe, counted over
everything the User could see, hidden things included, and they never change again. So is the
choice of who the top person was.

What is hidden is NOT frozen. Locking belongs to one browser session and the helper that makes a
recap has none, so there is no such thing as a recap "made while locked": a recap is made whole and
drawn for whoever opens it, in their vault state right now. Hiding a person in November hides them
from September's recap while the vault is locked. The rules, all in `_draw`:

  * A card that NAMES a thing hidden for the reader now is absent in Show nothing mode (no
    substitute, no hint) and a locked tile in placeholder mode. There is no next person down:
    choosing one would be a second recap made at draw time, and the recap is the frozen one.
  * A card whose figures carry a hidden part is said with that part taken out: 41 hours unlocked,
    35 locked. The hidden part is worked out when the recap is drawn, from the period's rows
    (`store.rows` re-splits a row whose split is older than the User's stamp), so hiding something
    after the recap was made moves it too.
  * A card left with nothing to say once the hidden part is out is absent. The O card is the one
    that rule was written for: locked, it counts presses on files that are not hidden, and when that
    is zero it is left out, as it is whenever the count is simply zero, so its absence reveals
    nothing. The whole recap follows the same rule: locked, and below the floor on what is not
    hidden, it is not there at all in Show nothing mode.

The WORDS are not frozen either. A card is said when it is drawn, from its recipe, by the same
builders the Insights page uses (`statements.py`): "last week" is true only for the seven days after
a week closed, and a recap is opened again long after.

## When a recap is made

The quiet helper calls `make_due` after it has added up a day. A period's recap is due once the
period has closed and its last day is added up, and only for the period immediately before the one
today falls in: last week on a Monday, last month on the 1st, last year on 1 January (`due`). It
is made only where the period passes the floor. A device that was off on Monday makes last week's
on Tuesday. A device off for the whole of the next week does NOT make the week before last: "Your
week" is about the week just gone, and a flood of old weeks on the first run after an install is not
a recap of anything anybody remembers.

A period already made is never made again: `make_due` skips a period that has a row, and the table
holds one row per User and period besides.

## Never a notification

A recap is announced by one card at the top of Insights and one quiet line on Browse's header, for a
week, inside Sift and nowhere else (`announced`). Opening it or pressing the cross ends that. It is
never shareable and never exportable: its audience is one person, and a share button is a way to
leak the library.
"""

from __future__ import annotations

import calendar
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from enum import StrEnum
from typing import cast

from pydantic import TypeAdapter, ValidationError

from sift.kernel.access import Viewer
from sift.kernel.access.sentences import Line, capitalized, said, text_of
from sift.kernel.access.viewer import Concealment
from sift.kernel.db import Database, in_clause
from sift.kernel.log import get_logger
from sift.kernel.wire import pieces_of
from sift.slices.insights import statements, store
from sift.slices.insights.models import Bar, BarPart, Chart, Figure, NamedRow, Unit, cover_of
from sift.slices.insights.path import achievement_head
from sift.slices.insights.recaps_models import (
    CardKind,
    KeptCard,
    NamedThing,
    Recap,
    RecapCard,
    RecapHead,
    Recipe,
)
from sift.slices.insights.statements import Named
from sift.slices.insights.store import RecapRow

log = get_logger(__name__)

#: THE RECAP FLOOR: sittings in the period, every kind, a Theater session counting as one (the
#: rollup counts it that way). The same number as the page's floor, and one constant.
FLOOR_SITTINGS = statements.SITTINGS_FLOOR

#: How long a recap is announced for, in days from when it was made.
ANNOUNCED_FOR_DAYS = 7


# --- periods --------------------------------------------------------------------------------------


class PeriodKind(StrEnum):
    WEEK = "week"
    MONTH = "month"
    YEAR = "year"


@dataclass(frozen=True, slots=True)
class Period:
    """A stretch of this device's local calendar: a week (Monday to Sunday), a month, a year."""

    kind: PeriodKind
    first: date
    last: date
    #: The clock a time of day on its cards is said on: the reader's, set when a recap is opened
    #: for somebody (`opened`); the default where nobody is reading, as when a recap is made.
    hours: statements.Clock = "12"

    @property
    def key(self) -> str:
        """The `recaps.period` value: 'week:2026-W39', 'month:2026-09', 'year:2026'."""
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
        """The days it covers: "September 21 to 27, 2026", "September 2026", "2026"."""
        month = calendar.month_name
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


def due(today: date, added_up_to: date | None) -> list[Period]:
    """THE DUE RULE: the periods whose recap may be made today.

    For each kind, the period immediately before the one today falls in, once its last day has been
    added up. Nothing is due before anything has been added up. See the module's note on why the
    week before last is not made late.
    """
    if added_up_to is None:
        return []
    closed = [period_of(kind, today).previous() for kind in PeriodKind]
    return [period for period in closed if period.last <= added_up_to]


def title_of(period: Period, today: date) -> str:
    """ "Your week", "Your September", "Your 2026". "Your September 2025" in another year."""
    if period.kind is PeriodKind.WEEK:
        return "Your week"
    return f"Your {statements.named_period(period.said_on(today))}"


# --- the figures a card reads ---------------------------------------------------------------------


def source(metric: str, key: str = "", *, before: bool = False) -> str:
    """The name a figure is kept under in a recipe: `metric|key`, `prev:` for the period before."""
    return f"{'prev:' if before else ''}{metric}|{key}"


def _parts(name: str) -> tuple[bool, str, str]:
    """(whether it is the period before's, the metric, the key) of a source name."""
    before = name.startswith("prev:")
    metric, _, key = name.removeprefix("prev:").partition("|")
    return before, metric, key


#: A card's figures by source name: whole, or what is not hidden while the vault is locked.
Figures = Mapping[str, int]


@dataclass(frozen=True, slots=True)
class Said:
    """What a card says: its sentence, and which of its figures is the one it shows."""

    statement: Line
    figure: str | None = None
    label: str = ""
    unit: Unit = "count"
    cover: str | None = None
    rows: tuple[NamedRow, ...] = ()
    chart: Chart | None = None


#: One card's words from its figures. None when there is nothing true to say with them.
Builder = Callable[[Figures, Recipe, Period, date], Said | None]

_KINDS = (*statements.KINDS, statements.THEATER)
_HOURS = tuple(f"{hour:02d}" for hour in range(24))
_WEEKDAYS = tuple(str(day) for day in range(7))


def _named(recipe: Recipe, kind: str) -> NamedThing | None:
    return next((one for one in recipe.named if one.kind == kind), None)


def _headline(figures: Figures, recipe: Recipe, period: Period, today: date) -> Said | None:
    total = figures.get(source("viewed_ms"), 0)
    if total <= 0:
        return None
    parts = {kind: figures.get(source("viewed_ms:kind", kind), 0) for kind in _KINDS}
    return Said(
        statements.viewed(period.said_on(today), total, parts),
        source("viewed_ms"),
        "Viewed",
        "ms",
    )


def _top_person(figures: Figures, recipe: Recipe, period: Period, today: date) -> Said | None:
    # No test of the time here: a card that names somebody is kept or dropped by whether they are
    # hidden (`_draw`), one rule in one place, and it was chosen for having the most time.
    person = _named(recipe, "person")
    if person is None:
        return None
    ms = figures.get(source("viewed_ms:person", person.id), 0)
    line = statements.most_viewed_person(
        period.said_on(today), Named("person", person.id, person.name), ms, {}
    )
    return Said(
        line,
        source("viewed_ms:person", person.id),
        "Viewed",
        "ms",
        cover=cover_of("person", person.id),
    )


def _top_five(figures: Figures, recipe: Recipe, period: Period, today: date) -> Said | None:
    """The people viewed most, as a ranked list. Two or more, or the top person says it all."""
    people = [one for one in recipe.named if one.kind == "person"]
    if len(people) < 2:
        return None
    rows = tuple(
        NamedRow(
            piece=pieces_of(said(statements.named(Named("person", one.id, one.name))))[0],
            value=figures.get(source("viewed_ms:person", one.id), 0),
            unit="ms",
            cover=cover_of("person", one.id),
        )
        for one in people
    )
    return Said(statements.most_viewed_people(period.said_on(today)), rows=rows)


def _crowned(kind: str) -> Builder:
    """The card naming the one Site, or the one tag, with the most time."""

    def build(figures: Figures, recipe: Recipe, period: Period, today: date) -> Said | None:
        one = _named(recipe, kind)
        if one is None:
            return None
        ms = figures.get(source(f"viewed_ms:{kind}", one.id), 0)
        line = statements.most_viewed(period.said_on(today), Named(kind, one.id, one.name), ms)
        return Said(
            line, source(f"viewed_ms:{kind}", one.id), "Viewed", "ms", cover=cover_of(kind, one.id)
        )

    return build


_top_site = _crowned("site")
_top_tag = _crowned("tag")
#: The song on the files viewed longest: the time of every file carrying it, as the Insights page's
#: Songs list counts it. A song the reader hid conceals its files, so its card is dropped by
#: `_draw`'s one rule, as a hidden person's is.
_top_song = _crowned("song")


def _theater(figures: Figures, recipe: Recipe, period: Period, today: date) -> Said | None:
    ms = figures.get(source("viewed_ms:kind", statements.THEATER), 0)
    if ms <= 0:
        return None
    sessions = figures.get(source("sittings:kind", statements.THEATER), 0)
    line = statements.theater(period.said_on(today), ms, None, 0, sessions)
    return Said(line, source("viewed_ms:kind", statements.THEATER), "In Theater", "ms")


def _most(figures: Figures, metric: str, keys: Sequence[str]) -> tuple[str, int] | None:
    """The key with the most, the earliest on a tie so the answer does not move between reads."""
    best: tuple[str, int] | None = None
    for key in keys:
        value = figures.get(source(metric, key), 0)
        if value > 0 and (best is None or value > best[1]):
            best = (key, value)
    return best


def _when(figures: Figures, recipe: Recipe, period: Period, today: date) -> Said | None:
    day = _most(figures, "viewed_ms:weekday", _WEEKDAYS)
    hour = _most(figures, "viewed_ms:hour", _HOURS)
    if day is None or hour is None:
        return None
    weekday = statements.busiest_weekday(
        period.said_on(today), int(day[0]), day[1], figures.get(source("viewed_ms"), 0)
    )
    if weekday is None:
        return None
    busiest = statements.busiest_hour(int(hour[0]), hour[1], period.hours)
    hours = Chart(
        unit="ms",
        bars=[
            Bar(
                label=key,
                parts=[BarPart(kind="all", value=figures.get(source("viewed_ms:hour", key), 0))],
            )
            for key in _HOURS
        ],
    )
    return Said(
        said(weekday, " ", busiest) if busiest else weekday,
        source("viewed_ms:weekday", day[0]),
        "Viewed",
        "ms",
        chart=hours,
    )


def _rated(figures: Figures, recipe: Recipe, period: Period, today: date) -> Said | None:
    rated = figures.get(source("rated"), 0)
    starred = figures.get(source("starred"), 0)
    line = statements.opinions(rated, starred)
    if line is None:
        return None
    if rated > 0:
        return Said(line, source("rated"), "Rated", "count")
    return Said(line, source("starred"), "Starred", "count")


def _o(figures: Figures, recipe: Recipe, period: Period, today: date) -> Said | None:
    presses = figures.get(source("o"), 0)
    line = statements.o_count(period.said_on(today), presses)
    return None if line is None else Said(line, source("o"), "O count", "count")


def _sift_did(figures: Figures, recipe: Recipe, period: Period, today: date) -> Said | None:
    added = figures.get(source("files_added"), 0)
    lines = [
        statements.arrived(period.said_on(today), added) if added > 0 else None,
        statements.named_and_filed(
            figures.get(source("faces_named"), 0), figures.get(source("files_filed"), 0)
        ),
        statements.organized(period.said_on(today), figures.get(source("decided"), 0)),
    ]
    shown = statements.statements_of(lines)
    if not shown:
        return None
    joined: list[Line | str] = []
    for at, line in enumerate(shown):
        joined.extend([" " if at else "", line])
    figure = (
        (source("files_added"), "Files arrived")
        if added > 0
        else (source("faces_named"), "Faces named")
        if figures.get(source("faces_named"), 0) > 0
        else (source("files_filed"), "Files filed")
        if figures.get(source("files_filed"), 0) > 0
        else (source("decided"), "Answered")
    )
    return Said(said(*joined), figure[0], figure[1], "count")


def _compared(figures: Figures, recipe: Recipe, period: Period, today: date) -> Said | None:
    if not recipe.compares:
        return None
    now = figures.get(source("viewed_ms"), 0)
    before = figures.get(source("viewed_ms", before=True), 0)
    if now <= 0 or before <= 0:
        return None
    line = statements.compared(period.said_on(today), now, period.previous().said_on(today), before)
    if line is None:
        return None
    return Said(line, source("viewed_ms", before=True), "Viewed before", "ms")


def _closing(figures: Figures, recipe: Recipe, period: Period, today: date) -> Said | None:
    name = statements.named_period(period.said_on(today))
    return Said(capitalized(said(f"That was {name}.")))


#: Each card of a period's recap, in the order it is read.
BUILDERS: dict[str, Builder] = {
    "headline": _headline,
    "compared": _compared,
    "top_person": _top_person,
    "top_five": _top_five,
    "top_site": _top_site,
    "top_tag": _top_tag,
    "top_song": _top_song,
    "when": _when,
    "theater": _theater,
    "sift_did": _sift_did,
    "rated": _rated,
    "o": _o,
    "closing": _closing,
}

#: Every metric a period's cards read.
_READS = frozenset(
    {
        "viewed_ms",
        "viewed_ms:kind",
        "sittings",
        "viewed_ms:person",
        "viewed_ms:site",
        "viewed_ms:tag",
        "viewed_ms:song",
        "sittings:kind",
        "viewed_ms:weekday",
        "viewed_ms:hour",
        "rated",
        "rated:file",
        "starred",
        "starred:file",
        "o",
        "files_added",
        "faces_named",
        "files_filed",
        "decided",
    }
)


# --- the figures, as the rows answer them ---------------------------------------------------------


#: The counts of FILES whose period figure is the number of files their per-file metric names,
#: each file once (the page's `Book.files`): the daily count summed over a week read one file rated
#: on three days as three files rated.
FILES_COUNTED: Mapping[str, str] = {"rated": "rated:file", "starred": "starred:file"}


@dataclass(frozen=True, slots=True)
class _Totals:
    """A span of rows added up by (metric, key), whole and hidden, readable for any sub-span."""

    rows: tuple[store.DayRow, ...]

    def of(self, first: date, last: date) -> dict[tuple[str, str], tuple[int, int]]:
        low, high = first.isoformat(), last.isoformat()
        out: dict[tuple[str, str], tuple[int, int]] = {}
        for row in self.rows:
            if low <= row.day <= high:
                whole, hidden = out.get((row.metric, row.key), (0, 0))
                out[(row.metric, row.key)] = (whole + row.whole, hidden + row.hidden)
        for counted, per_file in FILES_COUNTED.items():
            files = [pair for (metric, _), pair in out.items() if metric == per_file and pair[0]]
            if files:
                out[(counted, "")] = (len(files), sum(1 for whole, hid in files if hid >= whole))
        return out


async def _totals(
    database: Database, user_id: str, first: date, last: date, metrics: Iterable[str]
) -> _Totals:
    figures = await store.rows(database, user_id, first, last, metrics)
    return _Totals(figures.rows)


def _whole(totals: Mapping[tuple[str, str], tuple[int, int]], metric: str, key: str = "") -> int:
    return totals.get((metric, key), (0, 0))[0]


# --- making ---------------------------------------------------------------------------------------

#: The day the User's viewing was first recorded: whether a period before a recap's was recorded
#: WHOLE is whether it began on or after this. The primary key is (user, day, ...), so this is a
#: seek to the User's first day and a walk to its first `sittings` row.
_FIRST_VIEWED = (
    "SELECT MIN(day) AS first FROM insight_days WHERE user_id = ? AND metric = 'sittings'"
)
_PEOPLE_NAMES = "SELECT id, name FROM people WHERE id IN (?*)"
_SITE_NAMES = "SELECT id, name FROM sites WHERE id IN (?*)"
_TAG_NAMES = "SELECT id, name FROM tags WHERE id IN (?*)"
_SONG_NAMES = "SELECT id, name FROM songs WHERE id IN (?*)"

#: How many people the top-five card names.
TOP_FIVE = 5


async def _names(database: Database, statement: str, ids: Sequence[str]) -> dict[str, str]:
    if not ids:
        return {}
    sql, params = in_clause(statement, list(ids))
    return {str(row["id"]): str(row["name"]) for row in await database.fetch_all(sql, params)}


async def _top(
    database: Database,
    totals: Mapping[tuple[str, str], tuple[int, int]],
    metric: str,
    kind: str,
    names_of: str,
    most: int = 1,
) -> list[NamedThing]:
    """The `most` things with the most WHOLE time in the period, among those that still exist.
    Chosen once, here, over everything: whether the reader may be told about them is decided when
    the recap is drawn."""
    ranked = sorted(
        ((whole, key) for (asked, key), (whole, _) in totals.items() if asked == metric and whole),
        key=lambda pair: (-pair[0], pair[1]),
    )
    names = await _names(database, names_of, [key for _, key in ranked[:20]])
    found = [
        NamedThing(kind=kind, id=key, name=names[key]) for _, key in ranked[:20] if key in names
    ]
    return found[:most]


def _sources(
    totals: Mapping[tuple[str, str], tuple[int, int]],
    wanted: Iterable[tuple[str, str]],
    *,
    before: bool = False,
) -> dict[str, int]:
    return {
        source(metric, key, before=before): _whole(totals, metric, key) for metric, key in wanted
    }


async def build(
    database: Database, user_id: str, period: Period, *, today: date
) -> list[KeptCard] | None:
    """The cards of one User's recap of one period, or None where the period is below the floor.

    Every figure is WHOLE: this is what happened, counted over everything the User could see. The
    card is said once here, for the record, and said again from its recipe whenever it is drawn.
    """
    before = period.previous()
    rows = await _totals(database, user_id, before.first, period.last, _READS)
    totals = rows.of(period.first, period.last)
    if _whole(totals, "sittings") < FLOOR_SITTINGS:
        return None
    earlier = rows.of(before.first, before.last)
    first_row = await database.fetch_one(_FIRST_VIEWED, (user_id,))
    first = None if first_row is None or first_row["first"] is None else str(first_row["first"])
    compares = (
        first is not None
        and first <= before.first.isoformat()
        and _whole(earlier, "sittings") >= FLOOR_SITTINGS
    )
    people = await _top(database, totals, "viewed_ms:person", "person", _PEOPLE_NAMES, TOP_FIVE)
    person = people[0] if people else None
    site = next(iter(await _top(database, totals, "viewed_ms:site", "site", _SITE_NAMES)), None)
    tag = next(iter(await _top(database, totals, "viewed_ms:tag", "tag", _TAG_NAMES)), None)
    song = next(iter(await _top(database, totals, "viewed_ms:song", "song", _SONG_NAMES)), None)

    recipes: dict[str, Recipe] = {
        "headline": Recipe(
            sources=_sources(
                totals,
                [("viewed_ms", ""), ("sittings", "")]
                + [("viewed_ms:kind", kind) for kind in _KINDS],
            )
        ),
        "when": Recipe(
            sources=_sources(
                totals,
                [("viewed_ms", "")]
                + [("viewed_ms:weekday", day) for day in _WEEKDAYS]
                + [("viewed_ms:hour", hour) for hour in _HOURS],
            )
        ),
        "theater": Recipe(
            sources=_sources(
                totals,
                [("viewed_ms:kind", statements.THEATER), ("sittings:kind", statements.THEATER)],
            )
        ),
        "rated": Recipe(sources=_sources(totals, [("rated", ""), ("starred", "")])),
        "o": Recipe(sources=_sources(totals, [("o", "")])),
        "sift_did": Recipe(
            sources=_sources(
                totals,
                [("files_added", ""), ("faces_named", ""), ("files_filed", ""), ("decided", "")],
            )
        ),
        "compared": Recipe(
            sources={
                **_sources(totals, [("viewed_ms", "")]),
                **_sources(earlier, [("viewed_ms", "")], before=True),
            },
            compares=compares,
        ),
        # The closing card says nothing with a figure, and it is the one card every recap has: the
        # period's sittings ride in its recipe, which is what a locked reader's floor is read from.
        "closing": Recipe(sources=_sources(totals, [("sittings", "")])),
    }
    if person is not None:
        recipes["top_person"] = Recipe(
            sources=_sources(totals, [("viewed_ms:person", person.id)]),
            named=[person],
        )
    if len(people) > 1:
        recipes["top_five"] = Recipe(
            sources=_sources(totals, [("viewed_ms:person", one.id) for one in people]),
            named=people,
        )
    if site is not None:
        recipes["top_site"] = Recipe(
            sources=_sources(totals, [("viewed_ms:site", site.id)]), named=[site]
        )
    if tag is not None:
        recipes["top_tag"] = Recipe(
            sources=_sources(totals, [("viewed_ms:tag", tag.id)]), named=[tag]
        )
    if song is not None:
        recipes["top_song"] = Recipe(
            sources=_sources(totals, [("viewed_ms:song", song.id)]), named=[song]
        )

    cards: list[KeptCard] = []
    for kind, builder in BUILDERS.items():
        recipe = recipes.get(kind)
        if recipe is None:
            continue
        card = _said(kind, builder(recipe.sources, recipe, period, today), recipe.sources)
        if card is None:
            continue
        cards.append(
            KeptCard(
                **card.model_dump(exclude={"hidden_things"}),
                hidden_things=[one.id for one in recipe.named],
                recipe=recipe,
            )
        )
    return cards


def _said(kind: str, words: Said | None, figures: Figures) -> RecapCard | None:
    if words is None:
        return None
    figure = (
        None
        if words.figure is None
        else Figure(
            label=words.label, value=figures.get(words.figure, 0), unit=words.unit, hidden_part=0
        )
    )
    return RecapCard(
        id=kind,
        kind=cast(CardKind, kind),
        statement=pieces_of(words.statement),
        figure=figure,
        cover=words.cover,
        rows=list(words.rows),
        chart=words.chart,
    )


def body_of(cards: Sequence[KeptCard]) -> str:
    """The cards as `recaps.body` keeps them: a JSON list of cards, each with its recipe."""
    return "[" + ",".join(card.model_dump_json() for card in cards) + "]"


async def make_due(database: Database, user_id: str, today: date) -> list[RecapRow]:
    """Make every recap due for this User today, and answer the ones made.

    THE ONE CALL the quiet helper makes, after it has added a day up for this User. Idempotent: a
    period that has a recap is skipped before anything is read, and the write keeps the first row
    for a period whatever happens. A period below the floor makes nothing, and is asked again the
    next day while it is still the one just closed.
    """
    periods = due(today, await store.added_up_to(database, user_id))
    if not periods:
        return []
    made_before = {row.period for row in await store.recaps_of(database, user_id)}
    made: list[RecapRow] = []
    for period in periods:
        if period.key in made_before:
            continue
        cards = await build(database, user_id, period, today=today)
        if cards is None:
            continue
        made.append(await store.write_recap(database, user_id, period.key, body_of(cards)))
        log.info("insights.recap_made", period=period.key, cards=len(cards))
    return made


# --- drawing --------------------------------------------------------------------------------------


_CARDS: TypeAdapter[list[KeptCard]] = TypeAdapter(list[KeptCard])


def kept_cards(body: str) -> list[KeptCard]:
    """The cards a recap keeps. A body that does not read as cards draws as none, and says so."""
    try:
        return _CARDS.validate_json(body)
    except ValidationError:
        log.warning("insights.recap_unreadable")
        return []


@dataclass(frozen=True, slots=True)
class _Drawn:
    cards: list[RecapCard]
    #: Placeholder mode, locked, and something here is hidden.
    something_hidden: bool


def _stub(kind: str) -> RecapCard:
    """A locked tile: the card is there, and nothing it says is."""
    return RecapCard(id=kind, kind=cast(CardKind, kind), statement=[], hidden=True)


def _draw(
    cards: Sequence[KeptCard],
    period: Period | None,
    viewer: Viewer,
    hidden: Mapping[str, int],
    today: date,
) -> _Drawn | None:
    """THE VAULT RULE FOR A RECAP: the cards this reader may be shown now, or None for a recap that
    is not there at all for them. `hidden` is the hidden part of every source, worked out now.

    A card with no recipe (an achievement) is drawn as it was built: it names nothing the vault
    decides, and it carries no figure with a hidden part.
    """
    locked = not viewer.show_hidden
    placeholder = viewer.concealment is Concealment.PLACEHOLDER

    def shown(name: str, whole: int) -> int:
        return whole - min(hidden.get(name, 0), whole) if locked else whole

    below_floor = False
    if locked and period is not None:
        sittings = next(
            (
                card.recipe.sources[source("sittings")]
                for card in cards
                if card.kind == "closing"
                and card.recipe is not None
                and source("sittings") in card.recipe.sources
            ),
            None,
        )
        if sittings is not None and shown(source("sittings"), sittings) < FLOOR_SITTINGS:
            if not placeholder:
                return None
            below_floor = True

    out: list[RecapCard] = []
    something_hidden = below_floor
    for card in cards:
        recipe = card.recipe
        if recipe is None or period is None:
            out.append(RecapCard(**card.model_dump(exclude={"recipe", "hidden_things"})))
            continue
        # A named thing is hidden for this reader now when all of its time in the period is: the
        # store's split says so for a hidden person or Site, and for one whose every file is.
        gone_now = [
            one.id
            for one in recipe.named
            if (whole := recipe.sources.get(source(f"viewed_ms:{one.kind}", one.id), 0)) > 0
            and hidden.get(source(f"viewed_ms:{one.kind}", one.id), 0) >= whole
        ]
        if locked and (gone_now or (below_floor and card.kind != "closing")):
            something_hidden = True
            if placeholder and card.kind != "o":
                out.append(_stub(card.kind))
            continue
        figures = {name: shown(name, whole) for name, whole in recipe.sources.items()}
        if locked and any(hidden.get(name, 0) for name in recipe.sources):
            something_hidden = True
        words = BUILDERS[card.kind](figures, recipe, period, today)
        drawn = _said(card.kind, words, figures)
        if drawn is None or words is None:
            # Nothing true left to say once the hidden part is out. The O card is absent in both
            # modes; any other card is a locked tile in placeholder mode, so that mode's gap is
            # where the card was.
            if locked and placeholder and card.kind != "o":
                out.append(_stub(card.kind))
            continue
        if drawn.figure is not None and words.figure is not None:
            drawn.figure.hidden_part = 0 if locked else hidden.get(words.figure, 0)
        drawn.hidden_things = [] if locked else gone_now
        out.append(drawn)
    return _Drawn(out, something_hidden and locked and placeholder)


def _sources_of(cards: Sequence[KeptCard]) -> set[str]:
    return {name for card in cards if card.recipe is not None for name in card.recipe.sources}


async def _hidden_parts(
    database: Database, user_id: str, recaps: Sequence[tuple[Period, Sequence[KeptCard]]]
) -> list[dict[str, int]]:
    """The hidden part of every source of every recap, NOW, from one read of the rows they span.

    One read for the lot rather than one per recap: a year and the months and weeks inside it
    share their days, so a read per recap would read most days several times. The period before a
    recap's is read only where one of its cards compares with it.
    """
    if not recaps:
        return []
    metrics = {_parts(name)[1] for _, cards in recaps for name in _sources_of(cards)} & _READS
    metrics |= {FILES_COUNTED[metric] for metric in metrics if metric in FILES_COUNTED}
    if not metrics:
        return [{} for _ in recaps]

    def reaches_back(cards: Sequence[KeptCard]) -> bool:
        return any(_parts(name)[0] for name in _sources_of(cards))

    first = min(
        (period.previous().first if reaches_back(cards) else period.first)
        for period, cards in recaps
    )
    last = max(period.last for period, _ in recaps)
    rows = await _totals(database, user_id, first, last, metrics)
    out: list[dict[str, int]] = []
    for period, cards in recaps:
        now = rows.of(period.first, period.last)
        before = period.previous()
        earlier = rows.of(before.first, before.last) if reaches_back(cards) else {}
        parts: dict[str, int] = {}
        for name in _sources_of(cards):
            is_before, metric, key = _parts(name)
            parts[name] = (earlier if is_before else now).get((metric, key), (0, 0))[1]
        out.append(parts)
    return out


@dataclass(frozen=True, slots=True)
class _Opened:
    row: RecapRow
    period: Period | None
    drawn: _Drawn


def _named_as(opened: _Opened, today: date) -> tuple[str, str]:
    """(title, span) of a recap: a period's from its days, an achievement's from its learning
    path."""
    if opened.period is not None:
        return title_of(opened.period, today), opened.period.span
    head = achievement_head(opened.row)
    return (head.title, head.span) if head is not None else ("", "")


def _head(opened: _Opened, today: date) -> RecapHead:
    title, span = _named_as(opened, today)
    return RecapHead(
        id=opened.row.id,
        period=opened.row.period,
        title=title,
        span=span,
        made_at=opened.row.made_at,
        seen_at=opened.row.seen_at,
        cards=len(opened.drawn.cards),
    )


async def _open_all(
    database: Database,
    viewer: Viewer,
    rows: Sequence[RecapRow],
    today: date,
    *,
    only_counting: bool = False,
    hours: statements.Clock = "12",
) -> list[_Opened]:
    """These recaps as this reader may be shown them now, the ones not there for them left out.

    `only_counting` is the list's case: an open vault is shown every card as it was made, so where
    all that is wanted is how many there are, an open vault's reader costs no read of the rows.
    """
    kept = [
        (row, _on_clock(period_from_key(row.period), hours), kept_cards(row.body)) for row in rows
    ]
    periods = [(period, cards) for _, period, cards in kept if period is not None]
    found: list[dict[str, int]] = (
        [{} for _ in periods]
        if only_counting and viewer.show_hidden
        else await _hidden_parts(database, viewer.id, periods)
    )
    parts = iter(found)
    out: list[_Opened] = []
    for row, period, cards in kept:
        hidden = next(parts) if period is not None else {}
        drawn = _draw(cards, period, viewer, hidden, today)
        if drawn is not None:
            out.append(_Opened(row, period, drawn))
    return out


def _on_clock(period: Period | None, hours: statements.Clock) -> Period | None:
    """The period with the clock its cards say a time of day on."""
    return None if period is None else replace(period, hours=hours)


async def heads(
    database: Database, viewer: Viewer, *, today: date | None = None, now: float | None = None
) -> tuple[list[RecapHead], RecapHead | None]:
    """Every recap of a period this reader has, newest first, and the one being announced.

    Each is drawn for the reader's vault state now, so a recap a locked vault leaves out is neither
    listed nor announced, and each head's card count is the count they will be shown.
    """
    on = today or store.local_today(now)
    rows = [
        row
        for row in await store.recaps_of(database, viewer.id)
        if period_from_key(row.period) is not None
    ]
    drawn = await _open_all(database, viewer, rows, on, only_counting=True)
    listed = [_head(one, on) for one in drawn]
    return listed, announced(listed, now=time.time() if now is None else now)


def announced(listed: Sequence[RecapHead], *, now: float) -> RecapHead | None:
    """THE ANNOUNCEMENT: the newest recap made in the last week, neither opened nor dismissed.

    Opening a recap and pressing the cross both draw its `seen_at`, and either ends this. Only ever
    one: a second card announcing a second recap is a queue of chores, which a recap is not.
    """
    since = now - ANNOUNCED_FOR_DAYS * 24 * 3600
    fresh = [head for head in listed if head.seen_at is None and head.made_at >= since]
    return max(fresh, key=lambda head: (head.made_at, head.id), default=None)


async def opened(
    database: Database,
    viewer: Viewer,
    recap_id: str,
    *,
    today: date | None = None,
    hours: statements.Clock = "12",
) -> Recap | None:
    """One of this reader's recaps, drawn for them now on their clock, and marked seen the first
    time.

    None (a 404) for somebody else's, for one that is not there, and for one a locked vault
    leaves out: the three are the same answer, so the answer says nothing about which it was.
    """
    on = today or store.local_today()
    row = await store.recap(database, viewer.id, recap_id)
    if row is None:
        return None
    found = await _open_all(database, viewer, [row], on, hours=hours)
    if not found:
        return None
    one = found[0]
    await store.mark_seen(database, viewer.id, recap_id)
    title, span = _named_as(one, on)
    count = len(one.drawn.cards)
    cards = "1 card" if count == 1 else f"{count} cards"
    # An achievement is one card and is headed by what it is called; a period by its cards.
    heading = (
        text_of(capitalized(said(f"{statements.named_period(one.period.said_on(on))}, in {cards}")))
        if one.period is not None
        else title
    )
    line = (
        text_of(statements.some_hidden(one.period.said_on(on)))
        if one.drawn.something_hidden and one.period is not None
        else None
    )
    return Recap(
        id=row.id,
        period=row.period,
        title=title,
        span=span,
        heading=heading,
        made_at=row.made_at,
        cards=one.drawn.cards,
        hidden_line=line,
        first_day="" if one.period is None else one.period.first.isoformat(),
    )


async def dismissed(database: Database, viewer: Viewer, recap_id: str) -> bool:
    """The cross on the announcement: the recap stops being announced.

    False (a 404) for somebody else's recap and for one that is not there for this reader now.
    The table has one column for "this recap has been looked at", and dismissing is that: it is what
    ends the announcement, and a dismissed recap is still in the list to be opened.
    """
    row = await store.recap(database, viewer.id, recap_id)
    if row is None:
        return False
    if not await _open_all(database, viewer, [row], store.local_today()):
        return False
    await store.mark_seen(database, viewer.id, recap_id)
    return True
