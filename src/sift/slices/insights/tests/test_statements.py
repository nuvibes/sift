# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every statement Insights can say, built as pieces, read back and held to the eight rules.

One row per KIND of statement, in `SAID`, which is what `statements.py` being one module of pure
builders makes possible: the whole vocabulary is a table, and a rule checked over the table has no
hole where somebody forgot to check one sentence. The example set is pinned word for word
(`PINNED`), its people taken from the project's invented cast (`tests/gates/data/names_cast.txt`),
because a name-shaped string in the tree must be on it.
"""

from __future__ import annotations

import inspect
import re
from datetime import date
from fractions import Fraction

import pytest

from sift.kernel.access import sentences as say
from sift.slices.insights import definitions, statements_cards, together
from sift.slices.insights import statements as st
from sift.slices.insights.statements import Moment, Named, period_of

pytestmark = pytest.mark.unit

#: Friday, 25 September 2026: every period below is read from here.
TODAY = date(2026, 9, 25)

SOMEBODY = "Neve Alder"
PERSON = Named("person", "p1", SOMEBODY)
WALL = Named("wall", "w1", "Nine up")
SITE = Named("site", "s1", "Quillhouse")
FILE = Named("asset", "f1", "harbour.mp4")
OTHER_FILE = Named("asset", "f2", "lantern.jpg")

HOUR = 3_600_000
MINUTE = 60_000

THIS_MONTH = period_of("month", TODAY, TODAY, None)
AUGUST = period_of("month", date(2026, 8, 10), TODAY, None)
JULY = period_of("month", date(2026, 7, 10), TODAY, None)
THIS_WEEK = period_of("week", TODAY, TODAY, None)
LAST_WEEK = period_of("week", date(2026, 9, 16), TODAY, None)
TODAY_ONLY = period_of("day", TODAY, TODAY, None)
LAST_YEAR = period_of("year", date(2025, 6, 1), TODAY, None)
EVER = period_of("all", TODAY, TODAY, date(2026, 3, 3))

#: EVERY STATEMENT INSIGHTS CAN SAY, as (what it is, the line). Every public builder is here:
#: `test_every_builder_is_in_the_table` fails for one that is not.
SAID: tuple[tuple[str, say.Line | None], ...] = (
    (
        "headline, a closed month",
        st.viewed(AUGUST, 41 * HOUR, {"video": 29 * HOUR, "image": 9 * HOUR, "gif": 3 * HOUR}),
    ),
    (
        "headline, an open month",
        st.viewed(THIS_MONTH, 12 * HOUR, {"video": 8 * HOUR, "image": 3 * HOUR, "gif": HOUR}),
    ),
    ("headline, one kind", st.viewed(AUGUST, 41 * HOUR, {"video": 41 * HOUR})),
    (
        "headline, with Theater",
        st.viewed(AUGUST, 46 * HOUR, {"video": 41 * HOUR, "theater": 5 * HOUR}),
    ),
    (
        "headline, in minutes",
        st.viewed(TODAY_ONLY, 50 * MINUTE, {"video": 30 * MINUTE, "image": 20 * MINUTE}),
    ),
    ("headline, overall", st.viewed(EVER, 400 * HOUR, {"video": 300 * HOUR, "gif": 100 * HOUR})),
    ("compared, fewer", st.compared(AUGUST, 41 * HOUR, JULY, 53 * HOUR)),
    ("compared, more", st.compared(AUGUST, 41 * HOUR, JULY, 29 * HOUR)),
    ("compared, the same", st.compared(AUGUST, 41 * HOUR, JULY, 41 * HOUR)),
    ("files by kind", st.files_by_kind({"video": 312, "image": 1240, "gif": 96}, 212)),
    ("files by kind, one kind", st.files_by_kind({"video": 12}, 30)),
    (
        "most-viewed person",
        st.most_viewed_person(THIS_MONTH, PERSON, 6 * HOUR, {"video": 38, "image": 140}),
    ),
    ("Theater, mostly one wall", st.theater(AUGUST, 5 * HOUR, WALL, 4 * HOUR, 12)),
    ("Theater, no wall said", st.theater(AUGUST, 5 * HOUR, WALL, 2 * HOUR, 12)),
    ("came back", st.came_back(14)),
    ("came back, one file", st.came_back(1)),
    ("Photo Sets", st.photo_sets(36)),
    ("opened Sift", st.opened(THIS_WEEK, 38)),
    ("opened Sift twice", st.opened(TODAY_ONLY, 2)),
    ("opened first", st.opened_first(38, PERSON, 12)),
    ("opened first, every time", st.opened_first(12, PERSON, 12)),
    (
        "earliest and latest, a week",
        st.earliest_and_latest(
            LAST_WEEK,
            Moment(date(2026, 9, 15), 7 * 60 + 10),
            Moment(date(2026, 9, 18), 1540),
            "24",
        ),
    ),
    (
        "earliest and latest, a day",
        st.earliest_and_latest(
            TODAY_ONLY, Moment(TODAY, 9 * 60), Moment(TODAY, 23 * 60 + 40), "24"
        ),
    ),
    (
        "earliest and latest, a month",
        st.earliest_and_latest(
            AUGUST, Moment(date(2026, 8, 4), 6 * 60 + 5), Moment(date(2026, 8, 21), 23 * 60), "24"
        ),
    ),
    ("busiest weekday", st.busiest_weekday(AUGUST, 4, 12 * HOUR, 41 * HOUR)),
    ("opinions", st.opinions(44, 19)),
    ("opinions, starred only", st.opinions(0, 3)),
    ("O count", st.o_count(THIS_MONTH, 12)),
    ("organized", st.organized(THIS_MONTH, 120)),
    ("named and filed", st.named_and_filed(30, 45)),
    ("arrived", st.arrived(THIS_MONTH, 142)),
    ("nothing arrived", st.arrived(THIS_MONTH, 0)),
    ("deleted", st.deleted(3)),
    ("Sift worked", st.worked(THIS_MONTH, 14 * HOUR)),
    ("Sift found", st.found_and_fingerprinted(1204, 3000)),
    ("some hidden", st.some_hidden(THIS_MONTH)),
    ("below the floor", st.plain(st.NOT_ENOUGH)),
    ("nothing viewed yet", st.plain(st.NOTHING_YET)),
    ("a guest with no shares", st.plain(st.NOTHING_SHARED)),
    ("still counting", st.still_counting()),
    ("no Theater", st.theater(THIS_MONTH, 0, None, 0, 0)),
    ("Sift did no work", st.worked(JULY, 0)),
    ("a day against the usual, more", st.compared_with_usual(3 * HOUR, 2 * HOUR)),
    ("a day against the usual, less", st.compared_with_usual(20 * MINUTE, HOUR)),
    ("a day against the usual, the same", st.compared_with_usual(HOUR, HOUR + 10_000)),
    ("biggest day of a week", st.biggest(LAST_WEEK, date(2026, 9, 17), 5 * HOUR)),
    ("biggest day of a month", st.biggest(AUGUST, date(2026, 8, 14), 5 * HOUR)),
    ("biggest month overall", st.biggest(EVER, date(2026, 3, 1), 40 * HOUR)),
    ("busiest hour", st.busiest_hour(22, 40 * MINUTE, "24")),
    ("came back, the rule", st.three_times_or_more()),
    ("on the day, a week", st.on_the_day(LAST_WEEK, Moment(date(2026, 9, 15), 7 * 60))),
    ("on the day, past midnight", st.on_the_day(TODAY_ONLY, Moment(TODAY, 1540))),
    ("most from a Site", st.most_from(SITE, 40)),
    ("most-viewed Site", st.most_viewed(AUGUST, SITE, 5 * HOUR)),
    ("most-viewed tag", st.most_viewed(AUGUST, Named("tag", "t1", "runway"), 2 * HOUR)),
    ("most-viewed people", st.most_viewed_people(AUGUST)),
    ("busiest hour, twelve-hour clock", st.busiest_hour(22, 40 * MINUTE, "12")),
    (
        "earliest and latest, twelve-hour clock",
        st.earliest_and_latest(
            LAST_WEEK,
            Moment(date(2026, 9, 15), 7 * 60 + 10),
            Moment(date(2026, 9, 18), 1540),
            "12",
        ),
    ),
    ("busiest hour, midnight and noon", st.busiest_hour(0, 40 * MINUTE, "12")),
    ("songs", st.songs(12)),
    ("most-viewed file", statements_cards.most_viewed_file(AUGUST, FILE, 14)),
    (
        "first and last, a day",
        statements_cards.first_and_last(
            TODAY_ONLY, FILE, Moment(TODAY, 9 * 60 + 10), OTHER_FILE, Moment(TODAY, 1500), "12"
        ),
    ),
    (
        "first and last, a week",
        statements_cards.first_and_last(
            LAST_WEEK,
            FILE,
            Moment(date(2026, 9, 14), 8 * 60),
            OTHER_FILE,
            Moment(date(2026, 9, 20), 23 * 60),
            "24",
        ),
    ),
    ("a new favourite", statements_cards.new_favourite(AUGUST, FILE, 5)),
    ("rediscovered", statements_cards.rediscovered(AUGUST, FILE, 214)),
    ("longest session", statements_cards.longest_session(AUGUST, 2 * HOUR, 34)),
    ("longest session, no pages", statements_cards.longest_session(TODAY_ONLY, 40 * MINUTE, 0)),
    ("downloads", statements_cards.downloaded(AUGUST, 40)),
    ("Theater showed", statements_cards.theater_showed(AUGUST, 1240)),
    ("the five files", statements_cards.most_viewed_files(LAST_YEAR)),
    ("focus shifted", statements_cards.focus("January", "video", "December", "image")),
    ("focus the same", statements_cards.focus("January", "theater", "December", "theater")),
    ("months led", statements_cards.months_led(LAST_YEAR, PERSON, 7, 12)),
    ("days viewed", statements_cards.days_viewed(LAST_YEAR, 214, date(2025, 3, 14))),
    ("a definition", definitions.definition("Files opened")),
    ("together, weeks", together.together("week", together.Pair("theater", "starred", 9, 0.8))),
    (
        "together, the top person",
        together.together("day", together.Pair("person", "starred", 12, 0.7), PERSON),
    ),
    ("together, months", together.together("month", together.Pair("sessions", "decided", 8, 0.6))),
    ("together, arrivals", together.together("day", together.Pair("viewed", "added", 10, 0.9))),
    ("together, O", together.together("week", together.Pair("viewed", "o", 11, 0.65))),
)

#: THE EXAMPLE SET, WORD FOR WORD, pinned so the sentences cannot drift.
PINNED = [
    (SAID[0][1], "You viewed 41 hours in August: 29 of videos, 9 of pictures, 3 of GIFs."),
    (SAID[9][1], "312 videos, 1,240 pictures and 96 GIFs, across 212 sessions."),
    (
        SAID[11][1],
        f"Your most-viewed person this month was {SOMEBODY}: 6 hours, 38 videos and 140 pictures.",
    ),
    (SAID[12][1], "You spent 5 hours in Theater, mostly on your Saved Layout 'Nine up'."),
    (SAID[14][1], "14 files you came back to three times or more."),
    (SAID[16][1], "You looked through 36 Photo Sets."),
    (SAID[59][1], "You viewed files carrying 12 songs."),
    (SAID[17][1], "You opened Sift 38 times this week."),
    (
        SAID[21][1],
        "Your earliest start was 07:10 on Tuesday; your latest finish 01:40 on Saturday.",
    ),
    # Further lines, pinned so they cannot drift either.
    (
        SAID[1][1],
        "You viewed 12 hours this month so far: 8 of videos, 3 of pictures, 1 of GIFs.",
    ),
    (SAID[2][1], "You viewed 41 hours of videos in August."),
    (SAID[3][1], "You viewed 46 hours in August: 41 of videos, 5 in Theater."),
    (SAID[4][1], "You viewed 50 minutes today so far: 30 of videos, 20 of pictures."),
    (SAID[5][1], "You viewed 400 hours overall: 300 of videos, 100 of GIFs."),
    (SAID[6][1], "That's 12 hours fewer than July."),
    (SAID[7][1], "That's 12 hours more than July."),
    (SAID[8][1], "That's the same as July."),
    (SAID[10][1], "12 videos across 30 sessions."),
    (SAID[13][1], "You spent 5 hours in Theater."),
    (SAID[15][1], "1 file you came back to three times or more."),
    (SAID[18][1], "You opened Sift twice today."),
    (SAID[19][1], f"12 of those times, the first thing you opened was {SOMEBODY}."),
    (SAID[20][1], f"Every time, the first thing you opened was {SOMEBODY}."),
    (SAID[22][1], "Your earliest start was 09:00; your latest finish 23:40."),
    (
        SAID[23][1],
        "Your earliest start was 06:05 on August 4; your latest finish 23:00 on August 21.",
    ),
    (SAID[24][1], "You viewed the most on Fridays: 12 hours."),
    (SAID[25][1], "You rated 44 files and starred 19."),
    (SAID[26][1], "You starred 3 files."),
    (SAID[27][1], "You pressed O 12 times this month."),
    (SAID[28][1], "You answered 120 questions on Organize this month."),
    (SAID[29][1], "You named 30 faces and filed 45 files."),
    (SAID[30][1], "142 files were imported this month."),
    (SAID[31][1], "No files were imported this month."),
    (SAID[32][1], "3 files were deleted."),
    (SAID[33][1], "Sift worked on tasks for 14 hours this month."),
    (SAID[34][1], "Sift found 1,204 faces and fingerprinted 3,000 files."),
    (SAID[35][1], "Some of September is hidden. Unlock to include it."),
    (SAID[36][1], "Not enough yet to say."),
    (SAID[39][1], "Sift is still counting some earlier days."),
    (SAID[40][1], "You spent no time in Theater this month."),
    (SAID[41][1], "Sift spent no time on tasks in July."),
    (SAID[42][1], "That's 1 hour more than your daily average."),
    (SAID[43][1], "That's 40 minutes less than your daily average."),
    (SAID[44][1], "That's the same as your daily average."),
    (SAID[45][1], "Thursday was your biggest day: 5 hours."),
    (SAID[46][1], "August 14 was your biggest day: 5 hours."),
    (SAID[47][1], "March was your biggest month: 40 hours."),
    (SAID[48][1], "Your most-viewed hour began at 22:00: 40 minutes."),
    (SAID[49][1], "Each opened three times or more."),
    (SAID[50][1], "On Tuesday."),
    (SAID[51][1], "On Saturday."),
    (SAID[52][1], "Most came from Quillhouse: 40 files."),
    (SAID[53][1], "Your most-viewed Site in August was Quillhouse: 5 hours."),
    (SAID[54][1], "Your most-viewed tag in August was runway: 2 hours."),
    (SAID[55][1], "The people you viewed most in August."),
    (SAID[56][1], "Your most-viewed hour began at 10:00 PM: 40 minutes."),
    (
        SAID[57][1],
        "Your earliest start was 7:10 AM on Tuesday; your latest finish 1:40 AM on Saturday.",
    ),
    (SAID[58][1], "Your most-viewed hour began at 12:00 AM: 40 minutes."),
]


def said_as(what: str) -> say.Line | None:
    """A line of `SAID` by what it is."""
    return next(line for one, line in SAID if one == what)


PINNED += [
    (said_as("most-viewed file"), "The file you viewed most in August was harbour.mp4: 14 views."),
    (
        said_as("first and last, a day"),
        "Your first file today was harbour.mp4, at 9:10 AM, and your last lantern.jpg,"
        " on Saturday at 1:00 AM.",
    ),
    (
        said_as("first and last, a week"),
        "Your first file last week was harbour.mp4, on Monday at 08:00, and your last"
        " lantern.jpg, on Sunday at 23:00.",
    ),
    (
        said_as("a new favourite"),
        "A new favorite in August: harbour.mp4, viewed 5 times on the first day you opened it.",
    ),
    (
        said_as("rediscovered"),
        "Welcome back to harbour.mp4, viewed again in August after 214 days.",
    ),
    (
        said_as("longest session"),
        "Your longest visit in August ran 2 hours from opening Sift to closing it, across 34"
        " pages.",
    ),
    (
        said_as("longest session, no pages"),
        "Your longest visit today ran 40 minutes from opening Sift to closing it.",
    ),
    (said_as("downloads"), "Sift finished 40 downloads for you in August."),
    (said_as("Theater showed"), "Theater showed you 1,240 files in August."),
    (said_as("a definition"), "Files you opened outside Theater, each counted once."),
    (
        said_as("together, weeks"),
        "Over 9 weeks, the weeks you viewed Theater most were the weeks you starred the most"
        " files.",
    ),
    (
        said_as("together, the top person"),
        f"Over 12 days, the days you viewed {SOMEBODY} most were the days you starred the most"
        " files.",
    ),
    (
        said_as("together, months"),
        "Over 8 months, the months you had the most visits were the months you answered the"
        " most questions on Organize.",
    ),
]


@pytest.mark.parametrize(("line", "words"), PINNED, ids=[w[:40] for _, w in PINNED])
def test_the_statements_read_exactly(line: say.Line | None, words: str) -> None:
    assert say.text_of(line or ()) == words


@pytest.mark.parametrize(("what", "line"), SAID, ids=[one for one, _ in SAID])
def test_every_statement_is_a_sentence_in_sifts_words(what: str, line: say.Line | None) -> None:
    """Rules 5 and 7, and the house style: a sentence with its full stop, no decimal anywhere,
    "viewed" and never "watched", American spelling, and no dash bolting a second idea on."""
    words = say.text_of(line or ())
    assert words, what
    assert words.endswith("."), f"{what}: {words}"
    assert not re.search(r"\d\.\d", words), f"{what}: a decimal in {words}"
    assert "watch" not in words.lower(), f"{what}: {words}"
    assert not re.search(r"(?i)organis|recognis|colour|cancell", words), f"{what}: {words}"
    assert " -- " not in words and "\u2014" not in words, f"{what}: {words}"
    for word in ("only", "just", "great", "impressive", "too much", "should"):
        assert not re.search(rf"(?i)\b{word}\b", words), f"{what}: a judgement in {words}"


def test_every_builder_is_in_the_table() -> None:
    """A builder added and not listed in `SAID` fails here, so no sentence escapes the rules."""
    source = inspect.getsource(inspect.getmodule(test_every_builder_is_in_the_table))  # type: ignore[arg-type]
    table = source[source.index("SAID: tuple") : source.index("#: THE EXAMPLE SET")]
    modules = {"st": st, "statements_cards": statements_cards, "together": together}
    modules["definitions"] = definitions
    builders = [
        f"{alias}.{name}"
        for alias, module in modules.items()
        for name, function in inspect.getmembers(module, inspect.isfunction)
        if function.__module__ == module.__name__
        and not name.startswith("_")
        and "Line" in str(inspect.signature(function).return_annotation)
        and name not in ("statements_of",)
    ]
    assert len(builders) >= 30, builders
    missing = [name for name in builders if f"{name}(" not in table]
    assert not missing, missing


def test_the_floors_are_the_plans() -> None:
    """Rule 2: 10 sittings, 5 decisions, a share of no fewer than 10 things."""
    assert (st.SITTINGS_FLOOR, st.DECISIONS_FLOOR, st.SHARE_FLOOR) == (10, 5, 10)
    assert st.NOT_ENOUGH == "Not enough yet to say."


@pytest.mark.parametrize(
    ("ms", "said"),
    [
        (0, "less than a minute"),
        (29_999, "less than a minute"),
        (30_000, "1 minute"),
        (59 * MINUTE, "59 minutes"),
        (60 * MINUTE, "1 hour"),
        (85 * MINUTE, "1 hour 25 minutes"),
        (119 * MINUTE, "1 hour 59 minutes"),
        (2 * HOUR, "2 hours"),
        (11 * HOUR + 37 * MINUTE, "about 12 hours"),
        (23 * HOUR + 59 * MINUTE, "about 24 hours"),
        (24 * HOUR, "24 hours"),
        (41 * HOUR + 29 * MINUTE, "41 hours"),
        (41 * HOUR + 30 * MINUTE, "42 hours"),
        (1500 * HOUR, "1,500 hours"),
    ],
)
def test_a_length_of_time_is_rounded_like_a_person(ms: int, said: str) -> None:
    """Rule 5: minutes whole under an hour, hours whole from two, never a decimal."""
    assert st.duration(ms) == said


@pytest.mark.parametrize(
    "ms", [2 * HOUR + MINUTE, 11 * HOUR + 37 * MINUTE, 41 * HOUR + 29 * MINUTE]
)
def test_a_sentence_and_the_figure_above_it_never_say_two_lengths(ms: int) -> None:
    """A recap card says a length twice, "11 h 37 min" over "...: 12 hours.". Where the sentence's
    whole hours drop the minutes the figure keeps, it says "about"; where the figure is whole hours
    too, the two are the same number."""
    figure, sentence = st.duration_short(ms), st.duration(ms)
    if "min" in figure:
        assert sentence.startswith("about ")
        return
    days, _, hours, _ = figure.split()
    assert sentence == f"{int(days) * 24 + int(hours)} hours"


@pytest.mark.parametrize(
    ("value", "said"),
    [(7, "7"), (999, "999"), (1240, "1,240"), (100_000, "100,000"), (100_600, "about 101,000")],
)
def test_a_count_keeps_its_comma_and_says_about_past_a_hundred_thousand(
    value: int, said: str
) -> None:
    assert st.count(value) == said


def test_an_open_period_says_so_far_once_and_is_never_compared() -> None:
    """Rule 3: "so far" on an open period's headline; no comparison where either side is open."""
    parts = {"video": 5 * HOUR}
    assert "so far" in say.text_of(st.viewed(THIS_MONTH, 5 * HOUR, parts))
    assert "so far" not in say.text_of(st.viewed(AUGUST, 5 * HOUR, parts))
    assert st.compared(THIS_MONTH, 5 * HOUR, AUGUST, 9 * HOUR) is None
    assert st.compared(AUGUST, 5 * HOUR, THIS_MONTH, 9 * HOUR) is None


