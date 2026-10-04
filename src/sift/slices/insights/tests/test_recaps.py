# SPDX-License-Identifier: AGPL-3.0-or-later
"""Recaps: when one is due, the floor, the freeze, and the vault drawn when it is opened.

The rows are written straight into `insight_days`, as the helper would have added them up: what is
under test is what a recap makes of a closed period's rows, not how a day is added up (that is
`test_store.py`). The September example: the most-viewed person is hidden, the month is 41 hours
with 6 of them hers, and the recap is opened in the three vault states.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from sift.kernel.access import Role, Viewer
from sift.kernel.access.sentences import text_of
from sift.kernel.access.viewer import Concealment
from sift.kernel.wire import HistoryPiece
from sift.slices.insights import path, recaps, store
from sift.slices.insights.recaps import PeriodKind, due, period_of
from sift.slices.insights.recaps_models import NamedThing, Recap, Recipe
from sift.slices.insights.tests.conftest import World

pytestmark = pytest.mark.integration

HOUR = 3_600_000
SEPTEMBER_DAY = date(2026, 9, 10)
OCTOBER_FIRST = date(2026, 10, 1)
HER = "01HX00000000000000000000P1"
NEXT = "01HX00000000000000000000P2"
SITE = "01HX00000000000000000000S1"


def words(pieces: list[HistoryPiece]) -> str:
    return "".join(piece.lead + piece.text for piece in pieces)


async def row(world: World, day: date, metric: str, key: str, whole: int, hidden: int = 0) -> None:
    await world.run(
        "INSERT OR REPLACE INTO insight_days (user_id, day, metric, key, whole, hidden, split_at)"
        " VALUES (?, ?, ?, ?, ?, ?, 0)",
        (world.user, day.isoformat(), metric, key, whole, hidden),
    )


async def added_up_to(world: World, day: date) -> None:
    await world.run(
        "INSERT OR REPLACE INTO insight_progress (user_id, added_up_to) VALUES (?, ?)",
        (world.user, day.isoformat()),
    )


async def september(world: World, *, sittings: int = 212, o_hidden: int = 3) -> None:
    """The example September: 41 hours, 6 of them on a person the reader has hidden."""
    await world.run(
        "INSERT INTO people (id, name, created_at) VALUES (?, 'Elina Sorrel', 1)", (HER,)
    )
    await world.run(
        "INSERT INTO people (id, name, created_at) VALUES (?, 'Cassia Lynn', 1)", (NEXT,)
    )
    await world.run("INSERT INTO sites (id, name) VALUES (?, 'Another Studio')", (SITE,))
    day = SEPTEMBER_DAY
    await row(world, day, "viewed_ms", "", 41 * HOUR, 6 * HOUR)
    await row(world, day, "viewed_ms:kind", "video", 29 * HOUR, 6 * HOUR)
    await row(world, day, "viewed_ms:kind", "image", 9 * HOUR)
    await row(world, day, "viewed_ms:kind", "gif", 3 * HOUR)
    await row(world, day, "sittings", "", sittings, 20)
    await row(world, day, "viewed_ms:person", HER, 6 * HOUR, 6 * HOUR)
    await row(world, day, "viewed_ms:person", NEXT, 4 * HOUR)
    await row(world, day, "viewed_ms:site", SITE, 9 * HOUR)
    await row(world, day, "viewed_ms:weekday", "3", 41 * HOUR, 6 * HOUR)
    await row(world, day, "viewed_ms:hour", "22", 41 * HOUR, 6 * HOUR)
    await row(world, day, "rated", "", 44)
    await row(world, day, "starred", "", 19)
    await row(world, day, "o", "", 3, o_hidden)
    await row(world, day, "files_added", "", 142)
    await row(world, day, "faces_named", "", 30)
    await row(world, day, "decided", "", 120)
    await added_up_to(world, date(2026, 9, 30))


def reader(world: World, *, unlocked: bool, placeholder: bool = False) -> Viewer:
    return Viewer(
        id=world.user,
        role=Role.ADMIN,
        show_hidden=unlocked,
        concealment=Concealment.PLACEHOLDER if placeholder else Concealment.FULLY_GONE,
    )


async def made(world: World) -> str:
    (one,) = [
        recap
        for recap in await recaps.make_due(world.db, world.user, OCTOBER_FIRST)
        if recap.period == "month:2026-09"
    ]
    return one.id


async def open_as(world: World, recap_id: str, viewer: Viewer) -> Recap:
    found = await recaps.opened(world.db, viewer, recap_id, today=OCTOBER_FIRST)
    assert found is not None
    return found


def card(recap: Recap, kind: str) -> str | None:
    found = [one for one in recap.cards if one.kind == kind]
    return words(found[0].statement) if found else None


# --- when a recap is due ----------------------------------------------------------------------


def weeks(today: date, added: date | None) -> list[str]:
    return [p.key for p in due(today, added) if p.kind is PeriodKind.WEEK]


def test_the_due_rule_on_a_fixed_clock() -> None:
    sunday, monday = date(2026, 9, 27), date(2026, 9, 28)
    # The week of September 21 closes on Sunday; it is due on Monday once Sunday is added up.
    assert weeks(monday, sunday) == ["week:2026-W39"]
    # A Monday missed: the device was off, and on Tuesday the week is still due.
    assert weeks(date(2026, 9, 29), sunday) == ["week:2026-W39"]
    assert weeks(monday, sunday - date.resolution) == []
    assert weeks(sunday, sunday) == [period_of(PeriodKind.WEEK, date(2026, 9, 14)).key]
    # A whole week missed: the week before last is not made late.
    assert weeks(date(2026, 10, 5), date(2026, 10, 4)) == ["week:2026-W40"]
    # The first of the month, and the first of the year.
    assert {p.key for p in due(OCTOBER_FIRST, date(2026, 9, 30))} == {
        "week:2026-W39",
        "month:2026-09",
        "year:2025",
    }
    assert "year:2026" in {p.key for p in due(date(2027, 1, 1), date(2026, 12, 31))}
    assert due(monday, None) == []


def test_a_period_key_reads_back_as_its_days() -> None:
    week = recaps.period_from_key("week:2026-W39")
    assert week is not None and (week.first, week.last) == (date(2026, 9, 21), date(2026, 9, 27))
    assert week.span == "September 21 to 27, 2026"
    assert recaps.period_from_key("achievement:faces_100") is None


# --- making -----------------------------------------------------------------------------------


async def test_a_recap_is_made_once_and_never_remade(world: World) -> None:
    await september(world)
    first = await recaps.make_due(world.db, world.user, OCTOBER_FIRST)
    assert [one.period for one in first] == ["month:2026-09"]
    # The next night: nothing new is made, and the one made is the one there.
    assert await recaps.make_due(world.db, world.user, date(2026, 10, 2)) == []
    assert [one.id for one in await store.recaps_of(world.db, world.user)] == [first[0].id]


async def test_below_the_floor_no_recap_is_made(world: World) -> None:
    await september(world, sittings=recaps.FLOOR_SITTINGS - 1)
    assert await recaps.make_due(world.db, world.user, OCTOBER_FIRST) == []
    await row(world, SEPTEMBER_DAY, "sittings", "", recaps.FLOOR_SITTINGS, 0)
    assert [one.period for one in await recaps.make_due(world.db, world.user, OCTOBER_FIRST)] == [
        "month:2026-09"
    ]


async def test_a_recap_is_frozen_against_a_later_cleanup(world: World) -> None:
    """A February cleanup does not rewrite December."""
    await september(world)
    recap_id = await made(world)
    await world.run("DELETE FROM insight_days WHERE metric = 'viewed_ms:kind'")
    await world.run("UPDATE insight_days SET whole = 1 WHERE metric = 'viewed_ms'")
    opened = await open_as(world, recap_id, reader(world, unlocked=True))
    assert card(opened, "headline") == (
        "You viewed 41 hours in September: 29 of videos, 9 of pictures, 3 of GIFs."
    )


# --- the three lock states of the September example -------------------------------------------


async def test_unlocked_the_hidden_person_and_the_whole_month(world: World) -> None:
    await september(world)
    opened = await open_as(world, await made(world), reader(world, unlocked=True))
    assert card(opened, "headline") == (
        "You viewed 41 hours in September: 29 of videos, 9 of pictures, 3 of GIFs."
    )
    assert (
        card(opened, "top_person")
        == "Your most-viewed person in September was Elina Sorrel: 6 hours."
    )
    (top,) = [one for one in opened.cards if one.kind == "top_person"]
    assert top.hidden_things == [HER]
    assert card(opened, "o") == "You pressed O 3 times in September."
    assert opened.hidden_line is None
    assert opened.heading == f"September, in {len(opened.cards)} cards"
    assert card(opened, "closing") == "That was September."


async def test_the_cards_carry_the_portrait_the_top_five_the_hours_and_the_way_back(
    world: World,
) -> None:
    await september(world)
    await world.run("INSERT INTO tags (id, name, created_at) VALUES ('t1', 'runway', 1)")
    await row(world, SEPTEMBER_DAY, "viewed_ms:tag", "t1", 2 * HOUR)
    await row(world, SEPTEMBER_DAY, "viewed_ms:kind", "theater", HOUR)
    await row(world, SEPTEMBER_DAY, "sittings:kind", "theater", 2)
    opened = await open_as(world, await made(world), reader(world, unlocked=True))
    (top,) = [one for one in opened.cards if one.kind == "top_person"]
    assert top.cover == f"/api/people/{HER}/cover"
    (five,) = [one for one in opened.cards if one.kind == "top_five"]
    assert [(r.piece.text, r.value) for r in five.rows] == [
        ("Elina Sorrel", 6 * HOUR),
        ("Cassia Lynn", 4 * HOUR),
    ]
    assert card(opened, "top_tag") == "Your most-viewed tag in September was runway: 2 hours."
    assert card(opened, "theater") == "You spent 1 hour in Theater."
    (when,) = [one for one in opened.cards if one.kind == "when"]
    assert when.chart is not None and len(when.chart.bars) == 24
    assert when.chart.bars[22].parts[0].value == 41 * HOUR
    assert opened.first_day == "2026-09-01"
    # Locked, a hidden person takes the whole list with her: no next name moves up into it.
    locked = await open_as(world, opened.id, reader(world, unlocked=False))
    assert not [one for one in locked.cards if one.kind == "top_five"]


async def test_locked_leave_nothing_drops_her_card_without_a_hint(world: World) -> None:
    await september(world)
    opened = await open_as(world, await made(world), reader(world, unlocked=False))
    assert card(opened, "top_person") is None
    assert not any(one.hidden for one in opened.cards)
    assert card(opened, "headline") == (
        "You viewed 35 hours in September: 23 of videos, 9 of pictures, 3 of GIFs."
    )
    (headline,) = [one for one in opened.cards if one.kind == "headline"]
    assert headline.figure is not None
    assert (headline.figure.value, headline.figure.hidden_part) == (35 * HOUR, 0)
    assert opened.hidden_line is None
    text = json.dumps(opened.model_dump(mode="json"))
    assert HER not in text and "Elina" not in text


async def test_locked_placeholder_keeps_a_locked_tile_and_one_line(world: World) -> None:
    await september(world)
    opened = await open_as(
        world, await made(world), reader(world, unlocked=False, placeholder=True)
    )
    (tile,) = [one for one in opened.cards if one.kind == "top_person"]
    assert tile.hidden and tile.statement == [] and tile.figure is None and tile.hidden_things == []
    assert card(opened, "headline") == (
        "You viewed 35 hours in September: 23 of videos, 9 of pictures, 3 of GIFs."
    )
    assert opened.hidden_line == "Some of September is hidden. Unlock to include it."
    assert "Elina" not in json.dumps(opened.model_dump(mode="json"))


async def test_hiding_after_the_recap_was_made_moves_an_old_recap(world: World) -> None:
    """A recap freezes what happened, never what is hidden."""
    await september(world)
    await world.run("UPDATE insight_days SET hidden = 0")
    recap_id = await made(world)
    before = await open_as(world, recap_id, reader(world, unlocked=False))
    assert card(before, "top_person") is not None
    # She is hidden later, and the store's split says so for her row.
    await world.run(
        "UPDATE insight_days SET hidden = whole WHERE metric = 'viewed_ms:person' AND key = ?",
        (HER,),
    )
    after = await open_as(world, recap_id, reader(world, unlocked=False))
    assert card(after, "top_person") is None
    # Unlocking brings the card back.
    unlocked = await open_as(world, recap_id, reader(world, unlocked=True))
    assert card(unlocked, "top_person") is not None


async def test_the_song_viewed_longest_has_a_card_and_a_hidden_one_has_none(
    world: World,
) -> None:
    """The recap names the song on the files viewed longest, where it names a tag; a song whose
    files the reader may not see now (hidden, or every file of it in the vault) leaves no card
    while Hidden is shut, by the one rule every named card keeps."""
    await september(world)
    await world.run(
        "INSERT INTO songs (id, name, name_sort, created_at) VALUES ('s1', 'Harbour Lights', 'h', 1)"
    )
    await row(world, SEPTEMBER_DAY, "viewed_ms:song", "s1", 3 * HOUR)
    recap_id = await made(world)
    opened = await open_as(world, recap_id, reader(world, unlocked=True))
    assert card(opened, "top_song") == (
        "Your most-viewed song in September was Harbour Lights: 3 hours."
    )
    (top,) = [one for one in opened.cards if one.kind == "top_song"]
    assert top.cover == "/api/songs/s1/cover"
    await world.run(
        "UPDATE insight_days SET hidden = whole WHERE metric = 'viewed_ms:song' AND key = 's1'"
    )
    locked = await open_as(world, recap_id, reader(world, unlocked=False))
    assert card(locked, "top_song") is None


# --- the O card --------------------------------------------------------------------------------


async def test_the_o_card_is_absent_when_its_locked_count_is_zero(world: World) -> None:
    await september(world, o_hidden=3)
    recap_id = await made(world)
    for placeholder in (False, True):
        opened = await open_as(
            world, recap_id, reader(world, unlocked=False, placeholder=placeholder)
        )
        assert not [one for one in opened.cards if one.kind == "o"]


async def test_the_o_card_counts_only_what_is_not_hidden_while_locked(world: World) -> None:
    await september(world, o_hidden=1)
    opened = await open_as(world, await made(world), reader(world, unlocked=False))
    assert card(opened, "o") == "You pressed O twice in September."


# --- the announcement -------------------------------------------------------------------------


async def test_opening_or_dismissing_ends_the_announcement(world: World) -> None:
    await september(world)
    recap_id = await made(world)
    viewer = reader(world, unlocked=True)
    listed, announced = await recaps.heads(world.db, viewer, today=OCTOBER_FIRST)
    assert announced is not None and announced.id == recap_id
    assert listed[0].title == "Your September" and listed[0].span == "September 2026"
    assert await recaps.dismissed(world.db, viewer, recap_id)
    assert (await recaps.heads(world.db, viewer, today=OCTOBER_FIRST))[1] is None
    # Still in the list, to be opened whenever.
    assert [head.id for head in (await recaps.heads(world.db, viewer))[0]] == [recap_id]


async def test_somebody_elses_recap_is_not_found(world: World) -> None:
    await september(world)
    recap_id = await made(world)
    stranger = Viewer(id="u-stranger", role=Role.ADMIN, show_hidden=True)
    assert await recaps.opened(world.db, stranger, recap_id) is None
    assert not await recaps.dismissed(world.db, stranger, recap_id)


# --- the days a period says, and a key that is not one ------------------------------------------


def test_a_year_and_a_week_across_new_year_are_said_whole() -> None:
    year = recaps.period_from_key("year:2025")
    assert year is not None and (year.first, year.last, year.span) == (
        date(2025, 1, 1),
        date(2025, 12, 31),
        "2025",
    )
    turn = period_of(PeriodKind.WEEK, date(2026, 12, 31))
    assert turn.span == "December 28, 2026 to January 3, 2027"
    assert recaps.title_of(turn, date(2027, 1, 5)) == "Your week"


def test_a_key_that_names_no_real_period_reads_as_none() -> None:
    """A stored key is read back, so a malformed one is no period rather than a crash."""
    for key in ("week:2026-W99", "month:2026-13", "year:twenty", "decade:2020"):
        assert recaps.period_from_key(key) is None, key


# --- a card with nothing true to say is not said ------------------------------------------------

SEPTEMBER = period_of(PeriodKind.MONTH, SEPTEMBER_DAY)


def said(
    kind: str, figures: dict[str, int], today: date = OCTOBER_FIRST, *, compares: bool = False
) -> str | None:
    """What one card's builder says of these figures, or None where it says nothing."""
    recipe = Recipe(sources=figures, compares=compares)
    words = recaps.BUILDERS[kind](figures, recipe, SEPTEMBER, today)
    return None if words is None else text_of(words.statement)


