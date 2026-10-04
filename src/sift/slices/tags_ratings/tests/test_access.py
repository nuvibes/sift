# SPDX-License-Identifier: AGPL-3.0-or-later
"""Who may do what with tags, and what a refusal may say: a guest cannot edit tags or touch an
unshared asset, and a refusal looks like an unknown id. Every endpoint gets its own test."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sift.slices.tags_ratings.tests.conftest import (
    NEVER_EXISTED,
    Library,
    assign,
    make_tag,
    share,
    sign_in,
)

pytestmark = [pytest.mark.integration]


def _as_guest(client: TestClient) -> str:
    return sign_in(client, "guest", who="outsider")


# --- tags are shared, so editing them is admin-only ----------------------------------------------


def test_a_guest_cannot_create_a_tag(client: TestClient) -> None:
    """A guest cannot create a tag: the table and its grants serve the whole install."""
    _as_guest(client)

    assert client.post("/api/tags", json={"name": "Beach"}).status_code == 403


def test_a_guest_cannot_rename_or_delete_a_tag(client: TestClient) -> None:
    """Deleting is the one that matters most: it throws away grants somebody was relying on."""
    sign_in(client)
    tag_id = make_tag(client, "Beach")

    _as_guest(client)

    assert client.put(f"/api/tags/{tag_id}", json={"name": "Renamed"}).status_code == 403
    assert client.delete(f"/api/tags/{tag_id}").status_code == 403


def test_a_guest_cannot_tag_anything_even_what_they_can_see(
    client: TestClient, library: Library
) -> None:
    """A guest cannot tag anything: `asset_tags` has no owner and feeds the resolver."""
    guest = _as_guest(client)
    sign_in(client)
    tag_id = make_tag(client, "Beach")
    share(client, library.shared, guest)

    _as_guest(client)

    assert assign(client, [library.shared], [tag_id]).status_code == 403


# --- a refusal reads exactly like a miss ----------------------------------------------------------


def test_a_guest_rating_an_asset_nobody_shared_answers_like_one_that_is_not_there(
    client: TestClient, library: Library
) -> None:
    _as_guest(client)

    refused = client.put(f"/api/assets/{library.private}/rating", json={"rating": 4})
    absent = client.put(f"/api/assets/{NEVER_EXISTED}/rating", json={"rating": 4})

    assert refused.status_code == 404
    assert refused.status_code == absent.status_code
    assert refused.json() == absent.json(), "a refusal must not read differently from a miss"


def test_a_guest_favouriting_an_asset_nobody_shared_answers_like_one_that_is_not_there(
    client: TestClient, library: Library
) -> None:
    _as_guest(client)

    refused = client.put(f"/api/assets/{library.private}/favorite", json={"favorite": True})
    absent = client.put(f"/api/assets/{NEVER_EXISTED}/favorite", json={"favorite": True})

    assert refused.status_code == 404
    assert refused.status_code == absent.status_code
    assert refused.json() == absent.json()


def test_a_guest_reading_the_chips_of_an_asset_nobody_shared_answers_like_a_miss(
    client: TestClient, library: Library
) -> None:
    """The chips would name the tags on a file they were never shown, which is most of what the
    file is."""
    _as_guest(client)

    refused = client.get(f"/api/assets/{library.private}/tags")
    absent = client.get(f"/api/assets/{NEVER_EXISTED}/tags")

    assert refused.status_code == 404
    assert refused.json() == absent.json()


def test_an_admin_tagging_an_asset_that_is_not_there_writes_nothing_and_says_so(
    client: TestClient, library: Library
) -> None:
    """Tagging a missing asset writes nothing and reports the skip (see `sift.kernel.reach`)."""
    sign_in(client)
    tag_id = make_tag(client, "Beach")

    answer = assign(client, [NEVER_EXISTED], [tag_id])

    assert answer.status_code == 200
    assert (answer.json()["changed"], answer.json()["skipped"]) == (0, 1)


def test_one_unreachable_asset_is_skipped_and_the_batch_still_lands(
    client: TestClient, library: Library
) -> None:
    """One unreachable asset is skipped and reported; the rest are tagged."""
    sign_in(client)
    tag_id = make_tag(client, "Beach")

    answer = assign(client, [library.shared, NEVER_EXISTED], [tag_id])

    assert answer.status_code == 200
    assert (answer.json()["changed"], answer.json()["skipped"]) == (1, 1)
    assert [tag["name"] for tag in client.get(f"/api/assets/{library.shared}/tags").json()] == [
        "Beach"
    ]


# --- the tag list is scoped too --------------------------------------------------------------------


def test_a_tag_count_never_includes_what_the_viewer_cannot_see(
    client: TestClient, library: Library
) -> None:
    """The count is the disclosure, not the list.

    Both files carry the tag and only one is shared. A count of two tells a guest exactly how many
    files they are not being shown, and it would do it while the grid beside it listed one.
    """
    guest = _as_guest(client)
    sign_in(client)
    tag_id = make_tag(client, "Beach")
    assign(client, [library.shared, library.private], [tag_id])
    share(client, library.shared, guest)

    _as_guest(client)
    listed = client.get("/api/tags").json()["items"]

    assert [(tag["name"], tag["asset_count"]) for tag in listed] == [("Beach", 1)]


def test_a_tag_a_guest_can_reach_nothing_through_is_not_listed_at_all(
    client: TestClient, library: Library
) -> None:
    """A tag name says what a library is organised around.

    Shown at zero it still says it, about files the person was never shown. So the tag is absent
    until something under it is theirs to see.
    """
    _as_guest(client)
    sign_in(client)
    tag_id = make_tag(client, "Beach")
    assign(client, [library.private], [tag_id])

    guest = _as_guest(client)
    assert client.get("/api/tags").json()["items"] == []

    sign_in(client)
    share(client, library.private, guest)

    _as_guest(client)
    assert [tag["name"] for tag in client.get("/api/tags").json()["items"]] == ["Beach"]
