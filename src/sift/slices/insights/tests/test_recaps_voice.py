# SPDX-License-Identifier: AGPL-3.0-or-later
"""A recap card's voice: every phrasing short and in Sift's words, every phrasing reachable from
some figures, a story chosen before a phrasing that fits anything, no phrasing twice in a deck, and
the reader's usual read with the hidden part taken out."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from datetime import date, datetime, timedelta

import pytest
from tests.gates import history_ratchets, vocabulary

from sift.kernel.access import Role, Viewer
from sift.kernel.access.sentences import Piece, text_of
from sift.kernel.access.viewer import Concealment
from sift.kernel.wire import HistoryPiece
from sift.slices.insights import recaps, recaps_recipes, recaps_voice, recaps_voice_facts
from sift.slices.insights.recaps import PeriodKind, period_of
from sift.slices.insights.recaps_cards import BUILDERS, Said, within
from sift.slices.insights.recaps_models import NamedThing, Recipe
from sift.slices.insights.recaps_periods import USUAL, Period, source
from sift.slices.insights.recaps_voice import Facts, Voice
from sift.slices.insights.recaps_voice_lines import LINES, Phrasing
from sift.slices.insights.tests.conftest import World

MINUTE = 60_000
HOUR = 60 * MINUTE
TODAY = date(2026, 10, 1)
WEDNESDAY = date(2026, 9, 9)

#: Words no phrasing says: what the screen calls something else, a judgement, a comparison with
#: anybody but the reader.
BANNED = re.compile(
    r"\b(?:journey|session|sessions|sitting|sittings|making|at once|watch|watched|watching|"
    r"arrive|arrived|arrives|arrival|arriving|percentile|top \d+%|other users|everyone|"
    r"only|just|great|impressive|should|amazing|incredible|epic|we|us|our)\b",
    re.IGNORECASE,
)


def words_of(text: str) -> int:
    return len(re.sub(r"\{\w+\}", "X", text).split())


def sentences(text: str) -> list[str]:
    return [one for one in re.split(r"(?<=[.?!])\s+", text.strip()) if one]


# --- every phrasing ---------------------------------------------------------------------------


@pytest.mark.parametrize(("kind", "one"), recaps_voice.every_phrasing(), ids=lambda x: str(x))
def test_every_phrasing_is_short_and_in_sifts_words(kind: str, one: Phrasing) -> None:
    assert 1 <= words_of(one.headline) <= 5, one.id
    assert not one.headline.endswith("."), one.id
    said = sentences(one.context)
    assert 1 <= len(said) <= 2, one.id
    assert all(words_of(sentence) <= 10 for sentence in said), one.id
    for text in (one.headline, one.context):
        plain = re.sub(r"\{\w+\}", "Mara", text)
        assert plain.isascii() and " -- " not in plain, one.id
        assert not BANNED.search(plain), (one.id, BANNED.search(plain))
        for check in (*vocabulary.CHECKS, *vocabulary.HELD_AT_ZERO):
            assert not vocabulary.offences(check, plain), (one.id, check)
        assert not history_ratchets.history_words_in(plain), one.id


def test_every_kind_has_five_phrasings_and_none_is_said_twice() -> None:
    assert all(len(lines) >= 5 for lines in LINES.values())
    assert set(LINES) == set(recaps_voice_facts.FACTS)
    every = [one for lines in LINES.values() for one in lines]
    assert len({one.id for one in every}) == len(every)
    assert len({one.context for one in every}) == len(every)
    headlines = [(one.headline, one.id.partition(".")[0]) for one in every]
    assert len(set(headlines)) == len(headlines)


def test_the_banned_words_are_refused() -> None:
    """The check reads what it is meant to: a phrasing that says one is caught."""
    for line in ("Your viewing session ran late.", "5 files arrived.", "You watched it twice."):
        assert BANNED.search(line), line
    assert not BANNED.search("5 files imported. A quiet day for the library.")


# --- the numbers as a person says them ---------------------------------------------------------


def test_lengths_counts_and_hours_are_said_as_people_say_them() -> None:
    spoken = recaps_voice.spoken
    assert [spoken(ms) for ms in (0, 7 * MINUTE, 53 * MINUTE, 65 * MINUTE)] == [
        "under a minute",
        "7 minutes",
        "53 minutes",
        "an hour",
    ]
    assert [spoken(ms) for ms in (85 * MINUTE, 110 * MINUTE, 3 * HOUR)] == [
        "an hour and a half",
        "2 hours",
        "3 hours",
    ]
    films = recaps_voice.films
    assert [films(ms) for ms in (10 * MINUTE, 40 * MINUTE, 70 * MINUTE, 2 * HOUR)] == [
        None,
        "half a film",
        "a film",
        "a film and a half",
    ]
    assert films(15 * HOUR) == "10 films"
    share = recaps_voice.share
    assert [share(1, 40), share(1, 3), share(5, 9), share(9, 9), share(1, 0)] == [
        None,
        "a third",
        "half",
        "nearly all",
        None,
    ]
    times = recaps_voice.times
    assert [times(10, 6), times(10, 5), times(25, 2), times(1, 0)] == [
        None,
        "twice",
        "about 13 times",
        None,
    ]
    assert recaps_voice.hour_said(0, "12") == "midnight"
    assert recaps_voice.hour_said(12, "12") == "noon"
    assert recaps_voice.hour_said(22, "12") == "10 PM"
    assert recaps_voice.hour_said(22, "24") == "22:00"
    assert recaps_voice.hour_bare(21, "12") == "9"
    assert recaps_voice.hour_bare(21, "24") == "21:00"
    assert [recaps_voice.part_of_day(h) for h in (2, 8, 14, 21)] == [
        "night",
        "morning",
        "afternoon",
        "evening",
    ]


def test_a_name_sits_in_a_line_only_where_it_reads_as_words() -> None:
    fits, stem = recaps_voice.fits, recaps_voice.stem
    assert fits("Mara Vell") and fits("harbor_lights_04") and fits("McKenzie Hart")
    assert not fits("253b7adbe5c35a867251")
    assert not fits("ZqWnRtPlXv_dl_dl_gb")
    assert not fits("a-name-far-too-long-for-any-headline")
    assert not fits("clip_19840101")
    assert stem("harbor_lights_04.mp4") == "harbor_lights_04"
    assert stem(".hidden") == ".hidden"


# --- every phrasing is reached by some figures ------------------------------------------------

DAYS = [date(2026, 9, 1) + timedelta(days=n) for n in range(28)]
WEEKS = [date(2026, 7, 6) + timedelta(weeks=n) for n in range(10)]
MONTHS = [date(2026, month, 1) for month in range(1, 10)]
YEARS = [date(year, 1, 1) for year in range(2014, 2026)]


def periods(kind: PeriodKind) -> list[Period]:
    days = {PeriodKind.DAY: DAYS, PeriodKind.WEEK: WEEKS, PeriodKind.MONTH: MONTHS}
    return [period_of(kind, day) for day in days.get(kind, YEARS)]


def stamp(day: date, hour: int, minute: int = 0) -> int:
    return int(datetime(day.year, day.month, day.day, hour, minute).timestamp())


src = source
PERSON = NamedThing(kind="person", id="p1", name="Mara Vell")
LONG_NAMED = NamedThing(
    kind="person", id="p1", name="Orla Finch of the long and winding and very long name"
)
FILE = NamedThing(kind="asset", id="a1", name="harbor_lights_04.mp4")
OTHER = NamedThing(kind="asset", id="a2", name="quiet_cove.jpg")

#: (the figures, the things named, whether it compares) for a period.
Scenario = Callable[[Period], tuple[dict[str, int], list[NamedThing], bool]]


def plain(
    figures: dict[str, int], named: Iterable[NamedThing] = (), compares: bool = False
) -> Scenario:
    return lambda period: (figures, list(named), compares)


def said_by(
    kind: str, period: Period, figures: dict[str, int], named: list[NamedThing], compares: bool
) -> Voice | None:
    recipe = Recipe(sources=figures, named=named, compares=compares)
    words = BUILDERS[kind](figures, recipe, period, TODAY)
    if words is None:
        return None
    assert text_of(words.headline) and text_of(words.context), kind
    return recaps_voice_facts.voice(kind, words, figures, recipe, period, TODAY)


def said_words(line: tuple[Piece, ...]) -> int:
    """Words as said, a name counted as one."""
    return sum(
        1 if one.kind else sum(1 for word in one.text.split() if re.search(r"\w", word))
        for one in line
    )


def said_sentences(line: tuple[Piece, ...]) -> list[int]:
    """Each sentence's words, a name counted as one."""
    counts, words = [], 0
    for one in line:
        if one.kind:
            words += 1
            continue
        for run in re.split(r"(?<=[.?!])\s+", one.text):
            words += sum(1 for word in run.split() if re.search(r"\w", word))
            if re.search(r"[.?!]\s*$", run):
                counts.append(words)
                words = 0
    return counts + ([words] if words else [])