def test_each_card_is_left_out_when_its_figures_say_nothing() -> None:
    src = recaps.source
    assert said("headline", {src("viewed_ms"): 0}) is None
    assert said("top_person", {}) is None
    # A ranked list of one is the top person's card again.
    one = Recipe(named=[NamedThing(kind="person", id="p1", name="Neve Alder")])
    assert recaps.BUILDERS["top_five"]({}, one, SEPTEMBER, OCTOBER_FIRST) is None
    assert said("top_site", {}) is None
    assert said("when", {src("viewed_ms:hour", "22"): HOUR}) is None
    # A busiest day with no viewing under it: locked, the hidden part can take the whole total.
    assert (
        said("when", {src("viewed_ms:weekday", "3"): HOUR, src("viewed_ms:hour", "22"): HOUR})
        is None
    )
    assert said("rated", {src("rated"): 0, src("starred"): 0}) is None
    assert said("sift_did", {}) is None
    assert said("compared", {src("viewed_ms"): HOUR}, compares=True) is None


def test_a_card_of_stars_alone_shows_the_stars() -> None:
    card = recaps.BUILDERS["rated"](
        {recaps.source("starred"): 4}, Recipe(sources={}), SEPTEMBER, OCTOBER_FIRST
    )
    assert card is not None
    assert (text_of(card.statement), card.figure, card.label) == (
        "You starred 4 files.",
        recaps.source("starred"),
        "Starred",
    )


