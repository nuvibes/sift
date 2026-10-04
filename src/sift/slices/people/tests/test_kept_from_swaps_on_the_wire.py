# SPDX-License-Identifier: AGPL-3.0-or-later
"""A person or a Site marked "Don't swap" says so on every row a screen is handed.

The argument of `test_kept_local_on_the_wire.py`, for the refusal's other mark: swap mode draws a
mark on every card that will not go in a swap, with nobody pressing anything, so the mark rides on
the row rather than on a request per card. The wall, the by-id read and the write reply are three
statements and a screen reads all three.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from sift.slices.people.tests.conftest import (
    db_path,
    make_person,
    make_username,
    sign_in,
    write,
)

_MARK_A_PERSON = "UPDATE people SET keep_from_swaps = 1 WHERE id = ?"
_MARK_A_SITE = "UPDATE sites SET keep_from_swaps = 1 WHERE name = ?"

_A_SITE = "Marrowvale Studios"
_A_USERNAME = "marrowvale"


def test_a_person_marked_dont_swap_says_so_on_the_wall_and_on_their_page(
    client: TestClient,
) -> None:
    sign_in(client)
    marked = make_person(client, "Ada Lumen")
    other = make_person(client, "Neve Arbor")
    write(db_path(client), [(_MARK_A_PERSON, (marked,))])

    rows = {row["id"]: row for row in client.get("/api/people").json()["items"]}

    assert (rows[marked]["keep_from_swaps"], rows[other]["keep_from_swaps"]) == (True, False)
    # The two marks are two fields: "Don't swap" is not Kept local.
    assert rows[marked]["keep_local"] is False
    assert client.get(f"/api/people/{marked}").json()["keep_from_swaps"] is True


def test_renaming_a_person_marked_dont_swap_hands_the_mark_back(client: TestClient) -> None:
    sign_in(client)
    person = make_person(client, "Ada Lumen")
    write(db_path(client), [(_MARK_A_PERSON, (person,))])

    answer = client.put(f"/api/people/{person}", json={"name": "Neve Arbor"})

    assert answer.status_code == 200, answer.text
    assert answer.json()["keep_from_swaps"] is True


def test_a_site_marked_dont_swap_says_so_on_the_wall_its_page_and_a_rename(
    client: TestClient,
) -> None:
    sign_in(client)
    make_username(client, _A_SITE, _A_USERNAME)
    write(db_path(client), [(_MARK_A_SITE, (_A_SITE,))])

    kept = [row for row in client.get("/api/sites").json()["items"] if row["name"] == _A_SITE]

    assert [row["keep_from_swaps"] for row in kept] == [True]
    assert client.get(f"/api/sites/{kept[0]['id']}").json()["keep_from_swaps"] is True
    answer = client.put(
        f"/api/sites/{kept[0]['id']}", json={"name": "Northlight Studio Collection"}
    )
    assert answer.status_code == 200, answer.text
    assert answer.json()["keep_from_swaps"] is True
