# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every figure Insights shows, pinned to an answer worked out by hand.

Each test writes a small set of sittings, walls, opinions, decisions or runs, and its docstring does
the arithmetic a person would do with a pencil: which sittings are views, which are times somebody
sat down, which hour each minute of viewing falls in, what is hidden. The test then holds the
statements to that answer. A figure family per test: time viewed and its parts, the sittings and
the files, the times somebody sat down and the first and last of the day, the opinions, the
organizing, what Sift did, the period's own arithmetic (its boundaries, the files counted once over
its days, the daily average) and the rounding a sentence and a tile say it with.

Days are this device's local days (`store.day_bounds`); every moment below is written on the same
local clock (`conftest.at`), so the local-time rule is exercised by the hours and weekdays.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest

from sift.slices.insights import router as page
from sift.slices.insights import statements as st
from sift.slices.insights.models import Bar, BarPart, Calendar, Chart, DayValue
from sift.slices.insights.store import DayRow, count_day, day_bounds
from sift.slices.insights.tests.conftest import DAY, World, at

pytestmark = pytest.mark.integration

MINUTE = 60_000
HOUR = 60 * MINUTE
NEXT = DAY + timedelta(days=1)


def _by(rows: list[DayRow], metric: str) -> dict[str, tuple[int, int]]:
    return {row.key: (row.whole, row.hidden) for row in rows if row.metric == metric}


async def _count(world: World, day: date = DAY) -> list[DayRow]:
    return await count_day(world.db.fetch_all, world.user, day)


# --- time viewed, the sittings, the files, the hours --------------------------------------------


async def _an_ordinary_day(world: World) -> None:
    """Wednesday 11 March 2026 (`DAY`):

    * 09:50 video A (ten minutes long), 5 minutes of it: a view (a ten-minute video needs 30 s).
    * 11:00 video B, 20 seconds of it: NOT a view, but somebody sat down.
    * 12:00 picture C, hidden: a view the moment it opens, 0 ms of time.
    * 21:30 to 23:00 a Theater wall of two videos, D and E, each on screen the whole 90 minutes:
      one sitting of 90 minutes, whatever its cells played.
    """
    a = await world.add_file("video")
    b = await world.add_file("video")
    c = await world.add_file("image", length_ms=None, hidden=True)
    await world.sit(a, at(9, 50), 5 * MINUTE)
    await world.sit(b, at(11), 20_000)
    await world.sit(c, at(12), 0)
    await world.wall("evening", at(21, 30), at(23), arrangement="wall-one")
    for cell in (await world.add_file("video"), await world.add_file("video")):
        await world.sit(cell, at(21, 30), 90 * MINUTE, screen="theater", theater_session="evening")


async def test_time_viewed_is_the_views_and_the_wall_once(world: World) -> None:
    """Viewed: 5 min (A) + 0 (C) + 90 min (the wall, once) = 95 min = 5,700,000 ms; B is no view.
    By kind: videos 5 min, Theater 90 min; the picture's 0 ms files no row (a zero is not filed).
    Sittings: A, C and the wall = 3, one of them hidden (C); by kind video 1, picture 1, Theater 1.
    Files viewed: A, C, D and E = 4, one hidden. The wall's preset: 90 min."""
    await _an_ordinary_day(world)
    counted = await _count(world)
    assert _by(counted, "viewed_ms") == {"": (95 * MINUTE, 0)}
    assert _by(counted, "viewed_ms:kind") == {"video": (5 * MINUTE, 0), "theater": (90 * MINUTE, 0)}
    assert _by(counted, "sittings") == {"": (3, 1)}
    assert _by(counted, "sittings:kind") == {"video": (1, 0), "image": (1, 1), "theater": (1, 0)}
    assert _by(counted, "files_viewed") == {"": (4, 1)}
    assert _by(counted, "theater_ms:wall") == {"wall-one": (90 * MINUTE, 0)}


async def test_each_hour_holds_only_the_time_viewed_in_it(world: World) -> None:
    """A's 5 minutes from 09:50 are all in the 9 o'clock hour. The wall's 90 minutes from 21:30 are
    30 in the 21 o'clock hour and 60 in the 22 o'clock hour: never 90 minutes in an hour of 60.
    The weekday: Wednesday is 2 (Monday is 0), with all 95 minutes."""
    await _an_ordinary_day(world)
    counted = await _count(world)
    assert _by(counted, "viewed_ms:hour") == {
        "09": (5 * MINUTE, 0),
        "21": (30 * MINUTE, 0),
        "22": (60 * MINUTE, 0),
    }
    assert _by(counted, "viewed_ms:weekday") == {"2": (95 * MINUTE, 0)}