def test_a_file_rated_on_several_days_of_a_span_is_one_file_rated() -> None:
    """The days keep a per-file row, and a span counts each file once however many of its days it
    was rated on, hidden where every rating of it in the span was of a hidden file."""
    rows = (
        store.DayRow("2026-09-02", "rated", "", 2, 0),
        store.DayRow("2026-09-02", "rated:file", "x", 1, 0),
        store.DayRow("2026-09-03", "rated:file", "x", 1, 0),
        store.DayRow("2026-09-03", "rated:file", "y", 1, 1),
        store.DayRow("2026-09-04", "rated:file", "z", 0, 0),
        store.DayRow("2026-10-02", "rated:file", "w", 1, 0),
    )
    totals = recaps._Totals(rows).of(date(2026, 9, 1), date(2026, 9, 30))
    assert totals[("rated", "")] == (2, 1)
    assert ("starred", "") not in totals


def test_a_period_still_open_is_never_compared() -> None:
    figures = {recaps.source("viewed_ms"): 2 * HOUR, recaps.source("viewed_ms", before=True): HOUR}
    assert said("compared", figures, today=date(2026, 9, 20), compares=True) is None
    assert said("compared", figures, compares=True) == "That's 1 hour more than August."


# --- what a recap is made of, and how it is drawn -----------------------------------------------


