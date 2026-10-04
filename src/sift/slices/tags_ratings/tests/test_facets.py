# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Tags wall, counted along one dimension and filtered by it, over HTTP."""

from __future__ import annotations

from fastapi.testclient import TestClient

from sift.slices.tags_ratings.tests.conftest import db_path, make_tag, sign_in, write

_CATEGORY = "UPDATE tags SET category = ? WHERE id = ?"


def test_the_tags_wall_counts_tags_by_category(client: TestClient) -> None:
    """A row counts tags, not the files carrying them."""
    sign_in(client)
    place = make_tag(client, "beach")
    other_place = make_tag(client, "city")
    mood = make_tag(client, "quiet")
    write(
        db_path(client),
        [
            (_CATEGORY, ("PLACE", place)),
            (_CATEGORY, ("PLACE", other_place)),
            (_CATEGORY, ("MOOD", mood)),
        ],
    )

    response = client.get("/api/tags/facets", params={"facet": "category"})

    assert response.status_code == 200, response.text
    assert response.json() == {
        "facet": "category",
        "values": [
            {"value": "PLACE", "count": 2, "label": None},
            {"value": "MOOD", "count": 1, "label": None},
        ],
    }


def test_the_tags_wall_narrows_by_the_value_the_panel_handed_back(client: TestClient) -> None:
    """Clicking a row returns exactly the rows it counted, which is why the value IS the filter."""
    sign_in(client)
    place = make_tag(client, "beach")
    mood = make_tag(client, "quiet")
    write(db_path(client), [(_CATEGORY, ("PLACE", place)), (_CATEGORY, ("MOOD", mood))])

    response = client.get("/api/tags?category=PLACE")

    assert response.status_code == 200, response.text
    assert [one["id"] for one in response.json()["items"]] == [place]


def test_a_facet_the_tags_wall_does_not_have_is_refused(client: TestClient) -> None:
    """An unknown dimension is refused on the Tags wall too."""
    sign_in(client)

    response = client.get("/api/tags/facets", params={"facet": "favourite_colour"})

    assert response.status_code == 422, response.text


def test_the_tags_wall_declares_the_disagreements_column_too(client: TestClient) -> None:
    """The Tags wall declares the disagreements column; nothing disagrees here."""
    sign_in(client)
    make_tag(client, "beach")

    counted = client.get("/api/tags/facets", params={"facet": "disagrees"})
    assert counted.status_code == 200, counted.text
    assert counted.json()["values"] == [{"value": "no", "count": 1, "label": None}]

    narrowed = client.get("/api/tags", params={"disagrees": "yes"})
    assert narrowed.status_code == 200, narrowed.text
    assert narrowed.json()["total"] == 0
