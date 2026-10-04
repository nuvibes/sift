# SPDX-License-Identifier: AGPL-3.0-or-later
"""The index a running application keeps, and the searches and filters an account keeps."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sift.kernel.config import get_settings
from sift.kernel.ids import new_id
from sift.kernel.jobs import worker_pool
from sift.main import create_app
from sift.slices.search.tests.conftest import (
    EPOCH,
    World,
    db_path,
    found,
    read,
    sign_in,
    submit,
    write,
)

# --- the application fills its own index --------------------------------------------------------
#
# These start the real application, add an asset and wait: nothing calls the indexer by hand.


def _running(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """The real application, with nothing about the refresh altered."""
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()
    return TestClient(create_app())


#: One root for everything these tests seed. Fixed, so repeated adds share it.
_INDEXED_ROOT = "01JZZZINDEXROOTZZZZZZZZZZZ"


def _add(client: TestClient, name: str) -> str:
    """Seed an asset the way the scanner would: a row, and somewhere it actually is.

    The location is not decoration. An asset with none is a file that exists nowhere, and the grid
    refuses to draw one: that is what stops a removed library leaving a screen full of tiles for
    files that are gone. Seeding without one would be asking the index to find something the
    library does not contain.
    """
    asset_id = new_id()
    write(
        db_path(client),
        [
            (
                "INSERT OR IGNORE INTO library_roots (id, name, abs_path, created_at) "
                "VALUES (?, 'indexed', '/indexed', ?)",
                (_INDEXED_ROOT, EPOCH),
            ),
            (
                "INSERT INTO assets (id, identity, media_type, size_bytes, original_filename, "
                "added_at) VALUES (?, ?, 'video', 1, ?, ?)",
                (asset_id, f"digest-{name}", f"{name}.mp4", EPOCH),
            ),
            (
                "INSERT INTO asset_locations (id, asset_id, root_id, folder_id, rel_path, "
                "filename, first_seen_at, last_seen_at) VALUES (?, ?, ?, NULL, ?, ?, ?, ?)",
                (new_id(), asset_id, _INDEXED_ROOT, f"{name}.mp4", f"{name}.mp4", EPOCH, EPOCH),
            ),
        ],
    )
    return asset_id


def _wait_for(client: TestClient, term: str) -> list[str]:
    """Poll until the term is findable, submitting the search each round, as a retrying person
    does."""
    for _ in range(150):
        submit(client, term)
        ids, _total = found(client, q=term)
        if ids:
            return ids
        time.sleep(0.1)
    return []


@pytest.mark.indexes_itself
def test_a_booted_application_indexes_the_library_it_finds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Starting up over an un-indexed library makes it searchable.

    This is the fresh install, the restored backup, and the database written before there was an
    index at all. The asset is in place BEFORE the application starts, so nothing but the boot pass
    can account for it being findable.
    """
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()

    # One application to lay the library down and shut down again. Its own boot pass indexed an
    # empty database, so what it leaves behind is a library the index knows nothing about.
    try:
        with TestClient(create_app()) as first:
            sign_in(first, "admin", who="lays-it-down")
            asset_id = _add(first, "alreadyhere")
            path = db_path(first)
    finally:
        get_settings.cache_clear()

    # The handler registry is process-global, so a second application in the same test would refuse
    # to claim the types the first one already did. The suite clears it between tests the same way;
    # this test is the one place two applications run inside one.
    worker_pool._HANDLERS.clear()
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as second:
            assert db_path(second) == path, "the second application opened a different database"
            sign_in(second, "admin", who="finds-it")
            assert _wait_for(second, "alreadyhere") == [asset_id], (
                "starting up did not index the library that was already there"
            )
    finally:
        get_settings.cache_clear()


@pytest.mark.indexes_itself
def test_an_asset_added_while_running_becomes_findable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """And the other mechanism: something that arrives after the boot pass has been and gone.

    Nothing here waits for a timer, because there is no timer: a search notices the index is
    behind and asks for a catch-up, which is why the polling below works at all. The first asset is
    waited for deliberately: it proves the boot pass has finished, so the second cannot be picked
    up by it.
    """
    client = _running(tmp_path, monkeypatch)
    try:
        with client:
            sign_in(client, "admin", who="adds-later")

            settled = _add(client, "beforeboot")
            assert _wait_for(client, "beforeboot") == [settled], "the boot pass never ran"

            later = _add(client, "addedlater")
            assert _wait_for(client, "addedlater") == [later], (
                "an asset added while running never became findable"
            )
    finally:
        get_settings.cache_clear()