async def test_an_evening_past_midnight_is_shared_between_its_hours(world: World) -> None:
    """A sitting of 2 h 30 min from 23:15: 45 min in 23, 60 in 00, 45 in 01, all filed under the day
    it began on. The parts add up to the whole: 45 + 60 + 45 = 150 minutes."""
    film = await world.add_file("video", length_ms=4 * HOUR)
    await world.sit(film, at(23, 15), 150 * MINUTE)
    counted = await _count(world)
    assert _by(counted, "viewed_ms:hour") == {
        "23": (45 * MINUTE, 0),
        "00": (60 * MINUTE, 0),
        "01": (45 * MINUTE, 0),
    }
    assert sum(whole for whole, _ in _by(counted, "viewed_ms:hour").values()) == 150 * MINUTE


async def test_the_times_somebody_sat_down_and_the_first_and_last(world: World) -> None:
    """Every sitting of any length is somebody there, and a new time is one with nothing ending in
    the half hour before it: 09:50 (A), 11:00 (B, A ended 09:55), 12:00 (C, B ended 11:00:20) and
    21:30 (the wall) = 4, the one at 12:00 hidden. The earliest start 09:50 = 590 minutes; the latest
    finish the wall's end, 23:00 = 1,380 minutes."""
    await _an_ordinary_day(world)
    counted = await _count(world)
    assert _by(counted, "pickups") == {"": (4, 1)}
    assert _by(counted, "earliest_start") == {"": (590, 0)}
    assert _by(counted, "latest_finish") == {"": (1380, 0)}


async def test_the_latest_finish_follows_the_evening_past_midnight(world: World) -> None:
    """23:50 a ten-minute view that ends at 00:00; 00:10 the next day a view of 20 minutes, ten
    minutes after the last ended, so the same evening: it ends at 00:30, which is minute
    1,440 + 30 = 1,470 of the day the evening began on. 02:00 is a new time somebody sat down (an
    hour and a half later) and is the next day's."""
    late = await world.add_file("video", length_ms=HOUR)
    await world.sit(late, at(23, 50), 10 * MINUTE)
    await world.sit(late, at(0, 10, NEXT), 20 * MINUTE)
    await world.sit(late, at(2, 0, NEXT), 20 * MINUTE)
    assert _by(await _count(world), "latest_finish") == {"": (1470, 0)}
    assert _by(await _count(world, NEXT), "earliest_start") == {"": (120, 0)}


# --- the opinions --------------------------------------------------------------------------------


async def _opinion(
    world: World, subject: str, kind: str, before: int | None, after: int | None, when: int
) -> None:
    await world.run(
        "INSERT INTO opinions (id, user_id, subject_kind, subject_id, kind, before, after, at)"
        " VALUES (lower(hex(randomblob(8))), ?, 'asset', ?, ?, ?, ?, ?)",
        (world.user, subject, kind, before, after, when),
    )


async def test_the_opinions_of_a_day(world: World) -> None:
    """X rated 3 then 4 and Y rated 2: 2 files rated, one row each in `rated:file`. X starred, the
    star taken off, starred again: 1 file starred. O on X from 0 to 1, 1 to 2, then 2 back to 1:
    two presses (taking one back is no press)."""
    x = await world.add_file("video")
    y = await world.add_file("video", hidden=True)
    await _opinion(world, x, "rating", None, 3, at(10))
    await _opinion(world, x, "rating", 3, 4, at(11))
    await _opinion(world, y, "rating", None, 2, at(12))
    await _opinion(world, x, "favorite", 0, 1, at(10))
    await _opinion(world, x, "favorite", 1, 0, at(11))
    await _opinion(world, x, "favorite", 0, 1, at(12))
    await _opinion(world, x, "o", 0, 1, at(13))
    await _opinion(world, x, "o", 1, 2, at(14))
    await _opinion(world, x, "o", 2, 1, at(15))
    counted = await _count(world)
    assert _by(counted, "rated") == {"": (2, 1)}
    assert _by(counted, "rated:file") == {x: (1, 0), y: (1, 1)}
    assert _by(counted, "starred") == {"": (1, 0)}
    assert _by(counted, "o") == {"": (2, 0)}


