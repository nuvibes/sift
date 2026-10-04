# SPDX-License-Identifier: AGPL-3.0-or-later
"""The walls' own questions asked of a small library: where a card sits on a filtered wall, the
newest file under a username, and what a card counts.

Each answer is read through the same statement the wall pages with, so a filter that reached the
wall and not the position would put a deep link on the wrong page with nothing failing.
"""

from __future__ import annotations

import pytest

from sift.kernel.access import ObjectType, Repository
from sift.kernel.access.constraints import AssetFilter, Where
from sift.kernel.access.repository.entities import CARD_TABS
from sift.testing.fixtures import Actors, World

pytestmark = pytest.mark.anyio


async def test_a_card_on_a_narrowed_wall_is_found_where_the_narrowed_wall_puts_it(
    access: Repository, world: World, actors: Actors
) -> None:
    narrowed = AssetFilter(where=Where("tags", (world.tag,)))
    assert await access.position_of_tag(actors.admin, world.tag, asset_filter=narrowed) == 0
    assert (
        await access.position_of_collection(actors.admin, world.collection, asset_filter=narrowed)
        == 0
    )
    assert (
        await access.position_of_photo_set(actors.admin, world.photo_set, asset_filter=narrowed)
        == 0
    )


async def test_the_newest_file_under_a_username_is_one_the_viewer_may_see(
    access: Repository, world: World, actors: Actors
) -> None:
    assert await access.newest_under_usernames(actors.admin, [""]) == {}
    assert await access.newest_under_usernames(actors.admin, [world.username, world.username]) == {
        world.username: world.solo
    }


async def test_a_card_counts_only_the_cells_its_kind_carries(
    access: Repository, world: World, actors: Actors
) -> None:
    with pytest.raises(ValueError, match="no card counts"):
        await access.card_counts(actors.admin, "asset", [world.solo])
    counts = await access.card_counts(actors.admin, "photo_set", [world.photo_set])
    # The set's file is in a Collection too, and a Photo Set's card has no Collections cell.
    assert set(counts[world.photo_set]) == {"people", "tags", "sites"}
    assert counts[world.photo_set]["people"] == 1
    # A page of nothing that can be bound as an id draws no card: an empty one would read as
    # "every row" in the scoped statement.
    assert await access.card_counts(actors.admin, "photo_set", [""]) == {}


async def test_a_partner_the_card_has_no_cell_for_is_not_counted_into_one(
    access: Repository, world: World, actors: Actors, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A card draws the cells its kind declares and no others, whatever else it reaches."""
    monkeypatch.setitem(CARD_TABS, "photo_set", ("tags", "sites"))
    counts = await access.card_counts(actors.admin, "photo_set", [world.photo_set])
    assert set(counts[world.photo_set]) == {"tags", "sites"}


async def test_a_site_mark_for_nothing_that_is_an_id_is_no_mark(
    access: Repository, actors: Actors
) -> None:
    assert await access.visible_marks(actors.admin, ObjectType.SITE, ["not an id"]) == {}
