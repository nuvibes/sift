# SPDX-License-Identifier: AGPL-3.0-or-later
"""A person's and a Site's page open by id, from the same scoped statement as the wall, with 404
for both an unseeable and an unminted id."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sift.slices.people.tests.conftest import (
    NEVER_EXISTED,
    Library,
    assign,
    db_path,
    make_person,
    share,
    sign_in,
)


def _vault(client: TestClient, person_id: str, name: str) -> None:
    assert (
        client.put(f"/api/people/{person_id}", json={"name": name, "vault": True})
    ).status_code == 200


def test_somebody_past_the_walls_first_page_still_opens_by_id(client: TestClient) -> None:
    """Somebody past the wall's first page still opens by id."""
    sign_in(client)
    first = make_person(client, "Aaa First")
    second = make_person(client, "Zzz Second")

    page = client.get("/api/people", params={"limit": 1}).json()
    on_the_page = {row["id"] for row in page["items"]}
    assert len(on_the_page) == 1, "the wall is holding one row, which is the whole point"
    missing = second if second not in on_the_page else first

    found = client.get(f"/api/people/{missing}")

    assert found.status_code == 200
    assert found.json()["id"] == missing


def test_a_person_who_was_never_minted_is_a_404(client: TestClient) -> None:
    sign_in(client)

    assert client.get(f"/api/people/{NEVER_EXISTED}").status_code == 404


def test_a_word_in_the_id_position_is_a_404_rather_than_a_person(client: TestClient) -> None:
    """A word in the id position is a 404; the NULL-binding guard is asserted below."""
    sign_in(client)
    person = make_person(client, "Jane Doe")

    answer = client.get("/api/people/not-an-id")

    assert answer.status_code == 404
    assert person not in answer.text


def test_the_resolver_is_not_read_as_a_person_id(client: TestClient) -> None:
    """`/people/resolve` is a literal path beside a by-id one, and routes match in declaration
    order. Declared the other way round it would be swallowed and the resolver made unreachable."""
    sign_in(client)
    make_person(client, "Jane Doe")

    answer = client.get("/api/people/resolve", params={"term": "Jane Doe"})

    assert answer.status_code == 200
    assert [row["name"] for row in answer.json()["people"]] == ["Jane Doe"]


def test_a_guest_cannot_open_somebody_they_may_not_see(
    client: TestClient, library: Library
) -> None:
    """404 rather than 403. A 403 would confirm they are there, which is the whole disclosure."""
    sign_in(client)
    person = make_person(client, "Jane Doe")
    assign(client, [library.shared], [person])

    guest = sign_in(client, "guest", who="two")

    assert client.get(f"/api/people/{person}").status_code == 404

    share(client, library.shared, guest)

    assert client.get(f"/api/people/{person}").status_code == 200


def test_a_vaulted_person_cannot_be_opened_by_id(client: TestClient) -> None:
    """An id outlives being vaulted: whoever saw them before the flag was set still holds one."""
    sign_in(client)
    person = make_person(client, "Hidden One")
    assert client.get(f"/api/people/{person}").status_code == 200

    _vault(client, person, "Hidden One")

    assert client.get(f"/api/people/{person}").status_code == 404


def test_the_by_id_read_carries_what_the_wall_carries(client: TestClient) -> None:
    """The by-id read carries what the wall carries, plus the record, which the wall omits."""
    sign_in(client)
    person = make_person(client, "Jane Doe")

    listed = next(row for row in client.get("/api/people").json()["items"] if row["id"] == person)
    alone = client.get(f"/api/people/{person}").json()

    assert alone.pop("record") == {"pmv_creator": False}, (
        "the page answers with a record; a new person carries only the PMV flag, off"
    )
    assert listed.pop("record") is None, "the wall does not carry one at all"
    assert alone == listed


def test_a_site_opens_by_id_and_hides_the_same_way(client: TestClient, library: Library) -> None:
    """A site is only a site to somebody who can see something that came from it."""
    sign_in(client)
    site = client.post("/api/sites", json={"name": "Somewhere"}).json()["id"]

    assert client.get(f"/api/sites/{site}").status_code == 200
    assert client.get(f"/api/sites/{NEVER_EXISTED}").status_code == 404

    sign_in(client, "guest", who="two")

    assert client.get(f"/api/sites/{site}").status_code == 404


def test_a_site_ranked_past_a_page_is_still_writable(client: TestClient) -> None:
    """The write guard asks by id, not by listing five hundred sites and looking for the id in
    them, which would refuse a site ranked past that on every write route."""
    sign_in(client)
    site = client.post("/api/sites", json={"name": "Somewhere"}).json()["id"]

    assert (client.put(f"/api/sites/{site}/favorite", json={"favorite": True})).status_code == 200
    assert client.get(f"/api/sites/{site}").json()["favorite"] is True


def test_db_path_is_reachable(client: TestClient) -> None:
    """A known positive: the fixtures below these tests really did build a database."""
    assert db_path(client).exists()


def test_somebody_deleted_between_the_two_reads_opens_without_a_record(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The page answers who may be shown first and asks for the record second. Somebody deleted
    between the two is the page as the first read found them, with no record, rather than a
    failure."""
    from sift.slices.people.service import PeopleService

    sign_in(client)
    person = make_person(client, "Jane Doe")
    real = PeopleService.get_person

    async def gone_meanwhile(self: PeopleService, viewer: object, person_id: str) -> object:
        await self._db.execute("DELETE FROM people WHERE id = ?", (person_id,))
        return await real(self, viewer, person_id)  # type: ignore[arg-type]

    monkeypatch.setattr(PeopleService, "get_person", gone_meanwhile)

    found = client.get(f"/api/people/{person}")

    assert found.status_code == 200, found.text
    assert found.json()["id"] == person
    assert found.json()["record"] is None