async def test_a_file_rated_on_two_days_is_one_file_rated_that_week(world: World) -> None:
    """X rated on Wednesday and again on Thursday, Y (hidden) on Wednesday: the week rated 2 files,
    where the days' `rated` added up would say 3. Starred alike: X
    starred on both days is 1 file starred. Locked, Y is out: 1 file rated."""
    x = await world.add_file("video")
    y = await world.add_file("video", hidden=True)
    await _opinion(world, x, "rating", None, 3, at(10))
    await _opinion(world, y, "rating", None, 5, at(10))
    await _opinion(world, x, "rating", 3, 4, at(10, 0, NEXT))
    await _opinion(world, x, "favorite", 0, 1, at(10))
    await _opinion(world, x, "favorite", 0, 1, at(10, 0, NEXT))
    rows = [*await _count(world), *await _count(world, NEXT)]
    week = page.Book.of(rows, locked=False)
    assert week.total("rated") == 3
    assert week.files("rated:file") == (2, 1)
    assert week.files("starred:file") == (1, 0)
    assert page.Book.of(rows, locked=True).files("rated:file") == (1, 0)
    assert week.files_figure("Rated", "rated:file").value == 2


# --- the organizing, and what Sift did -----------------------------------------------------------


async def _decision(
    world: World,
    queue: str,
    verb: str,
    *,
    undone: bool = False,
    files: tuple[str, ...] = (),
    payload: dict[str, Any] | None = None,
) -> None:
    decision = f"d-{world._id('d')}"
    await world.run(
        "INSERT INTO workbench_decisions (id, queue, user_id, title, detail, payload, decided_at,"
        " reversed_at, verb, actor_kind, actor_id) VALUES (?, ?, ?, 't', '', ?, ?, ?, ?, 'user', ?)",
        (
            decision,
            queue,
            world.user,
            json.dumps(payload or {}),
            at(15),
            at(16) if undone else None,
            verb,
            world.user,
        ),
    )
    for asset in files:
        await world.run(
            "INSERT INTO workbench_decision_subjects (decision_id, kind, subject_id)"
            " VALUES (?, 'asset', ?)",
            (decision, asset),
        )


async def test_the_organizing_of_a_day(world: World) -> None:
    """Three answers on the duplicates queue, one of them undone, and one ledger record: 2 questions
    answered. One Yes naming 3 faces: 3 faces named. Files filed twice over two decisions, P and Q
    in the first, P again in the second: 2 files filed, Q hidden."""
    p = await world.add_file("video")
    q = await world.add_file("video", hidden=True)
    await _decision(world, "duplicates", "decided")
    await _decision(world, "duplicates", "decided")
    await _decision(world, "duplicates", "decided", undone=True)
    await _decision(world, "ledger", "filed", files=(p, q))
    await _decision(world, "ledger", "filed", files=(p,))
    await _decision(
        world,
        "identified",
        "decided",
        payload={"act": "named-groups", "track_ids": ["a", "b", "c"]},
    )
    counted = await _count(world)
    assert _by(counted, "decided") == {"": (3, 0)}
    assert _by(counted, "decided:queue") == {"duplicates": (2, 0), "identified": (1, 0)}
    assert _by(counted, "faces_named") == {"": (3, 0)}
    assert _by(counted, "files_filed") == {"": (2, 1)}


async def test_what_sift_did_in_a_day(world: World) -> None:
    """A finished fingerprint run of 12 videos and 3 pictures in 1 min of work, and an unfinished
    one of 100 that does not count yet; a finished scan of 30 s: fingerprinted 15 files, worked
    90 s (fingerprint 60 s, scan 30 s). Two faces found at 10:00 and 11:00, stamped in milliseconds
    as the faces slice stamps them, and one the next day: 2 faces found."""

    async def run(family: str, files: dict[str, Any], worker_ms: int, finished: bool) -> None:
        await world.run(
            "INSERT INTO work_runs (id, family, started_at, updated_at, finished_at, worker_ms,"
            " files) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                world._id("r"),
                family,
                at(10),
                at(10),
                at(11) if finished else None,
                worker_ms,
                json.dumps(files),
            ),
        )

    await run("fingerprint", {"video": {"n": 12, "bytes": 9}, "image": {"n": 3}}, 60_000, True)
    await run("fingerprint", {"video": {"n": 100}}, 5_000, False)
    await run("scan", {}, 30_000, True)
    face_file = await world.add_file("video")
    for when in (at(10), at(11), at(10, 0, NEXT)):
        await world.run(
            "INSERT INTO face_tracks (id, asset_id, started_ms, ended_ms, seen_in, quality,"
            " created_at) VALUES (?, ?, 0, 1, 1, 1.0, ?)",
            (world._id("f"), face_file, when * 1000),
        )
    counted = await _count(world)
    assert _by(counted, "fingerprints_made") == {"": (15, 0)}
    assert _by(counted, "work_ms:family") == {"fingerprint": (60_000, 0), "scan": (30_000, 0)}
    assert _by(counted, "faces_found") == {"": (2, 0)}


