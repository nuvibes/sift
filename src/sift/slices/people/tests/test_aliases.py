# SPDX-License-Identifier: AGPL-3.0-or-later
"""Finding a person by any of the three names they have.

A person is reachable by the name on their row, by an alias somebody typed, and by a username
linked to them. All three go through one resolver, and these tests go through the
endpoint that calls it rather than around it: the whole point of there being one resolver is
that everything reads the same one.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sift.slices.people.tests.conftest import (
    make_person,
    make_username,
    sign_in,
)


def _resolve(client: TestClient, term: str) -> list[str]:
    response = client.get("/api/people/resolve", params={"term": term})
    assert response.status_code == 200, response.text
    return [person["id"] for person in response.json()["people"]]


def test_a_person_is_found_by_name_and_by_alias(client: TestClient) -> None:
    """The contract the search screen depends on, both through the one resolver.

    There is no third way in through a username somebody pointed at them: a username IS an
    also-known-as name, so a download matches it against the aliases and files the file under the
    person directly. The alias itself is the link.
    """
    sign_in(client)
    person = make_person(client, "Jane Doe")

    client.post(f"/api/people/{person}/aliases", json={"alias": "janed"})

    assert _resolve(client, "Jane Doe") == [person], "by name"
    assert _resolve(client, "janed") == [person], "by an alias, which is what a username is"


def test_a_missed_term_names_nobody_which_is_what_the_prompt_reads(client: TestClient) -> None:
    """The empty answer is a feature, not an absence of one.

    It is what the "is this another name for someone?" prompt reads to decide whether to offer
    itself, which is how the alias list grows: at the moment somebody notices a name is missing,
    rather than as a data-entry chore nobody ever does.
    """
    sign_in(client)
    make_person(client, "Jane Doe")

    response = client.get("/api/people/resolve", params={"term": "Janey"})

    assert response.status_code == 200
    assert response.json() == {"term": "Janey", "people": []}


def test_accepting_the_prompt_adds_the_alias_and_the_term_then_resolves(client: TestClient) -> None:
    """The whole loop, end to end: a miss, one click, and the same term now finds her."""
    sign_in(client)
    person = make_person(client, "Jane Doe")

    assert _resolve(client, "Janey") == []

    added = client.post(f"/api/people/{person}/aliases", json={"alias": "Janey"})

    assert added.status_code == 201, added.text
    assert _resolve(client, "Janey") == [person]


def test_an_alias_is_matched_without_regard_to_case(client: TestClient) -> None:
    sign_in(client)
    person = make_person(client, "Jane Doe")
    client.post(f"/api/people/{person}/aliases", json={"alias": "Stage Name"})

    assert _resolve(client, "stage name") == [person]
    assert _resolve(client, "JANE DOE") == [person]


def test_the_same_alias_twice_on_one_person_is_refused(client: TestClient) -> None:
    """Per person, and without regard to case: "Jane" and "jane" on one person are one alias."""
    sign_in(client)
    person = make_person(client, "Jane Doe")

    assert client.post(f"/api/people/{person}/aliases", json={"alias": "JD"}).status_code == 201
    assert client.post(f"/api/people/{person}/aliases", json={"alias": "jd"}).status_code == 409


def test_two_people_may_carry_the_same_alias(client: TestClient) -> None:
    """Uniqueness is per person. Two people known by one name is the world being what it is."""
    sign_in(client)
    one = make_person(client, "Alva Renwick")
    two = make_person(client, "Alvarine Renwick")

    assert client.post(f"/api/people/{one}/aliases", json={"alias": "Al"}).status_code == 201
    assert client.post(f"/api/people/{two}/aliases", json={"alias": "Al"}).status_code == 201
    assert set(_resolve(client, "Al")) == {one, two}


def test_the_alias_list_holds_typed_aliases_and_not_linked_usernames(client: TestClient) -> None:
    """A linked username is searchable without being an alias somebody typed.

    Showing it in the editor would invite deleting it and being surprised that the username link
    survived: the username is not the alias, the link is.
    """
    sign_in(client)
    person = make_person(client, "Jane Doe")
    username = make_username(client, "Instagram", "janed")
    client.post(f"/api/usernames/{username}/link-person", json={"person_id": person})
    client.post(f"/api/people/{person}/aliases", json={"alias": "JD"})

    listed = client.get(f"/api/people/{person}/aliases").json()

    assert [alias["alias"] for alias in listed] == ["JD"]


def test_removing_an_alias_stops_it_resolving(client: TestClient) -> None:
    sign_in(client)
    person = make_person(client, "Jane Doe")
    alias_id = client.post(f"/api/people/{person}/aliases", json={"alias": "JD"}).json()["id"]

    removed = client.delete(f"/api/people/{person}/aliases/{alias_id}")

    assert removed.status_code == 204
    assert _resolve(client, "JD") == []


def test_an_alias_can_only_be_removed_through_the_person_carrying_it(client: TestClient) -> None:
    """Both ids, so a stray alias id cannot delete a row belonging to somebody else."""
    sign_in(client)
    one = make_person(client, "Alva Renwick")
    two = make_person(client, "Jane Doe")
    alias_id = client.post(f"/api/people/{one}/aliases", json={"alias": "Al"}).json()["id"]

    assert client.delete(f"/api/people/{two}/aliases/{alias_id}").status_code == 404
    assert _resolve(client, "Al") == [one], "still there"


def test_an_empty_term_names_nobody_rather_than_everybody(client: TestClient) -> None:
    """Every person would match an empty string under the collation."""
    sign_in(client)
    make_person(client, "Jane Doe")

    assert _resolve(client, "   ") == []


def test_deleting_a_person_takes_their_aliases_with_them(client: TestClient) -> None:
    sign_in(client)
    person = make_person(client, "Jane Doe")
    client.post(f"/api/people/{person}/aliases", json={"alias": "JD"})

    assert client.delete(f"/api/people/{person}").status_code == 204
    assert _resolve(client, "JD") == []


# --- links --------------------------------------------------------------------------------------
#
# Where somebody can be found. Not a column on a USERNAME, which would mean first having a row on a
# site, and a person with three addresses and no downloads would have nowhere to put any of them.


def test_a_link_is_kept_as_it_was_written(client: TestClient) -> None:
    """Not normalized. Two spellings of one address are the same often enough to be tempting and
    different often enough to lose a link somebody meant to keep."""
    sign_in(client)
    person = client.post("/api/people", json={"name": "Odalie"}).json()["id"]

    added = client.post(f"/api/people/{person}/links", json={"url": "https://Example.com/Whoever/"})

    assert added.status_code == 201
    assert added.json()["url"] == "https://Example.com/Whoever/"


def test_the_same_address_twice_is_one_link(client: TestClient) -> None:
    """Pasting a link twice is not a mistake worth a refusal."""
    sign_in(client)
    person = client.post("/api/people", json={"name": "Odalie"}).json()["id"]
    url = "https://example.com/whoever"

    first = client.post(f"/api/people/{person}/links", json={"url": url}).json()
    again = client.post(f"/api/people/{person}/links", json={"url": url}).json()

    assert again["id"] == first["id"]
    assert len(client.get(f"/api/people/{person}/links").json()) == 1


@pytest.mark.parametrize(
    "url",
    ["javascript:alert(1)", "data:text/html,x", "file:///etc/passwd", "  ", "example.com"],
    ids=["javascript", "data", "file", "blank", "no-scheme"],
)
def test_an_address_that_is_not_the_web_is_refused(client: TestClient, url: str) -> None:
    """A `javascript:` address in a field a screen turns into a clickable link is the oldest trick
    there is. One rule (http or https) rather than a list of the dangerous ones."""
    sign_in(client)
    person = client.post("/api/people", json={"name": "Odalie"}).json()["id"]

    assert client.post(f"/api/people/{person}/links", json={"url": url}).status_code == 422


def test_a_link_is_filed_under_a_site_sift_already_knows(client: TestClient) -> None:
    """`www.tiktok.com/x` finds the TikTok row a download made, without anybody picking from a
    list. A site Sift has never heard of is kept anyway and simply has none."""
    sign_in(client)
    client.post("/api/sites", json={"name": "TikTok"})
    person = client.post("/api/people", json={"name": "Odalie"}).json()["id"]

    known = client.post(
        f"/api/people/{person}/links", json={"url": "https://www.tiktok.com/@odaliefrisk"}
    ).json()
    unknown = client.post(
        f"/api/people/{person}/links", json={"url": "https://nowhere.example/odaliefrisk"}
    ).json()

    assert known["site_name"] == "TikTok"
    assert unknown["site_id"] is None


def test_a_link_can_only_be_removed_through_the_person_it_belongs_to(client: TestClient) -> None:
    sign_in(client)
    one = client.post("/api/people", json={"name": "Odalie"}).json()["id"]
    other = client.post("/api/people", json={"name": "Grace"}).json()["id"]
    link = client.post(
        f"/api/people/{one}/links", json={"url": "https://example.com/whoever"}
    ).json()["id"]

    assert client.delete(f"/api/people/{other}/links/{link}").status_code == 404
    assert client.delete(f"/api/people/{one}/links/{link}").status_code == 204


def test_a_link_whose_host_names_no_site_is_kept_with_none(client: TestClient) -> None:
    """An address with nothing to reduce: a bare hostname, or none at all.

    Kept rather than refused, for the reason every unknown site is: the field exists for exactly
    the addresses Sift has never heard of, and one it cannot file is still one somebody wanted.
    """
    sign_in(client)
    person = client.post("/api/people", json={"name": "Odalie"}).json()["id"]

    added = client.post(f"/api/people/{person}/links", json={"url": "https://localhost/whoever"})

    assert added.status_code == 201
    assert added.json()["site_id"] is None