def reached(kind: str, cases: Iterable[tuple[PeriodKind, Scenario]]) -> set[str]:
    out: set[str] = set()
    for over, scenario in cases:
        for period in periods(over):
            spoken = said_by(kind, period, *scenario(period))
            if spoken is not None:
                assert 2 <= said_words(spoken.headline) <= 5, spoken
                assert all(n <= 10 for n in said_sentences(spoken.context)), spoken
                out.add(spoken.phrasing)
    return out


def kinds_viewed(total: int, **parts: int) -> dict[str, int]:
    return {src("viewed_ms"): total} | {
        src("viewed_ms:kind", kind): ms for kind, ms in parts.items()
    }


def same_weekdays(metric: str, key: str, values: Iterable[int]) -> Scenario:
    """A day with its same weekday over the weeks before it, for the reader's usual."""

    def build(period: Period) -> tuple[dict[str, int], list[NamedThing], bool]:
        out = {
            src(metric, within(key, day, day), scope=USUAL): value
            for back, value in enumerate(values, start=1)
            if (day := period.first - timedelta(weeks=back))
        }
        return out, [], True

    return build


def joined(*scenarios: Scenario) -> Scenario:
    def build(period: Period) -> tuple[dict[str, int], list[NamedThing], bool]:
        figures: dict[str, int] = {}
        named: list[NamedThing] = []
        compares = False
        for one in scenarios:
            more, things, also = one(period)
            figures |= more
            named += things
            compares |= also
        return figures, named, compares

    return build