def test_mostly_is_said_only_over_a_majority_of_enough_sessions() -> None:
    """Rule 1: no adjective without its figure. "Mostly" means more than half, over at least ten."""
    assert "mostly" in say.text_of(st.theater(AUGUST, 5 * HOUR, WALL, 3 * HOUR, 10))
    assert "mostly" not in say.text_of(st.theater(AUGUST, 5 * HOUR, WALL, 3 * HOUR, 9))
    assert "mostly" not in say.text_of(st.theater(AUGUST, 6 * HOUR, WALL, 3 * HOUR, 20))


def test_a_share_of_fewer_than_ten_things_is_not_said() -> None:
    assert st.opened_first(9, PERSON, 5) is None
    assert st.opened_first(38, PERSON, 1) is None


def test_every_named_thing_is_a_link_where_it_sits() -> None:
    """The person and the wall are pieces with their kind and id, never words to search for."""
    person = st.most_viewed_person(THIS_MONTH, PERSON, HOUR, {"video": 1})
    (named,) = say.things_in(person)
    assert (named.kind, named.id, named.text) == ("person", "p1", SOMEBODY)
    wall = st.theater(AUGUST, 5 * HOUR, WALL, 4 * HOUR, 12)
    assert wall is not None
    (shown,) = say.things_in(wall)
    assert (shown.kind, shown.id, shown.href) == ("wall", "w1", "/theater?wall=w1")


