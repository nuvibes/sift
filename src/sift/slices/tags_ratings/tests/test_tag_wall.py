# SPDX-License-Identifier: AGPL-3.0-or-later
"""The wall of tags: its order, its pages, and whose heart is on it.

An unknown order is refused; every offered order has an arm in the statement (a missing one falls
through with a plausible wall); the count is the whole scoped list. The heart and the stars each
write one column, so what each leaves alone is part of the claim. The six orders here all differ.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sift.kernel.access import ENTITY_SORT_KEYS
from sift.slices.tags_ratings.tests.conftest import (
    NEVER_EXISTED,
    Library,
    assign,
    db_path,
    make_tag,
    read,
    sign_in,
)
from sift.testing.auth import TEST_PIN, give_pin

pytestmark = [pytest.mark.integration]


def _names(client: TestClient, sort: str, **params: object) -> list[str]:
    answer = client.get("/api/tags", params={"sort": sort, "limit": 50, **params})
    assert answer.status_code == 200, (sort, answer.text)
    return [tag["name"] for tag in answer.json()["items"]]


def _three_tags(client: TestClient, library: Library) -> None:
    """Three tags made Dorothy, Ada, Carol with one, none and two files, so all six orders differ.
    The expected lists are written out below, not derived."""
    dorothy, ada, carol = (make_tag(client, name) for name in ("Dorothy", "Ada", "Carol"))
    assign(client, [library.shared], [dorothy])
    assign(client, [library.shared, library.private], [carol])
    assert ada  # made, and deliberately left holding nothing


def test_an_order_the_tag_wall_does_not_know_is_refused(client: TestClient) -> None:
    """Refused out loud, not ignored. The People wall answers the same way and for the same reason."""
    sign_in(client)

    assert client.get("/api/tags", params={"sort": "sideways"}).status_code == 422
    assert client.get("/api/tags", params={"sort": ""}).status_code == 422


def test_the_tag_wall_takes_every_order_it_offers(client: TestClient) -> None:
    """The wall takes every order in `ENTITY_SORT_KEYS`."""
    sign_in(client)

    for order in sorted(ENTITY_SORT_KEYS):
        assert client.get("/api/tags", params={"sort": order}).status_code == 200, order


def test_the_universal_orders_actually_order_a_wall_of_tags(
    client: TestClient,
    library: Library,
) -> None:
    """Accepted is not ordered, and the difference is the whole of what these are for."""
    sign_in(client)
    _three_tags(client, library)

    assert _names(client, "name_az") == ["Ada", "Carol", "Dorothy"]
    assert _names(client, "name_za") == ["Dorothy", "Carol", "Ada"]

    # The id is a ULID and its leading bits are the millisecond it was minted, so this is creation
    # order without a `created_at` being selected anywhere.
    assert _names(client, "newest") == ["Carol", "Ada", "Dorothy"]
    assert _names(client, "oldest") == ["Dorothy", "Ada", "Carol"]

    # How many files each accounts for: Carol two, Dorothy one, Ada none.
    assert _names(client, "largest") == ["Carol", "Dorothy", "Ada"]
    assert _names(client, "smallest") == ["Ada", "Dorothy", "Carol"]


def test_the_opinion_orders_actually_order_a_wall_of_tags(
    client: TestClient,
    library: Library,
) -> None:
    """A heart and a rating each move a different row to the top."""
    sign_in(client)
    _three_tags(client, library)
    by_name = {tag["name"]: tag["id"] for tag in client.get("/api/tags").json()["items"]}

    assert (
        client.put(f"/api/tags/{by_name['Ada']}/favorite", json={"favorite": True}).status_code
        == 200
    )
    assert (
        client.put(f"/api/tags/{by_name['Dorothy']}/rating", json={"rating": 5}).status_code == 200
    )

    assert _names(client, "favorite")[0] == "Ada"
    assert _names(client, "rating")[0] == "Dorothy"


def test_the_count_beside_a_page_is_the_whole_list(
    client: TestClient,
    library: Library,
) -> None:
    """The count beside a page is the whole list, asked with an offset too."""
    sign_in(client)
    _three_tags(client, library)

    answer = client.get("/api/tags", params={"sort": "name_az", "limit": 1, "offset": 1})
    assert answer.status_code == 200, answer.text
    page = answer.json()

    assert [tag["name"] for tag in page["items"]] == ["Carol"]
    assert page["total"] == 3
    assert page["offset"] == 1


def test_a_page_past_the_end_comes_back_empty_and_says_nothing_is_there(
    client: TestClient,
    library: Library,
) -> None:
    """Past the end the page is empty and the window count reports zero, without raising."""
    sign_in(client)
    _three_tags(client, library)

    page = client.get("/api/tags", params={"limit": 10, "offset": 50}).json()
    assert page["items"] == []
    assert page["total"] == 0


def test_a_heart_on_a_tag_is_one_accounts_and_not_the_tags(
    client: TestClient,
    library: Library,
) -> None:
    """A heart on a tag is one account's, joined per viewer."""
    sign_in(client, who="one")
    tag_id = make_tag(client, "shared-vocabulary")
    assign(client, [library.shared], [tag_id])
    assert client.put(f"/api/tags/{tag_id}/favorite", json={"favorite": True}).status_code == 200

    mine = client.get("/api/tags").json()["items"]
    assert [tag["favorite"] for tag in mine] == [True]

    sign_in(client, who="two")
    theirs = client.get("/api/tags").json()["items"]
    assert [tag["id"] for tag in theirs] == [tag_id]
    assert [tag["favorite"] for tag in theirs] == [False]


