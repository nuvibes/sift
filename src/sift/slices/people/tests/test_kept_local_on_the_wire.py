# SPDX-License-Identifier: AGPL-3.0-or-later
"""A person or a Site kept local says so on every row a screen is handed.

## Why this is a wire field at all, when a route already answers the question

`GET /api/stash-boxes/enrichment/{subject}/{id}` answers where one row stands, and an entity menu
asks it as the menu opens: one request for the one row somebody right-clicked. That is the right
shape for a MENU and it cannot be the shape for a MARK: a mark is drawn on every card of a page of
sixty with nobody pressing anything, so drawing it from that route would be sixty requests to say
one word. The flag rides on the row instead.

## What each test here is really holding

The wall, the by-id read and the WRITE REPLY, because those are three different statements and a
screen reads all three. The write reply is the one that looks optional and is not: a screen puts a
write reply in place of the row it was holding, so a field left out of it is a mark that a rename
silently takes off the card, the fault `_person_view` guards against for the restricted badge.
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

_KEEP_A_PERSON = "UPDATE people SET keep_local = 1 WHERE id = ?"
_KEEP_A_SITE = "UPDATE sites SET keep_local = 1 WHERE name = ?"

_A_SITE = "Marrowvale Studios"
_A_USERNAME = "marrowvale"


def _keep(client: TestClient, sql: str, key: str) -> None:
    write(db_path(client), [(sql, (key,))])


def test_a_person_kept_local_says_so_on_the_wall(client: TestClient) -> None:
    sign_in(client)
    person = make_person(client, "Ada Lumen")
    _keep(client, _KEEP_A_PERSON, person)

    rows = client.get("/api/people").json()["items"]

    assert [row["keep_local"] for row in rows if row["id"] == person] == [True]


def test_a_person_nobody_kept_local_says_so_too(client: TestClient) -> None:
    """The other half, so the field is not simply always true."""
    sign_in(client)
    person = make_person(client, "Neve Arbor")

    rows = client.get("/api/people").json()["items"]

    assert [row["keep_local"] for row in rows if row["id"] == person] == [False]


def test_a_person_kept_local_says_so_on_their_own_page(client: TestClient) -> None:
    sign_in(client)
    person = make_person(client, "Ada Lumen")
    _keep(client, _KEEP_A_PERSON, person)

    assert client.get(f"/api/people/{person}").json()["keep_local"] is True


def test_renaming_a_person_hands_the_mark_back(client: TestClient) -> None:
    """The write reply. A rename that answered False would take the mark off the card."""
    sign_in(client)
    person = make_person(client, "Ada Lumen")
    _keep(client, _KEEP_A_PERSON, person)

    answer = client.put(f"/api/people/{person}", json={"name": "Neve Arbor"})

    assert answer.status_code == 200, answer.text
    assert answer.json()["keep_local"] is True


def test_a_site_kept_local_says_so_on_the_wall_and_on_its_page(client: TestClient) -> None:
    sign_in(client)
    make_username(client, _A_SITE, _A_USERNAME)
    _keep(client, _KEEP_A_SITE, _A_SITE)

    rows = client.get("/api/sites").json()["items"]
    kept = [row for row in rows if row["name"] == _A_SITE]

    assert [row["keep_local"] for row in kept] == [True]
    assert client.get(f"/api/sites/{kept[0]['id']}").json()["keep_local"] is True


def test_renaming_a_site_hands_the_mark_back(client: TestClient) -> None:
    sign_in(client)
    make_username(client, _A_SITE, _A_USERNAME)
    _keep(client, _KEEP_A_SITE, _A_SITE)
    site = next(row for row in client.get("/api/sites").json()["items"] if row["name"] == _A_SITE)

    answer = client.put(f"/api/sites/{site['id']}", json={"name": "Northlight Studio Collection"})

    assert answer.status_code == 200, answer.text
    assert answer.json()["keep_local"] is True
