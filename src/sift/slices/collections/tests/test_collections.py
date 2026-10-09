# SPDX-License-Identifier: AGPL-3.0-or-later
"""Building, filling and deleting a collection.

The claims worth breaking the build over are here rather than spread out: that filling one moves
nothing on disk, that deleting one takes its rows and never its files, and that a collection cannot
be used to count what somebody was not shown.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from sift.kernel.access import Concealment, Role, Viewer
from sift.kernel.ids import new_id
from sift.slices.auth import current_viewer
from sift.slices.collections.tests.conftest import (
    NAMES,
    NEVER_EXISTED,
    Library,
    db_path,
    edit_items,
    grant_on_collection,
    grants_naming,
    item_ids,
    make_collection,
    read,
    set_vault,
    share,
    sign_in,
    vault_asset,
    write,
)
from sift.testing.library import a_png

pytestmark = [pytest.mark.integration]


# --- building one --------------------------------------------------------------------------


def test_a_collection_is_made_empty_and_shows_up_in_the_list(client: TestClient) -> None:
    sign_in(client)
    collection_id = make_collection(client, "Best of")

    listed = client.get("/api/collections").json()["items"]
    assert [entry["id"] for entry in listed] == [collection_id]
    assert listed[0]["name"] == "Best of"
    assert listed[0]["item_count"] == 0
    assert listed[0]["cover_asset_id"] is None


def test_a_pinned_file_comes_first_under_every_order(client: TestClient, library: Library) -> None:
    """A collection is sorted like every wall of files, and what this user pinned stands before
    everything else under each order; among themselves the pinned files keep the order chosen."""
    sign_in(client)
    collection_id = make_collection(client, "Mixtape")
    edit_items(client, collection_id, library.all_ids)

    def drawn(sort: str | None = None) -> list[str]:
        params = {} if sort is None else {"sort": sort}
        response = client.get(f"/api/collections/{collection_id}/items", params=params)
        assert response.status_code == 200, response.text
        return [str(one["id"]) for one in response.json()["items"]]

    newest = [library.third, library.second, library.first]
    assert drawn() == newest == drawn("newest")
    assert drawn("oldest") == newest[::-1]

    assert client.put(f"/api/assets/{library.second}/pin", json={"pinned": True}).status_code == 200
    assert drawn() == [library.second, library.third, library.first]
    assert drawn("oldest") == [library.second, library.first, library.third]

    assert client.put(f"/api/assets/{library.first}/pin", json={"pinned": True}).status_code == 200
    assert drawn() == [library.second, library.first, library.third]
    assert drawn("oldest") == [library.first, library.second, library.third]


def test_a_collection_takes_every_order_a_wall_of_files_takes(
    client: TestClient, library: Library
) -> None:
    """Browse's default, its orders and its shuffle, and an order it does not know is refused."""
    sign_in(client)
    collection_id = make_collection(client, "Mixtape")
    edit_items(client, collection_id, [library.first, library.third, library.second])

    def drawn(**params: object) -> list[str]:
        response = client.get(f"/api/collections/{collection_id}/items", params=params)
        assert response.status_code == 200, response.text
        return [str(one["id"]) for one in response.json()["items"]]

    browse = [one["id"] for one in client.get("/api/assets").json()["items"]]
    assert drawn() == browse == [library.third, library.second, library.first]
    assert drawn(sort="name_az") == [library.first, library.second, library.third]
    assert drawn(sort="name_za") == [library.third, library.second, library.first]
    shuffled = drawn(sort="random", seed=7)
    assert shuffled == drawn(sort="random", seed=7)
    assert sorted(shuffled) == sorted(library.all_ids)

    refused = client.get(f"/api/collections/{collection_id}/items", params={"sort": "arranged"})
    assert refused.status_code == 422


def test_a_collection_tile_says_whether_its_thumbnail_has_been_built(
    client: TestClient, library: Library
) -> None:
    """The collection grid draws a tile the moment an item is added, before its still is made, so
    each item carries whether the thumbnail exists: the grid shimmers while it is on its way rather
    than requesting a picture that is not there and reading the 404 as a failure. Same fact the
    browse grid's tiles carry, by the same field on the underlying asset."""
    sign_in(client)
    collection_id = make_collection(client, "Best of")
    edit_items(client, collection_id, [library.first])

    def item(asset_id: str) -> dict[str, Any]:
        items = client.get(f"/api/collections/{collection_id}/items").json()["items"]
        return next(one for one in items if one["id"] == asset_id)

    # Freshly indexed: no thumbnail derivative, so the tile says so and will shimmer.
    assert item(library.first)["thumb"] is False

    # Once the thumbnail job has run, the very same item reports it built.
    write(
        db_path(client),
        [
            (
                "INSERT INTO derivatives (id, asset_id, kind, rel_cache_path, created_at) "
                "VALUES (?, ?, 'thumb', ?, ?)",
                (new_id(), library.first, f"{library.first}.jpg", 0),
            )
        ],
    )
    assert item(library.first)["thumb"] is True


def test_two_collections_may_share_a_name(client: TestClient) -> None:
    """Two shortlists really can both be called "best of". The id tells them apart."""
    sign_in(client)
    first = make_collection(client, "Best of")
    second = make_collection(client, "Best of")

    assert first != second
    assert len(client.get("/api/collections").json()["items"]) == 2


def test_create_add_and_cover(client: TestClient, library: Library) -> None:
    """The whole loop, in the order somebody would actually do it."""
    sign_in(client)
    collection_id = make_collection(client, "Mixtape")

    assert edit_items(client, collection_id, library.all_ids).json() == {
        "changed": 3,
        "skipped": 0,
        "reason": None,
        "reason_many": None,
        "vault_locked": False,
    }
    assert sorted(item_ids(client, collection_id)) == sorted(library.all_ids)

    response = client.put(
        f"/api/collections/{collection_id}/cover", json={"asset_id": library.third}
    )
    assert response.status_code == 200, response.text
    assert response.json()["cover_asset_id"] == library.third
    assert response.json()["item_count"] == 3


def test_adding_the_same_item_twice_changes_nothing(client: TestClient, library: Library) -> None:
    """A second drop of the same clip is a no-op."""
    sign_in(client)
    collection_id = make_collection(client, "Mixtape")
    edit_items(client, collection_id, library.all_ids)

    assert edit_items(client, collection_id, [library.first]).json() == {
        "changed": 0,
        "skipped": 0,
        "reason": None,
        "reason_many": None,
        "vault_locked": False,
    }
    assert sorted(item_ids(client, collection_id)) == sorted(library.all_ids)
    assert read(
        db_path(client),
        "SELECT COUNT(*) AS held FROM collection_items WHERE collection_id = ?",
        (collection_id,),
    )[0]["held"] == len(library.all_ids)


