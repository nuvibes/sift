# SPDX-License-Identifier: AGPL-3.0-or-later
"""The year's own cards: its days, its first and last month, its people month by month, its five
files, and the closing card's six figures, each drawn for the reader's vault as it stands."""

from __future__ import annotations

from datetime import date

import pytest

from sift.slices.insights import recaps
from sift.slices.insights.recaps import PeriodKind, period_of
from sift.slices.insights.recaps_models import Recap, RecapCard
from sift.slices.insights.tests.conftest import World
from sift.slices.insights.tests.test_recaps import (
    HER,
    HOUR,
    NEXT,
    SITE,
    added_up_to,
    named_file,
    reader,
    row,
    words,
)

pytestmark = pytest.mark.integration

YEAR = period_of(PeriodKind.YEAR, date(2025, 5, 1))
NEW_YEAR = date(2026, 1, 1)
JANUARY, JUNE, DECEMBER = date(2025, 1, 10), date(2025, 6, 5), date(2025, 12, 12)


async def a_year(world: World) -> tuple[str, str]:
    """January on videos, a June mostly hidden, December on pictures; one person hidden."""
    await world.run(
        "INSERT INTO people (id, name, created_at) VALUES (?, 'Elina Sorrel', 1)", (HER,)
    )
    await world.run(
        "INSERT INTO people (id, name, created_at) VALUES (?, 'Cassia Lynn', 1)", (NEXT,)
    )
    await world.run("INSERT INTO sites (id, name) VALUES (?, 'Another Studio')", (SITE,))
    harbour = await named_file(world, "harbour.mp4")
    lantern = await named_file(world, "lantern.jpg")
    for day, viewed, hidden, kind in (
        (JANUARY, 10, 0, "video"),
        (JUNE, 30, 12, "video"),
        (DECEMBER, 20, 0, "image"),
    ):
        await row(world, day, "viewed_ms", "", viewed * HOUR, hidden * HOUR)
        await row(world, day, "viewed_ms:kind", kind, viewed * HOUR, hidden * HOUR)
        await row(world, day, "sittings", "", 40)
    await row(world, DECEMBER, "viewed_ms:kind", "video", 5 * HOUR)
    for day, hers, next_ in ((JANUARY, 4, 2), (JUNE, 4, 0), (DECEMBER, 0, 6)):
        if hers:
            await row(world, day, "viewed_ms:person", HER, hers * HOUR, hers * HOUR)
        if next_:
            await row(world, day, "viewed_ms:person", NEXT, next_ * HOUR)
    await row(world, JUNE, "viewed_ms:site", SITE, 3 * HOUR)
    await row(world, JUNE, "files_added", "", 100)
    await row(world, JUNE, "sittings:file", f"video:{harbour}", 9)
    await row(world, DECEMBER, "sittings:file", f"image:{lantern}", 4)
    await added_up_to(world, date(2025, 12, 31))
    return harbour, lantern


async def year_recap(world: World) -> str:
    (one,) = [
        recap
        for recap in await recaps.make_due(world.db, world.user, NEW_YEAR)
        if recap.period == "year:2025"
    ]
    return one.id


async def opened(
    world: World, recap_id: str, *, unlocked: bool, placeholder: bool = False
) -> Recap:
    viewer = reader(world, unlocked=unlocked, placeholder=placeholder)
    found = await recaps.opened(world.db, viewer, recap_id, today=NEW_YEAR)
    assert found is not None
    return found


def of(recap: Recap, kind: str) -> RecapCard | None:
    return next((one for one in recap.cards if one.kind == kind), None)