async def test_a_month_is_compared_with_the_one_before_when_both_were_recorded_whole(
    world: World,
) -> None:
    await september(world)
    # August recorded from its first day, and over the floor.
    await row(world, date(2026, 8, 1), "sittings", "", 40)
    await row(world, date(2026, 8, 1), "viewed_ms", "", 30 * HOUR)
    opened = await open_as(world, await made(world), reader(world, unlocked=True))
    assert card(opened, "compared") == "That's 11 hours more than August."
    (compared,) = [one for one in opened.cards if one.kind == "compared"]
    assert compared.figure is not None and compared.figure.value == 30 * HOUR


async def test_a_person_or_site_no_longer_there_names_no_card(world: World) -> None:
    await september(world)
    await world.run("DELETE FROM people WHERE id IN (?, ?)", (HER, NEXT))
    await world.run("DELETE FROM insight_days WHERE metric = 'viewed_ms:site'")
    opened = await open_as(world, await made(world), reader(world, unlocked=True))
    assert card(opened, "top_person") is None and card(opened, "top_site") is None
    assert card(opened, "headline") is not None


async def test_a_person_gone_is_passed_over_for_the_next(world: World) -> None:
    await september(world)
    await world.run("DELETE FROM people WHERE id = ?", (HER,))
    opened = await open_as(world, await made(world), reader(world, unlocked=True))
    assert card(opened, "top_person") == (
        "Your most-viewed person in September was Cassia Lynn: 4 hours."
    )


