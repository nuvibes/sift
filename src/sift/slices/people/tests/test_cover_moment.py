# SPDX-License-Identifier: AGPL-3.0-or-later
"""A cover can name which moment of a clip it is. The moment is stored with the file in one write,
never asked for in an address, and its still is asked for through the saved Loop's seam.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from sift.kernel import wiring
from sift.slices.people.tests.conftest import Library, db_path, make_person, read, sign_in


class _Asked:
    """The still seam, remembering what it was asked for instead of queueing anything."""

    def __init__(self) -> None:
        self.wanted: list[tuple[str, int]] = []

    async def wants_still(self, asset_id: str, at_ms: int) -> None:
        self.wanted.append((asset_id, at_ms))


@pytest.fixture
def asked(app: FastAPI) -> _Asked:
    """Stand in for the queue, so a test can see what a cover write asked to have rendered."""
    seam = _Asked()
    wiring.provide(app, wiring.STILLS, seam)
    return seam


def _cover_row(client: TestClient, person: str) -> dict[str, Any]:
    rows = read(
        db_path(client),
        "SELECT cover_asset_id, cover_at_ms FROM people WHERE id = ?",
        (person,),
    )
    return dict(rows[0])


def test_the_moment_is_stored_beside_the_file(client: TestClient, library: Library) -> None:
    sign_in(client)
    person = make_person(client, "Somebody")

    answer = client.put(
        f"/api/people/{person}/cover",
        json={"asset_id": library.shared, "at_ms": 61_500},
    )

    assert answer.status_code == 200
    assert _cover_row(client, person) == {
        "cover_asset_id": library.shared,
        "cover_at_ms": 61_500,
    }


def test_choosing_a_different_file_does_not_leave_the_old_moment_behind(
    client: TestClient, library: Library
) -> None:
    """Choosing a different file clears the old moment: one statement writes the pair."""
    sign_in(client)
    person = make_person(client, "Somebody")
    client.put(f"/api/people/{person}/cover", json={"asset_id": library.shared, "at_ms": 61_500})

    client.put(f"/api/people/{person}/cover", json={"asset_id": library.private})

    assert _cover_row(client, person) == {"cover_asset_id": library.private, "cover_at_ms": None}


def test_clearing_the_cover_clears_the_moment(client: TestClient, library: Library) -> None:
    sign_in(client)
    person = make_person(client, "Somebody")
    client.put(f"/api/people/{person}/cover", json={"asset_id": library.shared, "at_ms": 1_000})

    client.put(f"/api/people/{person}/cover", json={"asset_id": None})

    assert _cover_row(client, person) == {"cover_asset_id": None, "cover_at_ms": None}


def test_a_moment_asks_for_its_picture(client: TestClient, library: Library, asked: _Asked) -> None:
    """Through the seam the marks already use. One job, one cache key, one way of asking."""
    sign_in(client)
    person = make_person(client, "Somebody")

    client.put(f"/api/people/{person}/cover", json={"asset_id": library.shared, "at_ms": 61_500})

    assert asked.wanted == [(library.shared, 61_500)]


def test_a_cover_with_no_moment_asks_for_nothing(
    client: TestClient, library: Library, asked: _Asked
) -> None:
    """The file's own still already exists. Asking for it again would be a job per cover set."""
    sign_in(client)
    person = make_person(client, "Somebody")

    client.put(f"/api/people/{person}/cover", json={"asset_id": library.shared})

    assert asked.wanted == []


def test_a_negative_moment_is_refused(client: TestClient, library: Library) -> None:
    """Not a time. Bounded below in the model; the ceiling is the clip's running time, which the
    model does not know. Past the end ffmpeg gives the last frame, as it does everywhere else."""
    sign_in(client)
    person = make_person(client, "Somebody")

    answer = client.put(
        f"/api/people/{person}/cover", json={"asset_id": library.shared, "at_ms": -1}
    )

    assert answer.status_code == 422


def test_the_cover_is_read_at_its_own_address(client: TestClient, library: Library) -> None:
    """The cover is read at its own address; a 404 means no cover, nothing openable, or not yet
    rendered."""
    sign_in(client)
    person = make_person(client, "Somebody")
    client.put(f"/api/people/{person}/cover", json={"asset_id": library.shared, "at_ms": 61_500})

    answer = client.get(f"/api/people/{person}/cover")

    assert answer.status_code == 404


def test_a_moment_cannot_be_asked_for_in_an_address(client: TestClient, library: Library) -> None:
    """The property the whole design rests on.

    A still is filed under `(asset_id, kind, params)`. If a caller could name the moment, they could
    ask for an unbounded number of distinct pictures of one file and fill the cache with them. So
    the moment comes off the ROW, and a query string naming one changes nothing at all.
    """
    sign_in(client)
    person = make_person(client, "Somebody")
    client.put(f"/api/people/{person}/cover", json={"asset_id": library.shared})

    with_moment = client.get(f"/api/people/{person}/cover", params={"at_ms": 61_500})
    without = client.get(f"/api/people/{person}/cover")

    assert with_moment.status_code == without.status_code
