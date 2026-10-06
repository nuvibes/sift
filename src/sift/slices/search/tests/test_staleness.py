# SPDX-License-Identifier: AGPL-3.0-or-later
"""Editing something already indexed, and finding it afterwards.

The index notices arrivals by an anti-join; an edit to an indexed row has to queue its own
reindex. Each test is the sequence a person performs: edit, then search the new words and the old.
"""

from __future__ import annotations

import json
from contextlib import AbstractAsyncContextManager
from typing import cast

import pytest
from fastapi.testclient import TestClient

from sift.kernel.db import Connection
from sift.slices.search.tests.conftest import (
    PASSWORD,
    World,
    db_path,
    found,
    read,
    reindex,
    sign_in,
    submit,
    write,
)

pytestmark = [pytest.mark.integration]


def _queued(client: TestClient) -> list[dict[str, object]]:
    rows = read(
        db_path(client),
        "SELECT id, payload FROM jobs WHERE type = ? AND state = ?",
        ("fts_reindex", "queued"),
    )
    return [{"id": row["id"], **json.loads(str(row["payload"]))} for row in rows]


def _drain(client: TestClient) -> None:
    """Run the reindex the last write queued, and nothing if it queued none, so the code under
    test must have asked for it: a rename's named files, or a whole-library pass."""
    queued = _queued(client)
    for job in queued:
        named = job.get("asset_ids")
        if isinstance(named, list):
            reindex(db_path(client), asset_ids=[str(one) for one in named])
            write(db_path(client), [("DELETE FROM jobs WHERE id = ?", (job["id"],))])
    # Only the whole-library pass sees an edit, not a catch-up.
    whole_library = [
        job
        for job in queued
        if job.get("scope") != "catch_up" and job.get("asset_id") is None and "asset_ids" not in job
    ]
    if not whole_library:
        return
    reindex(db_path(client))


def test_renaming_a_tag_makes_it_findable_by_the_new_name(client: TestClient, world: World) -> None:
    """Renaming a tag makes it findable by the new name and not the old. The `city` tag, since the
    beach clip's filename matches "beach" whatever its tags say."""
    sign_in(client)

    before, _ = found(client, q="city")
    assert world.walk in before

    renamed = client.put(f"/api/tags/{world.tag_city}", json={"name": "metropolis"})
    assert renamed.status_code == 200
    _drain(client)

    by_new_name, _ = found(client, q="metropolis")
    by_old_name, _ = found(client, q="city")

    assert world.walk in by_new_name, "the tag's new name finds nothing"
    assert world.walk not in by_old_name, "the tag's old name still finds it"


def test_putting_a_tag_on_a_clip_makes_the_clip_findable_by_it(
    client: TestClient, world: World
) -> None:
    """One asset, so it is reindexed inside the request rather than queued. No drain here, and
    that absence is the assertion: the next search is right, not eventually right."""
    sign_in(client)

    # The walk clip carries `city` already; `beach` is the one it does not have, and its filename
    # does not contain that word either, so a match afterwards can only have come from the tag.
    assert world.walk not in found(client, q="beach")[0]

    assigned = client.post(
        "/api/assets/tags",
        json={"asset_ids": [world.walk], "tag_ids": [world.tag_beach], "add": True},
    )
    assert assigned.status_code == 200

    assert world.walk in found(client, q="beach")[0]


def test_taking_a_tag_off_a_clip_stops_it_being_found_by_it(
    client: TestClient, world: World
) -> None:
    """The other direction. Worth its own test: an index that only ever gains text would pass the
    one above and still leave a removed tag findable for ever."""
    sign_in(client)
    assert world.walk in found(client, q="city")[0]

    client.post(
        "/api/assets/tags",
        json={"asset_ids": [world.walk], "tag_ids": [world.tag_city], "add": False},
    )

    assert world.walk not in found(client, q="city")[0]


def test_deleting_a_tag_stops_its_name_finding_anything(client: TestClient, world: World) -> None:
    sign_in(client)
    assert found(client, q="city")[0] != []

    client.delete(f"/api/tags/{world.tag_city}")
    _drain(client)

    assert found(client, q="city")[0] == []


