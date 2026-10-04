# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Collections wall, counted by whose it is and by its sharing decisions."""

from __future__ import annotations

from fastapi.testclient import TestClient

from sift.slices.collections.tests.conftest import (
    db_path,
    make_collection,
    sign_in,
    write,
)

_OWNER = "UPDATE collections SET owner_id = ? WHERE id = ?"


def test_the_collections_wall_counts_them_by_whose_they_are(client: TestClient) -> None:
    """The wall counts collections as mine or somebody else's, never by the maker's name."""
    sign_in(client)
    make_collection(client, "Best of")
    make_collection(client, "To watch")
    somebody_elses = make_collection(client, "Handed over")
    # A real second user, because `owner_id` is a real reference: an invented id is refused by
    # the foreign key rather than quietly stored, which is the schema doing its job.
    other = sign_in(client, role="guest", who="two")
    sign_in(client)
    write(db_path(client), [(_OWNER, (other, somebody_elses))])

    response = client.get("/api/collections/facets", params={"facet": "mine"})

    assert response.status_code == 200, response.text
    assert response.json()["values"] == [
        {"value": "yes", "count": 2, "label": None},
        {"value": "no", "count": 1, "label": None},
    ]

    # And the wall obeys the word the panel handed back.
    narrowed = client.get("/api/collections?mine=no")
    assert narrowed.status_code == 200, narrowed.text
    assert [one["id"] for one in narrowed.json()["items"]] == [somebody_elses]


def test_a_facet_the_collections_wall_does_not_have_is_refused(client: TestClient) -> None:
    """Refused rather than ignored: a caller who asked for one dimension and silently got another
    has a panel that looks wrong for no visible reason. A second copy of the check the other walls
    carry, and each wall's list is its own: one saying no proves nothing about the next."""
    sign_in(client)

    response = client.get("/api/collections/facets", params={"facet": "favourite_colour"})

    assert response.status_code == 422, response.text


def test_the_sharing_dimension_is_refused_to_somebody_who_is_not_an_admin(
    client: TestClient,
) -> None:
    """The sharing dimension is refused to a non-admin exactly as an unknown dimension is."""
    sign_in(client)
    make_collection(client, "Best of")
    admin_sees_it = client.get("/api/collections/facets", params={"facet": "sharing"})
    assert admin_sees_it.status_code == 200, admin_sees_it.text

    sign_in(client, role="guest", who="two")

    response = client.get("/api/collections/facets", params={"facet": "sharing"})

    assert response.status_code == 422, response.text