def test_two_families_activity_names_alike_are_one_row() -> None:
    """Two families Activity reads as Other, 3 h and 2 h: one row "Other" of 5 h, never two rows
    both called Other."""
    rows = [
        DayRow(DAY.isoformat(), "work_ms:family", "retired-one", 3 * HOUR, 0),
        DayRow(DAY.isoformat(), "work_ms:family", "retired-two", 2 * HOUR, 0),
    ]
    period = st.period_of("day", DAY, DAY, None)
    block = page._machine(
        cast(Any, SimpleNamespace(book=page.Book.of(rows, locked=False), period=period))
    )
    assert [(row.piece.text, row.value) for row in block.lists[0].rows] == [("Other", 5 * HOUR)]


# --- the period ----------------------------------------------------------------------------------


def test_a_period_starts_where_the_calendar_says() -> None:
    """A week is Monday to Sunday: Sunday 15 March 2026 is in the week of Monday the 9th. February
    2028 has 29 days. A year is 1 January to 31 December."""
    today = date(2026, 9, 29)
    week = st.period_of("week", date(2026, 3, 15), today, None)
    assert (week.start, week.end) == (date(2026, 3, 9), date(2026, 3, 15))
    month = st.period_of("month", date(2028, 2, 10), today, None)
    assert (month.start, month.end) == (date(2028, 2, 1), date(2028, 2, 29))
    year = st.period_of("year", date(2026, 6, 1), today, None)
    assert (year.start, year.end) == (date(2026, 1, 1), date(2026, 12, 31))


def test_every_day_is_bound_by_its_own_local_midnights() -> None:
    """Every day of 2026 starts at a local midnight and ends where the next begins, so a day the
    clocks change on is 23 or 25 hours and no second belongs to two days or none."""
    day = date(2026, 1, 1)
    while day.year == 2026:
        start, end = day_bounds(day)
        opened = datetime.fromtimestamp(start)
        assert (opened.date(), opened.hour, opened.minute) == (day, 0, 0)
        assert end == day_bounds(day + timedelta(days=1))[0]
        assert end - start in (23 * 3600, 24 * 3600, 25 * 3600)
        day += timedelta(days=1)


def test_a_daily_average_divides_by_the_days_sift_was_counting() -> None:
    """September 2026 looked at on the 29th: 29 days so far. A record that began on the 22nd has
    counted 8 of them (22 to 29), and a daily average divides by 8."""
    september = st.period_of("month", date(2026, 9, 29), date(2026, 9, 29), None)
    assert page._days_counted(september, None) == 29
    assert page._days_counted(september, date(2026, 9, 22)) == 8
    assert page._days_counted(september, date(2025, 1, 1)) == 29


# --- the words a figure is said in ---------------------------------------------------------------


def test_a_headlines_parts_add_up_to_its_whole() -> None:
    """26 hours: videos 6.4 h, pictures 7.3 h, GIFs 2.4 h, Theater 9.9 h. Rounded one by one they
    are 6 + 7 + 2 + 10 = 25. Shared out: 6, 7, 2, 9 (24) and the 2 left to the largest remainders,
    Theater's .9 and then videos' .4 (earlier than the GIFs' equal .4): 7 + 7 + 2 + 10 = 26."""
    march = st.period_of("month", date(2026, 3, 1), date(2026, 9, 29), None)
    parts = {
        "video": 384 * MINUTE,
        "image": 438 * MINUTE,
        "gif": 144 * MINUTE,
        "theater": 594 * MINUTE,
    }
    line = st.viewed(march, sum(parts.values()), parts)
    assert "".join(piece.text for piece in line) == (
        "You viewed 26 hours in March: 7 of videos, 7 of pictures, 2 of GIFs, 10 in Theater."
    )