def test_the_stars_survive_a_heart_and_the_heart_survives_the_stars(
    client: TestClient,
    library: Library,
) -> None:
    """Stars survive a heart and the heart survives stars: each statement names one column."""
    sign_in(client)
    tag_id = make_tag(client, "opinionated")
    assign(client, [library.shared], [tag_id])

    assert client.put(f"/api/tags/{tag_id}/rating", json={"rating": 4}).json() == {
        "favorite": False,
        "rating": 4,
    }
    # The heart goes on, and the stars are still there afterwards.
    assert client.put(f"/api/tags/{tag_id}/favorite", json={"favorite": True}).json() == {
        "favorite": True,
        "rating": 4,
    }
    # And the other way round: the stars change and the heart stays on.
    assert client.put(f"/api/tags/{tag_id}/rating", json={"rating": 2}).json() == {
        "favorite": True,
        "rating": 2,
    }
    # Clearing the stars is a null, and it leaves the heart where it is.
    assert client.put(f"/api/tags/{tag_id}/rating", json={"rating": None}).json() == {
        "favorite": True,
        "rating": None,
    }


def test_hiding_a_tag_does_not_take_the_heart_off_it(
    client: TestClient,
    library: Library,
) -> None:
    """Hiding a tag does not blank the heart or the stars on the same row."""
    user_id = sign_in(client)
    give_pin(db_path(client), user_id)
    tag_id = make_tag(client, "private")
    assign(client, [library.shared], [tag_id])

    assert client.put(f"/api/tags/{tag_id}/favorite", json={"favorite": True}).status_code == 200
    assert client.put(f"/api/tags/{tag_id}/rating", json={"rating": 3}).status_code == 200

    assert client.put(f"/api/tags/{tag_id}/vault", json={"vault": True}).status_code == 204
    assert client.get("/api/tags").json()["items"] == []

    assert client.post("/api/vault/unlock", json={"pin": TEST_PIN}).status_code == 200
    back = client.get("/api/tags").json()["items"]
    assert [(tag["favorite"], tag["rating"]) for tag in back] == [(True, 3)]


# Eleven rather than six. See the note in `test_ratings.py`. Zero stays: it is the value a
# caller reaches for to mean "unrated" and must never be stored as one.
@pytest.mark.parametrize("rating", [0, 11, -1])
def test_a_star_count_off_the_scale_is_refused_on_a_tag(
    client: TestClient,
    library: Library,
    rating: int,
) -> None:
    """One to five, and nothing else. Zero is the value a caller reaches for to mean unrated, and
    stored it would sort and filter as a real rating for ever after: clearing is a null."""
    sign_in(client)
    tag_id = make_tag(client, "rated")
    assign(client, [library.shared], [tag_id])

    assert client.put(f"/api/tags/{tag_id}/rating", json={"rating": rating}).status_code == 422


