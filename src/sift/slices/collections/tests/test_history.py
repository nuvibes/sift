# SPDX-License-Identifier: AGPL-3.0-or-later
"""A collection's history and its maker, over HTTP.

Thin, and deliberately: `collection_items` records no moment for a file going in, so the thread is
the shelf being made plus whatever sharing an admin has done. What is proved here is what only the
route decides: a shelf this user may not be shown answers the same 404 an id that was never
minted gets, and the reply is the shape a client was promised.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sift.kernel.ids import new_id
from sift.slices.collections.tests.conftest import (
    NEVER_EXISTED,
    db_path,
    make_collection,
    sign_in,
    write,
)

pytestmark = [pytest.mark.integration]


def test_a_shelf_says_it_was_made_and_by_the_account_that_made_it(client: TestClient) -> None:
    sign_in(client, "admin")
    shelf = make_collection(client, "Best of")

    answer = client.get(f"/api/collections/{shelf}/history")

    assert answer.status_code == 200
    events = answer.json()
    assert [event["kind"] for event in events] == ["added"]
    assert events[0]["what"] == "You created Best of"
    assert events[0]["actor"] == "you"
    assert [
        (one["kind"], one["id"], one["text"]) for one in events[0]["pieces"] if one["kind"]
    ] == [("collection", shelf, "Best of")]
    assert events[0]["via"] is None


def test_a_shelf_that_was_never_minted_is_a_404(client: TestClient) -> None:
    sign_in(client, "admin")

    assert client.get(f"/api/collections/{NEVER_EXISTED}/history").status_code == 404


def test_signing_out_is_refused_rather_than_answered(client: TestClient) -> None:
    assert client.get(f"/api/collections/{new_id()}/history").status_code == 401


@pytest.mark.parametrize("limit", [0, -1, 501])
def test_a_limit_outside_the_bound_is_refused_rather_than_clamped(
    client: TestClient, limit: int
) -> None:
    sign_in(client, "admin")
    shelf = make_collection(client, "Best of")

    assert client.get(f"/api/collections/{shelf}/history?limit={limit}").status_code == 422


def test_a_shelf_says_who_made_it_and_never_names_another_account(client: TestClient) -> None:
    """The line the header draws. A shelf is always somebody's: there is no pass that makes one
    and no answer from a stash-box that could, so the two answers here are "you" and "a
    user", and the second carries no name."""
    sign_in(client, "admin")
    shelf = make_collection(client, "Best of")

    answer = client.get(f"/api/collections/{shelf}/made-by")

    assert answer.status_code == 200
    assert answer.json() == {
        "kind": "you",
        "via": None,
        "act": None,
        "box_id": None,
        "box_name": None,
        "box_slug": None,
    }

    write(
        db_path(client),
        [
            (
                "UPDATE collections SET created_by_user_id = ? WHERE id = ?",
                ("somebody-else", shelf),
            )
        ],
    )
    theirs = client.get(f"/api/collections/{shelf}/made-by").json()
    assert theirs["kind"] == "another_user"
    assert "somebody-else" not in client.get(f"/api/collections/{shelf}/made-by").text


def test_a_shelf_whose_row_never_said_answers_nothing_rather_than_guessing(
    client: TestClient,
) -> None:
    """Every shelf made before the catalog recorded this. Null, and not an error."""
    sign_in(client, "admin")
    shelf = make_collection(client, "Best of")
    write(
        db_path(client),
        [("UPDATE collections SET created_by_kind = NULL WHERE id = ?", (shelf,))],
    )

    answer = client.get(f"/api/collections/{shelf}/made-by")

    assert answer.status_code == 200
    assert answer.json() is None


def test_a_maker_is_refused_for_a_shelf_that_was_never_minted(client: TestClient) -> None:
    sign_in(client, "admin")

    assert client.get(f"/api/collections/{NEVER_EXISTED}/made-by").status_code == 404


def test_a_maker_is_refused_to_nobody_at_all(client: TestClient) -> None:
    assert client.get(f"/api/collections/{new_id()}/made-by").status_code == 401