async def test_the_year_draws_its_own_cards(world: World) -> None:
    harbour, lantern = await a_year(world)
    recap = await opened(world, await year_recap(world), unlocked=True)
    heat = of(recap, "heatmap")
    assert heat is not None and heat.calendar is not None and heat.figure is not None
    assert (
        words(heat.statement)
        == "You viewed files on 3 days in 2025, the most on Thursday, June 5, 2025."
    )
    assert len(heat.calendar.days) == 365 and heat.figure.value == 30 * HOUR
    assert heat.figure.hidden_part == 12 * HOUR
    shifted = of(recap, "before_after")
    assert shifted is not None and shifted.chart is not None
    assert words(shifted.statement) == (
        "Your focus shifted: January was mostly videos, December mostly pictures."
    )
    assert shifted.chart.kind == "share"
    assert [bar.label for bar in shifted.chart.bars] == ["January", "December"]
    race = of(recap, "race")
    assert race is not None and race.chart is not None
    assert words(race.statement) == (
        "Elina Sorrel was the person you viewed most in 2 of 3 months in 2025."
    )
    assert [bar.label for bar in race.chart.bars] == ["Jan", "Jun", "Dec"]
    assert [row.value for row in race.rows] == [8 * HOUR, 8 * HOUR]
    mosaic = of(recap, "mosaic")
    assert mosaic is not None
    assert [row.piece.id for row in mosaic.rows] == [harbour, lantern]
    assert words(mosaic.statement) == "The files you viewed most in 2025."
    closing = of(recap, "closing")
    assert closing is not None and recap.cards[-1] is closing
    assert [(one.label, words(one.caption)) for one in closing.figures] == [
        ("Viewed", ""),
        ("Files arrived", ""),
        ("Top person", "Elina Sorrel"),
        ("Top Site", "Another Studio"),
        ("Busiest day", "Thursday, June 5, 2025"),
        ("Sessions", ""),
    ]
    assert all(one.defines for one in closing.figures)
    assert closing.hidden_things == [HER]


async def test_a_locked_year_leaves_out_what_it_may_not_say(world: World) -> None:
    await a_year(world)
    recap_id = await year_recap(world)
    recap = await opened(world, recap_id, unlocked=False)
    # The hidden person's cards are absent; the closing card keeps, without her.
    assert of(recap, "race") is None and of(recap, "top_person") is None
    closing = of(recap, "closing")
    assert closing is not None and closing.hidden_things == []
    assert [one.label for one in closing.figures] == [
        "Viewed",
        "Files arrived",
        "Top Site",
        "Busiest day",
        "Sessions",
    ]
    # June's hidden part is out of its day, so December is the busiest the reader is told of.
    heat = of(recap, "heatmap")
    assert heat is not None and heat.calendar is not None and heat.figure is not None
    assert heat.figure.value == 20 * HOUR and heat.figure.hidden_part == 0
    june = next(one for one in heat.calendar.days if one.day == JUNE.isoformat())
    assert june.value == 18 * HOUR
    assert words(closing.figures[3].caption) == "Friday, December 12, 2025"
    shown = await opened(world, recap_id, unlocked=False, placeholder=True)
    race = of(shown, "race")
    assert race is not None and race.hidden and race.chart is None


async def test_a_year_of_one_kind_says_much_the_same(world: World) -> None:
    await a_year(world)
    await row(world, DECEMBER, "viewed_ms:kind", "video", 25 * HOUR)
    recap = await opened(world, await year_recap(world), unlocked=True)
    shifted = of(recap, "before_after")
    assert shifted is not None
    assert words(shifted.statement) == "Much the same: mostly videos in January and in December."


async def test_the_first_and_last_carry_the_files_added_between(world: World) -> None:
    harbour, lantern = await a_year(world)
    await row(world, JANUARY, "first_file", harbour, 1_736_500_000)
    await row(world, DECEMBER, "last_file", lantern, 1_765_500_000)
    recap = await opened(world, await year_recap(world), unlocked=True)
    both = of(recap, "first_last")
    assert both is not None and both.figure is not None
    assert (both.figure.label, both.figure.value) == ("Files arrived", 100)


def test_a_part_of_the_period_round_trips() -> None:
    from sift.slices.insights.recaps_cards import span_of, within

    key = within("video", JANUARY, date(2025, 1, 31))
    assert key == "video@2025-01-10~2025-01-31"
    assert span_of(key) == ("video", JANUARY, date(2025, 1, 31))
    assert span_of("video") is None


async def test_a_year_of_little_draws_none_of_them(world: World) -> None:
    await row(world, JANUARY, "viewed_ms", "", 2 * HOUR)
    await row(world, JANUARY, "viewed_ms:kind", "video", 2 * HOUR)
    await row(world, JANUARY, "sittings", "", 12)
    await added_up_to(world, date(2025, 12, 31))
    cards = await recaps.build(world.db, world.user, YEAR, today=NEW_YEAR)
    assert cards is not None
    kinds = [one.kind for one in cards]
    assert "before_after" not in kinds and "race" not in kinds and "mosaic" not in kinds
    assert kinds[-1] == "closing" and "heatmap" in kinds