def test_renaming_a_person_makes_them_findable_by_the_new_name(
    client: TestClient, world: World
) -> None:
    """People are indexed the same way tags are, and refreshed the same way."""
    sign_in(client)

    client.put(f"/api/people/{world.person}", json={"name": "Jane Roe"})
    _drain(client)

    assert world.beach in found(client, q="Roe")[0]
    assert world.beach not in found(client, q="Doe")[0]


def test_adding_an_alias_makes_the_person_findable_by_it(client: TestClient, world: World) -> None:
    """An alias is indexed beside the name it belongs to, so adding one is a rename in effect."""
    sign_in(client)

    added = client.post(f"/api/people/{world.person}/aliases", json={"alias": "Janey"})
    assert added.status_code == 201
    _drain(client)

    assert world.beach in found(client, q="Janey")[0]


def test_putting_a_person_on_a_clip_makes_it_findable_by_their_name(
    client: TestClient, world: World
) -> None:
    sign_in(client)
    assert world.walk not in found(client, q="Jane")[0]

    client.post(
        "/api/assets/people",
        json={"asset_ids": [world.walk], "person_ids": [world.person], "add": True},
    )

    assert world.walk in found(client, q="Jane")[0]


# --- the seam's own behaviour ----------------------------------------------------------------


def test_renaming_a_tag_rewrites_its_own_files_and_rebuilds_nothing(
    client: TestClient, world: World
) -> None:
    """A tag rename queues its own files for a job and no whole-library rebuild. The control: a
    file the tag is not on stays findable only by its old filename."""
    sign_in(client)
    write(
        db_path(client),
        [
            (
                "INSERT INTO asset_tags (asset_id, tag_id) VALUES (?, ?)",
                (world.beach, world.tag_city),
            ),
            (
                "UPDATE assets SET original_filename = ? WHERE id = ?",
                ("zelkova.mp4", world.private),
            ),
        ],
    )

    client.put(f"/api/tags/{world.tag_city}", json={"name": "metropolis"})
    client.put(f"/api/tags/{world.tag_beach}", json={"name": "seaside"})
    client.put(f"/api/tags/{world.tag_city}", json={"name": "urban"})

    queued = _queued(client)
    assert all("asset_ids" in job for job in queued), "a rename queued a whole-library rebuild"
    named = sorted(str(one) for job in queued for one in cast(list[str], job["asset_ids"]))
    assert named == sorted([world.beach, world.walk, world.beach, world.beach, world.walk])
    # The answer came first: nothing was rewritten inside the requests.
    assert found(client, q="urban")[0] == []

    _drain(client)
    assert sorted(found(client, q="urban")[0]) == sorted([world.beach, world.walk])
    assert world.beach in found(client, q="seaside")[0]

    assert found(client, q="zelkova")[0] == [], "the rename reindexed a file the tag is not on"
    assert world.private in found(client, q="private_notes")[0]


@pytest.mark.anyio
async def test_a_failure_to_reindex_does_not_undo_the_write_that_caused_it() -> None:
    """A failure to reindex is logged, not raised: the tag is already on the clip."""
    from sift.slices.search.reindex import Reindexer

    class Broken:
        async def list(self, **_: object) -> object:
            raise RuntimeError("the queue is unavailable")

        async def enqueue(self, *_: object, **__: object) -> None:
            raise RuntimeError("the queue is unavailable")

    class BrokenDatabase:
        def write(self) -> object:
            raise RuntimeError("the database is unavailable")

    seam = Reindexer(database=BrokenDatabase(), queue=Broken())  # type: ignore[arg-type]

    # Neither raises. That is the whole assertion.
    await seam.touched("whatever")
    await seam.renamed()


# --- collections and sites are findable by name ------------------------------------------


def test_a_collection_name_finds_what_is_in_it(client: TestClient, world: World) -> None:
    """Typing a collection's name finds its contents."""
    sign_in(client)

    client.post(
        f"/api/collections/{world.collection}/items",
        json={"action": "add", "asset_ids": [world.walk]},
    )

    assert world.walk in found(client, q="Best of")[0]


