# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Photo sets wall, counted along its one dimension.

One, because a set has no owner column at all: `photo_sets` records where a set came from and
nothing about who made it, so there is no "mine" to ask without inventing the fact behind it.
What is left is admin-only, which makes this the wall that proves an empty panel is drawn rather
than a panel that is missing.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from sift.slices.photo_sets.tests.conftest import grant_on_set, make_set, sign_in


def test_the_sets_wall_counts_what_has_been_shared(client: TestClient) -> None:
    """The same two words the files wall uses, counting SETS.

    A set shared with somebody and a set held back from them are independent rather than exclusive,
    which is why they are counted by joining the grants rather than by reading one column.
    """
    sign_in(client)
    shared = make_set(client, "A shoot")
    make_set(client, "Another shoot")
    guest = sign_in(client, role="guest", who="two")
    sign_in(client)
    grant_on_set(client, shared, guest)

    response = client.get("/api/photo-sets/facets", params={"facet": "sharing"})

    assert response.status_code == 200, response.text
    assert response.json()["values"] == [{"value": "shared", "count": 1, "label": None}]


def test_the_sharing_facet_does_not_exist_for_anybody_but_an_admin(client: TestClient) -> None:
    """Refused in the same words an invented dimension is, and deliberately.

    What has been shared and withheld is the shape of an admin's decisions rather than anything
    about the media. Answering "you may not ask that" would confirm there is a dimension there to
    be asked about, so to everybody else it simply does not exist.
    """
    sign_in(client, role="guest")

    response = client.get("/api/photo-sets/facets", params={"facet": "sharing"})

    assert response.status_code == 422, response.text
