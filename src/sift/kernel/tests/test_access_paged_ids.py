# SPDX-License-Identifier: AGPL-3.0-or-later
"""A page under an order no index walks sorts the ids, then reads its own rows: the same answer."""

from __future__ import annotations

import pytest

from sift.kernel.access import Effect, ObjectType, Repository, Viewer
from sift.kernel.access.repository import assets
from sift.kernel.access.repository.assets import SORT_KEYS, assets_query
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.testing.fixtures import Actors, World

_NOTHING_FILTERED = "\n1"

_SORTED_FIRST = sorted(SORT_KEYS - assets._INDEX_ORDERED)

#: (favorite, rating, o_count, view_count, pinned) per file, ties on purpose.
_OPINIONS = [
    (1, 5, 3, 2, 0),
    (0, 5, 3, 2, 1),
    (1, None, 0, 9, 0),
    (0, 2, 7, 0, 0),
    (1, 2, 7, 1, 1),
    (0, None, 1, 0, 0),
]


async def _shelf(temp_db: Database, world: World, actors: Actors) -> None:
    """Twelve files over both roots, with the admin's opinions on some and the guest's on others."""
    for index in range(12):
        asset_id = new_id()
        root = world.root if index % 2 else world.root_two
        await temp_db.execute(
            "INSERT INTO assets (id, identity, identity_version, media_type, added_at)"
            " VALUES (?, ?, 1, 'video', ?)",
            (asset_id, f"digest-page-{index}", index % 4),
        )
        await temp_db.execute(
            "INSERT INTO asset_locations"
            " (id, asset_id, root_id, folder_id, rel_path, filename, first_seen_at, last_seen_at)"
            " VALUES (?, ?, ?, NULL, ?, ?, 0, 0)",
            (new_id(), asset_id, root, f"shelf-{index}.mp4", f"shelf-{index}.mp4"),
        )
        who = actors.admin if index < 6 else actors.guest
        favorite, rating, o_count, views, pinned = _OPINIONS[index % 6]
        await temp_db.execute(
            "INSERT INTO asset_user_state (asset_id, user_id, favorite, rating, o_count,"
            " view_count, last_viewed_at, pinned, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)",
            (asset_id, who.id, favorite, rating, o_count, views, views or None, pinned),
        )


async def _page(
    access: Repository, viewer: Viewer, sort: str, offset: int, pinned_first: bool
) -> tuple[int, list[str]]:
    page = await access.visible_assets(
        viewer, sort=sort, seed=4242, limit=4, offset=offset, pinned_first=pinned_first
    )
    return page.total, [item.asset.id for item in page.items]


def test_an_order_no_index_walks_sorts_ids_before_it_reads_a_column() -> None:
    """The sorted read carries only the id; the columns are read for the page's rows alone."""
    for sort in SORT_KEYS:
        page = assets_query(sort, _NOTHING_FILTERED, arranged=False, counted=False)
        ids, _, columns = page.partition("\nSELECT a.*,")
        if sort in _SORTED_FIRST:
            assert "paged(paged_id) AS (\nSELECT a.id\n" in ids, f"{sort} sorts every column"
            assert "LIMIT :limit OFFSET :offset" in ids and "LIMIT" not in columns
            assert assets._PAGED_IDS in columns, f"{sort} reads columns past its page"
        else:
            assert "paged" not in page, f"{sort} has an index and needs no second read"
    arranged = assets_query("newest", _NOTHING_FILTERED, arranged=True, counted=False)
    assert "paged(paged_id)" in arranged, "an arranged wall sorts every row"


@pytest.mark.parametrize("sort", [*_SORTED_FIRST, "newest"])
async def test_a_page_read_from_its_ids_is_the_page_read_in_one_statement(
    access: Repository,
    actors: Actors,
    world: World,
    temp_db: Database,
    monkeypatch: pytest.MonkeyPatch,
    sort: str,
) -> None:
    """The same rows in the same order, with the same total and position, page by page, for an
    admin and for a guest who may see one root, with and without the pin."""
    await _shelf(temp_db, world, actors)
    await access.grant(ObjectType.ROOT, world.root, actors.guest.id, Effect.SHARE)
    for viewer in (actors.admin, actors.guest):
        for pinned_first in (False, True):
            for offset in (0, 4, 8, 12):
                two_step = await _page(access, viewer, sort, offset, pinned_first)
                with monkeypatch.context() as one_statement:
                    one_statement.setattr(assets, "_sorts_ids_first", lambda *_, **__: False)
                    assert await _page(access, viewer, sort, offset, pinned_first) == two_step
                for index, asset_id in enumerate(two_step[1]):
                    assert offset + index == await access.position_of(
                        viewer, asset_id, sort=sort, seed=4242, pinned_first=pinned_first
                    )
    seen = [
        (await access.visible_assets(one, limit=50)).total for one in (actors.admin, actors.guest)
    ]
    assert 0 < seen[1] < seen[0], "the guest no longer sees part of the library"
