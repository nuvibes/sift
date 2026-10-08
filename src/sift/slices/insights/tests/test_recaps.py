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
from sift.slices.insights import path, recaps, recaps_recipes, store
from sift.slices.insights.metrics import METRICS_VERSION
from sift.slices.insights.recaps import PeriodKind, due, period_of
from sift.slices.insights.recaps_models import NamedThing, Recap, RecapHead, Recipe
from sift.slices.insights.tests.conftest import World, at

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
        "day:2026-09-30",
        "week:2026-W39",
        "month:2026-09",
        "year:2025",
    }
    # Only the kinds asked for: a switch turned off makes its kind not due.
    asked = due(OCTOBER_FIRST, date(2026, 9, 30), [PeriodKind.MONTH])
    assert [p.key for p in asked] == ["month:2026-09"]
    # A day is due the next day, once it is added up, and only the day just closed.
    assert [p.key for p in due(date(2026, 9, 24), date(2026, 9, 23), [PeriodKind.DAY])] == [
        "day:2026-09-23"
    ]
    assert due(date(2026, 9, 25), date(2026, 9, 23), [PeriodKind.DAY]) == []
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
    for kind in ("top_file", "new_favourite", "rediscovered", "first_last", "session"):
        assert said(kind, {}) is None, kind
    assert said("downloads", {src("downloads"): 0}) is None
    assert said("theater_files", {}) is None
    assert said("alongside", {}) is None
    # A day whose usual comes to nothing is not compared with it.
    day = recaps.BUILDERS["compared"](
        {src("viewed_ms"): HOUR, src("days", scope=recaps.USUAL): 7},
        Recipe(compares=True),
        period_of(PeriodKind.DAY, TUESDAY),
        OCTOBER_FIRST,
    )
    assert day is None


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
    totals = recaps_recipes._Totals(rows).of(date(2026, 9, 1), date(2026, 9, 30))
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


# --- a day's recap, the switches, the announcement, alongside ----------------------------------

TUESDAY = date(2026, 9, 22)


async def a_tuesday(world: World) -> None:
    """Eight recorded days of an hour each, then a Tuesday of three hours."""
    for back in range(1, 9):
        day = date.fromordinal(TUESDAY.toordinal() - back)
        await row(world, day, "viewed_ms", "", HOUR)
        await row(world, day, "sittings", "", 2)
    await row(world, TUESDAY, "viewed_ms", "", 3 * HOUR)
    await row(world, TUESDAY, "viewed_ms:kind", "video", 3 * HOUR)
    await row(world, TUESDAY, "sittings", "", 12)
    await row(world, TUESDAY, "viewed_ms:hour", "22", 3 * HOUR)
    await row(world, TUESDAY, "viewed_ms:weekday", "1", 3 * HOUR)
    await row(world, TUESDAY, "starred", "", 2)
    early, late = await named_file(world, "dawn.mp4"), await named_file(world, "dusk.mp4")
    await row(world, TUESDAY, "first_file", early, at(7, 5, TUESDAY))
    # The evening ran past midnight: the last file closed on Wednesday.
    await row(world, TUESDAY, "last_file", late, at(1, 0, date(2026, 9, 23)))
    await added_up_to(world, TUESDAY)


async def test_a_day_is_recapped_the_next_day_against_the_usual_day(world: World) -> None:
    await a_tuesday(world)
    made_now = await recaps.make_due(world.db, world.user, date(2026, 9, 23))
    (day,) = [one for one in made_now if one.period == "day:2026-09-22"]
    found = await recaps.opened(
        world.db, reader(world, unlocked=True), day.id, today=date(2026, 9, 23)
    )
    assert found is not None
    assert (found.title, found.span) == ("Your Tuesday", "September 22, 2026")
    assert [one.kind for one in found.cards] == [
        "headline",
        "compared",
        "first_last",
        "when",
        "rated",
        "closing",
    ]
    assert card(found, "first_last") == (
        "Your first file yesterday was dawn.mp4, at 7:05 AM, and your last dusk.mp4, on Wednesday"
        " at 1:00 AM."
    )
    assert card(found, "compared") == "That's 2 hours more than your daily average."
    assert card(found, "when") == "Your most-viewed hour began at 10:00 PM: 3 hours."
    compared = next(one for one in found.cards if one.kind == "compared")
    assert compared.figure is not None and compared.figure.value == 3 * HOUR
    assert words(compared.figure.defines).startswith("Time files were in front of you")
    period = recaps.period_from_key("day:2026-09-22")
    assert period is not None and period.key == "day:2026-09-22"
    assert recaps.period_from_key("day:2026-02-30") is None


