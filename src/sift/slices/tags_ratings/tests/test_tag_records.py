# SPDX-License-Identifier: AGPL-3.0-or-later
"""A tag's record: what it means, the other words for it, and its one category.

The three fields here are what every stash-box carries for a tag and are also worth typing by
hand: a tag whose meaning is written down once is a tag two people use the same way.

The category is ONE word and never a tree. That is asserted in the schema test beside this one,
where the absence of a column is the thing being defended; here it is only ever a value.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from sift.slices.tags_ratings.tests.conftest import make_tag, sign_in


def _record(client: TestClient, tag: str) -> dict[str, object]:
    held = client.get(f"/api/tags/{tag}").json()["record"]
    assert isinstance(held, dict)
    return held


def test_a_tags_record_is_written_and_read_back(client: TestClient) -> None:
    sign_in(client)
    tag = make_tag(client, "beach")

    answer = client.put(
        f"/api/tags/{tag}",
        json={
            "name": "beach",
            "description": "Sand, and the sea behind it.",
            "category": "Location",
            "aliases": ["seaside", "shore"],
        },
    )

    assert answer.status_code == 200
    assert answer.json()["record"]["category"] == "Location"
    held = _record(client, tag)
    assert held["description"] == "Sand, and the sea behind it."
    assert held["aliases"] == ["seaside", "shore"]


def test_a_rename_that_says_nothing_about_the_record_keeps_it(client: TestClient) -> None:
    """The statement behind this replaces all three columns at once.

    So a caller that sent only a name has to have the other two put back exactly as they were:
    otherwise renaming a tag destroys the paragraph somebody wrote about it, quietly.
    """
    sign_in(client)
    tag = make_tag(client, "beach")
    client.put(
        f"/api/tags/{tag}",
        json={"name": "beach", "description": "Sand.", "aliases": ["shore"]},
    )

    client.put(f"/api/tags/{tag}", json={"name": "seaside", "color": None})

    held = _record(client, tag)
    assert held["description"] == "Sand."
    assert held["aliases"] == ["shore"]


def test_one_field_can_be_changed_without_clearing_the_others(client: TestClient) -> None:
    """The read-modify-write, from the other side: sending one of three keeps the other two."""
    sign_in(client)
    tag = make_tag(client, "beach")
    client.put(
        f"/api/tags/{tag}",
        json={"name": "beach", "description": "Sand.", "aliases": ["shore"]},
    )

    client.put(f"/api/tags/{tag}", json={"name": "beach", "category": "Location"})

    held = _record(client, tag)
    assert held == {"description": "Sand.", "category": "Location", "aliases": ["shore"]}


def test_an_emptied_box_is_nothing_rather_than_an_empty_string(client: TestClient) -> None:
    """A tag described as `''` and one never described are the same tag."""
    sign_in(client)
    tag = make_tag(client, "beach")
    client.put(f"/api/tags/{tag}", json={"name": "beach", "description": "Sand."})

    client.put(f"/api/tags/{tag}", json={"name": "beach", "description": "  "})

    assert _record(client, tag) == {}


def test_other_names_are_replaced_whole(client: TestClient) -> None:
    sign_in(client)
    tag = make_tag(client, "beach")
    client.put(f"/api/tags/{tag}", json={"name": "beach", "aliases": ["shore"]})

    client.put(f"/api/tags/{tag}", json={"name": "beach", "aliases": ["seaside"]})

    assert _record(client, tag)["aliases"] == ["seaside"]


def test_two_spellings_of_one_other_name_are_one(client: TestClient) -> None:
    """Case-insensitively unique, like a tag's own name. The second is a mistake, not a name."""
    sign_in(client)
    tag = make_tag(client, "beach")

    client.put(f"/api/tags/{tag}", json={"name": "beach", "aliases": ["Shore", "shore"]})

    assert _record(client, tag)["aliases"] == ["Shore"]


def test_an_alias_that_is_only_spaces_is_not_stored_as_one(client: TestClient) -> None:
    """A blank in a list of names is a row nobody can search for and a chip nobody can read.

    The form sends the whole list every time, so a stray empty box arrives with the rest of it,
    which is the ordinary way one gets here rather than an unusual one.
    """
    sign_in(client)
    tag = make_tag(client, "beach")

    client.put(
        f"/api/tags/{tag}",
        json={
            "name": "beach",
            "description": None,
            "category": None,
            "aliases": ["seaside", "   ", ""],
        },
    )

    assert _record(client, tag)["aliases"] == ["seaside"]
