# SPDX-License-Identifier: AGPL-3.0-or-later
"""The guards that fire when a row goes away underneath a request: a write that landed on nothing
answers 404, never 200. The race is simulated by a service reporting exactly that; the visibility
check in front stays real.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sift.kernel import wiring
from sift.kernel.access import Repository, Viewer
from sift.kernel.db import Database
from sift.kernel.ledger import Actor
from sift.slices.people.router import _service
from sift.slices.people.service import PeopleService
from sift.testing.fixtures import Actors

from .conftest import NEVER_EXISTED, Library, make_person, sign_in

pytestmark = pytest.mark.integration


def _site(client: TestClient, name: str) -> str:
    response = client.post("/api/sites", json={"name": name, "kind": None})
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


class VanishedWrites:
    """The real service, proxied, except that every write lands on nothing."""

    def __init__(self, real: PeopleService) -> None:
        self._real = real

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)

    # Every call answers the same, so the service's parameters are not copied here.
    async def set_person_cover(self, *_args: object, **_kwargs: object) -> bool:
        return False

    async def set_site_cover(self, *_args: object, **_kwargs: object) -> bool:
        return False

    async def update_site_details(self, site_id: str, notes: str | None, *, actor: Actor) -> bool:
        return False


class GoneAfterTheCheck:
    """The real repository, except that the person is gone on the second read."""

    def __init__(self, real: Repository) -> None:
        self._real = real
        self._asked = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)

    async def visible_person(self, viewer: Viewer, person_id: str) -> Any:
        self._asked += 1
        if self._asked == 1:
            return await self._real.visible_person(viewer, person_id)
        return None


def _with_service(app: FastAPI, wrapper: type) -> None:
    real = app.state.people
    app.dependency_overrides[_service] = lambda: wrapper(real)


def _with_access(app: FastAPI, wrapper: type) -> None:
    real = app.state.access
    app.dependency_overrides[wiring.access] = lambda: wrapper(real)


@pytest.fixture(autouse=True)
def _clear_overrides(app: FastAPI):  # type: ignore[no-untyped-def]
    yield
    app.dependency_overrides.clear()


# --- a cover pointed at a file the setter cannot open -------------------------------------------


def test_a_person_cover_cannot_be_set_to_a_file_that_is_not_there(client: TestClient) -> None:
    """A cover cannot be set to a file that is missing or not this user's: the same 404."""
    sign_in(client)
    person = make_person(client, "Jane")

    refused = client.put(f"/api/people/{person}/cover", json={"asset_id": NEVER_EXISTED})

    assert refused.status_code == 404


def test_a_site_cover_cannot_be_set_to_a_file_that_is_not_there(client: TestClient) -> None:
    sign_in(client)
    site = _site(client, "Instagram")

    refused = client.put(f"/api/sites/{site}/cover", json={"asset_id": NEVER_EXISTED})

    assert refused.status_code == 404


# --- the row that went away mid-request ----------------------------------------------------------


def test_a_cover_write_that_lands_on_nothing_is_a_404(
    client: TestClient, app: FastAPI, library: Library
) -> None:
    """Somebody deleted the person while this request was in flight. 404, not 200: a route that
    reports success for a write that changed nothing leaves the screen showing a cover that is not
    stored anywhere."""
    sign_in(client)
    person = make_person(client, "Jane")
    _with_service(app, VanishedWrites)

    answer = client.put(f"/api/people/{person}/cover", json={"asset_id": library.shared})

    assert answer.status_code == 404


def test_a_person_who_vanishes_after_the_write_is_a_404_too(
    client: TestClient, app: FastAPI, library: Library
) -> None:
    """The narrower window: the write landed, and the person went before the answer could be built
    from them. There is nothing to return, and inventing one would be worse than the 404."""
    sign_in(client)
    person = make_person(client, "Jane")
    _with_access(app, GoneAfterTheCheck)

    answer = client.put(f"/api/people/{person}/cover", json={"asset_id": library.shared})

    assert answer.status_code == 404


def test_a_site_cover_write_that_lands_on_nothing_is_a_404(
    client: TestClient, app: FastAPI, library: Library
) -> None:
    sign_in(client)
    site = _site(client, "Instagram")
    _with_service(app, VanishedWrites)

    answer = client.put(f"/api/sites/{site}/cover", json={"asset_id": library.shared})

    assert answer.status_code == 404


def test_details_written_onto_nothing_are_a_404(client: TestClient, app: FastAPI) -> None:
    sign_in(client)
    site = _site(client, "Instagram")
    _with_service(app, VanishedWrites)

    answer = client.put(f"/api/sites/{site}/details", json={"notes": "a note"})

    assert answer.status_code == 404


# --- the link that is explicitly nothing ---------------------------------------------------------


def test_a_link_sent_as_nothing_is_stored_as_nothing(client: TestClient) -> None:
    """Sent, rather than left out. The two mean different things (one clears the links, the
    other leaves whatever was there alone), so the validator has to survive being handed a null."""
    sign_in(client)
    site = _site(client, "Instagram")
    client.put(
        f"/api/sites/{site}/details",
        json={"notes": None, "links": ["https://instagram.com"]},
    )

    cleared = client.put(f"/api/sites/{site}/details", json={"notes": None, "links": None})

    assert cleared.status_code == 200, cleared.text
    row = next(p for p in client.get("/api/sites").json()["items"] if p["id"] == site)
    assert row["site_url"] is None


# --- the rating bound, where it is enforced ------------------------------------------------------


async def test_the_service_refuses_a_rating_outside_the_stars(client: TestClient) -> None:
    """The service bounds a rating too, for callers without a request."""
    service: PeopleService = client.app.state.people  # type: ignore[attr-defined]

    with pytest.raises(ValueError, match="1 to 10"):
        # Eleven: what is stored is out of ten whichever scale is drawn, so six is a rating.
        await service.set_person_rating(NEVER_EXISTED, NEVER_EXISTED, 11)


# --- the same guards, one layer down -------------------------------------------------------------
#
# Unreachable through a request that is not racing, so asked of the service directly.


@pytest.fixture
async def people_service(temp_db: Database, access: Repository) -> PeopleService:
    """The service on a database of its own."""
    return PeopleService(temp_db, access)


async def test_editing_a_person_who_is_not_there_answers_nothing(
    people_service: PeopleService, actors: Actors
) -> None:
    """The write lands on no row, so there is nothing to describe and None is the answer."""
    assert (
        await people_service.update_person(
            actors.admin, NEVER_EXISTED, "Nobody", vault=False, notes=None
        )
    ) is None


async def test_hiding_a_site_that_is_not_there_answers_false(
    people_service: PeopleService, actors: Actors
) -> None:
    """Looked up first rather than left to the write, because the write cannot fail: the state row
    is the user's own and would happily be created against an id naming nothing."""
    assert await people_service.set_site_vault(actors.admin, NEVER_EXISTED, vault=True) is False
