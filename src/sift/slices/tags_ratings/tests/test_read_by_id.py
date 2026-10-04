# SPDX-License-Identifier: AGPL-3.0-or-later
"""A tag's page opens by id, from the same scoped statement as the wall, with 404 for both an
unseeable and an unminted id."""

from __future__ import annotations

from fastapi.testclient import TestClient

from sift.slices.tags_ratings.tests.conftest import (
    NEVER_EXISTED,
    Library,
    assign,
    make_tag,
    share,
    sign_in,
)


def test_a_tag_past_the_walls_first_page_still_opens_by_id(client: TestClient) -> None:
    """The page is asked for with a limit of one, so everything but the first row is off it."""
    sign_in(client)
    first = make_tag(client, "aaa-first")
    second = make_tag(client, "zzz-second")

    page = client.get("/api/tags", params={"limit": 1}).json()
    on_the_page = {row["id"] for row in page["items"]}
    assert len(on_the_page) == 1, "the wall is holding one row, which is the whole point"
    missing = second if second not in on_the_page else first

    found = client.get(f"/api/tags/{missing}")

    assert found.status_code == 200
    assert found.json()["id"] == missing


def test_a_tag_that_was_never_minted_is_a_404(client: TestClient) -> None:
    sign_in(client)

    assert client.get(f"/api/tags/{NEVER_EXISTED}").status_code == 404


def test_an_id_that_is_not_an_id_is_a_404_rather_than_an_arbitrary_tag(
    client: TestClient,
) -> None:
    """The statement reads a null id as "no filter", so anything that is not an id is refused
    before it is bound. Otherwise the answer is whichever row happened to sort first."""
    sign_in(client)
    tag = make_tag(client, "jaunty")

    answer = client.get("/api/tags/not-an-id")

    assert answer.status_code == 404
    assert tag not in answer.text


def test_a_guest_cannot_open_a_tag_that_reaches_nothing_they_may_see(
    client: TestClient, library: Library
) -> None:
    """404 rather than 403, for the reason every one of these is a 404: a 403 confirms it is there.

    A tag with nothing visible under it is not offered to a guest at all: the count beside it
    would otherwise publish the size of the set they were kept out of.
    """
    sign_in(client)
    tag = make_tag(client, "jaunty")
    assign(client, [library.shared], [tag])

    guest = sign_in(client, "guest", who="two")

    assert client.get(f"/api/tags/{tag}").status_code == 404

    share(client, library.shared, guest)

    assert client.get(f"/api/tags/{tag}").status_code == 200


def test_the_by_id_read_carries_what_the_wall_carries(client: TestClient) -> None:
    """The by-id read carries what the wall carries, plus the record."""
    sign_in(client)
    tag = make_tag(client, "jaunty")

    listed = next(row for row in client.get("/api/tags").json()["items"] if row["id"] == tag)
    alone = client.get(f"/api/tags/{tag}").json()

    assert alone.pop("record") == {}, "the page answers with a record, empty for a plain tag"
    assert listed.pop("record") is None, "the wall does not carry one at all"
    assert alone == listed
