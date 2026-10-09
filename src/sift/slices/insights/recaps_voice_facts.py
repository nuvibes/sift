# SPDX-License-Identifier: AGPL-3.0-or-later
"""What each kind of recap card's voice reads from its figures: the words for its slots, and the
stories those figures tell. See `recaps_voice`."""

from __future__ import annotations

import calendar
from collections.abc import Callable, Mapping
from datetime import date, datetime
from typing import TYPE_CHECKING

from sift.kernel import when
from sift.kernel.access.sentences import said
from sift.slices.insights import statements
from sift.slices.insights.metrics import split_file_key
from sift.slices.insights.recaps_models import Recipe
from sift.slices.insights.recaps_periods import USUAL, Period, PeriodKind, _parts, source
from sift.slices.insights.recaps_voice import (
    Card,
    Facts,
    Slot,
    Voice,
    compared_stories,
    films,
    fits,
    hour_bare,
    hour_said,
    name_slots,
    named_of,
    part_of_day,
    per_day,
    period_slots,
    share,
    short_period,
    spoken,
    spoken_by,
    spoken_short,
    stem,
    times,
    usual,
)
from sift.slices.insights.statements import KIND_WORDS, THEATER, Named

if TYPE_CHECKING:
    from sift.slices.insights.recaps_cards import Said

_MINUTE = 60_000
_HOUR = 60 * _MINUTE
_DAY = 24 * _HOUR

_KINDS = (*statements.KINDS, THEATER)

#: What each kind of time is called where a line names what most of it went on.
_LEADS = {"video": "videos", "image": "pictures", "gif": "GIFs", THEATER: "the wall"}

#: What each kind is called where a line sets two of them side by side.
_KIND_NAMES = {"video": "videos", "image": "pictures", "gif": "GIFs", THEATER: "Theater"}

#: How much viewing makes a long period, by kind.
_LONG = {
    PeriodKind.DAY: 4 * _HOUR,
    PeriodKind.WEEK: 15 * _HOUR,
    PeriodKind.MONTH: 50 * _HOUR,
    PeriodKind.YEAR: 300 * _HOUR,
}


def _story(*pairs: tuple[str, bool]) -> frozenset[str]:
    return frozenset(name for name, true in pairs if true)


def _words(slots: Mapping[str, Slot | None]) -> dict[str, Slot]:
    """The slots with words in them: one left empty keeps its phrasings from being chosen."""
    return {name: value for name, value in slots.items() if value}


def _long(total: int) -> str | None:
    if total >= 2 * _DAY:
        return f"{statements.counted(total * 2 // _DAY // 2, 'day', 'days')}, end to end"
    if 8 * _HOUR <= total <= 14 * _HOUR:
        return "a long flight's worth"
    found = films(total)
    return None if found is None else f"about {found}"


def _headline(card: Card) -> Facts | None:
    total = card.get("viewed_ms")
    if total <= 0:
        return None
    parts = {kind: card.get("viewed_ms:kind", kind) for kind in _KINDS}
    ranked = sorted(parts.items(), key=lambda one: -one[1])
    (lead, lead_ms), (second, second_ms) = ranked[0], ranked[1]
    slots = _words(
        {
            **period_slots(card),
            "total": spoken(total),
            "video": spoken(parts["video"]),
            "image": spoken(parts["image"]),
            "theater": spoken(parts[THEATER]),
            "rest": spoken(total - parts[THEATER]),
            "lead": _LEADS[lead],
            "lead_share": share(lead_ms, total),
            "a": spoken_short(lead_ms),
            "a_kind": _KIND_NAMES[lead],
            "b": spoken_short(second_ms),
            "b_kind": _KIND_NAMES[second],
            "long": _long(total),
        }
    )
    return Facts(
        slots,
        _story(
            ("theater", 2 * parts[THEATER] >= total),
            ("video", 5 * parts["video"] >= 3 * total),
            ("pictures", 5 * parts["image"] >= 3 * total),
            ("even", 10 * second_ms >= 9 * lead_ms and 3 * second_ms >= total),
            ("long", total >= _LONG[card.period.kind]),
        ),
    )