def test_an_opinion_about_a_tag_that_is_not_there_reads_like_any_other_miss(
    client: TestClient,
) -> None:
    """404 for an id that was never minted, so trying ids teaches nothing about what exists."""
    sign_in(client)

    assert (
        client.put(f"/api/tags/{NEVER_EXISTED}/favorite", json={"favorite": True}).status_code
        == 404
    )
    assert client.put(f"/api/tags/{NEVER_EXISTED}/rating", json={"rating": 3}).status_code == 404


def test_an_opinion_about_a_tag_this_viewer_is_not_shown_reads_like_a_miss(
    client: TestClient,
) -> None:
    """A guest shown nothing hears the same 404 for a real tag as for an id never minted, and no
    row is written against the tag: an opinion is only ever about something on this viewer's wall."""
    sign_in(client)
    tag_id = make_tag(client, "not-yours")
    guest = sign_in(client, "guest")

    for path, body in (
        ("favorite", {"favorite": True}),
        ("rating", {"rating": 3}),
        ("pin", {"pinned": True}),
    ):
        assert client.put(f"/api/tags/{tag_id}/{path}", json=body).status_code == 404, path
    written = read(
        db_path(client),
        "SELECT COUNT(*) AS n FROM tag_user_state WHERE tag_id = ? AND user_id = ?",
        (tag_id, guest),
    )
    assert written[0]["n"] == 0


def test_a_pinned_tag_comes_first_whatever_the_wall_is_sorted_by(
    client: TestClient,
    library: Library,
) -> None:
    """A pinned tag comes first under every order."""
    sign_in(client)
    _three_tags(client, library)
    by_name = {tag["name"]: tag["id"] for tag in client.get("/api/tags").json()["items"]}

    answer = client.put(f"/api/tags/{by_name['Dorothy']}/pin", json={"pinned": True})
    assert answer.status_code == 200, answer.text
    assert answer.json() == {"pinned": True}

    assert _names(client, "name_az")[0] == "Dorothy"
    assert _names(client, "oldest")[0] == "Dorothy"

    assert client.put(f"/api/tags/{by_name['Dorothy']}/pin", json={"pinned": False}).json() == {
        "pinned": False
    }
    assert _names(client, "name_az") == ["Ada", "Carol", "Dorothy"]


def test_a_pin_is_refused_for_a_tag_that_is_not_there(client: TestClient) -> None:
    """A pin on a tag that is not there is refused, since the state row would be created anyway."""
    sign_in(client)
    assert client.put(f"/api/tags/{NEVER_EXISTED}/pin", json={"pinned": True}).status_code == 404


# --- opening the wall where it was left ------------------------------------------------------


def test_the_tags_wall_can_be_opened_where_it_was_left(client: TestClient) -> None:
    """`from=` opens the tags wall at a tag, read in `name_za` as the page is."""
    sign_in(client)
    for name in ("alpha", "beta", "gamma", "delta"):
        make_tag(client, name)

    every = client.get("/api/tags", params={"sort": "name_za", "limit": 50}).json()
    ordered = [one["id"] for one in every["items"]]
    assert len(ordered) == 4

    landed = client.get(
        "/api/tags", params={"sort": "name_za", "limit": 2, "from": ordered[2]}
    ).json()

    assert [one["id"] for one in landed["items"]] == ordered[2:4]
    assert landed["offset"] == 2, "the answer says where it landed, so the pager can say so too"
    assert landed["total"] == every["total"], "anchoring narrows nothing"


def test_a_tag_anchor_that_resolves_to_nothing_serves_the_first_page(client: TestClient) -> None:
    """A tag anchor resolving to nothing serves the first page, whether gone or concealed."""
    sign_in(client)
    for name in ("alpha", "beta"):
        make_tag(client, name)

    landed = client.get(
        "/api/tags", params={"sort": "name_az", "limit": 2, "from": NEVER_EXISTED}
    ).json()

    assert landed["offset"] == 0
    assert [one["name"] for one in landed["items"]] == ["alpha", "beta"]
