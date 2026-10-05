# SPDX-License-Identifier: AGPL-3.0-or-later
"""Move earlier and Move later: one file swapped with its neighbour, in one write."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from sift.kernel.access import Role, Viewer
from sift.kernel.ids import new_id
from sift.slices.auth import current_viewer
from sift.slices.collections import service
from sift.slices.collections.tests.conftest import (
    _INSERT_ASSET,
    NEVER_EXISTED,
    Library,
    db_path,
    edit_items,
    item_ids,
    make_collection,
    positions,
    share,
    sign_in,
    vault_asset,
    write,
)

pytestmark = [pytest.mark.integration]


def move(client: TestClient, collection_id: str, asset_ids: list[str], direction: Any) -> Any:
    return client.post(
        f"/api/collections/{collection_id}/items",
        json={"asset_ids": asset_ids, "action": "move", "direction": direction},
    )


def arranged(client: TestClient, collection_id: str) -> list[str]:
    stored = positions(client, collection_id)
    return sorted(stored, key=lambda one: stored[one])


def filled(client: TestClient, library: Library) -> str:
    sign_in(client)
    collection_id = make_collection(client, "Mixtape")
    edit_items(client, collection_id, library.all_ids)
    return collection_id


def test_a_move_swaps_a_file_with_its_neighbour_and_writes_two_rows(
    client: TestClient, library: Library
) -> None:
    collection_id = filled(client, library)
    first, second, third = library.all_ids

    answer = move(client, collection_id, [second], "earlier")
    assert answer.status_code == 200, answer.text
    assert answer.json()["changed"] == 2
    assert arranged(client, collection_id) == [second, first, third]

    assert move(client, collection_id, [second], "later").json()["changed"] == 2
    assert move(client, collection_id, [second], "later").json()["changed"] == 2
    assert arranged(client, collection_id) == [first, third, second]
    assert positions(client, collection_id) == {first: 0, third: 1, second: 2}


def test_the_first_moved_earlier_and_the_last_moved_later_change_nothing(
    client: TestClient, library: Library
) -> None:
    collection_id = filled(client, library)
    history = client.get(f"/api/collections/{collection_id}/history").json()

    assert move(client, collection_id, [library.first], "earlier").json()["changed"] == 0
    assert move(client, collection_id, [library.third], "later").json()["changed"] == 0
    assert arranged(client, collection_id) == library.all_ids
    assert client.get(f"/api/collections/{collection_id}/history").json() == history


def test_a_pinned_file_moves_in_the_arrangement_and_stays_drawn_first(
    client: TestClient, library: Library
) -> None:
    collection_id = filled(client, library)
    assert client.put(f"/api/assets/{library.third}/pin", json={"pinned": True}).status_code == 200

    assert move(client, collection_id, [library.third], "earlier").json()["changed"] == 2
    assert arranged(client, collection_id) == [library.first, library.third, library.second]
    assert item_ids(client, collection_id) == [library.third, library.first, library.second]


def test_a_file_not_in_the_collection_is_refused_as_a_reorder_refuses_it(
    client: TestClient, library: Library
) -> None:
    sign_in(client)
    collection_id = make_collection(client, "Mixtape")
    edit_items(client, collection_id, [library.first, library.second])

    for asset_id in (library.third, NEVER_EXISTED):
        refused = move(client, collection_id, [asset_id], "earlier")
        reordered = edit_items(client, collection_id, [asset_id], "reorder")
        assert (refused.status_code, refused.json()) == (404, {"detail": "not found"})
        assert (reordered.status_code, reordered.json()) == (404, {"detail": "not found"})
    assert move(client, NEVER_EXISTED, [library.first], "later").status_code == 404
    assert arranged(client, collection_id) == [library.first, library.second]


def test_a_neighbour_in_the_shut_vault_keeps_its_place(
    client: TestClient, library: Library
) -> None:
    collection_id = filled(client, library)
    vault_asset(client, library.second)

    assert move(client, collection_id, [library.third], "earlier").json()["changed"] == 2
    assert positions(client, collection_id) == {
        library.third: 0,
        library.second: 1,
        library.first: 2,
    }

    user_id = sign_in(client)
    client.app.dependency_overrides[current_viewer] = lambda: Viewer(  # type: ignore[attr-defined]
        id=user_id, role=Role.ADMIN, show_hidden=True
    )
    try:
        assert move(client, collection_id, [library.first], "earlier").json()["changed"] == 2
    finally:
        client.app.dependency_overrides.clear()  # type: ignore[attr-defined]
    assert arranged(client, collection_id) == [library.third, library.first, library.second]


def test_a_neighbour_is_found_past_a_whole_page_of_files_in_the_shut_vault(
    client: TestClient, library: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service, "MAX_PAGE_SIZE", 1)
    collection_id = filled(client, library)
    vault_asset(client, library.second)

    assert move(client, collection_id, [library.third], "earlier").json()["changed"] == 2
    assert arranged(client, collection_id) == [library.third, library.second, library.first]


def test_a_guest_is_refused_alike_whatever_the_move_names(
    client: TestClient, library: Library
) -> None:
    guest = sign_in(client, "guest")
    collection_id = filled(client, library)
    share(client, library.first, guest)
    share(client, library.second, guest)

    sign_in(client, "guest")
    answers = {
        (answer.status_code, answer.text)
        for answer in (
            move(client, collection_id, [library.second], "earlier"),
            move(client, collection_id, [library.first], "earlier"),
            move(client, collection_id, [library.third], "later"),
            move(client, collection_id, [NEVER_EXISTED], "later"),
            move(client, NEVER_EXISTED, [library.second], "earlier"),
            move(client, collection_id, [library.first, library.second], "earlier"),
            move(client, collection_id, [library.second], None),
        )
    }
    assert answers == {(403, '{"detail":"admins only"}')}
    sign_in(client)
    assert arranged(client, collection_id) == library.all_ids


@pytest.mark.parametrize(
    ("body", "why"),
    [
        ({"asset_ids": ["a"], "action": "move"}, "a direction goes with a move"),
        ({"asset_ids": ["a"], "direction": "later"}, "a direction goes with a move"),
        ({"asset_ids": ["a", "b"], "action": "move", "direction": "later"}, "a move names one"),
    ],
)
def test_a_move_names_one_file_and_a_direction(
    client: TestClient, library: Library, body: dict[str, Any], why: str
) -> None:
    collection_id = filled(client, library)
    answer = client.post(f"/api/collections/{collection_id}/items", json=body)
    assert answer.status_code == 422
    assert why in answer.text


def test_a_sequence_with_no_stored_positions_is_numbered_by_a_move(
    client: TestClient, library: Library
) -> None:
    collection_id = filled(client, library)
    write(db_path(client), [("UPDATE collection_items SET position = NULL", ())])

    assert move(client, collection_id, [library.third], "earlier").json()["changed"] == 3
    order = sorted(library.all_ids)
    assert arranged(client, collection_id) == [order[0], order[2], order[1]]


def test_a_move_reaches_past_the_five_hundredth_file(client: TestClient, library: Library) -> None:
    ids = [new_id() for _ in range(700)]
    statements: list[tuple[str, tuple[object, ...]]] = []
    for at, asset_id in enumerate(ids):
        statements.append((_INSERT_ASSET, (asset_id, f"deep-{at}", 1, f"{at}.mp4", at)))
        statements.append(
            (
                "INSERT INTO asset_locations (id, asset_id, root_id, folder_id, rel_path, filename,"
                " first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, 0, 0)",
                (new_id(), asset_id, library.root, library.folder, f"clips/{at}.mp4", f"{at}.mp4"),
            )
        )
    write(db_path(client), statements)
    sign_in(client)
    collection_id = make_collection(client, "Long")
    edit_items(client, collection_id, ids[:500])
    edit_items(client, collection_id, ids[500:])

    assert move(client, collection_id, [ids[599]], "earlier").json()["changed"] == 2
    ids[598], ids[599] = ids[599], ids[598]
    assert arranged(client, collection_id) == ids