async def test_nothing_is_due_before_anything_is_added_up(world: World) -> None:
    assert await recaps.make_due(world.db, world.user, OCTOBER_FIRST) == []


async def test_locked_below_the_floor_the_recap_is_gone_or_all_tiles(world: World) -> None:
    """Locked, the floor is read over what is not hidden: under it, Show nothing has no recap at
    all (not listed, not opened, not dismissed) and placeholder mode keeps its tiles."""
    await september(world)
    recap_id = await made(world)
    await row(world, SEPTEMBER_DAY, "sittings", "", 212, 212 - recaps.FLOOR_SITTINGS + 1)
    gone = reader(world, unlocked=False)
    assert await recaps.opened(world.db, gone, recap_id, today=OCTOBER_FIRST) is None
    assert not await recaps.dismissed(world.db, gone, recap_id)
    assert await recaps.heads(world.db, gone, today=OCTOBER_FIRST) == ([], None)

    tiles = await open_as(world, recap_id, reader(world, unlocked=False, placeholder=True))
    assert [one.kind for one in tiles.cards if not one.hidden] == ["closing"]
    assert all(one.statement == [] for one in tiles.cards if one.hidden)
    assert "o" not in {one.kind for one in tiles.cards}
    assert tiles.hidden_line == "Some of September is hidden. Unlock to include it."