def test_a_period_is_this_devices_calendar() -> None:
    """A week is Monday to Sunday; a month its own days; "all" from the first recorded day."""
    week = period_of("week", TODAY, TODAY, None)
    assert (week.start, week.end) == (date(2026, 9, 21), date(2026, 9, 27))
    assert (THIS_MONTH.start, THIS_MONTH.end) == (date(2026, 9, 1), date(2026, 9, 30))
    assert (EVER.start, EVER.end) == (date(2026, 3, 3), TODAY)
    assert THIS_MONTH.previous() == AUGUST
    assert THIS_MONTH.days_so_far == 25 and AUGUST.days_so_far == 31
    assert st.when(LAST_WEEK) == "last week" and st.when(AUGUST) == "in August"


def test_a_day_and_a_week_are_named_from_where_today_stands() -> None:
    """A day is today, yesterday, or its weekday and date; a week this, last, or the week of."""
    yesterday = period_of("day", date(2026, 9, 24), TODAY, None)
    tuesday = period_of("day", date(2026, 9, 22), TODAY, None)
    last_year = period_of("day", date(2025, 9, 22), TODAY, None)
    assert [st.when(one) for one in (TODAY_ONLY, yesterday, tuesday, last_year)] == [
        "today",
        "yesterday",
        "on Tuesday, September 22",
        "on Monday, September 22, 2025",
    ]
    assert [st.named_period(one) for one in (TODAY_ONLY, yesterday, tuesday)] == [
        "today",
        "yesterday",
        "September 22",
    ]
    older = period_of("week", date(2026, 9, 1), TODAY, None)
    assert st.when(older) == "in the week of August 31"
    assert [st.named_period(one) for one in (THIS_WEEK, LAST_WEEK, older)] == [
        "this week",
        "last week",
        "the week of August 31",
    ]


