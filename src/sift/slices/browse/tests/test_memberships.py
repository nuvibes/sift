# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a selection is already on, as the pickers ask it.

The route exists so a menu row can draw a tick, and the whole of what makes a tick honest is the
difference between "every one of these" and "some of these". So that is what is asserted here: one
tag on both files and one tag on one of them, over the same request, coming back in two different
lists.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from sift.kernel.ids import new_id
from sift.slices.browse.tests.conftest import Library, db_path, sign_in, write

_TAG = "INSERT INTO tags (id, name, created_at) VALUES (?, ?, 0)"
_ON = "INSERT INTO asset_tags (asset_id, tag_id) VALUES (?, ?)"
_COLLECTION = "INSERT INTO collections (id, name, created_at) VALUES (?, ?, 0)"
_IN = "INSERT INTO collection_items (collection_id, asset_id, position) VALUES (?, ?, 0)"


def _ask(client: TestClient, asset_ids: list[str]) -> dict[str, object]:
    answer = client.post("/api/assets/memberships", json={"asset_ids": asset_ids})
    assert answer.status_code == 200, answer.text
    return dict(answer.json())


def test_a_tag_on_every_file_is_separated_from_one_on_some(
    client: TestClient, library: Library
) -> None:
    sign_in(client, "admin")
    both, only_one = new_id(), new_id()
    write(
        db_path(client),
        [
            (_TAG, (both, "on both")),
            (_TAG, (only_one, "on one")),
            (_ON, (library.shared, both)),
            (_ON, (library.private, both)),
            (_ON, (library.shared, only_one)),
        ],
    )

    answer = _ask(client, [library.shared, library.private])

    tags = answer["tags"]
    assert isinstance(tags, dict)
    assert tags["all"] == [both]
    assert tags["some"] == [only_one]


def test_a_collection_holding_neither_file_is_named_in_neither_list(
    client: TestClient, library: Library
) -> None:
    """Absence is the answer for most of a library, and it has to stay absence.

    A route that listed every collection with a zero beside it would answer with the whole wall on
    every menu open, which is the page the pickers deliberately do not fetch.
    """
    sign_in(client, "admin")
    empty, holding = new_id(), new_id()
    write(
        db_path(client),
        [
            (_COLLECTION, (empty, "holds nothing")),
            (_COLLECTION, (holding, "holds one")),
            (_IN, (holding, library.shared)),
        ],
    )

    answer = _ask(client, [library.shared, library.private])

    collections = answer["collections"]
    assert isinstance(collections, dict)
    assert collections["all"] == []
    assert collections["some"] == [holding]


def test_the_heart_answers_in_one_word(client: TestClient, library: Library) -> None:
    sign_in(client, "admin")
    assert _ask(client, [library.shared, library.private])["favorite"] == "none"

    client.post("/api/assets/favorite", json={"asset_ids": [library.shared], "favorite": True})
    assert _ask(client, [library.shared, library.private])["favorite"] == "some"

    client.post("/api/assets/favorite", json={"asset_ids": [library.private], "favorite": True})
    assert _ask(client, [library.shared, library.private])["favorite"] == "all"


def test_a_selection_of_nothing_reachable_answers_empty_rather_than_failing(
    client: TestClient, library: Library
) -> None:
    """A file this user cannot act on is left out of the question rather than failing it, and a
    selection where that leaves nothing at all is the same rule carried to its end.

    Empty and not an error, because the caller is a menu opening over a selection that has just
    changed underneath it: every tile deleted or vaulted by another screen between the click and
    the request. What the pickers must not be given is a tick drawn from a set they cannot write
    to, so the answer is no ticks: every kind empty and the heart at "none".
    """
    sign_in(client, "admin")

    answer = _ask(client, [new_id(), new_id()])

    assert answer["favorite"] == "none"
    for kind in ("people", "sites", "collections", "photo_sets", "tags"):
        drawn = answer[kind]
        assert isinstance(drawn, dict)
        assert (drawn["all"], drawn["some"]) == ([], [])


def test_a_guest_is_refused(client: TestClient, library: Library) -> None:
    """Not a narrowed answer: none at all.

    Every write a tick toggles is an admin\'s, so there is no guest surface that draws one, and
    the repository read behind this is deliberately unscoped on the strength of that. The refusal
    is what keeps the two facts tied together.
    """
    sign_in(client, "guest")
    answer = client.post("/api/assets/memberships", json={"asset_ids": [library.shared]})
    assert answer.status_code == 403
