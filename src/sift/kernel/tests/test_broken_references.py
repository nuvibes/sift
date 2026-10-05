# SPDX-License-Identifier: AGPL-3.0-or-later
"""A catalog row whose parent went while foreign keys were off is let go once, as its key says."""

from __future__ import annotations

import pytest

from sift.kernel.access import schema
from sift.kernel.db import Database
from sift.testing.fixtures import World

pytestmark = pytest.mark.anyio

_TAG = "INSERT INTO tags (id, name, cover_asset_id, parent_id, created_at) VALUES (?, ?, ?, ?, 0)"
_COLLECTION = "INSERT INTO collections (id, name, owner_id, created_at) VALUES (?, ?, ?, 0)"


async def test_a_broken_reference_goes_as_its_key_says_and_nothing_else_moves(
    temp_db: Database, world: World
) -> None:
    filed = await temp_db.fetch_all("SELECT asset_id, username_id FROM asset_usernames")
    async with temp_db.write() as connection:
        await connection.execute("PRAGMA foreign_keys=OFF")
        await connection.execute(
            "INSERT INTO asset_usernames (asset_id, username_id) VALUES ('gone', ?)",
            (world.username,),
        )
        await connection.execute(_COLLECTION, ("c-gone", "Gone", "nobody"))
        await connection.execute(_COLLECTION, ("c-shared", "Shared", None))
        await connection.execute(_TAG, ("t-left", "Harbour", "gone", "t-gone"))
        await connection.execute(_TAG, ("t-kept", "Lights", world.solo, "t-left"))
        await connection.execute("PRAGMA foreign_keys=ON")

    async with temp_db.write() as connection:
        await schema.initialize_catalog(connection, 89)

    assert await temp_db.fetch_all("SELECT * FROM pragma_foreign_key_check") == []
    left = await temp_db.fetch_all("SELECT asset_id, username_id FROM asset_usernames")
    assert sorted(tuple(one) for one in left) == sorted(tuple(one) for one in filed)
    kept = await temp_db.fetch_all("SELECT id FROM collections WHERE id LIKE 'c-%'")
    assert [str(one["id"]) for one in kept] == ["c-shared"]
    tags = await temp_db.fetch_all(
        "SELECT id, cover_asset_id, parent_id FROM tags WHERE id LIKE 't-%' ORDER BY id"
    )
    assert [tuple(one) for one in tags] == [
        ("t-kept", world.solo, "t-left"),
        ("t-left", None, None),
    ]