def test_a_year_and_everything_are_named_as_nouns_and_after_a_figure() -> None:
    this_year = period_of("year", TODAY, TODAY, None)
    last_year = period_of("year", date(2025, 6, 1), TODAY, None)
    assert (st.when(this_year), st.when(last_year), st.when(EVER)) == (
        "this year",
        "in 2025",
        "overall",
    )
    assert (st.named_period(last_year), st.named_period(EVER)) == ("2025", "this")


def test_everything_has_no_period_before_it_and_an_unknown_span_is_refused() -> None:
    assert EVER.previous() is None
    with pytest.raises(ValueError, match="not a span"):
        period_of("decade", TODAY, TODAY, None)


def test_a_part_in_another_unit_than_the_whole_keeps_its_own_unit() -> None:
    """ "3 hours: 2 of videos, 30 minutes of pictures": a bare 30 would read as 30 hours."""
    line = st.viewed(AUGUST, 3 * HOUR, {"video": 2 * HOUR, "image": 30 * MINUTE})
    assert say.text_of(line) == "You viewed 3 hours in August: 2 of videos, 30 minutes of pictures."


def test_a_part_in_the_wholes_unit_after_one_in_minutes_says_its_hours() -> None:
    """ "4 hours: 13 minutes of videos, 28 minutes of pictures, 3 in Theater" read the 3 as three
    minutes: a bare figure is only bare while the whole's unit is the last one said."""
    line = st.viewed(
        AUGUST,
        3 * HOUR + 33 * MINUTE,
        {"video": 13 * MINUTE, "image": 28 * MINUTE, "theater": 2 * HOUR + 52 * MINUTE},
    )
    assert say.text_of(line) == (
        "You viewed about 4 hours in August: 13 minutes of videos, 28 minutes of pictures, "
        "3 hours in Theater."
    )


