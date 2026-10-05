# SPDX-License-Identifier: AGPL-3.0-or-later
"""A collection's Files tab searched by the query language, in the collection's own order."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from sift.kernel.access.search_index import index_assets
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.slices.collections.tests.conftest import (
    Library,
    db_path,
    edit_items,
    make_collection,
    share,
    sign_in,
    write,
)

pytestmark = [pytest.mark.integration]

_INSERT_ASSET = """
INSERT INTO assets
    (id, identity, media_type, width, height, duration_ms, size_bytes, original_filename, added_at)
VALUES (?, ?, 'video', 1920, 1080, 4000, 10, ?, ?)
"""


def _index(client: TestClient) -> None:
    """Write the word index the way the app does, on a connection of this helper's own."""

    async def run() -> None:
        database = Database(db_path(client), readers=1)
        await database.connect()
        try:
            await index_assets(database, rebuild=True)
        finally:
            await database.close()

    asyncio.run(run())


def _long_collection(client: TestClient, library: Library, count: int) -> tuple[str, list[str]]:
    """A collection of `count` files, each made a second after the one before."""
    collection_id = make_collection(client, "Long reel")
    names = [
        "lantern dusk" if n == 7 else "harbour lantern" if n == 230 else f"clip {n:03d}"
        for n in range(count)
    ]
    ids = [new_id() for _ in names]
    statements: list[tuple[str, tuple[object, ...]]] = []
    for n, (asset_id, name) in enumerate(zip(ids, names, strict=True)):
        statements += [
            (_INSERT_ASSET, (asset_id, f"digest-{n}", f"{name}.mp4", 1_700_000_100 + n)),
            (
                "INSERT INTO asset_locations (id, asset_id, root_id, folder_id, rel_path,"
                " filename, first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, 0, 0)",
                (
                    new_id(),
                    asset_id,
                    library.root,
                    library.folder,
                    f"reel/{name}.mp4",
                    f"{name}.mp4",
                ),
            ),
            (
                "INSERT INTO collection_items (collection_id, asset_id, added_at) VALUES (?, ?, 0)",
                (collection_id, asset_id),
            ),
        ]
    write(db_path(client), statements)
    _index(client)
    return collection_id, ids


def test_words_find_a_match_past_the_two_hundredth_in_the_order_asked(
    client: TestClient, library: Library
) -> None:
    """The screen holds 200 rows; the words are answered over the whole collection, paged."""
    sign_in(client)
    collection_id, ids = _long_collection(client, library, 250)
    address = f"/api/collections/{collection_id}/items"

    found = client.get(address, params={"q": "lantern", "sort": "oldest", "limit": 200}).json()
    assert [item["id"] for item in found["items"]] == [ids[7], ids[230]]
    assert found["total"] == 2

    asked = {"q": "lantern", "sort": "oldest", "limit": 1, "offset": 1}
    second = client.get(address, params=asked).json()
    assert [item["id"] for item in second["items"]] == [ids[230]]
    assert second["total"] == 2

    assert client.get(address, params={"limit": 1}).json()["total"] == 250


def test_a_typed_field_narrows_as_a_pick_does(client: TestClient, library: Library) -> None:
    """`tags:` typed in the box is the language's, read by the same parser as the picks beside it."""
    sign_in(client)
    collection_id = make_collection(client, "Best of")
    edit_items(client, collection_id, library.all_ids)
    beach, dusk = new_id(), new_id()
    write(
        db_path(client),
        [
            ("INSERT INTO tags (id, name, created_at) VALUES (?, 'beach', 0)", (beach,)),
            ("INSERT INTO tags (id, name, created_at) VALUES (?, 'dusk', 0)", (dusk,)),
            ("INSERT INTO asset_tags (asset_id, tag_id) VALUES (?, ?)", (library.first, beach)),
            ("INSERT INTO asset_tags (asset_id, tag_id) VALUES (?, ?)", (library.third, beach)),
            ("INSERT INTO asset_tags (asset_id, tag_id) VALUES (?, ?)", (library.third, dusk)),
        ],
    )
    address = f"/api/collections/{collection_id}/items"

    typed = client.get(address, params={"q": "tags:beach"}).json()
    assert [item["id"] for item in typed["items"]] == [library.third, library.first]
    assert typed["total"] == 2

    both = client.get(address, params={"q": "tags:beach", "tags": "dusk"}).json()
    assert [item["id"] for item in both["items"]] == [library.third]


def test_a_guest_searching_learns_nothing_of_a_file_not_shared(
    client: TestClient, library: Library
) -> None:
    """Not by the total under words that match only what was not shared, and no row carries a
    stored place to count by."""
    guest = sign_in(client, "guest")
    sign_in(client)
    collection_id = make_collection(client, "Mixtape")
    edit_items(client, collection_id, library.all_ids)
    share(client, library.third, guest)
    _index(client)
    address = f"/api/collections/{collection_id}/items"
    assert "position" not in client.get(address).json()["items"][0]

    sign_in(client, "guest")
    hidden = client.get(address, params={"q": "first"})
    assert hidden.status_code == 200
    assert hidden.json() == {"items": [], "total": 0, "limit": 50, "offset": 0}

    shown = client.get(address, params={"q": "mp4"}).json()
    assert [item["id"] for item in shown["items"]] == [library.third]
    assert shown["total"] == 1
