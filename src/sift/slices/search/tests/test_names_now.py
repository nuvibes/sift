# SPDX-License-Identifier: AGPL-3.0-or-later
"""`/search/names-now`: what things kept by id are called today, and nothing a viewer may not see.

The picker's memory and the swap drawer keep ids, and draw them under the names this answers,
so a rename reads at once instead of when the thing is next picked.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sift.kernel.ids import new_id
from sift.slices.search.tests.conftest import World, db_path, put_song, sign_in, write

pytestmark = pytest.mark.integration


def test_ids_are_answered_with_the_names_they_have_now(client: TestClient) -> None:
    sign_in(client)
    harbour, gone = new_id(), new_id()
    write(
        db_path(client),
        [("INSERT INTO tags (id, name, created_at) VALUES (?, 'Harbour', 0)", (harbour,))],
    )
    write(db_path(client), [("UPDATE tags SET name = 'Quayside' WHERE id = ?", (harbour,))])

    answer = client.get("/api/search/names-now", params={"kind": "tag", "id": [harbour, gone]})

    assert answer.status_code == 200, answer.text
    # The id that names nothing is simply absent: gone and not-yours are one answer.
    assert answer.json() == {"items": [{"id": harbour, "name": "Quayside"}]}


def test_a_kind_nothing_is_kept_as_is_refused(client: TestClient) -> None:
    sign_in(client)
    answer = client.get("/api/search/names-now", params={"kind": "file", "id": ["x"]})
    assert answer.status_code == 422


def test_a_photo_set_and_a_song_are_answered_by_the_names_they_have_now(
    client: TestClient, world: World
) -> None:
    sign_in(client, "admin")
    song = put_song(db_path(client), world.beach, "Blue - Marla Quist")
    for kind, key, name in (
        ("photo_set", world.photo_set, "Beach shoot"),
        ("song", song, "Blue - Marla Quist"),
    ):
        answer = client.get("/api/search/names-now", params={"kind": kind, "id": [key, new_id()]})
        assert answer.status_code == 200, answer.text
        assert answer.json() == {"items": [{"id": key, "name": name}]}, kind


def test_more_ids_than_one_ask_may_name_are_refused(client: TestClient) -> None:
    """A ceiling, so one request is never a walk of the library."""
    sign_in(client)
    many = [new_id() for _ in range(101)]
    assert (
        client.get("/api/search/names-now", params={"kind": "tag", "id": many}).status_code == 422
    )
