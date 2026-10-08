# SPDX-License-Identifier: AGPL-3.0-or-later
"""A recap's cards: each one's words from its recipe's figures, said when the card is built and
again whenever it is drawn. See `recaps` for the design.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import cast

from sift.kernel.access.sentences import Line, capitalized, said
from sift.kernel.wire import pieces_of
from sift.slices.insights import statements, statements_cards, together
from sift.slices.insights.metrics import split_file_key
from sift.slices.insights.models import Bar, BarPart, Chart, Figure, NamedRow, Unit, cover_of
from sift.slices.insights.recaps_models import (
    CardKind,
    NamedThing,
    RecapCard,
    Recipe,
)
from sift.slices.insights.recaps_periods import (
    USUAL,
    Period,
    PeriodKind,
    _parts,
    source,
)
from sift.slices.insights.statements import Named

#: A card's figures by source name: whole, or what is not hidden while the vault is locked.
Figures = Mapping[str, int]


@dataclass(frozen=True, slots=True)
class Said:
    """What a card says: its sentence, and which of its figures is the one it shows."""

    statement: Line
    #: The source of the figure shown, or several added up (a wall's files, wall by wall).
    figure: str | tuple[str, ...] | None = None
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
    """The busiest weekday and hour; a day's busiest hour alone, a day having no other weekday."""
    day = None if period.kind is PeriodKind.DAY else _most(figures, "viewed_ms:weekday", _WEEKDAYS)
    hour = _most(figures, "viewed_ms:hour", _HOURS)
    if hour is None or (day is None and period.kind is not PeriodKind.DAY):
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
    if day is None:
        return Said(busiest or (), source("viewed_ms:hour", hour[0]), "Viewed", "ms", chart=hours)
    weekday = statements.busiest_weekday(
        period.said_on(today), int(day[0]), day[1], figures.get(source("viewed_ms"), 0)
    )
    if weekday is None:
        return None
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
    """Against the period before; a day against the usual day before it, never the day before."""
    if not recipe.compares:
        return None
    now = figures.get(source("viewed_ms"), 0)
    if period.kind is PeriodKind.DAY:
        days = figures.get(source("days", scope=USUAL), 0)
        usual = figures.get(source("viewed_ms", scope=USUAL), 0) // max(days, 1)
        if now <= 0 or usual <= 0:
            return None
        # The card's figure is the day's own: the usual is a sum over days, said as an average.
        return Said(statements.compared_with_usual(now, usual), source("viewed_ms"), "Viewed", "ms")
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


#: The per-file metrics a card may name a file by: each keyed by the file (`split_file_key`).
FILE_METRICS = frozenset(
    {"sittings:file", "first_file", "last_file", "new_favourites:file", "rediscovered:file"}
)


def _file_named(recipe: Recipe, metric: str) -> tuple[NamedThing, str] | None:
    """The file a card names and the source its figure is kept under."""
    for name in recipe.sources:
        _, asked, key = _parts(name)
        if asked == metric:
            asset = split_file_key(key)[1]
            one = next((one for one in recipe.named if one.id == asset), None)
            if one is not None:
                return one, name
    return None


def _file(one: NamedThing) -> Named:
    return Named("asset", one.id, one.name)


def _top_file(figures: Figures, recipe: Recipe, period: Period, today: date) -> Said | None:
    found = _file_named(recipe, "sittings:file")
    if found is None or figures.get(found[1], 0) <= 0:
        return None
    one, name = found
    line = statements_cards.most_viewed_file(period.said_on(today), _file(one), figures[name])
    return Said(line, name, "Views", "views", cover=cover_of("asset", one.id))


def _new_favourite(figures: Figures, recipe: Recipe, period: Period, today: date) -> Said | None:
    found = _file_named(recipe, "new_favourites:file")
    if found is None or figures.get(found[1], 0) <= 0:
        return None
    one, name = found
    line = statements_cards.new_favourite(period.said_on(today), _file(one), figures[name])
    return Said(line, name, "Views that day", "views", cover=cover_of("asset", one.id))


def _rediscovered(figures: Figures, recipe: Recipe, period: Period, today: date) -> Said | None:
    found = _file_named(recipe, "rediscovered:file")
    if found is None or figures.get(found[1], 0) <= 0:
        return None
    one, name = found
    line = statements_cards.rediscovered(period.said_on(today), _file(one), figures[name])
    return Said(line, name, "Days away", "count", cover=cover_of("asset", one.id))


def _moment(period: Period, seconds: int) -> statements.Moment:
    """A moment as the statements take it: its day and minute on this device's clock, a day's
    own moment past midnight kept on the day with a minute past 1439, as a latest finish is."""
    at = datetime.fromtimestamp(seconds)
    late = (at.date() - period.first).days if period.kind is PeriodKind.DAY else 0
    return statements.Moment(
        at.date() - timedelta(days=late), at.hour * 60 + at.minute + late * 24 * 60
    )