D, W, M, Y = PeriodKind.DAY, PeriodKind.WEEK, PeriodKind.MONTH, PeriodKind.YEAR


def compared_cases() -> list[tuple[PeriodKind, Scenario]]:
    out: list[tuple[PeriodKind, Scenario]] = []
    for now in (40, 26, 20, 14, 6):
        day = {
            src("viewed_ms"): now * HOUR // 10,
            src("viewed_ms", scope=USUAL): 28 * 2 * HOUR,
            src("days", scope=USUAL): 28,
        }
        out.append((D, plain(day, compares=True)))
        week = {src("viewed_ms"): now * HOUR, src("viewed_ms", before=True): 20 * HOUR}
        out.append((M, plain(week, compares=True)))
    return out


def file_key(kind: str = "video", asset: str = "a1") -> str:
    return f"{kind}:{asset}"


def first_last(first: tuple[int, int], last: tuple[int, int], days: int = 0) -> Scenario:
    def build(period: Period) -> tuple[dict[str, int], list[NamedThing], bool]:
        figures = {
            src("first_file", "a1"): stamp(period.first, *first),
            src("last_file", "a2"): stamp(period.first + timedelta(days=days), *last),
        }
        return figures, [FILE, OTHER], False

    return build


def months_of(year: int, values: dict[int, dict[str, int]], metric: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for month, parts in values.items():
        first, last = recaps_recipes._month(period_of(Y, date(year, 1, 1)), month)
        for key, value in parts.items():
            out[src(metric, within(key, first, last))] = value
    return out


def by_year(build: Callable[[int], tuple[dict[str, int], list[NamedThing]]]) -> Scenario:
    return lambda period: (*build(period.first.year), False)


def days_viewed(days: Iterable[tuple[int, int]], value: int = HOUR) -> Scenario:
    """A year with something viewed on these (month, day)s."""

    def build(period: Period) -> tuple[dict[str, int], list[NamedThing], bool]:
        out = {}
        for month, day in days:
            one = date(period.first.year, month, day)
            out[src("viewed_ms", within("", one, one))] = value + month * MINUTE
        return out, [], False

    return build


def race(leads: tuple[int, ...]) -> Callable[[int], tuple[dict[str, int], list[NamedThing]]]:
    """A year whose months were led by these of three people."""
    people = [
        NamedThing(kind="person", id=f"p{n}", name=name)
        for n, name in enumerate(("Mara Vell", "Juno Pike", "Cassia Lynn"))
    ]

    def build(year: int) -> tuple[dict[str, int], list[NamedThing]]:
        months = {
            month: {f"p{n}": 10 if lead == n else 1 for n in range(3)}
            for month, lead in enumerate(leads, start=1)
        }
        return months_of(year, months, "viewed_ms:person"), people

    return build


def every_week(weeks: int) -> Scenario:
    """A year viewed on the same weekday every week, and once more."""

    def build(period: Period) -> tuple[dict[str, int], list[NamedThing], bool]:
        days = [period.first + timedelta(weeks=n) for n in range(weeks)] + [
            period.first + timedelta(days=3)
        ]
        return {src("viewed_ms", within("", day, day)): HOUR for day in days}, [], False

    return build


CASES: dict[str, list[tuple[PeriodKind, Scenario]]] = {
    "headline": [
        (D, plain(kinds_viewed(3 * HOUR, theater=2 * HOUR, video=HOUR))),
        (D, plain(kinds_viewed(3 * HOUR, video=2 * HOUR, image=HOUR))),
        (D, plain(kinds_viewed(3 * HOUR, image=2 * HOUR, video=HOUR))),
        (D, plain(kinds_viewed(175 * MINUTE, video=90 * MINUTE, image=85 * MINUTE))),
        (D, plain(kinds_viewed(5 * HOUR, video=2 * HOUR, image=2 * HOUR, gif=HOUR))),
        (D, plain(kinds_viewed(9 * HOUR, video=4 * HOUR, image=3 * HOUR, gif=2 * HOUR))),
        (
            D,
            plain(
                kinds_viewed(3 * HOUR, video=HOUR, image=HOUR, gif=30 * MINUTE, theater=30 * MINUTE)
            ),
        ),
        (
            W,
            plain(
                kinds_viewed(
                    200 * MINUTE,
                    video=100 * MINUTE,
                    image=50 * MINUTE,
                    gif=30 * MINUTE,
                    theater=20 * MINUTE,
                )
            ),
        ),
        (Y, plain(kinds_viewed(400 * HOUR, video=150 * HOUR, image=150 * HOUR, gif=100 * HOUR))),
    ],
    "compared": compared_cases(),
    "top_person": [
        (
            D,
            plain({src("viewed_ms:person", "p1"): 6 * HOUR, src("viewed_ms"): 10 * HOUR}, [PERSON]),
        ),
        (
            D,
            plain({src("viewed_ms:person", "p1"): 4 * HOUR, src("viewed_ms"): 10 * HOUR}, [PERSON]),
        ),
        (
            D,
            plain({src("viewed_ms:person", "p1"): 2 * HOUR, src("viewed_ms"): 10 * HOUR}, [PERSON]),
        ),
        (
            D,
            plain(
                {src("viewed_ms:person", "p1"): 10 * MINUTE, src("viewed_ms"): 2 * HOUR}, [PERSON]
            ),
        ),
        (D, plain({src("viewed_ms:person", "p1"): 10 * MINUTE}, [LONG_NAMED])),
    ],
    "top_five": [
        (
            W,
            plain(
                {src("viewed_ms:person", f"p{n}"): ms * MINUTE for n, ms in enumerate(values)},
                [
                    NamedThing(kind="person", id=f"p{n}", name=name)
                    for n, name in enumerate(
                        ("Mara Vell", "Juno Pike", "Cassia Lynn", "Elina Sorrel", "Bryn Calloway")[
                            : len(values)
                        ]
                    )
                ],
            ),
        )
        for values in ((100, 40, 30, 20, 10), (100, 90, 10), (100, 80, 70, 60, 55), (100, 70, 20))
    ],
    "top_site": [
        (
            D,
            plain(
                {src("viewed_ms:site", "s1"): ms, src("viewed_ms"): total},
                [NamedThing(kind="site", id="s1", name="Glimmerhub")],
            ),
        )
        for ms, total in ((6 * HOUR, 10 * HOUR), (5 * MINUTE, 2 * HOUR), (40 * MINUTE, 3 * HOUR))
    ],
    "top_tag": [
        (
            D,
            plain(
                {src("viewed_ms:tag", "t1"): ms, src("viewed_ms"): total},
                [NamedThing(kind="tag", id="t1", name="Golden hour")],
            ),
        )
        for ms, total in ((6 * HOUR, 10 * HOUR), (5 * MINUTE, 2 * HOUR), (40 * MINUTE, 3 * HOUR))
    ],
    "top_song": [
        (
            D,
            plain(
                {src("viewed_ms:song", "g1"): ms, src("viewed_ms"): 3 * HOUR},
                [NamedThing(kind="song", id="g1", name="Low Tide")],
            ),
        )
        for ms in (40 * MINUTE, 5 * MINUTE, 20 * MINUTE)
    ],
    "first_last": [
        (D, first_last((6, 0), (20, 0))),
        (D, first_last((10, 0), (23, 30))),
        (D, first_last((6, 0), (23, 30))),
        (D, first_last((2, 0), (21, 0))),
        (D, first_last((10, 0), (11, 30))),
        (D, first_last((10, 0), (17, 0))),
        (W, first_last((10, 0), (17, 0))),
        (M, first_last((10, 0), (17, 0))),
        (M, first_last((10, 0), (17, 0), days=5)),
    ],
    "when": [
        (
            D,
            joined(
                plain({src("viewed_ms:hour", "22"): HOUR, src("viewed_ms"): 3 * HOUR}),
                plain({src("viewed_ms:hour", "22", scope=USUAL): HOUR}),
            ),
        ),
        (
            D,
            joined(
                plain({src("viewed_ms:hour", "22"): HOUR, src("viewed_ms"): 3 * HOUR}),
                plain({src("viewed_ms:hour", "21", scope=USUAL): HOUR}),
            ),
        ),
        (
            D,
            joined(
                plain({src("viewed_ms:hour", "22"): HOUR, src("viewed_ms"): 3 * HOUR}),
                plain({src("viewed_ms:hour", "14", scope=USUAL): HOUR}),
            ),
        ),
        (D, plain({src("viewed_ms:hour", "02"): HOUR, src("viewed_ms"): 3 * HOUR})),
        (D, plain({src("viewed_ms:hour", "09"): HOUR, src("viewed_ms"): 3 * HOUR})),
        (D, plain({src("viewed_ms:hour", "15"): HOUR, src("viewed_ms"): 3 * HOUR})),
        (
            W,
            plain(
                {
                    src("viewed_ms:hour", "15"): HOUR,
                    src("viewed_ms:weekday", "6"): HOUR,
                    src("viewed_ms"): 3 * HOUR,
                }
            ),
        ),
        (
            M,
            plain(
                {
                    src("viewed_ms:hour", "15"): HOUR,
                    src("viewed_ms:weekday", "6"): HOUR,
                    src("viewed_ms"): 3 * HOUR,
                    src("viewed_ms:hour", "21", before=True): HOUR,
                }
            ),
        ),
    ],
    "theater": [
        (W, plain({src("viewed_ms:kind", "theater"): 100 * MINUTE})),
        (D, plain({src("viewed_ms:kind", "theater"): 100 * MINUTE})),
        (
            D,
            joined(
                plain({src("viewed_ms:kind", "theater"): 50 * MINUTE}),
                same_weekdays("viewed_ms:kind", "theater", (20 * MINUTE, 20 * MINUTE)),
            ),
        ),
        (W, plain({src("viewed_ms:kind", "theater"): 160 * MINUTE})),
        (D, plain({src("viewed_ms:kind", "theater"): 20 * MINUTE})),
        (D, plain({src("viewed_ms:kind", "theater"): 45 * MINUTE})),
    ],
    "sift_did": [
        (D, joined(plain({src("files_added"): 5}), same_weekdays("files_added", "", (40, 40, 40)))),
        (W, plain({src("files_added"): 5, src("files_added", before=True): 40})),
        (W, plain({src("files_added"): 100, src("files_added", before=True): 30})),
        (W, plain({src("files_added"): 10})),
        (W, plain({src("files_added"): 20_000})),
        (W, plain({src("faces_named"): 3})),
        (W, plain({src("decided"): 5})),
        (W, plain({src("files_filed"): 4})),
        (W, plain({src("files_added"): 500})),
    ],
    "rated": [
        (W, plain({src("rated"): 10})),
        (W, plain({src("starred"): 4})),
        (W, plain({src("rated"): 10, src("starred"): 4})),
        (W, plain({src("rated"): 60})),
        (W, plain({src("rated"): 2})),
    ],
    "o": [(W, plain({src("o"): n})) for n in (5, 1, 12)],
    "closing": [
        (D, plain({src("sittings"): 12})),
        (Y, plain({src("viewed_ms"): 80 * HOUR, src("sittings"): 12})),
        (Y, plain({src("viewed_ms"): 10 * HOUR, src("sittings"): 12})),
    ],
    "top_file": [
        (W, plain({src("viewed_ms"): 1, src("sittings:file", file_key()): views}, [FILE]))
        for views in (6, 3, 2, 1)
    ],
    "new_favourite": [
        (W, plain({src("new_favourites:file", file_key()): views}, [FILE])) for views in (4, 12)
    ],
    "rediscovered": [
        (W, plain({src("rediscovered:file", file_key("image")): days}, [FILE]))
        for days in (30, 400, 100, 800)
    ],
    "session": [
        (W, plain({src("session_ms:session", "s1"): ms, src("session_pages:session", "s1"): pages}))
        for ms, pages in (
            (HOUR, 60),
            (5 * HOUR, 10),
            (10 * MINUTE, 3),
            (30 * HOUR, 0),
            (2 * HOUR, 10),
        )
    ],
    "downloads": [
        (W, plain({src("downloads"): n, src("download_bytes"): 1000})) for n in (5, 150, 1)
    ],
    "theater_files": [(W, plain({src("theater_files", "w1"): n})) for n in (50, 1500, 5, 300)],
    "alongside": [
        (M, plain({"together:theater|starred": 9})),
        (M, plain({"together:viewed|person": 9}, [PERSON])),
    ],
    "mosaic": [
        (
            Y,
            plain(
                {
                    src("sittings:file", file_key(asset=f"a{n}")): views
                    for n, views in enumerate(values)
                },
                [
                    NamedThing(kind="asset", id=f"a{n}", name=f"clip_{n}.mp4")
                    for n in range(len(values))
                ],
            ),
        )
        for values in ((10, 4), (10, 9), (10, 6, 5))
    ],
    "before_after": [
        (
            Y,
            by_year(
                lambda year: (
                    months_of(
                        year,
                        {
                            1: {"video": 300, "image": 10, "gif": 1, "theater": 1},
                            12: {"video": 10, "image": 300, "gif": 1, "theater": 1},
                        },
                        "viewed_ms:kind",
                    ),
                    [],
                )
            ),
        ),
        (
            Y,
            by_year(
                lambda year: (
                    months_of(
                        year,
                        {
                            1: {"video": 300, "image": 10, "gif": 1, "theater": 1},
                            12: {"video": 300, "image": 10, "gif": 1, "theater": 1},
                        },
                        "viewed_ms:kind",
                    ),
                    [],
                )
            ),
        ),
    ],
    "race": [(Y, by_year(race(leads))) for leads in ((0, 0, 0), (0, 0, 1), (0, 1, 2), (0,))],
    "heatmap": [
        (Y, days_viewed([(3, day) for day in range(1, 11)])),
        (Y, days_viewed([(1, 5), (5, 5), (9, 5)])),
        (
            Y,
            days_viewed(
                [(month, day) for month in (1, 2, 3) for day in (1, 8, 15, 22)]
                + [(4, d) for d in range(1, 20, 2)]
            ),
        ),
        (Y, days_viewed([(1 + n // 14, 1 + 2 * (n % 14)) for n in range(28)])),
        (Y, every_week(30)),
        (Y, days_viewed([(2, 3), (2, 5)], value=3 * HOUR)),
    ],
}


@pytest.mark.parametrize("kind", sorted(LINES))
def test_every_phrasing_of_a_kind_is_reached_across_periods(kind: str) -> None:
    assert reached(kind, CASES[kind]) == {one.id for one in LINES[kind]}


# --- the choice ----------------------------------------------------------------------------


def test_a_story_its_figures_tell_comes_before_a_phrasing_that_fits_anything() -> None:
    for day in DAYS:
        facts = Facts(
            {
                "time_head": "2 hours",
                "time": "2 hours",
                "films": "a film and a half",
                "when": "yesterday",
            },
            frozenset({"films"}),
        )
        chosen = recaps_voice.choose("theater", facts, period_of(D, day))
        assert chosen is not None and chosen.id == "theater.films"
    period = period_of(D, WEDNESDAY)
    facts = Facts(
        {
            "time_head": "2 hours",
            "time": "2 hours",
            "films": "a film and a half",
            "when": "yesterday",
        },
        frozenset({"films"}),
    )
    chosen = recaps_voice.choose("theater", facts, period)
    assert chosen is not None and chosen.id == "theater.films"
    # The same figures always read the same way.
    assert recaps_voice.choose("theater", facts, period) == chosen
    assert recaps_voice.choose("theater", Facts({}), period) is None
    assert recaps_voice.spoken_by("theater", None, period) is None
    assert recaps_voice.spoken_by("theater", Facts({}), period) is None


def test_a_usual_story_comes_first() -> None:
    """Five files on a Wednesday that usually brings 40 is told as that, not as a small batch."""
    slots = {"n": "5", "usual": "40", "weekday": "Wednesday", "when": "yesterday"}
    for day in DAYS:
        facts = Facts(slots, frozenset({"day_quiet", "small"}))
        chosen = recaps_voice.choose("sift_did", facts, period_of(D, day))
        assert chosen is not None and chosen.id == "sift_did.day_quiet"


def test_a_kind_with_no_voice_leads_with_its_statement() -> None:
    period = period_of(D, WEDNESDAY)
    assert (
        recaps_voice_facts.voice(
            "achievement",
            recaps_cards_said(),
            {},
            Recipe(),
            period,
            TODAY,
        )
        is None
    )


def test_a_card_says_nothing_where_its_facts_have_nothing() -> None:
    period = period_of(Y, date(2025, 1, 1))
    for kind in (
        "headline",
        "top_person",
        "top_five",
        "first_last",
        "when",
        "top_file",
        "session",
        "alongside",
        "mosaic",
        "before_after",
        "race",
        "heatmap",
    ):
        card = recaps_voice.Card(kind, recaps_cards_said(), {}, Recipe(), period, TODAY)
        assert recaps_voice_facts.FACTS[kind](card) is None, kind
    card = recaps_voice.Card(
        "heatmap", recaps_cards_said(calendar=True), {}, Recipe(), period, TODAY
    )
    assert recaps_voice_facts.FACTS["heatmap"](card) is None
    card = recaps_voice.Card("top_file", recaps_cards_said(), {}, Recipe(), period, TODAY)
    assert recaps_voice_facts._file_slot(card, "a1") is None
    for kind in ("new_favourite", "rediscovered"):
        card = recaps_voice.Card(kind, recaps_cards_said(), {}, Recipe(), period, TODAY)
        assert recaps_voice_facts.FACTS[kind](card) is None


def recaps_cards_said(*, calendar: bool = False) -> Said:
    from sift.slices.insights.models import Calendar, DayValue

    drawn = Calendar(unit="ms", days=[DayValue(day="2025-01-01", value=0)]) if calendar else None
    return Said((), calendar=drawn)


# --- a deck, made and drawn ---------------------------------------------------------------------


async def row(world: World, day: date, metric: str, key: str, whole: int, hidden: int = 0) -> None:
    await world.run(
        "INSERT OR REPLACE INTO insight_days (user_id, day, metric, key, whole, hidden, split_at)"
        " VALUES (?, ?, ?, ?, ?, ?, 0)",
        (world.user, day.isoformat(), metric, key, whole, hidden),
    )


def text(pieces: list[HistoryPiece]) -> str:
    return "".join(piece.lead + piece.text for piece in pieces)


async def a_wednesday(world: World, *, hidden: int = 0) -> None:
    """Four weeks of days with 40 files imported each Wednesday, then a Wednesday of 5."""
    for back in range(1, 29):
        day = WEDNESDAY - timedelta(days=back)
        await row(world, day, "viewed_ms", "", HOUR)
        await row(world, day, "sittings", "", 2)
        await row(world, day, "viewed_ms:hour", "21", HOUR)
        if day.weekday() == WEDNESDAY.weekday():
            await row(world, day, "files_added", "", 40, hidden)
    await row(world, WEDNESDAY, "viewed_ms", "", 3 * HOUR)
    await row(world, WEDNESDAY, "viewed_ms:kind", "theater", 2 * HOUR)
    await row(world, WEDNESDAY, "viewed_ms:kind", "video", HOUR)
    await row(world, WEDNESDAY, "viewed_ms:hour", "22", 2 * HOUR)
    await row(world, WEDNESDAY, "sittings", "", 12)
    await row(world, WEDNESDAY, "files_added", "", 5)
    await world.run(
        "INSERT OR REPLACE INTO insight_progress (user_id, added_up_to) VALUES (?, ?)",
        (world.user, WEDNESDAY.isoformat()),
    )


async def made_day(world: World) -> str:
    made = await recaps.make_due(world.db, world.user, WEDNESDAY + timedelta(days=1))
    (day,) = [one for one in made if one.period == f"day:{WEDNESDAY.isoformat()}"]
    return day.id


async def drawn_day(world: World, recap: str, *, unlocked: bool) -> dict[str, tuple[str, str]]:
    viewer = Viewer(
        id=world.user, role=Role.ADMIN, show_hidden=unlocked, concealment=Concealment.FULLY_GONE
    )
    found = await recaps.opened(world.db, viewer, recap, today=WEDNESDAY + timedelta(days=1))
    assert found is not None
    return {one.kind: (text(one.headline), text(one.context)) for one in found.cards}


async def test_a_day_is_told_against_its_own_usual_wednesday(world: World) -> None:
    await a_wednesday(world)
    cards = await drawn_day(world, await made_day(world), unlocked=True)
    assert cards["sift_did"] == (
        "5 files imported",
        "A quiet day for the library. Your usual Wednesday brings 40.",
    )
    assert cards["when"] == ("10 PM was your hour", "Your evenings usually peak closer to 9.")
    assert cards["headline"][0] == "A Theater Wednesday"
    # No two cards of the deck share a phrasing.
    assert len({head for head, _ in cards.values()}) == len(cards)
    assert len({context for _, context in cards.values()}) == len(cards)


async def test_a_locked_reader_s_usual_leaves_out_what_is_hidden(world: World) -> None:
    await a_wednesday(world, hidden=30)
    recap = await made_day(world)
    unlocked = await drawn_day(world, recap, unlocked=True)
    locked = await drawn_day(world, recap, unlocked=False)
    assert unlocked["sift_did"][1].endswith("Your usual Wednesday brings 40.")
    assert locked["sift_did"][1].endswith("Your usual Wednesday brings 10.")
