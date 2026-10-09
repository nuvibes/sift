# SPDX-License-Identifier: AGPL-3.0-or-later
"""A recap's cards as drawn: the cover card's shares, what a reader took out before sharing, and
the hue a card takes from its top file's still."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from PIL import Image

from sift.kernel.content import ContentStore, DerivativeKind
from sift.slices.insights import recaps, recaps_draw, schema, store
from sift.slices.insights.recaps_models import RecapCard
from sift.slices.insights.recaps_periods import period_from_key
from sift.slices.insights.recaps_recipes import wall_of
from sift.slices.insights.tests.conftest import World, at
from sift.slices.insights.tests.test_recaps import (
    HER,
    HOUR,
    OCTOBER_FIRST,
    SEPTEMBER_DAY,
    SITE,
    made,
    open_as,
    reader,
    row,
    september,
)


def still(*colours: tuple[int, int, int]) -> bytes:
    """A small JPEG in bands of the given colours."""
    image = Image.new("RGB", (30, 30 * len(colours)))
    for band, colour in enumerate(colours):
        image.paste(colour, (0, 30 * band, 30, 30 * (band + 1)))
    out = BytesIO()
    image.save(out, "JPEG")
    return out.getvalue()


def apart(one: int, other: int) -> int:
    turn = abs(one - other) % 360
    return min(turn, 360 - turn)


def test_a_still_gives_the_hue_it_mostly_is() -> None:
    red = recaps_draw.accent_hue(still((200, 40, 40)))
    blue = recaps_draw.accent_hue(still((40, 90, 220)))
    assert red is not None and apart(red, 28) <= 6
    assert blue is not None and apart(blue, 262) <= 6
    # Mostly blue with a band of grey: still blue.
    mixed = recaps_draw.accent_hue(still((40, 90, 220), (40, 90, 220), (128, 128, 128)))
    assert mixed is not None and apart(mixed, 262) <= 6


def test_a_grey_still_or_not_a_picture_gives_none() -> None:
    assert recaps_draw.accent_hue(still((128, 128, 128), (30, 30, 30))) is None
    assert recaps_draw.accent_hue(b"not a picture") is None


@pytest.mark.integration
async def test_the_cover_card_carries_its_time_by_kind_as_shares(world: World) -> None:
    await september(world)
    opened = await open_as(world, await made(world), reader(world, unlocked=True))
    (cover,) = [one for one in opened.cards if one.kind == "headline"]
    assert cover.chart is not None and cover.chart.kind == "share"
    assert [(part.kind, part.value) for part in cover.chart.bars[0].parts] == [
        ("video", 29 * HOUR),
        ("image", 9 * HOUR),
        ("gif", 3 * HOUR),
        ("theater", 0),
    ]
    # Locked, the hidden part is out of the shares as it is out of the figure.
    locked = await open_as(world, opened.id, reader(world, unlocked=False))
    (cover,) = [one for one in locked.cards if one.kind == "headline"]
    assert cover.chart is not None and cover.chart.bars[0].parts[0].value == 23 * HOUR


@pytest.mark.integration
async def test_what_a_reader_took_out_is_absent_and_said_around(world: World) -> None:
    await september(world)
    recap_id = await made(world)
    found = await world.db.fetch_one("SELECT body FROM recaps WHERE id = ?", (recap_id,))
    assert found is not None
    cards = recaps_draw.kept_cards(str(found["body"]))
    period = period_from_key("month:2026-09")
    viewer = reader(world, unlocked=True)

    def drawn(left_out: frozenset[str]) -> dict[str, RecapCard]:
        out = recaps_draw._draw(cards, period, viewer, {}, OCTOBER_FIRST, left_out)
        assert out is not None
        return {card.kind: card for card in out.cards}

    whole = drawn(frozenset())
    assert {"top_person", "top_five", "top_site"} <= whole.keys()
    without = drawn(frozenset({HER, SITE}))
    # A card naming what was taken out is gone, with no locked tile in its place.
    assert {"top_person", "top_five", "top_site"}.isdisjoint(without.keys())
    assert all(not card.hidden for card in without.values())
    # The cards that name nothing are as they were.
    assert without["headline"].figure == whole["headline"].figure


# --- the colour, read once as the recap is made --------------------------------------------------


class _Cache:
    """A content store holding one thumbnail, of one file, at one place."""

    def __init__(self, asset_id: str, path: Path) -> None:
        self.asset_id, self.path = asset_id, path

    async def derivatives(self, asset_id: str) -> list[Any]:
        if asset_id != self.asset_id:
            return []
        return [SimpleNamespace(kind=DerivativeKind.THUMB, rel_cache_path="thumb.jpg")]

    async def derivative_at(self, rel_cache_path: str) -> Path | None:
        return self.path if self.path.exists() else None


async def _with_a_top_file(world: World) -> str:
    asset = await world.add_file()
    await row(world, SEPTEMBER_DAY, "sittings:file", f"video:{asset}", 9)
    return asset


@pytest.mark.integration
async def test_every_card_takes_the_hue_of_the_most_viewed_file(
    world: World, tmp_path: Path
) -> None:
    await september(world)
    asset = await _with_a_top_file(world)
    (tmp_path / "thumb.jpg").write_bytes(still((200, 40, 40)))
    cache = cast(ContentStore, _Cache(asset, tmp_path / "thumb.jpg"))
    (one,) = [
        made_one
        for made_one in await recaps.make_due(world.db, world.user, OCTOBER_FIRST, content=cache)
        if made_one.period == "month:2026-09"
    ]
    opened = await open_as(world, one.id, reader(world, unlocked=True))
    hues = {card.accent_hue for card in opened.cards}
    assert len(hues) == 1
    (hue,) = hues
    assert hue is not None and apart(hue, 28) <= 6
    # A file the reader took out of the recap gives it no colour either.
    assert await store.leave_out(world.db, world.user, one.id, [asset])
    taken = await open_as(world, one.id, reader(world, unlocked=True))
    assert {card.accent_hue for card in taken.cards} == {None}
    assert await store.leave_out(world.db, world.user, one.id, [])
    # Locked, a hidden most-viewed file gives nothing, not even its colour.
    await world.set_hidden(asset, True)
    locked = await open_as(world, one.id, reader(world, unlocked=False))
    assert {card.accent_hue for card in locked.cards} == {None}


@pytest.mark.integration
async def test_no_still_or_no_cache_is_no_colour(world: World, tmp_path: Path) -> None:
    await september(world)
    asset = await _with_a_top_file(world)
    gone = cast(ContentStore, _Cache(asset, tmp_path / "never.jpg"))
    for content in (gone, None):
        assert await recaps_draw.still_hue(gone, "another") is None
        # A place that is not a file reads as no still.
        assert (
            await recaps_draw.still_hue(cast(ContentStore, _Cache(asset, tmp_path)), asset) is None
        )
        made_now = await recaps.make_due(world.db, world.user, OCTOBER_FIRST, content=content)
        for one in made_now:
            opened = await open_as(world, one.id, reader(world, unlocked=True))
            assert {card.accent_hue for card in opened.cards} == {None}


# --- the Theater wall in miniature -----------------------------------------------------------------


SHAPE = (
    '{"rows":2,"cols":3,"slots":[{"row":0,"col":0,"row_span":2,"col_span":2},'
    '{"row":0,"col":2,"row_span":1,"col_span":1},{"row":1,"col":2,"row_span":1,"col_span":1}]}'
)


@pytest.mark.integration
async def test_the_theater_card_draws_the_most_used_wall_with_a_file_in_each_cell(
    world: World,
) -> None:
    await september(world)
    await world.run(
        "INSERT INTO theater_arrangements (id, user_id, name, layout, created_at, updated_at,"
        " shape) VALUES ('wall-one', ?, 'Nine up', 'custom', 1, 1, ?)",
        (world.user, SHAPE),
    )
    first, second = await world.add_file(), await world.add_file()
    await world.wall("evening", at(21, day=SEPTEMBER_DAY), at(23, day=SEPTEMBER_DAY), "wall-one")
    for asset, times in ((first, 3), (second, 1)):
        for n in range(times):
            await world.sit(
                asset,
                at(21, n, day=SEPTEMBER_DAY),
                60_000,
                screen="theater",
                theater_session="evening",
            )
        await row(world, SEPTEMBER_DAY, "sittings:file", f"video:{asset}", times)
    await row(world, SEPTEMBER_DAY, "theater_files", "wall-one", 2)
    recap_id = await made(world)

    opened = await open_as(world, recap_id, reader(world, unlocked=True))
    (wall,) = [card for card in opened.cards if card.kind == "theater_files"]
    assert wall.wall is not None and (wall.wall.rows, wall.wall.cols) == (2, 3)
    assert [cell.cover for cell in wall.rows] == [
        f"/api/assets/{first}/thumb",
        f"/api/assets/{second}/thumb",
    ]
    # Locked, a file whose every sitting is hidden leaves its cell empty: the wall is said around it.
    await world.set_hidden(first, True)
    await row(world, SEPTEMBER_DAY, "sittings:file", f"video:{first}", 3, hidden=3)
    locked = await open_as(world, recap_id, reader(world, unlocked=False))
    (wall,) = [card for card in locked.cards if card.kind == "theater_files"]
    assert [cell.cover for cell in wall.rows] == [f"/api/assets/{second}/thumb"]


def test_a_wall_with_no_shape_is_an_even_grid_of_its_cells() -> None:
    four = wall_of(None, 4)
    assert four is not None and (four.rows, four.cols, len(four.slots)) == (2, 2, 4)
    assert wall_of("not a shape", 0) is None
    too_many = (
        '{"rows":1,"cols":12,"slots":['
        + ",".join(f'{{"row":0,"col":{n},"row_span":1,"col_span":1}}' for n in range(12))
        + "]}"
    )
    three = wall_of(too_many, 3)
    assert three is not None and len(three.slots) == 3
    assert wall_of('{"rows": 0}', 2) is not None


# --- what a reader took out, kept --------------------------------------------------------------


@pytest.mark.integration
async def test_version_eight_gives_every_recap_an_empty_left_out(world: World) -> None:
    kept = await store.write_recap(world.db, world.user, "week:2026-W11", "[]")
    await world.run("ALTER TABLE recaps DROP COLUMN left_out")
    async with world.db.write() as connection:
        await schema.initialize(connection, 7)
    again = await store.recap(world.db, world.user, kept.id)
    assert again is not None and again.left_out == ()
    await world.run("UPDATE recaps SET left_out = 'not a list'")
    again = await store.recap(world.db, world.user, kept.id)
    assert again is not None and again.left_out == ()
