# SPDX-License-Identifier: AGPL-3.0-or-later
"""A recap's recipes: the figures each card is built from, read once when the recap is made, and
the cards each kind of recap holds in the order they are read. See `recaps` for the design.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from sift.kernel.access import arrivals
from sift.kernel.db import Database, in_clause
from sift.slices.insights import statements, store, together
from sift.slices.insights.metrics import split_file_key
from sift.slices.insights.recaps_cards import (
    _HOURS,
    _KINDS,
    _TOGETHER,
    _TOGETHER_OVER,
    _WEEKDAYS,
    _WHOLE,
    BUILDERS,
    _said,
)
from sift.slices.insights.recaps_models import (
    KeptCard,
    NamedThing,
    Recipe,
)
from sift.slices.insights.recaps_periods import (
    FLOOR_SITTINGS,
    USUAL,
    Period,
    PeriodKind,
    _usual_window,
    source,
)

#: The most cards a deck holds, the closing card among them: a year read in a few minutes.
DECK_MOST = 18


_WEEK_DECK = (
    "headline",
    "compared",
    "top_person",
    "top_five",
    "top_site",
    "top_tag",
    "top_song",
    "top_file",
    "new_favourite",
    "rediscovered",
    "first_last",
    "when",
    "session",
    "theater",
    "theater_files",
    "downloads",
    "sift_did",
    "rated",
    "o",
)


#: EACH KIND'S DECK, in the order it is read; a card with nothing to say is left out. A day is the
#: day's few; a year is paced figure, content, surprise, figure, and closes on its summary.
DECKS: Mapping[PeriodKind, tuple[str, ...]] = {
    PeriodKind.DAY: (
        "headline",
        "compared",
        "top_person",
        "top_five",
        "top_site",
        "top_tag",
        "top_song",
        "first_last",
        "when",
        "theater",
        "sift_did",
        "rated",
        "o",
        "closing",
    ),
    PeriodKind.WEEK: (*_WEEK_DECK, "closing"),
    PeriodKind.MONTH: (*_WEEK_DECK, "alongside", "closing"),
    PeriodKind.YEAR: (
        "headline",
        "top_person",
        "new_favourite",
        "compared",
        "top_five",
        "top_site",
        "rediscovered",
        "theater",
        "top_file",
        "alongside",
        "when",
        "session",
        "theater_files",
        "downloads",
        "sift_did",
        "rated",
        "o",
        "top_tag",
        "top_song",
        "first_last",
        "closing",
    ),
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
        "sittings:file",
        "first_file",
        "last_file",
        "new_favourites:file",
        "rediscovered:file",
        "session_ms:session",
        "session_pages:session",
        "downloads",
        "download_bytes",
        "theater_files",
    }
)


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


#: A period's rows added up by (metric, key): whole, and the part from hidden things.
Totals = Mapping[tuple[str, str], tuple[int, int]]


async def build(
    database: Database, user_id: str, period: Period, *, today: date, floor: bool = True
) -> list[KeptCard] | None:
    """The cards of one User's recap of one period, or None where the period is below the floor.

    Every figure is WHOLE: this is what happened, counted over everything the User could see. The
    card is said once here, for the record, and said again from its recipe whenever it is drawn.
    `floor=False` is a recap made again after a correction: it exists, so it is said over what the
    corrected days hold however few.
    """
    before, (usual_first, _) = period.previous(), _usual_window(period)
    rows = await _totals(database, user_id, min(before.first, usual_first), period.last, _READS)
    totals = rows.of(period.first, period.last)
    if floor and _whole(totals, "sittings") < FLOOR_SITTINGS:
        return None
    recipes = _figure_recipes(totals)
    recipes["compared"] = await _compared_recipe(database, user_id, rows, period, totals)
    named, person = await _named_recipes(database, totals)
    recipes.update(named)
    recipes.update(await _file_recipes(database, rows, period, totals))
    alongside = _together_recipe(rows, period, person)
    if alongside is not None:
        recipes["alongside"] = alongside
    return _cards(recipes, period, today)


def _figure_recipes(totals: Totals) -> dict[str, Recipe]:
    """The cards read from the period's figures alone."""
    return {
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
        "session": _session_recipe(totals),
        "downloads": Recipe(sources=_sources(totals, [("downloads", ""), ("download_bytes", "")])),
        "theater_files": Recipe(
            sources=_sources(
                totals, [("theater_files", key) for key in _keys(totals, "theater_files")]
            )
        ),
        # The closing card says nothing with a figure, and it is the one card every recap has: the
        # period's sittings ride in its recipe, which is what a locked reader's floor is read from.
        "closing": Recipe(sources=_sources(totals, [("sittings", "")])),
    }