def _compared(card: Card) -> Facts | None:
    now = card.get("viewed_ms")
    if card.is_day:
        days = card.figures.get(source("days", scope=USUAL), 0)
        then: int | None = card.figures.get(source("viewed_ms", scope=USUAL), 0) // max(days, 1)
        stories = compared_stories(now, then, "day_")
    else:
        then = card.get("viewed_ms", before=True)
        stories = compared_stories(now, then)
    before = card.period.previous()
    return Facts(
        _words(
            {
                **period_slots(card),
                "now": spoken(now),
                "usual": spoken(then or 0),
                "diff": spoken(now - (then or 0)) if now > (then or 0) else None,
                "times": times(now, then or 0),
                "before": short_period(before, card.today, "before"),
                "before_when": short_period(before, card.today, "when"),
            }
        ),
        frozenset(stories),
    )


def _crowned(kind: str, prefix: str) -> Callable[[Card], Facts | None]:
    """A card crowning one person, Site, tag or song: its time, and its share of everything."""

    def read(card: Card) -> Facts | None:
        one = named_of(card, kind)
        if one is None:
            return None
        ms, total = card.get(f"viewed_ms:{kind}", one.id), card.get("viewed_ms")
        slots = _words(
            {
                **period_slots(card),
                **name_slots(prefix, one),
                "time": spoken(ms),
                "share": share(ms, total),
                "films": films(ms),
            }
        )
        return Facts(
            slots,
            _story(
                ("mostly", total > 0 and 2 * ms >= total),
                ("owned", total > 0 and 10 * ms >= 3 * total),
                ("long", ms >= (90 if kind == "person" else 30) * _MINUTE),
                ("little", 0 < ms < 10 * _MINUTE),
            ),
        )

    return read


def _people(card: Card) -> list[tuple[Named, int]]:
    return [
        (Named("person", one.id, one.name), card.get("viewed_ms:person", one.id))
        for one in card.recipe.named
        if one.kind == "person"
    ]


def _top_five(card: Card) -> Facts | None:
    people = _people(card)
    if len(people) < 2:
        return None
    (first, t1), (second, t2), (_, last) = people[0], people[1], people[-1]
    return Facts(
        _words(
            {
                **name_slots("first", first),
                "second": statements.named(second),
                "t1": spoken(t1),
                "t2": spoken(t2),
                "gap": spoken(t1 - t2),
                "n": statements.count(len(people)),
                "total": spoken(sum(ms for _, ms in people)),
            }
        ),
        _story(
            ("runaway", t1 >= 2 * t2),
            ("close", 20 * t2 >= 17 * t1),
            ("spread", len(people) >= 4 and 2 * last >= t1),
        ),
    )


def _moments(card: Card, metric: str) -> list[tuple[int, str]]:
    return [
        (card.figures[name], _parts(name)[2])
        for name in card.recipe.sources
        if _parts(name)[1] == metric and card.figures.get(name, 0) > 0
    ]


def _minute(period: Period, at: datetime) -> int:
    """A moment's minute of the period's first day: past 1439 for a day's own late finish."""
    return (at.date() - period.first).days * 24 * 60 + at.hour * 60 + at.minute


def _at(card: Card, at: datetime) -> tuple[str, str]:
    """(where a moment sits in a line, the shorter form for a headline)."""
    clock = statements.clock(at.hour * 60 + at.minute, card.period.hours)
    if card.is_day:
        return clock, clock
    if card.period.kind is PeriodKind.WEEK:
        day = statements.WEEKDAYS[at.weekday()]
    else:
        day = f"{calendar.month_name[at.month]} {at.day}"
    return f"{clock} on {day}", day


def _file_slot(card: Card, asset: str) -> Slot | None:
    one = next((one for one in card.recipe.named if one.id == asset), None)
    if one is None or not fits(stem(one.name)):
        return None
    return statements.named(Named("asset", one.id, stem(one.name)))