def test_a_headline_with_no_part_says_the_whole_alone() -> None:
    assert say.text_of(st.viewed(AUGUST, 41 * HOUR, {})) == "You viewed 41 hours in August."


def test_two_closed_days_are_compared_with_the_day_before() -> None:
    tuesday = period_of("day", date(2026, 9, 22), TODAY, None)
    monday = period_of("day", date(2026, 9, 21), TODAY, None)
    line = st.compared(tuesday, 3 * HOUR, monday, HOUR)
    assert say.text_of(line or ()) == "That's 2 hours more than the day before."


def test_a_figure_of_nothing_says_nothing() -> None:
    """Rule 2's other half: a zero is not a sentence, and neither is a busiest weekday of a
    single day, which has no other day to be busier than."""
    assert st.opened(THIS_WEEK, 0) is None
    assert st.busiest_weekday(TODAY_ONLY, 4, HOUR, HOUR) is None
    assert st.busiest_weekday(AUGUST, 4, 0, HOUR) is None
    assert st.organized(THIS_MONTH, 0) is None
    assert st.opinions(0, 0) is None
    assert say.text_of(st.opinions(44, 0) or ()) == "You rated 44 files."
    # The tallest bar of an empty chart, and a single day's, which has one bar to be tallest.
    assert st.biggest(AUGUST, date(2026, 8, 14), 0) is None
    assert st.biggest(TODAY_ONLY, TODAY, 5 * HOUR) is None
    assert st.most_from(SITE, 0) is None
    # A single day's own start needs no day under it: it is that day.
    assert st.on_the_day(TODAY_ONLY, Moment(TODAY, 9 * 60)) is None
    assert statements_cards.downloaded(AUGUST, 0) is None
    assert statements_cards.theater_showed(AUGUST, 0) is None