def test_renaming_a_collection_moves_what_finds_it(client: TestClient, world: World) -> None:
    sign_in(client)
    client.post(
        f"/api/collections/{world.collection}/items",
        json={"action": "add", "asset_ids": [world.walk]},
    )
    assert world.walk in found(client, q="Best of")[0]

    client.put(f"/api/collections/{world.collection}", json={"name": "Keepers"})
    _drain(client)

    assert world.walk in found(client, q="Keepers")[0]
    assert world.walk not in found(client, q="Best of")[0]


def test_taking_an_asset_out_of_a_collection_stops_its_name_finding_it(
    client: TestClient, world: World
) -> None:
    sign_in(client)
    client.post(
        f"/api/collections/{world.collection}/items",
        json={"action": "add", "asset_ids": [world.walk]},
    )
    assert world.walk in found(client, q="Best of")[0]

    client.post(
        f"/api/collections/{world.collection}/items",
        json={"action": "remove", "asset_ids": [world.walk]},
    )

    assert world.walk not in found(client, q="Best of")[0]


def test_joining_a_handle_to_somebody_reindexes_the_alias_it_writes_onto_them(
    client: TestClient, world: World
) -> None:
    """Joining a username to a person writes it as an alias (`attach_username`'s `as_alias` defaults
    to true), so every file of theirs is reindexed, including one the username is not on."""
    sign_in(client)
    assert (
        client.put(f"/api/usernames/{world.username}", json={"person_id": None}).status_code == 200
    )
    write(
        db_path(client),
        [
            (
                "INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)",
                (world.walk, world.person),
            )
        ],
    )

    joined = client.put(f"/api/usernames/{world.username}", json={"person_id": world.person})
    assert joined.status_code == 200, joined.text

    # Nothing drained: the username's own file and every other file of theirs were both rewritten.
    assert sorted(found(client, q="janed")[0]) == sorted([world.beach, world.walk])


def test_a_site_name_finds_what_came_from_it(client: TestClient, world: World) -> None:
    """The site something was downloaded from is a thing people remember and search for."""
    sign_in(client)

    assert world.beach in found(client, q="TikTok")[0]


def test_renaming_a_site_moves_what_finds_it(client: TestClient, world: World) -> None:
    sign_in(client)
    assert world.beach in found(client, q="TikTok")[0]

    client.put(f"/api/sites/{world.site}", json={"name": "ShortClips"})
    _drain(client)

    assert world.beach in found(client, q="ShortClips")[0]
    assert world.beach not in found(client, q="TikTok")[0]


def test_a_vaulted_collection_name_finds_nothing_while_the_vault_is_shut(
    client: TestClient, world: World
) -> None:
    """A vaulted collection's name is indexed, but the scoped query conceals at search time, so it
    finds nothing while the vault is shut."""
    sign_in(client)
    client.post(
        f"/api/collections/{world.collection}/items",
        json={"action": "add", "asset_ids": [world.walk]},
    )
    assert world.walk in found(client, q="Best of")[0]

    client.put("/api/auth/pin", json={"pin": "918273", "current_password": PASSWORD})
    vaulted = client.put(f"/api/collections/{world.collection}/vault", json={"vault": True})
    assert vaulted.status_code == 204
    _drain(client)

    # The name is still in the index, and finds nothing, because the asset is concealed.
    assert found(client, q="Best of")[0] == []