async def _compared_recipe(
    database: Database, user_id: str, rows: _Totals, period: Period, totals: Totals
) -> Recipe:
    """What the period is compared with: the period before, recorded whole and past the floor, or
    for a day the usual day over the days recorded before it, enough of them."""
    first_row = await database.fetch_one(_FIRST_VIEWED, (user_id,))
    first = None if first_row is None or first_row["first"] is None else str(first_row["first"])
    now = _sources(totals, [("viewed_ms", "")])
    if period.kind is PeriodKind.DAY:
        usual_first, usual_last = _usual_window(period)
        earlier = rows.of(usual_first, usual_last)
        counted = (
            0
            if first is None
            else (usual_last - max(usual_first, date.fromisoformat(first))).days + 1
        )
        usual = {
            source(metric, scope=USUAL): _whole(earlier, metric)
            for metric in ("viewed_ms", "sittings")
        }
        return Recipe(
            sources={**now, **usual, source("days", scope=USUAL): max(counted, 0)},
            compares=counted >= statements.USUAL_FLOOR_DAYS
            and _whole(earlier, "sittings") >= FLOOR_SITTINGS,
        )
    before = period.previous()
    earlier = rows.of(before.first, before.last)
    return Recipe(
        sources={**now, **_sources(earlier, [("viewed_ms", "")], before=True)},
        compares=first is not None
        and first <= before.first.isoformat()
        and _whole(earlier, "sittings") >= FLOOR_SITTINGS,
    )


async def _named_recipes(
    database: Database, totals: Totals
) -> tuple[dict[str, Recipe], NamedThing | None]:
    """The cards that crown a person, the top five, a Site, a tag and a song, and the top person."""
    people = await _top(database, totals, "viewed_ms:person", "person", _PEOPLE_NAMES, TOP_FIVE)
    out: dict[str, Recipe] = {}
    if len(people) > 1:
        out["top_five"] = Recipe(
            sources=_sources(totals, [("viewed_ms:person", one.id) for one in people]),
            named=people,
        )
    crowned = [("top_person", people[:1])]
    for kind, names in (("site", _SITE_NAMES), ("tag", _TAG_NAMES), ("song", _SONG_NAMES)):
        crowned.append(
            (f"top_{kind}", await _top(database, totals, f"viewed_ms:{kind}", kind, names))
        )
    for card, found in crowned:
        for one in found:
            out[card] = Recipe(
                sources=_sources(totals, [(f"viewed_ms:{one.kind}", one.id)]), named=[one]
            )
    return out, people[0] if people else None


def _cards(recipes: Mapping[str, Recipe], period: Period, today: date) -> list[KeptCard]:
    """The cards the recipes say, in the kind's order, past the most a deck holds the last cards
    before the closing one left out."""
    cards: list[KeptCard] = []
    for kind in DECKS[period.kind]:
        recipe = recipes.get(kind)
        if recipe is None:
            continue
        card = _said(kind, BUILDERS[kind](recipe.sources, recipe, period, today), recipe.sources)
        if card is None:
            continue
        cards.append(
            KeptCard(
                **card.model_dump(exclude={"hidden_things"}),
                hidden_things=[one.id for one in recipe.named],
                recipe=recipe,
            )
        )
    return cards if len(cards) <= DECK_MOST else [*cards[: DECK_MOST - 1], cards[-1]]


def _keys(totals: Mapping[tuple[str, str], tuple[int, int]], metric: str) -> list[str]:
    return sorted(key for (asked, key), (whole, _) in totals.items() if asked == metric and whole)


def _ranked(totals: Mapping[tuple[str, str], tuple[int, int]], metric: str) -> list[str]:
    """A metric's keys, the largest whole figure first, the smallest key on a tie."""
    found = [
        (whole, key) for (asked, key), (whole, _) in totals.items() if asked == metric and whole
    ]
    return [key for _, key in sorted(found, key=lambda pair: (-pair[0], pair[1]))]


def _session_recipe(totals: Mapping[tuple[str, str], tuple[int, int]]) -> Recipe:
    """The longest session of the period (its span summed over the days it ran across) and its
    pages."""
    longest = _ranked(totals, "session_ms:session")[:1]
    wanted = [("session_ms:session", key) for key in longest]
    return Recipe(
        sources=_sources(totals, wanted + [("session_pages:session", key) for key in longest])
    )