def test_removing_an_item_leaves_the_rest(client: TestClient, library: Library) -> None:
    sign_in(client)
    collection_id = make_collection(client, "Mixtape")
    edit_items(client, collection_id, library.all_ids)

    assert edit_items(client, collection_id, [library.second], "remove").json() == {
        "changed": 1,
        "skipped": 0,
        "reason": None,
        "reason_many": None,
        "vault_locked": False,
    }
    assert item_ids(client, collection_id) == [library.third, library.first]


def test_removing_something_the_collection_never_held_changes_nothing(
    client: TestClient, library: Library
) -> None:
    sign_in(client)
    collection_id = make_collection(client, "Mixtape")
    edit_items(client, collection_id, [library.first])

    assert edit_items(client, collection_id, [library.second], "remove").json() == {
        "changed": 0,
        "skipped": 0,
        "reason": None,
        "reason_many": None,
        "vault_locked": False,
    }
    assert item_ids(client, collection_id) == [library.first]


def test_a_collection_is_never_moved_or_rearranged(client: TestClient, library: Library) -> None:
    """A collection has no order of its own to write: a move and a rearrange are not actions."""
    sign_in(client)
    collection_id = make_collection(client, "Mixtape")
    edit_items(client, collection_id, library.all_ids)

    for body in (
        {"asset_ids": [library.first], "action": "move", "direction": "later"},
        {"asset_ids": [library.third, library.first, library.second], "action": "reorder"},
    ):
        response = client.post(f"/api/collections/{collection_id}/items", json=body)
        assert response.status_code == 422, response.text
    assert item_ids(client, collection_id) == [library.third, library.second, library.first]


# --- the cover -----------------------------------------------------------------------------


def test_a_cover_has_to_be_one_of_the_items(client: TestClient, library: Library) -> None:
    """A cover pointing outside the collection is a membership nothing else in the model knows."""
    sign_in(client)
    # A default cover is a picture, and the library's files are videos.
    write(
        db_path(client),
        [
            ("UPDATE assets SET media_type = 'image' WHERE id = ?", (one,))
            for one in library.all_ids
        ],
    )
    collection_id = make_collection(client, "Mixtape")
    edit_items(client, collection_id, [library.first])

    response = client.put(
        f"/api/collections/{collection_id}/cover", json={"asset_id": library.second}
    )
    assert response.status_code == 404
    # Refused with nothing written: the cover is still the first item's, which the collection
    # wears by default (`kernel/access/default_covers.py`).
    assert client.get(f"/api/collections/{collection_id}").json()["cover_asset_id"] == library.first


def test_taking_out_the_item_a_cover_names_clears_the_cover(
    client: TestClient, library: Library
) -> None:
    """A cover is one of the items, and that has to stay true after the items change.

    Enforced only when a cover is set, the rule would hold on the way in and nowhere else: take the
    item out and the collection goes on wearing its picture, which is a tile showing something the
    collection does not contain. What it wears instead is its first remaining item's picture, the
    default every empty cover takes (`kernel/access/default_covers.py`).
    """
    sign_in(client)
    # A default cover is a picture, and the library's files are videos.
    write(
        db_path(client),
        [
            ("UPDATE assets SET media_type = 'image' WHERE id = ?", (one,))
            for one in library.all_ids
        ],
    )
    collection_id = make_collection(client, "Mixtape")
    edit_items(client, collection_id, library.all_ids)
    client.put(f"/api/collections/{collection_id}/cover", json={"asset_id": library.second})

    edit_items(client, collection_id, [library.second], "remove")

    body = client.get(f"/api/collections/{collection_id}").json()
    assert body["cover_asset_id"] != library.second, "the cover outlived the item it named"
    assert body["cover_asset_id"] == library.first


def test_taking_out_another_item_leaves_the_cover_alone(
    client: TestClient, library: Library
) -> None:
    """The other half: only the cover that went stale is cleared, not any cover at all."""
    sign_in(client)
    collection_id = make_collection(client, "Mixtape")
    edit_items(client, collection_id, library.all_ids)
    client.put(f"/api/collections/{collection_id}/cover", json={"asset_id": library.first})

    edit_items(client, collection_id, [library.second], "remove")

    body = client.get(f"/api/collections/{collection_id}").json()
    assert body["cover_asset_id"] == library.first


def test_a_cover_can_be_cleared(client: TestClient, library: Library) -> None:
    sign_in(client)
    collection_id = make_collection(client, "Mixtape")
    edit_items(client, collection_id, [library.first])
    client.put(f"/api/collections/{collection_id}/cover", json={"asset_id": library.first})

    response = client.put(f"/api/collections/{collection_id}/cover", json={"asset_id": None})
    assert response.status_code == 200
    assert response.json()["cover_asset_id"] is None


def test_a_cover_of_an_asset_that_never_existed_is_refused(
    client: TestClient, library: Library
) -> None:
    sign_in(client)
    collection_id = make_collection(client, "Mixtape")

    response = client.put(
        f"/api/collections/{collection_id}/cover", json={"asset_id": NEVER_EXISTED}
    )
    assert response.status_code == 404


# --- renaming ------------------------------------------------------------------------------


def test_a_collection_can_be_renamed(client: TestClient) -> None:
    sign_in(client)
    collection_id = make_collection(client, "Mixtape")

    response = client.put(f"/api/collections/{collection_id}", json={"name": "Best of"})
    assert response.status_code == 200
    assert response.json()["name"] == "Best of"


def test_a_name_of_nothing_but_spaces_is_refused(client: TestClient) -> None:
    sign_in(client)
    assert client.post("/api/collections", json={"name": "   "}).status_code == 422


# --- deleting ------------------------------------------------------------------------------