@pytest.mark.indexes_itself
def test_an_idle_library_queues_no_work_at_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An idle Sift looks idle, and that is a rule rather than a nicety.

    A timer would put a line on the jobs dashboard every few seconds that never meant anything.
    Work is queued when there is some, so a library with nothing to index queues nothing, for ever.
    """
    client = _running(tmp_path, monkeypatch)
    try:
        with client:
            sign_in(client, "admin", who="idles")

            asset_id = _add(client, "settled")
            assert _wait_for(client, "settled") == [asset_id]

            # Everything is indexed. Searching repeatedly must not accumulate work (submitted
            # searches included, since those are the ones that ask).
            for _ in range(5):
                submit(client, "settled")
                found(client, q="settled")
                submit(client, "nothing at all")
                found(client, q="nothing at all")
            time.sleep(0.5)

            queued = read(
                db_path(client),
                "SELECT id FROM jobs WHERE type = 'fts_reindex' AND state = 'queued'",
            )
            assert queued == [], "an idle library is still queueing reindex work"
    finally:
        get_settings.cache_clear()


# --- saved searches ---------------------------------------------------------------------------


def test_a_search_can_be_saved_named_and_read_back(client: TestClient, world: World) -> None:
    sign_in(client, "admin")

    assert (
        client.post(
            "/api/search/saved", json={"name": "Beach clips", "query": "tags:beach type:video"}
        ).status_code
        == 204
    )

    body = client.get("/api/search/saved").json()
    assert len(body["items"]) == 1
    assert body["items"][0]["name"] == "Beach clips"
    assert body["items"][0]["query"] == "tags:beach type:video"
    assert body["items"][0]["id"]


def test_saving_under_a_used_name_replaces_the_query_rather_than_duplicating(
    client: TestClient, world: World
) -> None:
    """The name is the handle, so saving 'Beach clips' again is editing it, not making a second."""
    sign_in(client, "admin")
    client.post("/api/search/saved", json={"name": "Beach clips", "query": "tags:beach"})
    client.post("/api/search/saved", json={"name": "Beach clips", "query": "tags:beach rating:4"})

    body = client.get("/api/search/saved").json()
    assert len(body["items"]) == 1
    assert body["items"][0]["query"] == "tags:beach rating:4"


def test_an_account_may_not_keep_more_saved_searches_than_the_cap(
    client: TestClient, world: World
) -> None:
    """A guest may write here and a sign-in can be taken over, so the row count is bounded.

    Refused at the cap rather than trimmed to it: these are somebody's own named searches, and
    dropping the oldest to make room would lose one they meant to keep. Replacing a name they
    already hold is still allowed, because it adds nothing, which is the case that makes a naive
    "count >= cap" check wrong.
    """
    from sift.slices.search.service import MAX_SAVED_SEARCHES

    sign_in(client, "admin")
    for index in range(MAX_SAVED_SEARCHES):
        assert (
            client.post(
                "/api/search/saved", json={"name": f"Search {index}", "query": "tags:beach"}
            ).status_code
            == 204
        )

    full = client.post("/api/search/saved", json={"name": "One more", "query": "tags:beach"})
    assert full.status_code == 409
    assert str(MAX_SAVED_SEARCHES) in full.json()["detail"]

    # Editing one they already have is not adding one, so it still goes through at the cap.
    assert (
        client.post(
            "/api/search/saved", json={"name": "Search 0", "query": "tags:sunset"}
        ).status_code
        == 204
    )
    assert len(client.get("/api/search/saved").json()["items"]) == MAX_SAVED_SEARCHES


def test_a_saved_search_can_be_deleted(client: TestClient, world: World) -> None:
    sign_in(client, "admin")
    client.post("/api/search/saved", json={"name": "Keep", "query": "tags:beach"})
    saved_id = client.get("/api/search/saved").json()["items"][0]["id"]

    assert client.delete(f"/api/search/saved/{saved_id}").status_code == 204
    assert client.get("/api/search/saved").json()["items"] == []


def test_saved_searches_belong_to_the_account_that_made_them(
    client: TestClient, world: World
) -> None:
    """One user's saved searches are never another's, and one user cannot delete another's
    by naming its id: the delete is scoped to the asker, so a known id from elsewhere is inert."""
    sign_in(client, "admin")
    client.post("/api/search/saved", json={"name": "Private", "query": "tags:beach"})
    admin_id = client.get("/api/search/saved").json()["items"][0]["id"]

    sign_in(client, "guest")
    assert client.get("/api/search/saved").json()["items"] == []
    # A guest naming an admin's saved-search id deletes nothing.
    assert client.delete(f"/api/search/saved/{admin_id}").status_code == 204

    sign_in(client, "admin")
    assert len(client.get("/api/search/saved").json()["items"]) == 1


def test_renaming_a_saved_search_keeps_the_query_it_points_at(
    client: TestClient, world: World
) -> None:
    """The half saving cannot do.

    Saving under a used name replaces that name's query, so editing the query is a save; calling a
    search something else is this. What this asserts is the part
    that makes it a rename rather than a second save: the query is untouched.
    """
    sign_in(client, "admin")
    client.post("/api/search/saved", json={"name": "Beach clips", "query": "tags:beach type:video"})
    saved_id = client.get("/api/search/saved").json()["items"][0]["id"]

    assert client.patch(f"/api/search/saved/{saved_id}", json={"name": "Summer"}).status_code == 204

    body = client.get("/api/search/saved").json()
    assert len(body["items"]) == 1
    assert body["items"][0]["name"] == "Summer"
    assert body["items"][0]["query"] == "tags:beach type:video"


def test_one_account_cannot_rename_anothers_saved_search(client: TestClient, world: World) -> None:
    """Scoped in the statement, exactly as the delete is.

    A guest naming an admin's id renames nothing, and is told not-found rather than refused:
    the two answers are one on purpose, because telling them apart says the row exists.
    """
    sign_in(client, "admin")
    client.post("/api/search/saved", json={"name": "Private", "query": "tags:beach"})
    admin_id = client.get("/api/search/saved").json()["items"][0]["id"]

    sign_in(client, "guest")
    assert client.patch(f"/api/search/saved/{admin_id}", json={"name": "Mine"}).status_code == 404

    sign_in(client, "admin")
    kept = client.get("/api/search/saved").json()["items"][0]
    assert kept["name"] == "Private"
    assert kept["query"] == "tags:beach"


def test_renaming_a_saved_search_that_is_not_there_is_a_plain_not_found(
    client: TestClient, world: World
) -> None:
    sign_in(client, "admin")
    assert client.patch("/api/search/saved/nope", json={"name": "x"}).status_code == 404


def test_renaming_onto_a_name_already_used_is_refused_rather_than_merging(
    client: TestClient, world: World
) -> None:
    """The opposite of what SAVING does, on purpose.

    Saving under a used name is somebody editing that search. Renaming onto a used name is
    somebody about to lose one, so it is refused, and refused as an ordinary answer they can act
    on rather than as a server fault, which is what the bare database error would have been.
    """
    sign_in(client, "admin")
    client.post("/api/search/saved", json={"name": "Beach", "query": "tags:beach"})
    client.post("/api/search/saved", json={"name": "Summer", "query": "tags:sun"})
    beach = next(
        item["id"]
        for item in client.get("/api/search/saved").json()["items"]
        if item["name"] == "Beach"
    )

    clash = client.patch(f"/api/search/saved/{beach}", json={"name": "Summer"})

    assert clash.status_code == 409
    assert "Summer" in clash.json()["detail"]
    # And nothing moved: both are still there, under their own names, with their own queries.
    kept = {item["name"]: item["query"] for item in client.get("/api/search/saved").json()["items"]}
    assert kept == {"Beach": "tags:beach", "Summer": "tags:sun"}


def test_a_renamed_saved_search_still_needs_a_name(client: TestClient, world: World) -> None:
    sign_in(client, "admin")
    client.post("/api/search/saved", json={"name": "Keep", "query": "tags:beach"})
    saved_id = client.get("/api/search/saved").json()["items"][0]["id"]

    assert client.patch(f"/api/search/saved/{saved_id}", json={"name": ""}).status_code == 422
    assert client.patch(f"/api/search/saved/{saved_id}", json={"name": "   "}).status_code == 422


def test_a_kept_filter_says_which_wall_it_is_about(client: TestClient, world: World) -> None:
    """The panel offers a kept filter on the wall it was kept on and nowhere else, so the row has
    to carry that wall back with it.

    Defaulted rather than required: everything kept before the walls were told apart is a question
    about files, and so is anything a client sends without saying.
    """
    sign_in(client, "admin")
    client.post(
        "/api/search/saved", json={"name": "Tall ones", "query": "hair=brown", "kind": "person"}
    )
    client.post("/api/search/saved", json={"name": "Long ones", "query": "duration:20m+"})

    kept = {item["name"]: item["kind"] for item in client.get("/api/search/saved").json()["items"]}

    assert kept == {"Tall ones": "person", "Long ones": "asset"}


def test_one_name_on_two_walls_is_two_filters(client: TestClient, world: World) -> None:
    """A name belongs to a wall.

    Under one name per user, keeping "Favourites" on People would have upserted over the
    "Favourites" already kept on the library: a filter about files silently replaced by a question
    about people, under a name that still looked right in the list. Two rows, and saving the same
    name on the SAME wall still replaces, because that is what editing one is.
    """
    sign_in(client, "admin")
    client.post("/api/search/saved", json={"name": "Favourites", "query": "fav:yes"})
    client.post(
        "/api/search/saved", json={"name": "Favourites", "query": "mine=yes", "kind": "person"}
    )

    items = client.get("/api/search/saved").json()["items"]
    assert {(one["kind"], one["query"]) for one in items} == {
        ("asset", "fav:yes"),
        ("person", "mine=yes"),
    }

    client.post("/api/search/saved", json={"name": "Favourites", "query": "fav:yes tags:beach"})
    items = client.get("/api/search/saved").json()["items"]
    assert len(items) == 2
    assert {one["query"] for one in items} == {"fav:yes tags:beach", "mine=yes"}


def test_a_filter_cannot_be_kept_on_a_wall_that_does_not_exist(
    client: TestClient, world: World
) -> None:
    """The same refusal the box's memory makes, for the same reason: a kind no wall answers to is a
    filter stored against somebody's cap and drawn to them nowhere."""
    sign_in(client, "admin")

    refused = client.post(
        "/api/search/saved", json={"name": "Nowhere", "query": "fav:yes", "kind": "sculpture"}
    )

    assert refused.status_code == 422
    assert client.get("/api/search/saved").json()["items"] == []


