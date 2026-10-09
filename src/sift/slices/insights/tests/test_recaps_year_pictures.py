# SPDX-License-Identifier: AGPL-3.0-or-later
"""The pictures a year's cards draw beyond their figures: the first and last month's files beside
their shares, and the top file on the closing card, never one the reader may not be shown."""

from __future__ import annotations

import pytest

from sift.slices.insights.tests.conftest import World
from sift.slices.insights.tests.test_recaps import named_file, row
from sift.slices.insights.tests.test_recaps_year import (
    DECEMBER,
    JANUARY,
    JUNE,
    a_year,
    of,
    opened,
    year_recap,
)

pytestmark = pytest.mark.integration

GONE = "01HX00000000000000000000G1"


def thumb(asset: str) -> str:
    return f"/api/assets/{asset}/thumb"


async def test_the_months_draw_their_files(world: World) -> None:
    harbour, lantern = await a_year(world)
    quarry = await named_file(world, "quarry.mp4")
    veiled = await named_file(world, "veiled.mp4", hidden=True)
    await row(world, JANUARY, "sittings:file", f"video:{harbour}", 6)
    await row(world, JANUARY, "sittings:file", f"video:{quarry}", 3)
    await row(world, JANUARY, "sittings:file", f"video:{veiled}", 30, 30)
    await row(world, JANUARY, "sittings:file", f"video:{GONE}", 20)
    await row(world, DECEMBER, "sittings:file", f"image:{lantern}", 8)
    recap = await opened(world, await year_recap(world), unlocked=True)
    shifted = of(recap, "before_after")
    assert shifted is not None
    # As many of each month: January's most viewed that is neither hidden nor gone, then December's.
    assert [one.cover for one in shifted.rows] == [thumb(harbour), thumb(lantern)]
    assert [(one.piece.text, one.value, one.unit) for one in shifted.rows] == [
        ("harbour.mp4", 6, "views"),
        ("lantern.jpg", 8, "views"),
    ]


async def test_the_close_draws_the_top_file(world: World) -> None:
    harbour, _ = await a_year(world)
    recap = await opened(world, await year_recap(world), unlocked=False)
    closing = of(recap, "closing")
    assert closing is not None and closing.cover == thumb(harbour)


async def test_the_close_draws_no_file_that_names_something_hidden(world: World) -> None:
    await a_year(world)
    veiled = await named_file(world, "veiled.mp4", hidden=True)
    await row(world, JUNE, "sittings:file", f"video:{veiled}", 40, 40)
    recap_id = await year_recap(world)
    unlocked = await opened(world, recap_id, unlocked=True)
    mosaic = of(unlocked, "mosaic")
    assert mosaic is not None and mosaic.hidden_things == [veiled]
    closing = of(unlocked, "closing")
    assert closing is not None and closing.cover is None
    locked = await opened(world, recap_id, unlocked=False)
    closing = of(locked, "closing")
    assert closing is not None and closing.cover is None
