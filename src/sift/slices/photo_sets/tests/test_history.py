# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Photo Set's history and its maker, over HTTP.

Thin for the reason a collection's is (`photo_set_items` records no moment), with one thing it has
not got: `origin` says HOW the set came to be, and the first line says which. What is proved here is
what only the route decides.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sift.kernel.ids import new_id
from sift.slices.photo_sets.tests.conftest import (
    NEVER_EXISTED,
    db_path,
    make_set,
    sign_in,
    write,
)

pytestmark = [pytest.mark.integration]


def test_a_set_made_by_hand_says_so_and_names_itself(client: TestClient) -> None:
    """A set made through this route has `origin = 'manual'`, which is somebody assembling it."""
    sign_in(client, "admin")
    shoot = make_set(client, "Pool shoot")

    answer = client.get(f"/api/photo-sets/{shoot}/history")

    assert answer.status_code == 200
    events = answer.json()
    assert [event["kind"] for event in events] == ["added"]
    assert events[0]["what"] == "Pool shoot was created"
    assert events[0]["actor"] == "somebody"
    assert [
        (one["kind"], one["id"], one["text"]) for one in events[0]["pieces"] if one["kind"]
    ] == [("photo_set", shoot, "Pool shoot")]
    assert events[0]["via"] is None


def test_a_set_that_was_never_minted_is_a_404(client: TestClient) -> None:
    sign_in(client, "admin")

    assert client.get(f"/api/photo-sets/{NEVER_EXISTED}/history").status_code == 404


def test_signing_out_is_refused_rather_than_answered(client: TestClient) -> None:
    assert client.get(f"/api/photo-sets/{new_id()}/history").status_code == 401


@pytest.mark.parametrize("limit", [0, -1, 501])
def test_a_limit_outside_the_bound_is_refused_rather_than_clamped(
    client: TestClient, limit: int
) -> None:
    sign_in(client, "admin")
    shoot = make_set(client, "Pool shoot")

    assert client.get(f"/api/photo-sets/{shoot}/history?limit={limit}").status_code == 422


def test_a_set_says_who_made_it_and_which_pass_did(client: TestClient) -> None:
    """The line the header draws: the word, the pass, and never a name for another user.

    A set made through the route is `origin = 'manual'`, which the insert reads as somebody typing
    a name, so the user who made it is told "you" and no other user is ever named. The
    folder case is written onto the row rather than derived here, because what is being proved is
    that the read carries the pass across, not that the derivation picks one.
    """
    mine = sign_in(client, "admin")
    shoot = make_set(client, "Pool shoot")

    answer = client.get(f"/api/photo-sets/{shoot}/made-by")

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
                "UPDATE photo_sets SET created_by_kind = 'sift', created_by_via = 'folder',"
                " created_by_user_id = NULL WHERE id = ?",
                (shoot,),
            )
        ],
    )
    made = client.get(f"/api/photo-sets/{shoot}/made-by").json()
    assert (made["kind"], made["via"]) == ("sift", "folder")

    # Somebody else's, and the other user is not named: the withholding the sharing screens
    # already make, and the one answer on this route that is about the viewer at all.
    write(
        db_path(client),
        [
            (
                "UPDATE photo_sets SET created_by_kind = 'user', created_by_via = NULL,"
                " created_by_user_id = ? WHERE id = ?",
                ("somebody-else", shoot),
            )
        ],
    )
    theirs = client.get(f"/api/photo-sets/{shoot}/made-by").json()
    assert theirs == {
        "kind": "another_user",
        "via": None,
        "act": None,
        "box_id": None,
        "box_name": None,
        "box_slug": None,
    }
    assert mine not in answer.text


def test_a_set_whose_row_never_said_answers_nothing_rather_than_guessing(
    client: TestClient,
) -> None:
    """Every set made before the catalog recorded this. Null is the honest answer and not an error."""
    sign_in(client, "admin")
    shoot = make_set(client, "Pool shoot")
    write(
        db_path(client),
        [("UPDATE photo_sets SET created_by_kind = NULL WHERE id = ?", (shoot,))],
    )

    answer = client.get(f"/api/photo-sets/{shoot}/made-by")

    assert answer.status_code == 200
    assert answer.json() is None


def test_a_maker_is_refused_for_a_set_that_was_never_minted(client: TestClient) -> None:
    """Resolved first, so a set this viewer may not be shown answers what a missing id answers."""
    sign_in(client, "admin")

    assert client.get(f"/api/photo-sets/{NEVER_EXISTED}/made-by").status_code == 404


def test_a_maker_is_refused_to_nobody_at_all(client: TestClient) -> None:
    assert client.get(f"/api/photo-sets/{new_id()}/made-by").status_code == 401