def test_deleting_a_collection_deletes_its_rows_and_never_its_assets(
    client: TestClient, library: Library
) -> None:
    """The one that would be catastrophic to get wrong.

    `collection_items` names the asset with the cascade pointing at the collection. Pointed the
    other way it is a one-character difference, and deleting a shortlist would delete the media.
    The files, their rows and their bytes all have to be exactly as they were.
    """
    sign_in(client)
    collection_id = make_collection(client, "Mixtape")
    edit_items(client, collection_id, library.all_ids)
    before = {name: library.path_of(name).read_bytes() for name in NAMES}

    assert client.delete(f"/api/collections/{collection_id}").status_code == 204

    surviving = read(db_path(client), "SELECT id FROM assets")
    assert {str(row["id"]) for row in surviving} == set(library.all_ids), (
        "deleting a collection destroyed the media in it"
    )
    locations = read(db_path(client), "SELECT asset_id FROM asset_locations")
    assert {str(row["asset_id"]) for row in locations} == set(library.all_ids)
    for name in NAMES:
        assert library.path_of(name).exists()
        assert library.path_of(name).read_bytes() == before[name]

    membership = read(db_path(client), "SELECT * FROM collection_items")
    assert membership == [], "the membership rows outlived the collection"


def test_deleting_a_collection_forgets_every_grant_that_named_it(
    client: TestClient, library: Library
) -> None:
    """`acl_grants.object_id` carries no foreign key, so nothing cascades and this is manual.

    A grant left behind applies to whatever object later gets that id: a stale share hands
    somebody a file, and a stale restrict is a guarantee that quietly stopped being kept.
    """
    guest = sign_in(client, "guest")
    sign_in(client)
    collection_id = make_collection(client, "Mixtape")
    grant_on_collection(client, collection_id, guest, "share")
    grant_on_collection(client, collection_id, guest, "restrict")
    assert len(grants_naming(client, collection_id)) == 2

    assert client.delete(f"/api/collections/{collection_id}").status_code == 204

    assert grants_naming(client, collection_id) == [], "grants outlived the collection they named"


def test_deleting_an_empty_collection_asks_for_no_library_rebuild(client: TestClient) -> None:
    """A collection holding nothing changes no indexed text, so it must queue no pass at all.

    A collection's name is indexed on the assets that carry it and on no other, so an empty one
    changes nothing anywhere. A whole-library rebuild queued regardless would hold the write lock
    for as long as it runs, so the next delete would wait behind it, and clearing out two dozen
    empty shortlists would take tens of seconds on a large library.

    Counted rather than asserted absent, because the same job type is queued by other things
    entirely: what has to be true is that THIS request added none.
    """
    sign_in(client)
    collection_id = make_collection(client, "Nothing in here")
    before = read(db_path(client), "SELECT id FROM jobs WHERE type = 'fts_reindex'")

    assert client.delete(f"/api/collections/{collection_id}").status_code == 204

    after = read(db_path(client), "SELECT id FROM jobs WHERE type = 'fts_reindex'")
    assert len(after) == len(before), "an empty collection queued a rebuild of the whole library"


def test_deleting_a_collection_takes_its_name_off_the_files_it_held(
    client: TestClient, library: Library
) -> None:
    """The half an empty collection cannot prove.

    A version that handed back nothing at all would satisfy the test above and leave every file
    that was in a deleted collection findable by its name for ever. So this asserts the indexed
    text really did change, on exactly the assets that carried it, and that the index was
    corrected rather than emptied, which is the other way to make the name go away.
    """
    sign_in(client)
    collection_id = make_collection(client, "Mixtape")
    edit_items(client, collection_id, library.all_ids)
    named = read(db_path(client), "SELECT collections FROM assets_fts")
    assert any("Mixtape" in str(row["collections"]) for row in named), (
        "the name was never indexed in the first place, so its absence below proves nothing"
    )

    assert client.delete(f"/api/collections/{collection_id}").status_code == 204

    after = read(db_path(client), "SELECT collections FROM assets_fts")
    assert len(after) == len(named), "the index was emptied rather than corrected"
    assert not any("Mixtape" in str(row["collections"]) for row in after), (
        "a deleted collection's name is still indexed on the files it held"
    )


def _stamp_of(client: TestClient, user_id: str) -> int:
    """How many times what this user may see has changed. Read straight from the row, because
    nothing puts it in a response: it reaches a screen only inside a picture's address."""
    rows = read(db_path(client), "SELECT cache_stamp FROM users WHERE id = ?", (user_id,))
    assert rows, "no such account"
    return int(str(rows[0]["cache_stamp"]))


def test_taking_an_item_out_reaches_the_guest_the_collection_was_shared_with(
    client: TestClient, library: Library
) -> None:
    """**Removing an item revokes access, so it has to invalidate the pictures too.**

    A generated picture is served with an address a browser may keep for a week, and a copy in that
    store is served with no request and therefore no permission check. Taking the file out of the
    only collection that reached this guest ends their access to it, but nothing of theirs is
    written by that, so unless the removal raises their number the addresses they already hold go
    on working.

    An admin doing the removing has no grant on the collection and has not hidden it, so their own
    grid must not be thrown away for somebody else's revocation.
    """
    guest = sign_in(client, "guest")
    admin = sign_in(client)
    collection_id = make_collection(client, "Mixtape")
    assert edit_items(client, collection_id, library.all_ids).status_code == 200
    grant_on_collection(client, collection_id, guest, "share")
    before_guest = _stamp_of(client, guest)
    before_admin = _stamp_of(client, admin)

    assert edit_items(client, collection_id, [library.first], "remove").status_code == 200

    assert _stamp_of(client, guest) > before_guest, (
        "the guest's picture addresses still work after the file left the shared collection"
    )
    assert _stamp_of(client, admin) == before_admin, (
        "the admin's whole grid was thrown away for a change to somebody else's access"
    )


def test_dropping_a_file_onto_a_collection_it_already_holds_costs_nobody_anything(
    client: TestClient, library: Library
) -> None:
    """Nothing changed, so no address needs to move. The insert reports nothing written and the
    number stays where it is. Otherwise a re-drop, which is an ordinary slip, would empty the
    grid of everyone the collection reaches."""
    guest = sign_in(client, "guest")
    sign_in(client)
    collection_id = make_collection(client, "Mixtape")
    assert edit_items(client, collection_id, library.all_ids).status_code == 200
    grant_on_collection(client, collection_id, guest, "share")
    before = _stamp_of(client, guest)

    assert edit_items(client, collection_id, library.all_ids).status_code == 200

    assert _stamp_of(client, guest) == before, "a duplicate drop invalidated everybody's pictures"


def test_deleting_something_that_never_existed_is_the_same_404(client: TestClient) -> None:
    sign_in(client)
    assert client.delete(f"/api/collections/{NEVER_EXISTED}").status_code == 404


# --- the promise about the filesystem --------------------------------------------------------