async def test_a_day_with_too_few_days_before_it_is_not_compared(world: World) -> None:
    await row(world, TUESDAY, "viewed_ms", "", 3 * HOUR)
    await row(world, TUESDAY, "sittings", "", 12)
    await added_up_to(world, TUESDAY)
    cards = await recaps.build(
        world.db, world.user, period_of(PeriodKind.DAY, TUESDAY), today=date(2026, 9, 23)
    )
    assert cards is not None and "compared" not in [one.kind for one in cards]


async def switch(world: World, kind: str, stored: str) -> None:
    await world.run(
        "INSERT INTO user_settings (user_id, key, value) VALUES (?, ?, ?)",
        (world.user, f"insights.recap_{kind}", stored),
    )


async def test_a_kind_switched_off_is_not_created_and_the_default_is_on(world: World) -> None:
    assert await recaps.kinds_on(world.db, world.user) == list(PeriodKind)
    await a_tuesday(world)
    await switch(world, "day", "false")
    # A value that does not read as a setting's is the default, as the settings service reads it.
    await switch(world, "week", "not json")
    assert await recaps.kinds_on(world.db, world.user) == [
        PeriodKind.WEEK,
        PeriodKind.MONTH,
        PeriodKind.YEAR,
    ]
    made_now = await recaps.make_due(world.db, world.user, date(2026, 9, 23))
    assert [one.period for one in made_now] == ["week:2026-W38"]


def test_every_switch_is_registered_on_by_default_for_each_user() -> None:
    from sift.kernel.settings_registry import registered_settings

    settings = registered_settings()
    for kind, key in recaps.SETTING_KEYS.items():
        one = settings[key]
        assert (one.scope.value, one.default, one.section) == ("user", True, "Insights")
        assert one.label == f"Create a recap of each {kind.value}"


def head(period: str, made_at: int) -> RecapHead:
    return RecapHead(id=period, period=period, title="", made_at=made_at, cards=3)


def test_the_longest_period_is_announced_first_and_a_day_for_one_day() -> None:
    now = 1_000_000_000
    hour = 3600
    month, week, day = (
        head("month:2026-09", now - 2 * hour),
        head("week:2026-W40", now - 2 * hour),
        head("day:2026-09-30", now - hour),
    )
    assert recaps.announced([day, week, month], now=now) == month
    assert recaps.announced([day, week], now=now) == week
    assert recaps.announced([day], now=now) == day
    stale = head("day:2026-09-29", now - 25 * hour)
    assert recaps.announced([stale], now=now) is None


async def a_month_together(world: World, *, hidden_star: int = 0) -> None:
    """Ten days of September on which Theater time and starring rose together."""
    for n in range(1, 11):
        day = date(2026, 9, n)
        await row(world, day, "viewed_ms", "", n * HOUR)
        await row(world, day, "viewed_ms:kind", "theater", n * HOUR)
        await row(world, day, "sittings", "", 3)
        await row(world, day, "starred", "", n, hidden_star if n == 5 else 0)
    await added_up_to(world, date(2026, 9, 30))


async def test_a_month_recap_says_what_rose_together(world: World) -> None:
    await a_month_together(world)
    recap = await open_as(world, await made(world), reader(world, unlocked=True))
    assert card(recap, "alongside") == (
        "Over 10 days, the days you viewed Theater most were the days you starred the most files."
    )


async def test_alongside_read_partly_over_hidden_things_says_nothing_while_locked(
    world: World,
) -> None:
    await a_month_together(world, hidden_star=2)
    recap_id = await made(world)
    assert card(await open_as(world, recap_id, reader(world, unlocked=True)), "alongside")
    assert card(await open_as(world, recap_id, reader(world, unlocked=False)), "alongside") is None


# --- the cards the new figures support ----------------------------------------------------------


async def named_file(world: World, title: str, *, hidden: bool = False) -> str:
    asset = await world.add_file(hidden=hidden)
    await world.run("UPDATE assets SET title = ? WHERE id = ?", (title, asset))
    return asset