def _gap(minutes: int) -> str:
    if minutes >= 48 * 60:
        return statements.counted((minutes + 12 * 60) // (24 * 60), "day", "days")
    return spoken(minutes * _MINUTE)


def _first_last(card: Card) -> Facts | None:
    firsts, lasts = _moments(card, "first_file"), _moments(card, "last_file")
    if not firsts or not lasts:
        return None
    (began_s, first), (ended_s, last) = min(firsts), max(lasts)
    began, ended = when.wall(began_s), when.wall(ended_s)
    span = _minute(card.period, ended) - _minute(card.period, began)
    (first_at, first_head), (last_at, last_head) = _at(card, began), _at(card, ended)
    start, end = _minute(card.period, began), _minute(card.period, ended)
    day = card.is_day
    return Facts(
        _words(
            {
                "first_at": first_at,
                "last_at": last_at,
                "first_head": first_head,
                "last_head": last_head,
                "first_file": _file_slot(card, first),
                "last_file": _file_slot(card, last),
                "span_hours": statements.count((span + 30) // 60),
                "gap": _gap(span),
            }
        ),
        _story(
            ("early", day and 4 * 60 <= start < 8 * 60),
            ("late", day and end >= 23 * 60),
            ("both", day and 4 * 60 <= start < 8 * 60 and end >= 23 * 60),
            ("long", day and span >= 18 * 60),
            ("short", day and span <= 2 * 60),
        ),
    )


def _top(card: Card, metric: str, keys: tuple[str, ...]) -> tuple[int, int] | None:
    """The key with the most, as a number, and its figure; the earliest on a tie."""
    best: tuple[int, int] | None = None
    for key in keys:
        value = card.figures.get(metric + key, 0)
        if value > 0 and (best is None or value > best[1]):
            best = (int(key), value)
    return best


_HOURS = tuple(f"{hour:02d}" for hour in range(24))


def _usual_hour(card: Card) -> int | None:
    stem_of = source("viewed_ms:hour", scope=USUAL if card.is_day else "", before=not card.is_day)
    found = _top(card, stem_of, _HOURS)
    return None if found is None else found[0]


def _when(card: Card) -> Facts | None:
    peak = _top(card, source("viewed_ms:hour"), _HOURS)
    if peak is None:
        return None
    hour, hours = peak[0], card.period.hours
    total = card.get("viewed_ms")
    then = _usual_hour(card)
    slots: dict[str, Slot | None] = {
        **period_slots(card),
        "hour": hour_said(hour, hours),
        "share": share(peak[1], total),
        "before": short_period(card.period.previous(), card.today, "before"),
    }
    if then is not None:
        slots |= {
            "usual_hour": hour_said(then, hours),
            "usual_bare": hour_bare(then, hours),
            "part": part_of_day(then),
        }
    slots |= _weekday_slots(card, total)
    near = then is not None and part_of_day(then) == part_of_day(hour)
    return Facts(
        _words(slots),
        _story(
            ("day_same", card.is_day and then == hour),
            ("day_near", card.is_day and then != hour and near),
            ("day_far", card.is_day and then is not None and not near),
            ("moved", not card.is_day and then is not None and then != hour),
            ("late", 1 <= hour < 5),
            ("morning", 5 <= hour < 12),
        ),
    )


def _weekday_slots(card: Card, total: int) -> dict[str, Slot | None]:
    day = _top(card, source("viewed_ms:weekday"), tuple(str(n) for n in range(7)))
    if card.is_day or day is None:
        return {}
    name = statements.WEEKDAYS[day[0]]
    plural = name if card.period.kind is PeriodKind.WEEK else f"{name}s"
    return {"weekday_head": plural, "weekday_plain": plural, "weekday_share": share(day[1], total)}


def _theater(card: Card) -> Facts | None:
    ms = card.get("viewed_ms:kind", THEATER)
    then = usual(card, "viewed_ms:kind", THEATER)
    minutes = ms // _MINUTE
    return Facts(
        _words(
            {
                **period_slots(card),
                "time": spoken(ms),
                "time_head": spoken_short(ms),
                "films": films(ms),
                "diff": spoken(ms - (then or 0)) if then else None,
                "left": spoken(180 * _MINUTE - ms) if 150 <= minutes < 180 else None,
            }
        ),
        _story(
            ("films", minutes >= 68),
            ("night", card.is_day and minutes >= 60),
            ("more", bool(compared_stories(ms, then) & {"big", "up"})),
            ("double", 150 <= minutes < 180),
            ("short", minutes < 30),
        ),
    )


def _sift_did(card: Card) -> Facts | None:
    n, then = card.get("files_added"), usual(card, "files_added")
    faces, decided, filed = card.get("faces_named"), card.get("decided"), card.get("files_filed")
    quiet = n > 0 and then is not None and 2 * n <= then
    count = statements.count
    return Facts(
        _words(
            {
                **period_slots(card),
                "n": count(n) if n > 0 else None,
                "usual": count(then) if then else None,
                "times": times(n, then or 0),
                "before": short_period(card.period.previous(), card.today, "before"),
                "faces": count(faces) if faces else None,
                "decided": count(decided) if decided else None,
                "filed": count(filed) if filed else None,
            }
        ),
        _story(
            ("day_quiet", quiet and card.is_day),
            ("quiet", quiet and not card.is_day),
            ("big", then is not None and then > 0 and n >= 2 * then),
            ("small", 0 < n < 20),
            ("huge", n >= 10_000),
            ("faces", n == 0 and faces > 0),
            ("organized", n == 0 and faces == 0 and decided > 0),
            ("filed", n == 0 and faces == 0 and decided == 0 and filed > 0),
        ),
    )


def _rated(card: Card) -> Facts | None:
    rated, starred = card.get("rated"), card.get("starred")
    counted, count = statements.counted, statements.count
    return Facts(
        _words(
            {
                "when": short_period(card.period, card.today, "when"),
                "rated": counted(rated, "file", "files") if rated else None,
                "rated_n": count(rated) if rated else None,
                "starred": counted(starred, "file", "files") if starred else None,
                "starred_n": count(starred) if starred else None,
                "per_day": per_day(rated, card.period, card.today),
            }
        ),
        _story(
            ("both", rated > 0 and starred > 0),
            ("stars", rated == 0 and starred > 0),
            ("many", rated >= 50),
            ("few", 0 < rated <= 3),
        ),
    )


def _o(card: Card) -> Facts | None:
    n = card.get("o")
    return Facts(
        _words(
            {
                "when": short_period(card.period, card.today, "when"),
                "n": statements.count(n),
                "presses": statements.counted(n, "O press", "O presses"),
                "per_day": per_day(n, card.period, card.today),
            }
        ),
        _story(("one", n == 1), ("many", n >= 10)),
    )


def _closing(card: Card) -> Facts | None:
    year = card.period.kind is PeriodKind.YEAR
    total = card.get("viewed_ms")
    days = statements.counted((total * 2 // _DAY + 1) // 2, "day", "days")
    return Facts(
        _words({**period_slots(card), "total": spoken(total), "days": days}),
        _story(("year", year), ("year_long", year and total >= 2 * _DAY)),
    )


def _file(card: Card, metric: str) -> tuple[str, int, str] | None:
    """(the file's kind, its figure, its id) for the card that names one file."""
    for name in card.recipe.sources:
        if _parts(name)[1] == metric and card.figures.get(name, 0) > 0:
            kind, asset = split_file_key(_parts(name)[2])
            return kind, card.figures[name], asset
    return None


def _file_facts(card: Card, metric: str) -> dict[str, Slot | None] | None:
    found = _file(card, metric)
    if found is None:
        return None
    kind, value, asset = found
    return {
        **period_slots(card),
        "file": _file_slot(card, asset),
        "views": statements.counted(value, "view", "views"),
        "once": statements.once(value),
        "more": statements.count(value - 1),
        "kind_word": KIND_WORDS.get(kind, ("file", "files"))[0],
        "figure": str(value),
    }


def _top_file(card: Card) -> Facts | None:
    slots = _file_facts(card, "sittings:file")
    if slots is None:
        return None
    views = int(str(slots["figure"]))
    return Facts(
        _words(slots), _story(("repeat", views >= 5), ("stuck", views >= 3), ("twice", views == 2))
    )


def _new_favourite(card: Card) -> Facts | None:
    slots = _file_facts(card, "new_favourites:file")
    if slots is None:
        return None
    return Facts(_words(slots), _story(("hooked", int(str(slots["figure"])) >= 10)))


def _rediscovered(card: Card) -> Facts | None:
    slots = _file_facts(card, "rediscovered:file")
    if slots is None:
        return None
    days = int(str(slots["figure"]))
    slots |= {
        "days": statements.counted(days, "day", "days"),
        "years": "a year" if days < 730 else f"{days // 365} years",
        "months": statements.counted(days // 30, "month", "months"),
    }
    return Facts(_words(slots), _story(("archive", days >= 365), ("dusted", 60 <= days < 365)))


def _session(card: Card) -> Facts | None:
    name = next(
        (one for one in card.recipe.sources if _parts(one)[1] == "session_ms:session"), None
    )
    if name is None:
        return None
    ms = card.figures.get(name, 0)
    pages = card.get("session_pages:session", _parts(name)[2])
    return Facts(
        _words(
            {
                "time": spoken(ms),
                "pages": statements.counted(pages, "page", "pages") if pages else None,
                "pages_n": statements.count(pages) if pages else None,
            }
        ),
        _story(
            ("deep", pages >= 50),
            ("long", 4 * _HOUR <= ms < _DAY),
            ("short", ms < 30 * _MINUTE),
            ("open", ms >= _DAY),
        ),
    )


def _counted(noun: str, nouns: str, metric: str, big: int) -> Callable[[Card], Facts | None]:
    """A card that counts one thing: downloads, or the files a wall showed."""

    def read(card: Card) -> Facts | None:
        n = sum(
            card.figures.get(name, 0) for name in card.recipe.sources if _parts(name)[1] == metric
        )
        each = per_day(n, card.period, card.today)
        return Facts(
            _words(
                {
                    "when": short_period(card.period, card.today, "when"),
                    "n": statements.count(n),
                    nouns: statements.counted(n, noun, nouns),
                    "per_day": each,
                }
            ),
            _story(
                ("one", n == 1),
                ("big", n >= big and each is not None),
                ("packed", n >= big and each is not None),
                ("small", n < 20),
                ("rounds", n >= 200),
            ),
        )

    return read


#: Each measure Alongside pairs, as a line names it.
_MEASURES = {
    "theater": "Theater",
    "starred": "stars",
    "viewed": "viewing",
    "added": "imports",
    "sessions": "views",
    "decided": "answers",
    "o": "O presses",
}


def _alongside(card: Card) -> Facts | None:
    pair = next((name for name in card.recipe.sources if name.startswith("together:")), None)
    if pair is None:
        return None
    first, _, second = pair.removeprefix("together:").partition("|")
    person = named_of(card, "person")

    def called(measure: str) -> Slot | None:
        if measure == "person":
            return None if person is None else said(statements.named(person))
        return _MEASURES.get(measure)

    unit = "week" if card.period.kind is PeriodKind.YEAR else "day"
    return Facts(
        _words(
            {
                "a": called(first),
                "b": called(second),
                "n": statements.counted(card.figures.get(pair, 0), unit, f"{unit}s"),
                "units": f"{unit}s",
            }
        )
    )


def _mosaic(card: Card) -> Facts | None:
    views = [row.value for row in card.words.rows]
    if len(views) < 2:
        return None
    v1, v2 = views[0], views[1]
    counted = statements.counted
    return Facts(
        _words(
            {
                "n": statements.count(len(views)),
                "v1": counted(v1, "view", "views"),
                "v2": counted(v2, "view", "views"),
                "total": counted(sum(views), "view", "views"),
            }
        ),
        _story(("runaway", v1 >= 2 * v2), ("close", 10 * v2 >= 9 * v1)),
    )


def _before_after(card: Card) -> Facts | None:
    chart = card.words.chart
    if chart is None or len(chart.bars) != 2:
        return None
    leads = [_KIND_NAMES[max(bar.parts, key=lambda part: part.value).kind] for bar in chart.bars]
    same = leads[0] == leads[1]
    return Facts(
        _words(
            {
                "m1": chart.bars[0].label,
                "m2": chart.bars[1].label,
                "k1": leads[0],
                "k2": leads[1],
                "k": leads[0] if same else None,
            }
        ),
        _story(("changed", not same), ("same", same)),
    )


def _race(card: Card) -> Facts | None:
    chart, people = card.words.chart, _people(card)
    if chart is None or not people:
        return None
    led = [
        max(bar.parts, key=lambda part: part.value).kind
        for bar in chart.bars
        if any(part.value for part in bar.parts)
    ]
    top = max((one for one, _ in people), key=lambda one: led.count(one.id))
    m, of = led.count(top.id), len(led)
    counted = statements.counted
    return Facts(
        _words(
            {
                **name_slots("person", top),
                "m": statements.count(m),
                "of": statements.count(of),
                "of_months": counted(of, "month", "months"),
                "m_months": counted(m, "month", "months"),
                "leaders": statements.count(len(set(led))),
            }
        ),
        _story(
            ("every", m == of >= 2),
            ("most", of >= 2 and m < of <= 2 * m),
            ("varied", len(set(led)) >= 3),
        ),
    )


def _streak(days: list[tuple[date, int]]) -> tuple[date, date] | None:
    """The longest run of days with something viewed, the earliest on a tie."""
    best: tuple[date, date] | None = None
    start: date | None = None
    for day, value in days:
        start = (start or day) if value > 0 else None
        if start is not None and (best is None or (day - start) > (best[1] - best[0])):
            best = (start, day)
    return best


def _heatmap(card: Card) -> Facts | None:
    drawn = card.words.calendar
    if drawn is None:
        return None
    days = [(date.fromisoformat(one.day), one.value) for one in drawn.days]
    viewed = [(day, value) for day, value in days if value > 0]
    if not viewed:
        return None
    so_far = (min(card.period.last, card.today) - card.period.first).days + 1
    busiest = min(viewed, key=lambda one: (-one[1], one[0]))
    run = _streak(days)
    length = 0 if run is None else (run[1] - run[0]).days + 1

    def words(day: date, _: date) -> str:
        return f"{calendar.month_name[day.month]} {day.day}"

    slots: dict[str, Slot | None] = {
        "when": short_period(card.period, card.today, "when"),
        "days": statements.counted(len(viewed), "day", "days"),
        "share": share(len(viewed), so_far),
        "busiest": statements.WEEKDAYS[busiest[0].weekday()] + ", " + words(busiest[0], card.today),
        "busiest_time": spoken(busiest[1]),
        "streak": statements.counted(length, "day", "days"),
        "streak_from": words(run[0], card.today) if run else None,
        "streak_to": words(run[1], card.today) if run else None,
    }
    shape, column = _months(viewed), _weekdays(viewed)
    return Facts(
        _words(slots | shape | column),
        _story(("streak", length >= 7), ("shape", bool(shape)), ("column", bool(column))),
    )


def _months(viewed: list[tuple[date, int]]) -> dict[str, Slot | None]:
    months: dict[int, int] = {}
    for day, value in viewed:
        months[day.month] = months.get(day.month, 0) + value
    if len(months) < 3:
        return {}
    ranked = sorted(months.items(), key=lambda one: (-one[1], one[0]))
    return {
        "best_month": calendar.month_name[ranked[0][0]],
        "quiet_month": calendar.month_name[ranked[-1][0]],
    }


def _weekdays(viewed: list[tuple[date, int]]) -> dict[str, Slot | None]:
    if len(viewed) < 28:
        return {}
    sums = [0] * 7
    for day, value in viewed:
        sums[day.weekday()] += value
    best = max(range(7), key=lambda one: (sums[one], -one))
    found = share(sums[best], sum(sums))
    if 5 * sums[best] < sum(sums):
        return {}
    return {"weekday": statements.WEEKDAYS[best], "weekday_share": found}


#: Each kind's facts, by the kind of card.
FACTS: Mapping[str, Callable[[Card], Facts | None]] = {
    "headline": _headline,
    "compared": _compared,
    "top_person": _crowned("person", "person"),
    "top_five": _top_five,
    "top_site": _crowned("site", "site"),
    "top_tag": _crowned("tag", "tag"),
    "top_song": _crowned("song", "song"),
    "first_last": _first_last,
    "when": _when,
    "theater": _theater,
    "sift_did": _sift_did,
    "rated": _rated,
    "o": _o,
    "closing": _closing,
    "top_file": _top_file,
    "new_favourite": _new_favourite,
    "rediscovered": _rediscovered,
    "session": _session,
    "downloads": _counted("download", "downloads", "downloads", 100),
    "theater_files": _counted("file", "files", "theater_files", 1000),
    "alongside": _alongside,
    "mosaic": _mosaic,
    "before_after": _before_after,
    "race": _race,
    "heatmap": _heatmap,
}


def voice(
    kind: str,
    words: Said,
    figures: Mapping[str, int],
    recipe: Recipe,
    period: Period,
    today: date,
) -> Voice | None:
    """What a card of this kind leads with, over these figures; None for a kind with no voice."""
    read = FACTS.get(kind)
    if read is None:
        return None
    return spoken_by(kind, read(Card(kind, words, figures, recipe, period, today)), period)