def test_an_hour_mark_is_the_hour_alone_on_either_clock() -> None:
    """The mark under an hour's bar: short enough to stand a bar apart from the next."""
    assert [st.hour_mark(hour, "12") for hour in (0, 1, 11, 12, 13, 23)] == [
        "12 AM",
        "1 AM",
        "11 AM",
        "12 PM",
        "1 PM",
        "11 PM",
    ]
    assert [st.hour_mark(hour, "24") for hour in (0, 9, 23)] == ["00", "09", "23"]


def test_a_figure_carries_its_words_by_the_statements_own_rule() -> None:
    """A tile never reads "41.2 h": the figure's words are the server's, sent beside the number,
    in the short form a measured length takes on a tile ("1 d 17 h" where a sentence says "41
    hours"), and a time of day or a size is left to the screen."""
    from sift.slices.insights.models import Figure, NamedRow

    assert Figure(label="Viewed", value=148_000_000, unit="ms").said == "1 d 17 h"
    assert st.duration(148_000_000) == "41 hours"
    assert Figure(label="Files", value=1, unit="files", hidden_part=2).hidden_said == "2 files"
    assert Figure(label="First", value=430, unit="minute_of_day").said == ""
    row = NamedRow(piece={"text": "x"}, value=13, unit="views")  # type: ignore[arg-type]
    assert row.said == "13 views"
    assert "said" in Figure(label="Viewed", value=0, unit="count").model_dump()