def _first_last(figures: Figures, recipe: Recipe, period: Period, today: date) -> Said | None:
    """The first file opened in the period and the last one closed: of the moments the recipe
    keeps, the earliest and the latest the reader may be told of (a hidden file's reads 0 locked)."""

    def chosen(metric: str, pick: Callable[..., tuple[int, str]]) -> tuple[int, str] | None:
        shown = [
            (figures[name], _parts(name)[2])
            for name in recipe.sources
            if _parts(name)[1] == metric and figures.get(name, 0) > 0
        ]
        return pick(shown) if shown else None

    first, last = chosen("first_file", min), chosen("last_file", max)
    if first is None or last is None:
        return None
    named = {one.id: one for one in recipe.named}
    began, ended = _moment(period, first[0]), _moment(period, last[0])
    line = statements_cards.first_and_last(
        period.said_on(today),
        _file(named[first[1]]),
        began,
        _file(named[last[1]]),
        ended,
        period.hours,
    )
    rows = tuple(
        NamedRow(
            piece=pieces_of(said(statements.named(_file(named[key]))))[0],
            value=moment.minute % (24 * 60),
            unit="minute_of_day",
            cover=cover_of("asset", key),
        )
        for key, moment in ((first[1], began), (last[1], ended))
    )
    return Said(line, rows=rows)


def _session(figures: Figures, recipe: Recipe, period: Period, today: date) -> Said | None:
    name = next((one for one in recipe.sources if _parts(one)[1] == "session_ms:session"), None)
    if name is None or figures.get(name, 0) <= 0:
        return None
    pages = source("session_pages:session", _parts(name)[2])
    line = statements_cards.longest_session(
        period.said_on(today), figures[name], figures.get(pages, 0)
    )
    return Said(line, name, "Longest visit", "ms")


def _downloads(figures: Figures, recipe: Recipe, period: Period, today: date) -> Said | None:
    line = statements_cards.downloaded(period.said_on(today), figures.get(source("downloads"), 0))
    return None if line is None else Said(line, source("download_bytes"), "Downloaded", "bytes")


def _theater_files(figures: Figures, recipe: Recipe, period: Period, today: date) -> Said | None:
    walls = tuple(name for name in recipe.sources if _parts(name)[1] == "theater_files")
    line = statements_cards.theater_showed(
        period.said_on(today), sum(figures.get(name, 0) for name in walls)
    )
    return None if line is None else Said(line, walls, "Files in Theater", "count")


#: Where an alongside card keeps its pair (`together:<first>|<second>`, the sample), and the
#: whole of each measure beside it (`whole:`): a measure that is not whole for the reader now is
#: a pattern read partly over hidden things, so the card says nothing.
_TOGETHER, _WHOLE = "together:", "whole:"


#: The closed periods an alongside card reads its recap over.
_TOGETHER_OVER: Mapping[PeriodKind, str] = {PeriodKind.MONTH: "day", PeriodKind.YEAR: "week"}


def _alongside(figures: Figures, recipe: Recipe, period: Period, today: date) -> Said | None:
    pair = next((name for name in recipe.sources if name.startswith(_TOGETHER)), None)
    if pair is None or period.kind not in _TOGETHER_OVER:
        return None
    if any(
        figures.get(name.removeprefix(_WHOLE), 0) != whole
        for name, whole in figures.items()
        if name.startswith(_WHOLE)
    ):
        return None
    first, _, second = pair.removeprefix(_TOGETHER).partition("|")
    person = _named(recipe, "person")
    line = together.together(
        _TOGETHER_OVER[period.kind],
        together.Pair(first, second, figures[pair], 0.0),
        None if person is None else Named("person", person.id, person.name),
    )
    return Said(line)


#: Each card of a period's recap, by kind; the order a deck reads them in is `DECKS`'.
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
    "top_file": _top_file,
    "first_last": _first_last,
    "new_favourite": _new_favourite,
    "rediscovered": _rediscovered,
    "session": _session,
    "downloads": _downloads,
    "theater_files": _theater_files,
    "alongside": _alongside,
}


def _figure_names(words: Said) -> tuple[str, ...]:
    """The sources a card's figure adds up: none, one, or several."""
    if words.figure is None:
        return ()
    return (words.figure,) if isinstance(words.figure, str) else words.figure


def _said(kind: str, words: Said | None, figures: Figures) -> RecapCard | None:
    if words is None:
        return None
    names = _figure_names(words)
    figure = (
        None
        if words.figure is None
        else Figure(
            label=words.label,
            value=sum(figures.get(name, 0) for name in names),
            unit=words.unit,
            hidden_part=0,
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