def test_a_name_is_free_on_another_wall_when_a_rename_asks_for_it(
    client: TestClient, world: World
) -> None:
    """Renaming is refused only by a name held on the SAME wall.

    Asked any wider, somebody renaming a People filter would be told a name was taken while the
    list in front of them did not hold it: an answer they cannot act on, about a row they cannot
    see from there.
    """
    sign_in(client, "admin")
    client.post("/api/search/saved", json={"name": "Long ones", "query": "duration:20m+"})
    client.post(
        "/api/search/saved", json={"name": "Everyone", "query": "mine=yes", "kind": "person"}
    )
    theirs = next(
        one["id"]
        for one in client.get("/api/search/saved").json()["items"]
        if one["kind"] == "person"
    )

    assert (
        client.patch(f"/api/search/saved/{theirs}", json={"name": "Long ones"}).status_code == 204
    )

    # And the library's own row is untouched: two filters, one name, two walls.
    kept = {(one["kind"], one["name"]) for one in client.get("/api/search/saved").json()["items"]}
    assert kept == {("asset", "Long ones"), ("person", "Long ones")}


def test_a_saved_search_needs_a_name(client: TestClient, world: World) -> None:
    sign_in(client, "admin")
    # Pydantic refuses an empty name at the edge.
    assert client.post("/api/search/saved", json={"name": "", "query": "x"}).status_code == 422
    # Whitespace-only trims to empty and the service refuses it.
    assert client.post("/api/search/saved", json={"name": "   ", "query": "x"}).status_code == 422
