# SPDX-License-Identifier: AGPL-3.0-or-later
"""A saved filter goes with the last thing it named, and one that holds anything else stays."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sift.kernel import changes
from sift.kernel.audience import Audience
from sift.kernel.changes import About
from sift.kernel.ids import new_id
from sift.slices.search.tests.conftest import World, db_path, put_song, read, sign_in, write
from sift.testing.auth import hide_for_caller


def _keep(client: TestClient, name: str, query: str, kind: str = "asset") -> None:
    answer = client.post("/api/search/saved", json={"name": name, "query": query, "kind": kind})
    assert answer.status_code == 204, answer.text


def _kept(client: TestClient) -> dict[str, dict[str, object]]:
    return {one["name"]: one for one in client.get("/api/search/saved").json()["items"]}


def _lines(client: TestClient) -> list[str]:
    """Every line the History feed says about a saved filter, as it reads."""
    items = client.get("/api/ledger", params={"kind": "saved_filter"}).json()["items"]
    return ["".join(piece["text"] for piece in one["pieces"]) for one in items]


def test_a_filter_naming_only_the_deleted_tag_goes_and_one_holding_more_stays(
    client: TestClient, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = sign_in(client, "admin")
    _keep(client, "Only beach", "tags=beach")
    _keep(client, "Beach videos", "tags=beach&media=video")
    told: list[tuple[object, object]] = []
    monkeypatch.setattr(changes, "announce", lambda who, about: told.append((who, about)))

    assert client.delete(f"/api/tags/{world.tag_beach}").status_code == 204

    kept = _kept(client)
    assert list(kept) == ["Beach videos"]
    assert kept["Beach videos"]["named"] == [
        {"field": "tags", "value": world.tag_beach, "name": None}
    ]
    assert _lines(client) == [
        "You deleted the saved filter Only beach: the tag it named was deleted"
    ]
    assert (Audience.of_user(owner), About.MINE) in told
    [event] = read(
        db_path(client),
        "SELECT object_kind, object_id, object_name, payload FROM workbench_decisions"
        " WHERE verb = 'deleted' AND id IN (SELECT decision_id FROM workbench_decision_subjects"
        " WHERE kind = 'saved_filter')",
    )
    assert (event["object_kind"], event["object_id"], event["object_name"]) == (
        "tag",
        world.tag_beach,
        "beach",
    )
    assert json.loads(str(event["payload"])) == {
        "named": "tag",
        "of": owner,
        "called": "Only beach",
    }


def test_a_list_goes_only_with_its_last_value_and_words_keep_it(
    client: TestClient, world: World
) -> None:
    sign_in(client, "admin")
    _keep(client, "Either", "tags=beach|city")
    _keep(client, "Not beach", "tags=-beach")
    _keep(client, "Beach typed", "q=tags:beach")
    _keep(client, "Beach and words", "q=tags:beach sunset")

    assert client.delete(f"/api/tags/{world.tag_city}").status_code == 204
    assert set(_kept(client)) == {"Either", "Not beach", "Beach typed", "Beach and words"}

    assert client.delete(f"/api/tags/{world.tag_beach}").status_code == 204
    assert set(_kept(client)) == {"Beach and words"}
    assert len(_lines(client)) == 1


def test_two_filters_one_delete_took_are_one_line(client: TestClient, world: World) -> None:
    sign_in(client, "admin")
    _keep(client, "Clicked", "tags=beach")
    _keep(client, "Typed", "q=tags:beach")

    assert client.delete(f"/api/tags/{world.tag_beach}").status_code == 204

    assert _lines(client) == ["You deleted 2 saved filters: the tag they named was deleted"]


def test_a_value_hidden_from_its_owner_is_still_there(client: TestClient, world: World) -> None:
    sign_in(client, "admin")
    hide_for_caller(client, "tag", world.tag_city)
    _keep(client, "Either", f"tags={world.tag_beach}|{world.tag_city}")

    assert client.delete(f"/api/tags/{world.tag_beach}").status_code == 204

    assert set(_kept(client)) == {"Either"}


def test_a_filter_of_a_user_who_is_turned_off_is_left_alone(
    client: TestClient, world: World
) -> None:
    owner = sign_in(client, "admin", who="owner")
    _keep(client, "Mine", "tags=city")
    write(db_path(client), [("UPDATE users SET disabled = 1 WHERE id = ?", (owner,))])
    sign_in(client, "admin", who="tidier")

    assert client.delete(f"/api/tags/{world.tag_city}").status_code == 204

    assert read(db_path(client), "SELECT name FROM saved_searches") == [{"name": "Mine"}]


def _song(client: TestClient, world: World) -> str:
    return put_song(db_path(client), world.beach, "Harbour Lights")


@pytest.mark.parametrize(
    ("field", "thing", "address"),
    [
        ("people", lambda c, w: w.person, "/api/people/{}"),
        ("sites", lambda c, w: w.site, "/api/sites/{}"),
        ("collections", lambda c, w: w.collection, "/api/collections/{}"),
        ("photo_sets", lambda c, w: w.photo_set, "/api/photo-sets/{}"),
        ("songs", _song, "/api/songs/{}"),
        ("in", lambda c, w: w.holiday, "/api/folders/{}"),
    ],
)
def test_every_delete_takes_the_filter_that_named_only_it(
    client: TestClient,
    world: World,
    field: str,
    thing: Callable[[TestClient, World], str],
    address: str,
) -> None:
    sign_in(client, "admin")
    gone = thing(client, world)
    _keep(client, "Only it", f"{field}={gone}")
    _keep(client, "It and a tag", f"{field}={gone}&tags={world.tag_city}")

    assert client.delete(address.format(gone)).status_code in (200, 204)

    assert set(_kept(client)) == {"It and a tag"}
    assert len(_lines(client)) == 1


def test_a_wall_of_things_filter_goes_with_its_last_tag(client: TestClient, world: World) -> None:
    sign_in(client, "admin")
    _keep(client, "City people", f"tags={world.tag_city}", kind="person")
    _keep(client, "City women", f"tags={world.tag_city}&gender=female", kind="person")
    _keep(client, "City networks", f"parent={world.site}", kind="site")
    _keep(
        client,
        "City or beach",
        f"tags={world.tag_city}&tags={world.tag_beach}&sort=newest",
        kind="person",
    )

    assert client.delete(f"/api/tags/{world.tag_city}").status_code == 204

    assert set(_kept(client)) == {"City women", "City networks", "City or beach"}


def test_another_users_filter_goes_and_the_line_names_who_deleted(
    client: TestClient, world: World
) -> None:
    sign_in(client, "admin", who="owner")
    _keep(client, "Mine", "tags=city")
    sign_in(client, "admin", who="tidier")

    assert client.delete(f"/api/tags/{world.tag_city}").status_code == 204

    sign_in(client, "admin", who="owner")
    assert _kept(client) == {}
    assert _lines(client) == [
        "search-admin-tidier deleted the saved filter Mine: the tag it named was deleted"
    ]


def test_a_folder_delete_takes_the_filters_of_its_subfolders(
    client: TestClient, world: World
) -> None:
    sign_in(client, "admin")
    [root] = read(db_path(client), "SELECT abs_path FROM library_roots")
    (Path(str(root["abs_path"])) / "clips" / "holiday" / "deeper").mkdir()
    deeper = new_id()
    write(
        db_path(client),
        [
            (
                "INSERT INTO folders (id, root_id, parent_id, rel_path, name)"
                " VALUES (?, ?, ?, ?, ?)",
                (deeper, world.root, world.holiday, "clips/holiday/deeper", "deeper"),
            )
        ],
    )
    _keep(client, "Holiday", f"in={world.holiday}")
    _keep(client, "Deeper", f"in={deeper}")

    assert client.delete(f"/api/folders/{world.holiday}").status_code == 200

    assert _kept(client) == {}
    assert _lines(client) == ["You deleted 2 saved filters: the folder they named was deleted"]


def test_a_folder_that_is_not_there_takes_no_filter(client: TestClient, world: World) -> None:
    sign_in(client, "admin")
    _keep(client, "Holiday", f"in={world.holiday}")

    assert client.delete(f"/api/folders/{new_id()}").status_code == 404

    assert set(_kept(client)) == {"Holiday"}


def _feed(client: TestClient) -> str:
    return str(client.get("/api/ledger", params={"kind": "saved_filter"}).text)


def test_only_the_owner_reads_the_name_of_a_saved_filter(client: TestClient, world: World) -> None:
    sign_in(client, "admin", who="owner")
    _keep(client, "Harbour errands", "tags=city")
    sign_in(client, "admin", who="tidier")

    assert client.delete(f"/api/tags/{world.tag_city}").status_code == 204

    assert _lines(client) == ["You deleted a saved filter: the tag it named was deleted"]
    assert "Harbour errands" not in _feed(client)
    sign_in(client, "admin", who="owner")
    assert "Harbour errands" in _feed(client)


def test_a_fold_names_only_the_readers_own_filters(client: TestClient, world: World) -> None:
    sign_in(client, "admin", who="owner")
    _keep(client, "Harbour errands", "tags=city")
    sign_in(client, "admin", who="tidier")
    _keep(client, "Tidy city", "tags=city")

    assert client.delete(f"/api/tags/{world.tag_city}").status_code == 204

    assert _lines(client) == ["You deleted 2 saved filters: the tag they named was deleted"]
    assert "Tidy city" in _feed(client)
    assert "Harbour errands" not in _feed(client)
    sign_in(client, "admin", who="owner")
    assert "Harbour errands" in _feed(client)
    assert "Tidy city" not in _feed(client)


def test_the_name_is_kept_off_every_reader_that_does_not_know_whose_it_was(
    client: TestClient, world: World
) -> None:
    owner = sign_in(client, "admin")
    _keep(client, "Harbour errands", "tags=city")

    assert client.delete(f"/api/tags/{world.tag_city}").status_code == 204

    [subject] = read(
        db_path(client), "SELECT name FROM workbench_decision_subjects WHERE kind = 'saved_filter'"
    )
    assert subject["name"] is None
    assert "Harbour errands" not in client.get(f"/api/tags/{world.tag_city}/history").text
    [event] = read(
        db_path(client),
        "SELECT payload FROM workbench_decisions WHERE id IN"
        " (SELECT decision_id FROM workbench_decision_subjects WHERE kind = 'saved_filter')",
    )
    assert json.loads(str(event["payload"])) == {
        "named": "tag",
        "of": owner,
        "called": "Harbour errands",
    }
