# SPDX-License-Identifier: AGPL-3.0-or-later
"""Similarity pages: page two continues page one, and a file nothing was compared with sorts last.

Known distances go in as the filter's neighbours, as the model's answer arrives, and the read
orders by them. Several files share a distance and an arrival moment on purpose: the order is
total only through its last key, the id, and a page boundary inside a tie is where an order that
is not total shows a file twice and another never.
"""

from __future__ import annotations

import pytest

from sift.kernel.access import SIMILARITY, AssetFilter, Repository
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.testing.fixtures import Actors

pytestmark = pytest.mark.integration

_EPOCH = 1_700_000_000


async def _files(temp_db: Database, count: int) -> list[str]:
    """`count` files, every one arriving at the same moment, so nothing but the id breaks a tie."""
    root_id = new_id()
    await temp_db.execute(
        "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
        (root_id, "root", "/library", _EPOCH),
    )
    ids = []
    for at in range(count):
        asset_id = new_id()
        ids.append(asset_id)
        await temp_db.execute(
            "INSERT INTO assets (id, identity, media_type, original_filename, added_at) "
            "VALUES (?, ?, 'image', ?, ?)",
            (asset_id, f"digest-{at}", f"still-{at}.jpg", _EPOCH),
        )
        # A file is somewhere or it is on nobody's wall: the stored visibility is of its places.
        await temp_db.execute(
            "INSERT INTO asset_locations "
            "(id, asset_id, root_id, folder_id, rel_path, filename, first_seen_at, last_seen_at) "
            "VALUES (?, ?, ?, NULL, ?, ?, ?, ?)",
            (new_id(), asset_id, root_id, f"still-{at}.jpg", f"still-{at}.jpg", _EPOCH, _EPOCH),
        )
    return ids


async def test_page_two_continues_page_one_through_a_tie(
    access: Repository, actors: Actors, temp_db: Database
) -> None:
    ids = await _files(temp_db, 7)
    # Four at one distance, one nearer, two the model was never asked about (no description yet).
    compared = AssetFilter(
        neighbours=(
            (ids[3], 0.05),
            (ids[0], 0.30),
            (ids[1], 0.30),
            (ids[2], 0.30),
            (ids[4], 0.30),
        )
    )

    whole = await access.visible_assets(
        actors.admin, limit=7, offset=0, asset_filter=compared, sort=SIMILARITY
    )
    paged: list[str] = []
    for offset in (0, 3, 6):
        page = await access.visible_assets(
            actors.admin, limit=3, offset=offset, asset_filter=compared, sort=SIMILARITY
        )
        paged.extend(item.asset.id for item in page.items)

    order = [item.asset.id for item in whole.items]
    assert paged == order
    assert len(set(order)) == 7
    assert order[0] == ids[3]
    # The tie is broken by the id, newest-minted last, the same way on every page.
    assert order[1:5] == sorted([ids[0], ids[1], ids[2], ids[4]], reverse=True)
    # Never dropped: the two with no description are on the wall, behind every compared file.
    assert set(order[5:]) == {ids[5], ids[6]}