async def _file_recipes(
    database: Database,
    rows: _Totals,
    period: Period,
    totals: Mapping[tuple[str, str], tuple[int, int]],
) -> dict[str, Recipe]:
    """The cards that name a file: the most viewed, a new favorite, the one come back to after
    the longest time, and the first and last of the period. A file is named as it was called when
    the recap was made, and only one still in the library: the first of each ranking that is."""
    ranked = {kind: _ranked(totals, metric)[:20] for kind, metric in _FILE_CARDS.items()}
    low, high = period.first.isoformat(), period.last.isoformat()
    inside = [row for row in rows.rows if low <= row.day <= high]
    # The first and last moments, a couple of each, so a reader who may not be told of one is
    # told of the next (`_first_last`).
    firsts = sorted((row.whole, row.key) for row in inside if row.metric == "first_file")
    lasts = sorted(
        ((row.whole, row.key) for row in inside if row.metric == "last_file"), reverse=True
    )
    ids = {split_file_key(key)[1] for keys in ranked.values() for key in keys}
    ids |= {key for _, key in (*firsts, *lasts)}
    names = {
        str(row["id"]): str(row["name"])
        for row in await arrivals.file_names(database.fetch_all, sorted(ids))
    }
    out: dict[str, Recipe] = {}
    for kind, keys in ranked.items():
        key = next((key for key in keys if split_file_key(key)[1] in names), None)
        if key is not None:
            asset = split_file_key(key)[1]
            out[kind] = Recipe(
                sources=_sources(totals, [(_FILE_CARDS[kind], key)]),
                named=[NamedThing(kind="asset", id=asset, name=names[asset])],
            )
    first = _distinct([(at, key) for at, key in firsts if key in names])
    last = _distinct([(at, key) for at, key in lasts if key in names])
    if first and last:
        out["first_last"] = Recipe(
            sources={
                **{source("first_file", key): at for at, key in first},
                **{source("last_file", key): at for at, key in last},
            },
            named=_distinct_named([key for _, key in (*first, *last)], names),
        )
    return out


#: The cards that crown one file, and the per-file metric each ranks by.
_FILE_CARDS: Mapping[str, str] = {
    "top_file": "sittings:file",
    "new_favourite": "new_favourites:file",
    "rediscovered": "rediscovered:file",
}


def _distinct(moments: Sequence[tuple[int, str]], most: int = 2) -> list[tuple[int, str]]:
    """The first `most` moments of different files, in the order given."""
    out: list[tuple[int, str]] = []
    for at, key in moments:
        if key not in {one for _, one in out}:
            out.append((at, key))
    return out[:most]


def _distinct_named(keys: Sequence[str], names: Mapping[str, str]) -> list[NamedThing]:
    return [NamedThing(kind="asset", id=key, name=names[key]) for key in dict.fromkeys(keys)]


def _together_recipe(rows: _Totals, period: Period, person: NamedThing | None) -> Recipe | None:
    """The strongest pair of measures that rose and fell together over a month's days or a
    year's weeks, with the whole of each measure kept beside it (`_alongside`)."""
    if period.kind not in _TOGETHER_OVER:
        return None
    daily: dict[str, dict[tuple[str, str], int]] = {}
    for row in rows.rows:
        daily.setdefault(row.day, {})
        daily[row.day][(row.metric, row.key)] = (
            daily[row.day].get((row.metric, row.key), 0) + row.whole
        )
    _, spans = together.closed_spans(period.said_on(period.last + timedelta(days=1)))
    pairs = together.together_pairs(
        together.series_of(daily, spans, None if person is None else person.id)
    )
    if not pairs:
        return None
    best = pairs[0]
    totals = rows.of(period.first, period.last)
    sources = {f"{_TOGETHER}{best.first}|{best.second}": best.n}
    for measure in (best.first, best.second):
        metric, key = together.MEASURES[measure]
        key = person.id if measure == "person" and person is not None else key
        sources[source(metric, key)] = _whole(totals, metric, key)
        sources[_WHOLE + source(metric, key)] = _whole(totals, metric, key)
    named = [person] if person is not None and "person" in (best.first, best.second) else []
    return Recipe(sources=sources, named=named)


def body_of(cards: Sequence[KeptCard]) -> str:
    """The cards as `recaps.body` keeps them: a JSON list of cards, each with its recipe."""
    return "[" + ",".join(card.model_dump_json() for card in cards) + "]"
