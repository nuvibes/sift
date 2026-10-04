# SPDX-License-Identifier: AGPL-3.0-or-later
"""The shelf of collections: its order, its pages, and whose heart is on it. The items inside a
collection keep their hand arrangement; only the shelf is ordered."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sift.kernel.access import ENTITY_SORT_KEYS
from sift.slices.collections.tests.conftest import (
    NEVER_EXISTED,
    Library,
    edit_items,
    make_collection,
    sign_in,
)

pytestmark = [pytest.mark.integration]


def _names(client: TestClient, sort: str, **params: object) -> list[str]:
    answer = client.get("/api/collections", params={"sort": sort, "limit": 50, **params})
    assert answer.status_code == 200, (sort, answer.text)
    return [one["name"] for one in answer.json()["items"]]


def _three_collections(client: TestClient, library: Library) -> dict[str, str]:
    """Three shelves where no two of the six orders agree; `largest` differs from the name order."""
    ids = {name: make_collection(client, name) for name in ("Dorothy", "Ada", "Carol")}
    assert edit_items(client, ids["Dorothy"], [library.first]).status_code == 200
    assert edit_items(client, ids["Carol"], library.all_ids).status_code == 200
    return ids


def test_an_order_the_collection_wall_does_not_know_is_refused(client: TestClient) -> None:
    """Refused out loud rather than quietly served in some other order."""
    sign_in(client)

    assert client.get("/api/collections", params={"sort": "sideways"}).status_code == 422
    assert client.get("/api/collections", params={"sort": ""}).status_code == 422


def test_the_collection_wall_takes_every_order_it_offers(client: TestClient) -> None:
    """Read off the set rather than written out, so an order added and forgotten here fails."""
    sign_in(client)

    for order in sorted(ENTITY_SORT_KEYS):
        assert client.get("/api/collections", params={"sort": order}).status_code == 200, order


def test_the_universal_orders_actually_order_a_shelf(
    client: TestClient,
    library: Library,
) -> None:
    """Accepted is not ordered. A key with no arm in the statement leaves every row tied."""
    sign_in(client)
    _three_collections(client, library)

    assert _names(client, "name_az") == ["Ada", "Carol", "Dorothy"]
    assert _names(client, "name_za") == ["Dorothy", "Carol", "Ada"]

    # The id is a ULID whose leading bits are the millisecond it was minted, so this is creation
    # order with no `created_at` selected anywhere.
    assert _names(client, "newest") == ["Carol", "Ada", "Dorothy"]
    assert _names(client, "oldest") == ["Dorothy", "Ada", "Carol"]

    # How many files each holds: Carol three, Dorothy one, Ada none.
    assert _names(client, "largest") == ["Carol", "Dorothy", "Ada"]
    assert _names(client, "smallest") == ["Ada", "Dorothy", "Carol"]


def test_the_opinion_orders_actually_order_a_shelf(
    client: TestClient,
    library: Library,
) -> None:
    """A heart and a rating each lift a row the name order would not."""
    sign_in(client)
    ids = _three_collections(client, library)

    assert (
        client.put(f"/api/collections/{ids['Dorothy']}/favorite", json={"favorite": True})
    ).status_code == 200
    assert (
        client.put(f"/api/collections/{ids['Carol']}/rating", json={"rating": 5})
    ).status_code == 200

    assert _names(client, "name_az")[0] == "Ada"
    assert _names(client, "favorite")[0] == "Dorothy"
    assert _names(client, "rating")[0] == "Carol"


def test_the_count_beside_a_page_of_the_shelf_is_the_whole_shelf(
    client: TestClient,
    library: Library,
) -> None:
    """The count beside a page is the whole shelf, asked with an offset too."""
    sign_in(client)
    _three_collections(client, library)

    answer = client.get("/api/collections", params={"sort": "name_az", "limit": 1, "offset": 1})
    assert answer.status_code == 200, answer.text
    page = answer.json()

    assert [one["name"] for one in page["items"]] == ["Carol"]
    assert page["total"] == 3
    assert page["offset"] == 1


def test_a_heart_on_a_collection_is_one_accounts_and_not_the_collections(
    client: TestClient,
    library: Library,
) -> None:
    """Two users sharing an install hold their own opinion of one shelf.

    Both are admins, so neither is being shown a different LIST: the only thing that differs is
    whose row was joined.
    """
    sign_in(client, who="one")
    collection_id = make_collection(client, "Summer")
    assert edit_items(client, collection_id, [library.first]).status_code == 200
    assert (
        client.put(f"/api/collections/{collection_id}/favorite", json={"favorite": True})
    ).status_code == 200

    mine = client.get("/api/collections").json()["items"]
    assert [one["favorite"] for one in mine] == [True]

    sign_in(client, who="two")
    theirs = client.get("/api/collections").json()["items"]
    assert [one["id"] for one in theirs] == [collection_id]
    assert [one["favorite"] for one in theirs] == [False]


def test_the_stars_on_a_collection_survive_a_heart_and_the_heart_survives_the_stars(
    client: TestClient,
) -> None:
    """The stars survive a heart and the heart survives the stars."""
    sign_in(client)
    collection_id = make_collection(client, "Opinionated")

    def put(what: str, body: dict[str, object]) -> dict[str, object]:
        answer = client.put(f"/api/collections/{collection_id}/{what}", json=body)
        assert answer.status_code == 200, answer.text
        return dict(answer.json())

    assert put("rating", {"rating": 4}) == {"favorite": False, "rating": 4}
    assert put("favorite", {"favorite": True}) == {"favorite": True, "rating": 4}
    assert put("rating", {"rating": 2}) == {"favorite": True, "rating": 2}
    # Clearing the stars is a null, and it leaves the heart where it is.
    assert put("rating", {"rating": None}) == {"favorite": True, "rating": None}


# Eleven rather than six: a rating is STORED out of ten whichever scale is drawn, so six is an
# ordinary rating: three stars on a five-star screen.
@pytest.mark.parametrize("rating", [0, 11, -1])
def test_a_star_count_off_the_scale_is_refused_on_a_collection(
    client: TestClient,
    rating: int,
) -> None:
    """One to ten and nothing else. Zero is what a caller reaches for to mean unrated, and stored it
    would sort and filter as a real rating ever after: clearing is a null."""
    sign_in(client)
    collection_id = make_collection(client, "Rated")

    assert (
        client.put(f"/api/collections/{collection_id}/rating", json={"rating": rating})
    ).status_code == 422


def test_an_opinion_about_a_collection_that_is_not_there_reads_like_any_other_miss(
    client: TestClient,
) -> None:
    """404 for an id that was never minted, so trying ids teaches nothing about what exists."""
    sign_in(client)

    assert (
        client.put(f"/api/collections/{NEVER_EXISTED}/favorite", json={"favorite": True})
    ).status_code == 404
    assert (
        client.put(f"/api/collections/{NEVER_EXISTED}/rating", json={"rating": 3})
    ).status_code == 404


def _pin(client: TestClient, collection_id: str, pinned: bool) -> None:
    answer = client.put(f"/api/collections/{collection_id}/pin", json={"pinned": pinned})
    assert answer.status_code == 200, answer.text
    assert answer.json()["pinned"] is pinned


def test_a_pinned_shelf_comes_first_whatever_the_order_is(
    client: TestClient,
    library: Library,
) -> None:
    """A pinned shelf comes first under every order."""
    sign_in(client)
    ids = _three_collections(client, library)
    _pin(client, ids["Dorothy"], True)

    assert _names(client, "name_az") == ["Dorothy", "Ada", "Carol"]
    assert _names(client, "name_za") == ["Dorothy", "Carol", "Ada"]
    assert _names(client, "oldest") == ["Dorothy", "Ada", "Carol"]
    assert _names(client, "smallest") == ["Dorothy", "Ada", "Carol"]


def test_pinned_shelves_keep_the_order_they_were_asked_for_among_themselves(
    client: TestClient,
    library: Library,
) -> None:
    """Pinned shelves keep the asked order among themselves."""
    sign_in(client)
    ids = _three_collections(client, library)
    _pin(client, ids["Dorothy"], True)
    _pin(client, ids["Ada"], True)

    assert _names(client, "name_az") == ["Ada", "Dorothy", "Carol"]
    assert _names(client, "name_za") == ["Dorothy", "Ada", "Carol"]


def test_a_pin_comes_off_and_the_wall_goes_back(client: TestClient, library: Library) -> None:
    """The other half of the write, which a test that only ever pins would never reach."""
    sign_in(client)
    ids = _three_collections(client, library)
    _pin(client, ids["Dorothy"], True)
    assert _names(client, "name_az") == ["Dorothy", "Ada", "Carol"]

    _pin(client, ids["Dorothy"], False)
    assert _names(client, "name_az") == ["Ada", "Carol", "Dorothy"]


def test_a_pin_is_refused_for_a_shelf_that_is_not_there(client: TestClient) -> None:
    """The same 404 an unknown id gets everywhere else, so aiming at ids teaches nothing."""
    sign_in(client)

    answer = client.put(f"/api/collections/{NEVER_EXISTED}/pin", json={"pinned": True})
    assert answer.status_code == 404


# --- filtering by what is being typed ---------------------------------------------------------
#
# A picker draws a page of this wall, so filtering by typed text happens here.


def test_a_prefix_narrows_the_shelf(client: TestClient, library: Library) -> None:
    sign_in(client)
    _three_collections(client, library)

    assert _names(client, "name_az", prefix="Da") == []
    assert _names(client, "name_az", prefix="D") == ["Dorothy"]
    assert _names(client, "name_az", prefix="") == ["Ada", "Carol", "Dorothy"]


def test_a_prefix_matches_without_regard_to_case(client: TestClient, library: Library) -> None:
    """Somebody typing looks for the name, not for the capital it happens to carry."""
    sign_in(client)
    _three_collections(client, library)

    assert _names(client, "name_az", prefix="ado") == []
    assert _names(client, "name_az", prefix="ad") == ["Ada"]


def test_the_box_above_the_shelf_matches_anywhere_in_a_name(
    client: TestClient, library: Library
) -> None:
    """The wall's box sends `anywhere`, as the People wall's does: part of a name finds it."""
    sign_in(client)
    _three_collections(client, library)

    assert _names(client, "name_az", prefix="rol") == []
    assert _names(client, "name_az", prefix="ROL", anywhere="true") == ["Carol"]
    counted = client.get(
        "/api/collections/facets", params={"facet": "mine", "prefix": "rol", "anywhere": "true"}
    )
    assert counted.status_code == 200, counted.text
    assert counted.json()["values"] == [{"value": "yes", "count": 1, "label": None}]


def test_a_wildcard_typed_into_the_box_is_a_character(client: TestClient, library: Library) -> None:
    """`%` and `_` are wildcards to LIKE and ordinary things to type. Unescaped, an underscore
    matches every row, which reads as a broken box rather than as a pattern being interpreted."""
    sign_in(client)
    _three_collections(client, library)

    assert _names(client, "name_az", prefix="%") == []
    assert _names(client, "name_az", prefix="_") == []


def test_the_total_is_the_narrowed_one(client: TestClient, library: Library) -> None:
    """The picker draws its last row from this: how many matched and did not fit. Counted over the
    whole filtered list rather than over the page, or the number describes nothing."""
    sign_in(client)
    _three_collections(client, library)

    answer = client.get("/api/collections", params={"sort": "name_az", "prefix": "D", "limit": 50})
    assert answer.status_code == 200
    assert answer.json()["total"] == 1


# --- opening the wall where it was left ------------------------------------------------------


def test_the_collections_wall_can_be_opened_where_it_was_left(client: TestClient) -> None:
    """`from=` opens the collections wall at a collection, read in `name_za`."""
    sign_in(client)
    for name in ("alpha", "beta", "gamma", "delta"):
        make_collection(client, name)

    every = client.get("/api/collections", params={"sort": "name_za", "limit": 50}).json()
    ordered = [one["id"] for one in every["items"]]
    assert len(ordered) == 4

    landed = client.get(
        "/api/collections", params={"sort": "name_za", "limit": 2, "from": ordered[2]}
    ).json()

    assert [one["id"] for one in landed["items"]] == ordered[2:4]
    assert landed["offset"] == 2, "the answer says where it landed, so the pager can say so too"
    assert landed["total"] == every["total"], "anchoring narrows nothing"


def test_a_collection_anchor_that_resolves_to_nothing_serves_the_first_page(
    client: TestClient,
) -> None:
    """A collection anchor resolving to nothing serves the first page."""
    sign_in(client)
    for name in ("alpha", "beta"):
        make_collection(client, name)

    landed = client.get(
        "/api/collections", params={"sort": "name_az", "limit": 2, "from": NEVER_EXISTED}
    ).json()

    assert landed["offset"] == 0
    assert [one["name"] for one in landed["items"]] == ["alpha", "beta"]