async def test_a_card_whose_every_figure_is_hidden_is_a_tile_or_absent(world: World) -> None:
    await september(world)
    await row(world, SEPTEMBER_DAY, "rated", "", 44, 44)
    await row(world, SEPTEMBER_DAY, "starred", "", 19, 19)
    recap_id = await made(world)
    tiles = await open_as(world, recap_id, reader(world, unlocked=False, placeholder=True))
    (rated,) = [one for one in tiles.cards if one.kind == "rated"]
    assert rated.hidden and rated.statement == []
    gone = await open_as(world, recap_id, reader(world, unlocked=False))
    assert card(gone, "rated") is None


async def test_a_recap_whose_body_cannot_be_read_opens_as_no_cards(world: World) -> None:
    kept = await store.write_recap(world.db, world.user, "month:2026-09", "not cards")
    opened = await open_as(world, kept.id, reader(world, unlocked=True))
    assert (opened.cards, opened.heading) == ([], "September, in 0 cards")


async def test_an_achievement_opens_as_the_card_it_was_filed_with(world: World) -> None:
    reached = path.Reached(
        "faces_100", date(2026, 9, 3), "You named your 100th face.", "Faces named", 100
    )
    await path.freeze(world.db, world.user, reached)
    (kept,) = await store.recaps_of(world.db, world.user)
    opened = await open_as(world, kept.id, reader(world, unlocked=False, placeholder=True))
    assert (opened.title, opened.span, opened.heading) == (
        "100 faces named",
        "September 3, 2026",
        "100 faces named",
    )
    assert [words(one.statement) for one in opened.cards] == ["You named your 100th face."]
    assert opened.hidden_line is None
    # An achievement is Your path's to list, never the recaps list's.
    assert await recaps.heads(world.db, reader(world, unlocked=True), today=OCTOBER_FIRST) == (
        [],
        None,
    )