async def a_month_of_files(world: World, *, harbour_hidden: bool = False) -> tuple[str, str]:
    """September with a file viewed most and come back to, a new favorite, a long session,
    downloads and a wall's files."""
    await september(world)
    harbour = await named_file(world, "harbour.mp4", hidden=harbour_hidden)
    lantern = await named_file(world, "lantern.jpg")
    hid = 1 if harbour_hidden else 0
    first, last = at(10, 0, date(2026, 9, 2)), at(23, 20, date(2026, 9, 20))
    await row(world, date(2026, 9, 2), "first_file", harbour, first, first * hid)
    # The first file not hidden, which the day keeps beside the first of all.
    await row(world, date(2026, 9, 2), "first_file", lantern, first + 60)
    await row(world, date(2026, 9, 3), "sittings:file", f"video:{harbour}", 14, 14 * hid)
    await row(world, date(2026, 9, 3), "rediscovered:file", harbour, 214, 214 * hid)
    await row(world, date(2026, 9, 3), "sittings:file", f"image:{lantern}", 5)
    await row(world, date(2026, 9, 3), "new_favourites:file", lantern, 5)
    await row(world, date(2026, 9, 20), "last_file", lantern, last)
    await row(world, date(2026, 9, 4), "session_ms:session", "s1", 90 * 60_000)
    await row(world, date(2026, 9, 5), "session_ms:session", "s1", 30 * 60_000)
    await row(world, date(2026, 9, 4), "session_pages:session", "s1", 34)
    await row(world, date(2026, 9, 6), "session_ms:session", "s2", 60 * 60_000)
    await row(world, date(2026, 9, 6), "downloads", "", 40)
    await row(world, date(2026, 9, 6), "download_bytes", "", 12_000_000)
    await row(world, date(2026, 9, 7), "theater_files", "", 1240)
    await row(world, date(2026, 9, 7), "theater_files", "w1", 10)
    # A file viewed more, and earlier, that has since left the library: never named.
    await row(world, date(2026, 9, 1), "sittings:file", "video:01HX0000000000000000000GONE", 99)
    await row(world, date(2026, 9, 1), "first_file", "01HX0000000000000000000GONE", first - 3600)
    return harbour, lantern


async def test_the_new_cards_say_what_the_new_figures_hold(world: World) -> None:
    harbour, _ = await a_month_of_files(world)
    recap = await open_as(world, await made(world), reader(world, unlocked=True))
    assert (
        card(recap, "top_file")
        == "The file you viewed most in September was harbour.mp4: 14 views."
    )
    assert card(recap, "new_favourite") == (
        "A new favorite in September: lantern.jpg, viewed 5 times on the first day you opened it."
    )
    assert card(recap, "rediscovered") == (
        "Welcome back to harbour.mp4, viewed again in September after 214 days."
    )
    assert card(recap, "first_last") == (
        "Your first file in September was harbour.mp4, on September 2 at 10:00 AM, and your last"
        " lantern.jpg, on September 20 at 11:20 PM."
    )
    assert (
        card(recap, "session")
        == "Your longest visit in September ran 2 hours from opening Sift to closing it,"
        " across 34 pages."
    )
    assert card(recap, "downloads") == "Sift finished 40 downloads for you in September."
    assert card(recap, "theater_files") == "Theater showed you 1,250 files in September."
    by_kind = {one.kind: one for one in recap.cards}
    assert by_kind["top_file"].cover == f"/api/assets/{harbour}/thumb"
    first_last = by_kind["first_last"]
    assert [(one.value, one.unit) for one in first_last.rows] == [
        (600, "minute_of_day"),
        (1400, "minute_of_day"),
    ]
    downloads = by_kind["downloads"].figure
    assert downloads is not None and (downloads.value, downloads.unit) == (12_000_000, "bytes")
    walls = by_kind["theater_files"].figure
    assert walls is not None and walls.value == 1250 and words(walls.defines)
    order = [one.kind for one in recap.cards]
    assert order.index("top_file") < order.index("session") < order.index("closing")


async def test_a_hidden_file_takes_its_cards_with_it_while_locked(world: World) -> None:
    harbour, _ = await a_month_of_files(world, harbour_hidden=True)
    recap_id = await made(world)
    locked = await open_as(world, recap_id, reader(world, unlocked=False))
    for kind in ("top_file", "rediscovered"):
        assert card(locked, kind) is None, kind
    assert card(locked, "new_favourite")
    # The first file of all is hidden: the first the reader may be told of is said instead.
    assert card(locked, "first_last") == (
        "Your first file in September was lantern.jpg, on September 2 at 10:01 AM, and your last"
        " lantern.jpg, on September 20 at 11:20 PM."
    )
    unlocked = await open_as(world, recap_id, reader(world, unlocked=True))
    top_file = next(one for one in unlocked.cards if one.kind == "top_file")
    assert top_file.figure is not None and top_file.figure.hidden_part == 14
    assert top_file.hidden_things == [harbour]


