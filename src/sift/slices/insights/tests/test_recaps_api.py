# SPDX-License-Identifier: AGPL-3.0-or-later
"""The recaps over HTTP: the list with its announcement, one opened, the cross, and another's.

A small application carrying only this router and the database, with the sign-in and the cross-site
check stood in for: those are the auth slice's, and who may call each route at all is proved by
the authz matrix. What is under test here is what the routes answer: a User reads only their own,
and somebody else's recap is the same 404 as one that never existed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from sift.kernel import wiring
from sift.kernel.access import Repository, Role, Viewer
from sift.kernel.wiring import provide
from sift.slices.auth import csrf_protect, current_viewer
from sift.slices.insights import recaps_router
from sift.slices.insights.tests.conftest import World
from sift.slices.insights.tests.test_api import Preferences
from sift.slices.insights.tests.test_recaps import made, reader, september

pytestmark = pytest.mark.integration

NEVER_MINTED = "01HX0000000000000000000099"


@pytest.fixture
def preferences() -> Preferences:
    return Preferences()


@pytest.fixture
def app(world: World, preferences: Preferences, access: Repository) -> FastAPI:
    app = FastAPI()
    provide(app, wiring.DATABASE, world.db)
    provide(app, wiring.ACCESS, access)
    provide(app, wiring.SETTINGS_HUB, preferences)
    app.include_router(recaps_router.router, prefix="/api")
    app.dependency_overrides[csrf_protect] = lambda: None
    return app


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://sift.test") as running:
        yield running


def as_(app: FastAPI, viewer: Viewer) -> None:
    app.dependency_overrides[current_viewer] = lambda: viewer


async def test_the_list_announces_it_and_opening_ends_that(
    app: FastAPI, client: httpx.AsyncClient, world: World
) -> None:
    await september(world)
    recap_id = await made(world)
    as_(app, reader(world, unlocked=True))

    listed = (await client.get("/api/insights/recaps")).json()
    assert [head["id"] for head in listed["recaps"]] == [recap_id]
    assert listed["announced"]["id"] == recap_id
    assert listed["announced"]["title"] == "Your September"

    opened = await client.get(f"/api/insights/recaps/{recap_id}")
    assert opened.status_code == 200, opened.text
    body = opened.json()
    assert body["cards"][0]["kind"] == "headline"
    assert body["cards"][-1]["kind"] == "closing"
    # The recipe (the whole figures) never crosses the wire.
    assert "recipe" not in opened.text

    after = (await client.get("/api/insights/recaps")).json()
    assert after["announced"] is None
    assert after["recaps"][0]["seen_at"] is not None


async def test_the_cross_ends_the_announcement_and_another_users_recap_is_missing(
    app: FastAPI, client: httpx.AsyncClient, world: World
) -> None:
    await september(world)
    recap_id = await made(world)

    stranger = Viewer(id=await world.add_user("u-stranger"), role=Role.GUEST, show_hidden=True)
    as_(app, stranger)
    assert (await client.get(f"/api/insights/recaps/{recap_id}")).status_code == 404
    assert (await client.post(f"/api/insights/recaps/{recap_id}/dismiss")).status_code == 404
    assert (await client.get("/api/insights/recaps")).json() == {"recaps": [], "announced": None}

    as_(app, reader(world, unlocked=True))
    assert (await client.get(f"/api/insights/recaps/{NEVER_MINTED}")).status_code == 404
    assert (await client.post(f"/api/insights/recaps/{recap_id}/dismiss")).status_code == 204
    listed = (await client.get("/api/insights/recaps")).json()
    assert listed["announced"] is None
    assert [head["id"] for head in listed["recaps"]] == [recap_id]


async def test_a_recap_says_its_favourite_hour_on_the_readers_clock(
    app: FastAPI, client: httpx.AsyncClient, world: World, preferences: Preferences
) -> None:
    await september(world)
    recap_id = await made(world)
    as_(app, reader(world, unlocked=True))

    def when(body: Any) -> str:
        (card,) = [one for one in body["cards"] if one["kind"] == "when"]
        return "".join(piece["text"] for piece in card["statement"])

    preferences.clock = "24"
    body = (await client.get(f"/api/insights/recaps/{recap_id}")).json()
    assert "began at 22:00" in when(body)

    preferences.clock = "12"
    body = (await client.get(f"/api/insights/recaps/{recap_id}")).json()
    assert "began at 10:00 PM" in when(body)