#: The words that would say one measure caused another: a sentence putting two side by side says
#: they happened together, never why.
CAUSE_WORDS = ("because", "so", "led to", "made you", "thanks to", "due to")


@pytest.mark.parametrize(("what", "line"), SAID, ids=[one for one, _ in SAID])
def test_no_statement_says_one_thing_caused_another(what: str, line: say.Line | None) -> None:
    words = say.text_of(line or ()).lower()
    for cause in CAUSE_WORDS:
        # "so far" is the open period's, a time and never a joiner.
        assert not re.search(rf"\b{cause}\b(?! far)", words), f"{what}: {cause!r} in {words}"


def _labels_drawn(module: object) -> set[str]:
    """Every figure label a module's source draws: `label="..."`, `figure("...")`,
    `files_figure("...")`, and a definition asked for by name."""
    source = inspect.getsource(module)  # type: ignore[arg-type]
    found = set(re.findall(r'label="([^"]+)"', source))
    found |= set(re.findall(r'\.(?:files_)?figure\(\s*"([^"]+)"', source))
    found |= set(re.findall(r'_defined\("([^"]+)"\)', source))
    return found


def test_every_figure_the_page_draws_carries_its_definition() -> None:
    from sift.slices.insights import router, router_blocks

    labels = _labels_drawn(router) | _labels_drawn(router_blocks)
    assert {"Viewed", "Files opened", "In Theater", "Theater visits", "Tasks"} <= labels
    missing = sorted(label for label in labels if definitions.definition(label) is None)
    assert not missing, missing
    for label in labels:
        words = say.text_of(definitions.definition(label) or ())
        assert words.endswith(".") and "watch" not in words.lower(), words
        assert not re.search(r"(?i)\bsittings?\b", words), words