def test_a_headline_under_two_hours_shares_out_its_minutes() -> None:
    """1 hour 25 minutes = 85 minutes: 50.5 and 34.5, each rounded up, would be 51 + 35 = 86.
    Shared out: 50 and 34 (84) and the one left to the first of two equal halves: 51 + 34."""
    march = st.period_of("month", date(2026, 3, 1), date(2026, 9, 29), None)
    parts = {"video": 3_030_000, "image": 2_070_000}
    line = st.viewed(march, 5_100_000, parts)
    assert "".join(piece.text for piece in line) == (
        "You viewed 1 hour 25 minutes in March: 51 minutes of videos, 34 minutes of pictures."
    )


def test_shares_add_up() -> None:
    assert st.shares_of([1, 1, 1], 10) == [4, 3, 3]
    assert st.shares_of([0, 0], 5) == [0, 0]
    assert sum(st.shares_of([7, 13, 29, 51], 97)) == 97


@pytest.mark.parametrize(
    ("ms", "words"),
    [
        (0, "0 min"),
        (59_000, "under a minute"),
        (90_000, "2 min"),
        (3_570_000, "1 h"),
        (72 * MINUTE, "1 h 12 min"),
        (25 * HOUR, "1 d 1 h"),
        (24 * HOUR, "1 d"),
    ],
)
def test_a_length_on_a_tile_is_the_short_form(ms: int, words: str) -> None:
    """A tile, a list row, a bar: the short form, `$lib/shell/duration`'s `sayDuration` rule. 90 s is 1.5
    minutes, a half going up: 2 min. 59 min 30 s rounds to 60 minutes before it is split: 1 h."""
    assert st.figure_said(ms, "ms") == words


def test_a_sentence_spells_the_same_length_out() -> None:
    assert st.duration(72 * MINUTE) == "1 hour 12 minutes"
    assert st.duration(41 * HOUR) == "41 hours"
    assert st.duration(20_000) == "less than a minute"


def test_a_count_past_a_hundred_thousand_is_about_the_nearest_thousand() -> None:
    assert (st.count(100_000), st.count(100_500), st.count(1_240)) == (
        "100,000",
        "about 101,000",
        "1,240",
    )


def test_every_bar_and_every_day_carries_its_words() -> None:
    """A bar of 1 h 12 min of videos and 30 min of Theater: the parts "1 h 12 min" and "30 min",
    the bar "1 h 42 min". A calendar day of 3 h: "3 h"."""
    chart = Chart(
        unit="ms",
        bars=[
            Bar(
                label="Mon",
                parts=[
                    BarPart(kind="video", value=72 * MINUTE),
                    BarPart(kind="theater", value=30 * MINUTE),
                ],
            )
        ],
    )
    assert [part.said for part in chart.bars[0].parts] == ["1 h 12 min", "30 min"]
    assert chart.bars[0].said == "1 h 42 min"
    calendar = Calendar(unit="ms", days=[DayValue(day=DAY.isoformat(), value=3 * HOUR)])
    assert calendar.days[0].said == "3 h"


def test_a_figures_trend_is_the_charts_bars() -> None:
    """A week of Monday 9 March 2026: 2 sessions on Tuesday, 5 on Friday, none else: the trend is
    the seven days, [0, 2, 0, 0, 5, 0, 0]. A year of them: 7 in March, the rest nothing. A day has
    no hours of sessions, and its time viewed is the hours."""
    rows = [
        DayRow("2026-03-10", "sittings", "", 2, 0),
        DayRow("2026-03-13", "sittings", "", 5, 0),
        DayRow("2026-03-13", "viewed_ms:hour", "21", 3 * MINUTE, 0),
    ]
    book = page.Book.of(rows, locked=False)
    today = date(2026, 9, 29)
    week = st.period_of("week", date(2026, 3, 10), today, None)
    assert page._trend(week, book, "sittings") == [0, 2, 0, 0, 5, 0, 0]
    year = st.period_of("year", date(2026, 3, 10), today, None)
    assert page._trend(year, book, "sittings") == [0, 0, 7, 0, 0, 0, 0, 0, 0, 0, 0, 0]
    day = st.period_of("day", date(2026, 3, 13), today, None)
    assert page._trend(day, book, "sittings") == []
    assert page._trend(day, book, "viewed_ms")[21] == 3 * MINUTE