def test_adding_to_a_collection_moves_no_file(client: TestClient, library: Library) -> None:
    """The load-bearing one. Organising logically means the disk is untouched.

    Path, bytes and the location row are all compared before and after. A drag-to-add that moved,
    copied or renamed anything would break the promise the whole storage model is built on, and it
    would do it silently.
    """
    sign_in(client)
    collection_id = make_collection(client, "Mixtape")

    before_bytes = {name: library.path_of(name).read_bytes() for name in NAMES}
    before_rows = read(
        db_path(client),
        "SELECT asset_id, rel_path, filename FROM asset_locations ORDER BY rel_path",
    )

    edit_items(client, collection_id, library.all_ids)
    client.put(f"/api/collections/{collection_id}/cover", json={"asset_id": library.first})
    edit_items(client, collection_id, [library.second], "remove")

    for name in NAMES:
        assert library.path_of(name).exists(), f"{name} left its place on disk"
        assert library.path_of(name).read_bytes() == before_bytes[name], f"{name} changed on disk"
    after_rows = read(
        db_path(client),
        "SELECT asset_id, rel_path, filename FROM asset_locations ORDER BY rel_path",
    )
    assert after_rows == before_rows, "a location row moved"


# --- permission scoping --------------------------------------------------------------------


def test_a_guest_sees_only_permitted_items_and_a_matching_count(
    client: TestClient, library: Library
) -> None:
    """The count-based existence oracle, closed.

    A collection showing one item above a count of three tells a guest that two more things exist.
    The number beside the collection and the number of items in it have to be the same number.
    """
    guest = sign_in(client, "guest")
    sign_in(client)
    collection_id = make_collection(client, "Mixtape")
    edit_items(client, collection_id, library.all_ids)
    share(client, library.first, guest)

    sign_in(client, "guest")
    listed = client.get("/api/collections").json()["items"]
    assert len(listed) == 1
    assert listed[0]["item_count"] == 1, "the count reported items the guest was never shown"

    contents = client.get(f"/api/collections/{collection_id}/items").json()
    assert [item["id"] for item in contents["items"]] == [library.first]
    assert contents["total"] == 1
    assert contents["total"] == len(contents["items"])


def test_a_guest_cannot_see_a_collection_holding_nothing_for_them(
    client: TestClient, library: Library
) -> None:
    """Absent, not empty. A collection they can see nothing in is one they are not told about."""
    sign_in(client, "guest")
    sign_in(client)
    collection_id = make_collection(client, "Mixtape")
    edit_items(client, collection_id, library.all_ids)

    sign_in(client, "guest")
    assert client.get("/api/collections").json()["items"] == []
    assert client.get(f"/api/collections/{collection_id}").status_code == 404
    assert client.get(f"/api/collections/{collection_id}/items").status_code == 404


def test_a_guest_is_not_shown_a_cover_they_may_not_open(
    client: TestClient, library: Library
) -> None:
    """A cover is a picture of an item, so it leaks the item as surely as counting it does."""
    guest = sign_in(client, "guest")
    sign_in(client)
    collection_id = make_collection(client, "Mixtape")
    edit_items(client, collection_id, library.all_ids)
    client.put(f"/api/collections/{collection_id}/cover", json={"asset_id": library.second})
    share(client, library.first, guest)

    sign_in(client, "guest")
    assert client.get("/api/collections").json()["items"][0]["cover_asset_id"] is None


def test_a_guest_may_not_edit_a_collection(client: TestClient, library: Library) -> None:
    """Every write is admin-only: a collection is shared, so editing one edits what others see."""
    guest = sign_in(client, "guest")
    sign_in(client)
    collection_id = make_collection(client, "Mixtape")
    edit_items(client, collection_id, library.all_ids)
    share(client, library.first, guest)

    sign_in(client, "guest")
    assert client.post("/api/collections", json={"name": "Mine"}).status_code == 403
    assert edit_items(client, collection_id, [library.first]).status_code == 403
    assert edit_items(client, collection_id, [library.first], "remove").status_code == 403
    assert client.delete(f"/api/collections/{collection_id}").status_code == 403
    assert (
        client.put(
            f"/api/collections/{collection_id}", json={"name": "x", "vault": False}
        ).status_code
        == 403
    )
    assert (
        client.put(
            f"/api/collections/{collection_id}/cover", json={"asset_id": library.first}
        ).status_code
        == 403
    )


def test_an_admin_adding_an_asset_that_never_existed_adds_nothing_and_is_told_why(
    client: TestClient, library: Library
) -> None:
    """Answered rather than refused, and the collection stays empty either way.

    A 200 here is not the call succeeding: `changed` is zero and `skipped` is one. It is the shape
    every bulk write in the app uses, and it exists so that a selection where only SOME of the
    items are out of reach can report both halves. Answering 404 for the all-out-of-reach case
    would give this route two vocabularies for one situation.
    """
    sign_in(client)
    collection_id = make_collection(client, "Mixtape")

    answer = edit_items(client, collection_id, [NEVER_EXISTED])

    assert answer.status_code == 200
    assert answer.json() == {
        "changed": 0,
        "skipped": 1,
        "reason": "Sift could not find the file.",
        "reason_many": "Sift could not find the files.",
        "vault_locked": False,
    }
    assert item_ids(client, collection_id) == []