def test_a_label_with_no_definition_carries_none() -> None:
    assert definitions.definition("Not a figure") is None


def _run(pattern: str) -> list[int]:
    return [int(one) for one in pattern.split()]


#: Nine closed periods: Theater rising with starring, viewing against arrivals with no order.
SERIES = {
    "theater": _run("1 2 3 4 5 6 7 8 9"),
    "starred": _run("2 1 4 3 6 5 8 7 9"),
    "viewed": _run("5 1 9 2 8 3 7 4 6"),
    "added": _run("1 2 3 4 5 6 7 8 9"),
    "sessions": _run("1 2 3 4 5 6 7 8 9"),
    "decided": _run("1 2 3 4 5 6 7 8 9"),
}


def test_together_says_only_the_strong_pairs_strongest_first() -> None:
    pairs = together.together_pairs(SERIES)
    assert [(one.first, one.second, one.n) for one in pairs] == [
        ("sessions", "decided", 9),
        ("theater", "starred", 9),
    ]
    assert pairs[0].rho == pytest.approx(1.0)


def test_together_reads_only_periods_with_both_measures_and_at_least_eight() -> None:
    seven = {"sessions": _run("1 2 3 4 5 6 7 0 0"), "decided": _run("1 2 3 4 5 6 7 8 9")}
    assert together.together_pairs(seven) == []
    eight = {"sessions": _run("1 2 3 4 5 6 7 0 8"), "decided": _run("1 2 3 4 5 6 7 8 9")}
    assert [one.n for one in together.together_pairs(eight)] == [8]
    flat = {"sessions": _run("3 3 3 3 3 3 3 3"), "decided": _run("1 2 3 4 5 6 7 8")}
    assert together.together_pairs(flat) == []


def test_together_says_at_most_three() -> None:
    rising = _run("1 2 3 4 5 6 7 8 9 10")
    every = {measure: rising for measure in together.MEASURES}
    assert len(together.together_pairs(every)) == together.TOGETHER_MOST == 3
    assert (8, Fraction(3, 5)) == (together.TOGETHER_FLOOR, together.TOGETHER_AT)


def test_a_pair_of_the_top_person_names_them() -> None:
    with pytest.raises(ValueError, match="names the person"):
        together.together("day", together.Pair("person", "starred", 9, 0.9))


def test_the_spans_together_reads_are_closed_and_whole() -> None:
    """A month's days before today; a year's whole weeks; everything's whole months."""
    unit, days = together.closed_spans(THIS_MONTH)
    assert unit == "day" and len(days) == 24 and days[-1] == (date(2026, 9, 24),) * 2
    unit, weeks = together.closed_spans(period_of("year", TODAY, TODAY, None))
    assert unit == "week" and weeks[0] == (date(2026, 1, 5), date(2026, 1, 11))
    assert weeks[-1] == (date(2026, 9, 14), date(2026, 9, 20))
    unit, months = together.closed_spans(EVER)
    assert unit == "month" and months[0] == (date(2026, 4, 1), date(2026, 4, 30))
    assert months[-1] == (date(2026, 8, 1), date(2026, 8, 31))
    from_first = period_of("all", TODAY, TODAY, date(2026, 7, 1))
    assert together.closed_spans(from_first)[1][0] == (date(2026, 7, 1), date(2026, 7, 31))


def test_a_series_adds_each_span_up_and_reads_the_top_person_only_with_one() -> None:
    daily = {
        "2026-09-01": {("viewed_ms", ""): 5, ("viewed_ms:person", "p1"): 2},
        "2026-09-02": {("viewed_ms", ""): 7},
    }
    spans = [(date(2026, 9, 1), date(2026, 9, 2)), (date(2026, 9, 3), date(2026, 9, 3))]
    with_her = together.series_of(daily, spans, "p1")
    assert with_her["viewed"] == [12, 0] and with_her["person"] == [2, 0]
    assert "person" not in together.series_of(daily, spans, None)


def test_a_figure_takes_its_definition_from_its_label_unless_named() -> None:
    from sift.slices.insights.models import Figure

    assert say.text_of(definitions.definition("Deleted") or ()) in str(
        Figure(label="Deleted", value=3, unit="count").defines
    )
    assert Figure(label="Not a figure", value=3, unit="count").defines == []