def test_a_merge_is_not_silenced_by_a_catch_up_already_waiting(
    client: TestClient, world: World
) -> None:
    """A merge still queues the whole-library pass when a catch-up (queued by any search that finds
    the index behind) is already waiting: the guard ignores catch-ups."""
    sign_in(client)
    write(
        db_path(client),
        [
            (
                "INSERT INTO assets (id, identity, media_type, size_bytes, original_filename, "
                "added_at) VALUES ('newly-arrived', 'a-hash', 'video', 1024, 'arrived.mp4', 0)",
                (),
            )
        ],
    )

    survivor = client.post("/api/people", json={"name": "Odile Fenwick"}).json()["id"]

    submit(client, "anything")  # a SUBMITTED search is what queues the catch-up

    waiting = read(
        db_path(client),
        "SELECT payload FROM jobs WHERE type = ? AND state = ?",
        ("fts_reindex", "queued"),
    )
    assert waiting, "the catch-up this test depends on was never queued"

    merged = client.post("/api/people/merge", json={"into": survivor, "people": [world.person]})
    assert merged.status_code == 200, merged.text

    rebuilds = [
        row
        for row in read(
            db_path(client),
            "SELECT payload FROM jobs WHERE type = ? AND state = ?",
            ("fts_reindex", "queued"),
        )
        if json.loads(str(row["payload"])).get("scope") != "catch_up"
    ]
    assert rebuilds, "the merge queued nothing, so it will never be indexed"

    _drain(client)
    assert world.beach in found(client, q="Odile")[0]


def test_placeholder_mode_answers_a_guessed_name_with_a_locked_tile(
    client: TestClient, world: World
) -> None:
    """In placeholder mode a guessed name in free text returns locked tiles, confirming it exists;
    `collections:` and the dropdown still refuse, through the scoped lister."""
    sign_in(client)
    client.post(
        f"/api/collections/{world.collection}/items",
        json={"action": "add", "asset_ids": [world.walk]},
    )
    client.put("/api/auth/pin", json={"pin": "918273", "current_password": PASSWORD})
    assert (
        client.put(f"/api/collections/{world.collection}/vault", json={"vault": True}).status_code
        == 204
    )
    _drain(client)

    # The default: absent from the search box entirely.
    assert found(client, q="Best of")[0] == []

    applied = client.put("/api/settings", json={"values": {"vault.concealment": "placeholder"}})
    assert applied.status_code == 204, applied.text

    ids, total = found(client, q="Best of")

    # Two tiles that say nothing about themselves; the fixture already had one in the collection.
    assert set(ids) == {world.walk, world.beach}
    assert total == 2
    body = client.get("/api/assets", params={"q": "Best of"}).json()
    assert body["items"][0]["concealed"] is True
    assert body["items"][0]["media_type"] == ""
    assert "sunsetwalk" not in str(body)

    # And the two channels that DO refuse, in the same mode, so the difference is pinned down.
    assert client.get("/api/assets", params={"collections": "Best of"}).json()["items"] == []
    suggested = client.get("/api/search/suggest", params={"q": "collections:Bes"}).json()
    assert suggested.get("matches", []) == []


def test_a_bulk_assign_opens_one_write_transaction_not_one_per_asset(
    client: TestClient, world: World
) -> None:
    """A bulk assign opens one write transaction, not one per asset, counted with a spy since the
    indexed result is the same either way."""
    sign_in(client)
    database = client.app.state.database  # type: ignore[attr-defined]
    real_write = database.write
    opened = 0

    def counting_write() -> AbstractAsyncContextManager[Connection]:
        nonlocal opened
        opened += 1
        return real_write()  # type: ignore[no-any-return]

    database.write = counting_write
    try:
        assigned = client.post(
            "/api/assets/tags",
            json={
                "asset_ids": [world.beach, world.walk, world.private],
                "tag_ids": [world.tag_city],
                "add": True,
            },
        )
    finally:
        database.write = real_write

    assert assigned.status_code == 200
    # One for the assignment itself, one for the index. Not one per asset.
    assert opened <= 2, f"{opened} write transactions for a 3-asset assign"

    # And it actually worked: the cheap version of this must not pass on its own.
    assert world.private in found(client, q="city")[0]


@pytest.mark.anyio
async def test_the_group_refresh_is_a_no_op_for_an_empty_selection_and_survives_a_failure() -> None:
    """An empty selection costs nothing, and a failing refresh after the commit does not raise."""
    from sift.slices.search.reindex import Reindexer

    class BrokenDatabase:
        def write(self) -> object:
            raise RuntimeError("the database is unavailable")

    seam = Reindexer(database=BrokenDatabase(), queue=None)  # type: ignore[arg-type]

    await seam.touched_many([])
    await seam.touched_many(["one", "two"])