def test_an_asset_deleted_mid_request_is_the_same_404_and_not_a_crash(
    client: TestClient, library: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The window between resolving an asset and writing the membership row.

    Two requests, one adding a file and one deleting it. The foreign key on the membership row
    catches it; the answer has to be the 404 the resolve would have given a moment later, not the
    500 an uncaught constraint would surface.
    """
    sign_in(client)
    collection_id = make_collection(client, "Mixtape")

    from sift.slices.collections import service as collections_service

    original = collections_service.CollectionService.add

    async def vanish(self, cid, asset_ids, *, actor):  # type: ignore[no-untyped-def]
        # The state a competing delete would have left, applied inside the window. Written on the
        # application's own handle because this runs on the application's loop.
        await self._db.execute("DELETE FROM assets WHERE id = ?", (library.first,))
        return await original(self, cid, asset_ids, actor=actor)

    monkeypatch.setattr(collections_service.CollectionService, "add", vanish)

    response = edit_items(client, collection_id, [library.first])
    assert response.status_code == 404, response.text


def test_a_collection_that_never_existed_is_the_same_404_as_one_not_shared(
    client: TestClient,
) -> None:
    sign_in(client)
    assert client.get(f"/api/collections/{NEVER_EXISTED}").status_code == 404
    assert client.get(f"/api/collections/{NEVER_EXISTED}/items").status_code == 404
    assert client.get("/api/collections/not-an-id").status_code == 404


# --- the vault -----------------------------------------------------------------------------


def test_a_vaulted_collection_is_absent_from_the_list(client: TestClient, library: Library) -> None:
    """Absent, not locked. A locked row still says something is there.

    Concealed from admins too. The vault is not a permission: it hides from everybody until it
    is unlocked, and an admin who could still see the row would be the exception that makes it
    worthless.
    """
    sign_in(client)
    collection_id = make_collection(client, "Private")
    edit_items(client, collection_id, library.all_ids)

    assert set_vault(client, collection_id, True).status_code == 204

    assert client.get("/api/collections").json()["items"] == []
    assert client.get(f"/api/collections/{collection_id}").status_code == 404
    assert client.get(f"/api/collections/{collection_id}/items").status_code == 404


def test_a_vaulted_collection_cannot_be_taken_back_out_while_the_vault_is_locked(
    client: TestClient, library: Library
) -> None:
    """The seal. Vaulting is one-way until the vault can be unlocked.

    Two things would leak if this answered. A 204 where a 404 belongs tells anybody holding the id
    that the collection is there, which is what concealing it was for. And clearing the flag from
    behind the concealment would put every item back on the grid while the vault is still locked:
    the write would be the reveal.
    """
    sign_in(client)
    collection_id = make_collection(client, "Private")
    edit_items(client, collection_id, library.all_ids)
    set_vault(client, collection_id, True)

    assert set_vault(client, collection_id, False).status_code == 404
    assert client.get("/api/collections").json()["items"] == [], (
        "still concealed after the refused write"
    )


def test_a_vaulted_collection_answers_every_write_the_way_an_unknown_id_does(
    client: TestClient, library: Library
) -> None:
    """One id, one answer. The vault write is no exception among the writes."""
    sign_in(client)
    collection_id = make_collection(client, "Private")
    edit_items(client, collection_id, library.all_ids)
    set_vault(client, collection_id, True)

    for concealed in (collection_id, NEVER_EXISTED):
        assert set_vault(client, concealed, True).status_code == 404, concealed
        assert set_vault(client, concealed, False).status_code == 404, concealed


def test_vaulting_something_that_never_existed_is_the_same_404(client: TestClient) -> None:
    sign_in(client)
    assert set_vault(client, NEVER_EXISTED, True).status_code == 404


def test_a_vaulted_collection_conceals_its_items_everywhere(
    client: TestClient, library: Library
) -> None:
    """The resolver's half of the rule: the items go too, not just the collection's own row.

    Not a second check written here: this asserts the one in the access layer is actually
    reached, which is what makes a second one unnecessary rather than merely redundant.
    """
    sign_in(client)
    collection_id = make_collection(client, "Private")
    edit_items(client, collection_id, [library.first])
    set_vault(client, collection_id, True)

    grid = client.get("/api/assets").json()
    assert library.first not in [item["id"] for item in grid["items"]]


def test_vaulting_a_collection_afterwards_conceals_it_too(
    client: TestClient, library: Library
) -> None:
    sign_in(client)
    collection_id = make_collection(client, "Mixtape")
    edit_items(client, collection_id, library.all_ids)
    assert len(client.get("/api/collections").json()["items"]) == 1

    assert set_vault(client, collection_id, True).status_code == 204
    assert client.get("/api/collections").json()["items"] == []


def test_a_vaulted_item_inside_a_normal_collection_is_absent_and_the_count_adjusts(
    client: TestClient, library: Library
) -> None:
    """The other half of the vault rule, and the one a count would give away.

    Concealing the item but leaving the count at three would say a third thing is in there without
    showing it, which is the same oracle the guest test closes from the other direction.
    """
    sign_in(client)
    collection_id = make_collection(client, "Mixtape")
    edit_items(client, collection_id, library.all_ids)

    vault_asset(client, library.second)

    contents = client.get(f"/api/collections/{collection_id}/items").json()
    assert [item["id"] for item in contents["items"]] == [library.third, library.first]
    assert contents["total"] == 2, "the count still described the concealed item"
    assert client.get("/api/collections").json()["items"][0]["item_count"] == 2


def test_a_vaulted_item_is_dropped_as_a_cover_too(client: TestClient, library: Library) -> None:
    sign_in(client)
    collection_id = make_collection(client, "Mixtape")
    edit_items(client, collection_id, library.all_ids)
    client.put(f"/api/collections/{collection_id}/cover", json={"asset_id": library.second})

    vault_asset(client, library.second)

    assert client.get(f"/api/collections/{collection_id}").json()["cover_asset_id"] is None


def test_a_placeholder_in_a_collection_says_no_more_than_a_placeholder_on_the_grid(
    client: TestClient, library: Library
) -> None:
    """The mode that keeps a gap rather than removing it, and what fills the gap.

    A concealed item reaches this screen only here: the default takes it out of the read entirely.
    When it does arrive it has to be described the way the grid describes one (its concealment
    and nothing else), or a collection becomes the one place a hidden file is listed with its
    dimensions and its kind next to it.

    Reached by handing the route a viewer in placeholder mode, which no session builds yet. Once
    one does, this is the behaviour it gets.
    """
    user_id = sign_in(client)
    collection_id = make_collection(client, "Mixtape")
    edit_items(client, collection_id, library.all_ids)
    vault_asset(client, library.second)

    def placeholder_viewer() -> Viewer:
        return Viewer(
            id=user_id,
            role=Role.ADMIN,
            show_hidden=False,
            concealment=Concealment.PLACEHOLDER,
        )

    client.app.dependency_overrides[current_viewer] = placeholder_viewer  # type: ignore[attr-defined]
    try:
        contents = client.get(f"/api/collections/{collection_id}/items").json()

        # The gap is kept, so the wall still has three places in it and the count agrees.
        assert [item["id"] for item in contents["items"]] == library.all_ids[::-1]
        assert contents["total"] == 3

        hidden = contents["items"][1]
        assert hidden["concealed"] is True
        assert hidden["media_type"] == ""
        assert hidden["width"] is None
        assert hidden["height"] is None
        assert hidden["duration_ms"] is None
        # The heart and the stars are this user's opinion OF A FILE, so a placeholder carrying
        # them would say a file is worth four stars while refusing to say what it is. The route
        # does not even ask about a concealed row, which is the stronger form of the same rule.
        assert hidden["favorite"] is False
        assert hidden["rating"] is None
    finally:
        client.app.dependency_overrides.clear()  # type: ignore[attr-defined]


def test_an_unlocked_vault_shows_a_hidden_collection_item_in_full(
    client: TestClient, library: Library
) -> None:
    """The other half of the placeholder test above. `view.concealed` says this item is vaulted;
    it does not by itself say the viewer reading it may not see its contents: an unlocked vault
    is exactly the case where they may, on a collection the same as on the grid or in search.
    An open vault shows the item, not a bare placeholder.
    """
    user_id = sign_in(client)
    collection_id = make_collection(client, "Mixtape")
    edit_items(client, collection_id, library.all_ids)
    vault_asset(client, library.second)

    def unlocked_viewer() -> Viewer:
        return Viewer(id=user_id, role=Role.ADMIN, show_hidden=True)

    client.app.dependency_overrides[current_viewer] = unlocked_viewer  # type: ignore[attr-defined]
    try:
        contents = client.get(f"/api/collections/{collection_id}/items").json()
    finally:
        client.app.dependency_overrides.clear()  # type: ignore[attr-defined]

    revealed = contents["items"][1]
    assert revealed["id"] == library.second
    assert revealed["concealed"] is False
    assert revealed["media_type"] != ""
    assert revealed["width"] is not None


def test_a_placeholder_is_still_not_allowed_to_be_the_cover(
    client: TestClient, library: Library
) -> None:
    """Keeping the gap is not the same as unlocking the vault.

    A placeholder is a locked tile with no content in it, and the mode that keeps one is a display
    preference rather than a way in. A cover is a picture, so it stays hidden on the stricter test:
    nothing short of a real unlock brings it back. Getting these two flags the same way round
    would publish, at thumbnail size, exactly what the vault is holding.
    """
    user_id = sign_in(client)
    collection_id = make_collection(client, "Mixtape")
    edit_items(client, collection_id, library.all_ids)
    client.put(f"/api/collections/{collection_id}/cover", json={"asset_id": library.second})
    vault_asset(client, library.second)

    def placeholder_viewer() -> Viewer:
        return Viewer(
            id=user_id,
            role=Role.ADMIN,
            show_hidden=False,
            concealment=Concealment.PLACEHOLDER,
        )

    client.app.dependency_overrides[current_viewer] = placeholder_viewer  # type: ignore[attr-defined]
    try:
        body = client.get(f"/api/collections/{collection_id}").json()
        assert body["cover_asset_id"] is None, "the vault published its contents as a cover"
        # The count still includes the placeholder, because the list of items still shows it.
        # The two numbers agree; it is the picture that is withheld.
        assert body["item_count"] == 3
    finally:
        client.app.dependency_overrides.clear()  # type: ignore[attr-defined]


# --- the hooks sharing depends on ------------------------------------------------------------


def test_a_collection_is_a_valid_grant_object_type(client: TestClient, library: Library) -> None:
    """The schema accepts a grant naming a collection.

    If a grant naming a collection were refused by the table, sharing one would need a migration
    rather than rows and a screen.
    """
    guest = sign_in(client, "guest")
    sign_in(client)
    collection_id = make_collection(client, "Mixtape")
    edit_items(client, collection_id, library.all_ids)

    grant_on_collection(client, collection_id, guest, "share")

    stored = grants_naming(client, collection_id)
    assert len(stored) == 1
    assert stored[0]["effect"] == "share"


def test_sharing_a_collection_reveals_its_items(client: TestClient, library: Library) -> None:
    """The resolver already treats a collection as a share scope, which is the hook's whole point."""
    guest = sign_in(client, "guest")
    sign_in(client)
    collection_id = make_collection(client, "Mixtape")
    edit_items(client, collection_id, library.all_ids)
    grant_on_collection(client, collection_id, guest, "share")

    sign_in(client, "guest")
    contents = client.get(f"/api/collections/{collection_id}/items").json()
    assert [item["id"] for item in contents["items"]] == library.all_ids[::-1]
    assert contents["total"] == 3


def test_there_is_no_query_column_and_no_smart_collection(client: TestClient) -> None:
    """Manual only. Saved-search collections do not exist and must not be half-built.

    A `query` column arriving early is not harmless: it is a place for a half-written feature to
    accumulate rows that nothing evaluates, and a screen that has to decide what a collection with
    both a query and hand-picked items means.
    """
    columns = {
        str(row["name"])
        for row in read(db_path(client), "SELECT name FROM pragma_table_info('collections')")
    }
    assert "query" not in columns
    assert columns == {
        "id",
        "name",
        "cover_asset_id",
        # The key this tag is ORDERED by, beside the name it is drawn with. `name COLLATE NOCASE`
        # folds ASCII only, so every accented name sorted after Z; see `kernel.sorting`.
        "name_sort",
        # A picture somebody UPLOADED as the cover, rather than a still from a file in the
        # library. Null is a cover taken from a file, which is what every row meant before.
        "cover_upload_id",
        # Which MOMENT of that file, for a cover taken from a clip. Null is the file's own
        # picture, which is what every row meant before the column existed.
        "cover_at_ms",
        # How the cover sits in its frame: the window of the picture the card is drawn as
        # (`kernel.cover_frame`). Null is the middle of the picture, cut to fit.
        "cover_frame",
        "owner_id",
        "created_at",
        # WHO MADE IT, as the kind of maker and the user. `owner_id` is whose shelf it IS and
        # decides who may see it; these say who made it, which is the same value today and need not
        # stay so. Nothing in Sift makes a shelf on its own, so the kind here is always 'user'.
        "created_by_kind",
        "created_by_via",
        "created_by_user_id",
        # When somebody took the cover away, so Sift's own rule leaves the shelf without one until
        # a picture is chosen again (`kernel.covers.cleared_mark`). Null while a cover is chosen.
        "cover_cleared_at",
        # The file Sift's rule gave the shelf as its cover when nobody chose one, so a choice is
        # told apart from the rule's pick (`kernel.covers`).
        "cover_by_default",
        # When the shelf itself was last edited, kept by the database for Recently edited
        # (`kernel.access.edited`).
        "edited_at",
    }


# --- names that could never be typed as a filter token ---------------------------------------


def test_a_collection_name_carrying_a_control_character_is_cleaned(client: TestClient) -> None:
    """Collection names are not in the free-text index, but `collections:"name"` is how one is
    named in a query, so the same rule applies for the same reason."""
    sign_in(client)

    made = client.post("/api/collections", json={"name": "summ\x00er"})

    assert made.status_code == 201
    assert made.json()["name"] == "summer"


def test_a_collection_name_with_a_double_quote_is_refused(client: TestClient) -> None:
    sign_in(client)

    refused = client.post("/api/collections", json={"name": 'the "best" ones'})

    assert refused.status_code == 422
    assert "double quote" in refused.text


def test_the_wall_says_which_collections_have_been_shared(
    client: TestClient, library: Library
) -> None:
    """The badge on a collection card, and the gate in front of it.

    A collection has nothing above it to inherit from, so this is the plain read: was a grant
    written on this row. What an admin is shown, a guest is not: their screen already is the
    answer, and on a wall both users can see, a badge would describe decisions that are not
    theirs to read.
    """
    guest = sign_in(client, "guest")
    sign_in(client, "admin")
    shared = make_collection(client, "Handed over")
    closed = make_collection(client, "Kept back")
    quiet = make_collection(client, "Nothing said")
    grant_on_collection(client, shared, guest, "share")
    grant_on_collection(client, closed, guest, "restrict")
    # Something in it that the guest can see, or their wall is empty and the second half of this
    # test passes without checking anything.
    edit_items(client, shared, [library.first])
    share(client, library.first, guest)

    sign_in(client, "admin")
    rows = {row["id"]: row for row in client.get("/api/collections").json()["items"]}
    assert (rows[shared]["shared"], rows[shared]["restricted"]) == (True, False)
    assert (rows[closed]["shared"], rows[closed]["restricted"]) == (False, True)
    assert (rows[quiet]["shared"], rows[quiet]["restricted"]) == (False, False)

    sign_in(client, "guest")
    theirs = client.get("/api/collections").json()["items"]
    assert [row["id"] for row in theirs] == [shared]
    assert (theirs[0]["shared"], theirs[0]["restricted"]) == (False, False)


# --- tags on a collection -----------------------------------------------------------------------
#
# A collection carries tags the way a file, a person and a site do, through a table of its own.


def test_a_collection_can_carry_a_tag_and_lose_it(client: TestClient) -> None:
    sign_in(client, "admin")
    collection = make_collection(client, "Keepers")
    tag = client.post("/api/tags", json={"name": "archive"}).json()["id"]

    on = client.post(f"/api/collections/{collection}/tags", json={"tag_id": tag})
    assert [one["name"] for one in on.json()] == ["archive"]

    off = client.post(f"/api/collections/{collection}/tags", json={"tag_id": tag, "add": False})
    assert off.json() == []


def test_the_same_tag_twice_on_one_collection_is_one_tag(client: TestClient) -> None:
    """Idempotent, like every other assignment here: pressing twice is not two rows."""
    sign_in(client, "admin")
    collection = make_collection(client, "Keepers")
    tag = client.post("/api/tags", json={"name": "archive"}).json()["id"]

    client.post(f"/api/collections/{collection}/tags", json={"tag_id": tag})
    twice = client.post(f"/api/collections/{collection}/tags", json={"tag_id": tag})

    assert len(twice.json()) == 1


# Who may read and who may write these is asserted by the authorization matrix, which puts every
# mounted route to a real admin and a real guest, so it is not repeated here. What is here is the
# behaviour that matrix cannot see.


def test_reading_the_tags_of_a_collection_that_is_not_there_is_a_404(client: TestClient) -> None:
    """The same answer an unknown id and one that is not yours both get."""
    sign_in(client, "admin")

    assert client.get(f"/api/collections/{NEVER_EXISTED}/tags").status_code == 404


def test_the_tags_on_a_collection_can_be_read_back(client: TestClient) -> None:
    """The read, on its own. Every other test here goes through the write, which answers with the
    whole set, so the reader was never actually asked."""
    sign_in(client, "admin")
    collection = make_collection(client, "Keepers")
    tag = client.post("/api/tags", json={"name": "archive"}).json()["id"]
    client.post(f"/api/collections/{collection}/tags", json={"tag_id": tag})

    listed = client.get(f"/api/collections/{collection}/tags")

    assert [one["name"] for one in listed.json()] == ["archive"]


def test_a_moment_of_a_clip_can_be_the_cover(client: TestClient, library: Library) -> None:
    """A cover is a file AND which moment of it. The still is queued rather than rendered on the
    request, so what a request can answer is that the choice was written."""
    sign_in(client)
    collection_id = make_collection(client, "Mixtape")
    edit_items(client, collection_id, [library.first])

    written = client.put(
        f"/api/collections/{collection_id}/cover",
        json={"asset_id": library.first, "at_ms": 4200},
    )

    assert written.status_code == 200, written.text
    assert written.json()["cover_asset_id"] == library.first


def test_a_picture_from_outside_the_library_can_be_uploaded_and_read_back(
    client: TestClient,
) -> None:
    """The case the membership rule above had no answer for: a picture of the SHELF, which is not
    a pointer at an asset at all. In as a PNG, out as Sift's own JPEG."""
    sign_in(client)
    collection_id = make_collection(client, "Mixtape")

    sent = client.post(
        f"/api/collections/{collection_id}/cover-picture",
        files={"file": ("chosen.png", a_png(), "image/png")},
    )

    assert sent.status_code == 200, sent.text
    assert sent.json()["cover_upload_id"] is not None
    assert sent.json()["cover_asset_id"] is None

    served = client.get(f"/api/collections/{collection_id}/cover")

    assert served.status_code == 200
    assert served.headers["content-type"] == "image/jpeg"
    assert served.content.startswith(b"\xff\xd8\xff"), "what is served is a JPEG Sift wrote"


def test_bytes_that_are_not_a_picture_are_refused(client: TestClient) -> None:
    """The one route here where a stranger chooses the bytes; ffmpeg is what reads them."""
    sign_in(client)
    collection_id = make_collection(client, "Mixtape")

    sent = client.post(
        f"/api/collections/{collection_id}/cover-picture",
        files={"file": ("chosen.png", b"this is a sentence, not a picture", "image/png")},
    )

    assert sent.status_code == 400


def test_a_shelf_with_no_cover_serves_a_404(client: TestClient) -> None:
    """The same 404 as a shelf that is not there, and as a cover the asker may not open."""
    sign_in(client)
    collection_id = make_collection(client, "Mixtape")

    assert client.get(f"/api/collections/{collection_id}/cover").status_code == 404
    assert client.get(f"/api/collections/{NEVER_EXISTED}/cover").status_code == 404


def test_uploading_onto_a_shelf_that_is_not_there_is_a_404(client: TestClient) -> None:
    """Checked BEFORE the bytes are read, so naming a shelf that does not exist cannot make Sift
    run ffmpeg."""
    sign_in(client)

    sent = client.post(
        f"/api/collections/{NEVER_EXISTED}/cover-picture",
        files={"file": ("chosen.png", a_png(), "image/png")},
    )

    assert sent.status_code == 404


def test_the_list_can_be_narrowed_to_the_collections_holding_one_file(
    client: TestClient, library: Library
) -> None:
    """`?asset=` answers the file's own screen: which collections is this in. The same leaf the
    walls filter by, reached by name, so a file in two of three collections lists exactly those
    two, and a file in none lists none rather than everything."""
    sign_in(client)
    holding = make_collection(client, "holds it")
    also = make_collection(client, "holds it too")
    make_collection(client, "does not")
    assert edit_items(client, holding, [library.first]).status_code == 200
    assert edit_items(client, also, [library.first, library.second]).status_code == 200

    named = {
        one["name"]
        for one in client.get("/api/collections", params={"asset": library.first}).json()["items"]
    }
    assert named == {"holds it", "holds it too"}
    assert client.get("/api/collections", params={"asset": library.third}).json()["items"] == []


def test_a_collections_rows_carry_this_accounts_heart_and_stars(
    client: TestClient, library: Library
) -> None:
    """So the wall can hand its selection to the shared file verbs, like every other wall.

    Without these, a collection's bar could offer only one act (take it out of this collection)
    and the verbs somebody uses constantly would be missing on the one screen where they have
    gathered the files they care about. The wall cannot draw them without knowing whether a file
    is hearted, and a bar that asked per row would be a request per tile.

    Read for the whole page in one statement, which is what the ids list beside the items is for.
    A file nobody has said anything about comes back as `false` and `null` rather than as a gap:
    "not hearted" is a real answer and the tile draws it.
    """
    sign_in(client)
    collection_id = make_collection(client, "The good ones")
    edit_items(client, collection_id, [library.first, library.second])
    client.put(f"/api/assets/{library.first}/favorite", json={"favorite": True})
    client.put(f"/api/assets/{library.first}/rating", json={"rating": 4})

    rows = {
        str(one["id"]): one
        for one in client.get(f"/api/collections/{collection_id}/items").json()["items"]
    }

    assert rows[library.first]["favorite"] is True
    assert rows[library.first]["rating"] == 4
    assert rows[library.second]["favorite"] is False
    assert rows[library.second]["rating"] is None


# --- filtered by the cards picked on its tabs ------------------------------------------------


def _tag(client: TestClient, name: str, *asset_ids: str) -> None:
    """One tag, on these files. Seeded directly: what is under test is the filtering, not tagging."""
    tag_id = new_id()
    statements: list[tuple[str, tuple[object, ...]]] = [
        ("INSERT INTO tags (id, name, created_at) VALUES (?, ?, 0)", (tag_id, name))
    ]
    statements += [
        ("INSERT INTO asset_tags (asset_id, tag_id) VALUES (?, ?)", (asset_id, tag_id))
        for asset_id in asset_ids
    ]
    write(db_path(client), statements)


def test_a_pick_narrows_the_contents_and_the_count_with_them(
    client: TestClient, library: Library
) -> None:
    """The Files tab of a collection reads the picks the way a person's Files tab does.

    Two tags given twice is AND, the meaning a repeated field has everywhere in the query language,
    and a name nobody holds filters to nothing rather than being ignored.
    """
    sign_in(client)
    collection_id = make_collection(client, "Best of")
    newest = [library.third, library.second, library.first]
    assert edit_items(client, collection_id, library.all_ids).status_code == 200
    _tag(client, "beach", library.first, library.third)
    _tag(client, "dusk", library.third)
    address = f"/api/collections/{collection_id}/items"

    one = client.get(address, params={"tags": "beach"})
    assert one.status_code == 200, one.text
    assert [item["id"] for item in one.json()["items"]] == [library.third, library.first]
    assert one.json()["total"] == 2

    both = client.get(address, params=[("tags", "beach"), ("tags", "dusk")])
    assert [item["id"] for item in both.json()["items"]] == [library.third]
    assert both.json()["total"] == 1

    nobody = client.get(address, params={"tags": "no-such-tag"})
    assert nobody.json()["items"] == []
    assert nobody.json()["total"] == 0

    whole = client.get(address)
    assert [item["id"] for item in whole.json()["items"]] == newest
    assert whole.json()["total"] == 3


def test_a_typed_exclusion_on_a_picked_field_is_read_as_the_language_reads_it(
    client: TestClient, library: Library
) -> None:
    """The route hands each value to the one parser whole, so a minus still means NOT here."""
    sign_in(client)
    collection_id = make_collection(client, "Best of")
    assert edit_items(client, collection_id, library.all_ids).status_code == 200
    _tag(client, "beach", library.first)

    response = client.get(f"/api/collections/{collection_id}/items", params={"tags": "-beach"})

    assert response.status_code == 200, response.text
    assert {item["id"] for item in response.json()["items"]} == {library.second, library.third}


def test_a_collection_listing_names_an_uploaded_cover_so_it_is_kept(client: TestClient) -> None:
    """The wall hands out what the address needs to name this upload, so the picture is KEPT.

    The server makes the week-long promise only to an address carrying the user's token and the
    upload id (`kernel/covers.py names_its_cover`); a wall row that carried no token would leave
    every uploaded cover re-checked on every visit. Built from the wire exactly as `coverToken` in
    `lib/entity/art.ts` builds it.
    """
    sign_in(client)
    thing = make_collection(client, "Mixtape")
    sent = client.post(
        f"/api/collections/{thing}/cover-picture",
        files={"file": ("chosen.png", a_png(), "image/png")},
    )
    assert sent.status_code == 200, sent.text

    listed = client.get("/api/collections")
    assert listed.status_code == 200, listed.text
    row = next(one for one in listed.json()["items"] if one["id"] == thing)
    assert row["art"], "the wall row carries the account's token"
    served = client.get(
        f"/api/collections/{thing}/cover", params={"v": f"{row['art']}.{row['cover_upload_id']}"}
    )

    assert served.status_code == 200
    assert "immutable" in served.headers["cache-control"]