async def test_a_deck_holds_at_most_its_cards_and_always_closes(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    await a_month_of_files(world)
    monkeypatch.setattr(recaps_recipes, "DECK_MOST", 5)
    cards = await recaps.build(
        world.db, world.user, period_of(PeriodKind.YEAR, SEPTEMBER_DAY), today=date(2027, 1, 1)
    )
    assert cards is not None
    assert [one.kind for one in cards] == [
        "headline",
        "top_person",
        "new_favourite",
        "mosaic",
        "closing",
    ]
    assert len(recaps.DECKS[PeriodKind.YEAR]) > 18 and set(recaps.DECKS[PeriodKind.YEAR]) <= set(
        recaps.BUILDERS
    )


# --- a recap made again after a correction ------------------------------------------------------


async def test_a_recap_behind_a_correction_is_made_again_once(world: World) -> None:
    await september(world)
    recap_id = await made(world)
    await store.mark_seen(world.db, world.user, recap_id, at=123)
    await world.run("UPDATE recaps SET made_at = 1000 WHERE id = ?", (recap_id,))
    before = await store.recap(world.db, world.user, recap_id)
    assert before is not None
    # A version step found the month's viewing over-counted: the days are added up again.
    await world.run(
        "UPDATE recaps SET metrics_version = metrics_version - 1 WHERE id = ?", (recap_id,)
    )
    await row(world, SEPTEMBER_DAY, "viewed_ms", "", 30 * HOUR)
    achievement = await store.write_recap(world.db, world.user, "achievement:faces_100", "[]")
    await world.run(
        "UPDATE recaps SET metrics_version = metrics_version - 1 WHERE id = ?", (achievement.id,)
    )

    assert await recaps.remake_behind(world.db, world.user, OCTOBER_FIRST, None) == []
    await recaps.make_due(world.db, world.user, OCTOBER_FIRST)
    after = await store.recap(world.db, world.user, recap_id)
    assert after is not None
    assert (after.id, after.made_at, after.seen_at) == (before.id, before.made_at, 123)
    assert after.metrics_version == METRICS_VERSION
    # One History line, Sift's, on the same write: "Sift re-counted your September 2026 recap ...".
    lines = await world.db.fetch_all(
        "SELECT d.verb, d.actor_kind, d.actor_id, s.kind, s.subject_id, s.name"
        " FROM workbench_decisions d JOIN workbench_decision_subjects s ON s.decision_id = d.id"
        " WHERE d.verb = 'recounted'"
    )
    assert [tuple(one) for one in lines] == [
        ("recounted", "sift", "insights", "recap", recap_id, "September 2026 recap")
    ]
    recap = await open_as(world, recap_id, reader(world, unlocked=True))
    headline = card(recap, "headline")
    assert headline is not None and headline.startswith("You viewed 30 hours in September")
    # Once: the second run finds nothing behind, and the achievement is what it was.
    assert await recaps.remake_behind(world.db, world.user, OCTOBER_FIRST, date(2026, 9, 30)) == []
    assert len(await world.db.fetch_all("SELECT id FROM workbench_decisions")) == 1
    kept = await store.recap(world.db, world.user, achievement.id)
    assert kept is not None and kept.metrics_version == METRICS_VERSION - 1


async def test_a_recap_is_made_again_only_once_its_days_are_added_up_again(world: World) -> None:
    await september(world)
    recap_id = await made(world)
    await world.run("UPDATE recaps SET metrics_version = 0 WHERE id = ?", (recap_id,))
    assert await recaps.remake_behind(world.db, world.user, OCTOBER_FIRST, date(2026, 9, 29)) == []
    # Below the floor once corrected, it is still the period's recap, said over what is there.
    await row(world, SEPTEMBER_DAY, "sittings", "", 3)
    assert await recaps.remake_behind(world.db, world.user, OCTOBER_FIRST, date(2026, 9, 30)) == [
        "month:2026-09"
    ]


async def test_alongside_in_a_recap_names_the_top_person_where_it_is_them(world: World) -> None:
    """Ten days on which the time on the top person and starring rose together, Theater less so."""
    await world.run(
        "INSERT INTO people (id, name, created_at) VALUES (?, 'Elina Sorrel', 1)", (HER,)
    )
    noisy = [3, 1, 2, 5, 4, 7, 6, 9, 10, 8]
    for n in range(1, 11):
        day = date(2026, 9, n)
        await row(world, day, "viewed_ms", "", n * HOUR)
        await row(world, day, "viewed_ms:kind", "theater", noisy[n - 1] * HOUR)
        await row(world, day, "viewed_ms:person", HER, n * HOUR)
        await row(world, day, "sittings", "", 3)
        await row(world, day, "starred", "", n)
    await added_up_to(world, date(2026, 9, 30))
    recap = await open_as(world, await made(world), reader(world, unlocked=True))
    assert card(recap, "alongside") == (
        "Over 10 days, the days you viewed Elina Sorrel most were the days you starred the most"
        " files."
    )
