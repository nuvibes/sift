# SPDX-License-Identifier: AGPL-3.0-or-later
"""Your path over HTTP: the three routes, answered for whoever is signed in.

A small application carrying only this router and the parts it reads, with the sign-in and the
cross-site check stood in for: those two are the auth slice's and are proved there and by the
authz matrix. What is under test here is what the routes answer and refuse. Driven in the test's
own event loop (`httpx.ASGITransport`), because the database it reads was opened in that loop.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime

import httpx
import pytest
from fastapi import FastAPI

import sift.main  # noqa: F401 (every component registers its tables on import)
from sift.kernel import wiring
from sift.kernel.access import Role, Viewer
from sift.kernel.config import Settings
from sift.kernel.content import LibraryStore
from sift.kernel.db import Database
from sift.kernel.wiring import part_of_app_or_none, provide
from sift.kernel.workbench import Workbench
from sift.slices.auth import csrf_protect, current_viewer
from sift.slices.insights.path import PathService
from sift.slices.insights.path_router import SERVICE, router
from sift.slices.insights.tests.test_path import Pile
from sift.slices.settings_hub.service import SettingsService
from sift.testing.fixtures import create_user

NOW = datetime(2026, 9, 23, 12, 0).timestamp()


@pytest.fixture
async def people(temp_db: Database) -> AsyncIterator[dict[str, Viewer]]:
    await temp_db.initialize_schema()
    yield {
        "admin": await create_user(temp_db, Role.ADMIN),
        "guest": await create_user(temp_db, Role.GUEST),
    }


@pytest.fixture
def app(temp_db: Database, people: dict[str, Viewer], settings: Settings) -> FastAPI:
    app = FastAPI()
    board = Workbench()
    board.register(Pile("folders", 25, verb="folders to name"))
    hub = SettingsService(temp_db)
    provide(app, wiring.DATABASE, temp_db)
    provide(app, wiring.WORKBENCH, board)
    provide(app, wiring.INTERFACE_STATE, hub)
    # Held already, on a fixed clock, which is also what the router does on first use.
    provide(
        app,
        SERVICE,
        PathService(temp_db, board, hub, library=LibraryStore(temp_db, settings), now=lambda: NOW),
    )
    app.include_router(router, prefix="/api")
    app.dependency_overrides[csrf_protect] = lambda: None
    return app


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://sift.test") as running:
        yield running


def as_(app: FastAPI, viewer: Viewer) -> None:
    app.dependency_overrides[current_viewer] = lambda: viewer


async def test_the_paths_are_read_whole(
    app: FastAPI, client: httpx.AsyncClient, people: dict[str, Viewer]
) -> None:
    as_(app, people["admin"])
    body = (await client.get("/api/insights/path")).json()
    assert set(body) == {"paths", "hints"}
    assert [one["id"] for one in body["paths"]] == ["set_up", "watch", "organize"]
    first = body["paths"][0]
    assert first["title"] == "Set up your library" and first["sentence"][0]["text"]
    goal = first["goals"][0]
    assert goal["help"][0]["text"] and goal["done"] is False and goal["done_at"] is None


async def test_no_quest_can_be_declined_any_more(
    app: FastAPI, client: httpx.AsyncClient, people: dict[str, Viewer]
) -> None:
    """There are no weekly quests: the address answers as one that does not exist."""
    as_(app, people["admin"])
    assert (await client.post("/api/insights/path/quests/folders/decline")).status_code in (
        404,
        405,
    )


async def test_a_hint_seen_is_remembered_and_an_unknown_one_is_missing(
    app: FastAPI, client: httpx.AsyncClient, people: dict[str, Viewer]
) -> None:
    as_(app, people["admin"])
    assert (await client.post("/api/insights/path/hints/first_insights/seen")).status_code == 204
    hints = {
        one["name"]: one["seen"] for one in (await client.get("/api/insights/path")).json()["hints"]
    }
    assert hints["first_insights"] is True and hints["first_pile"] is False
    assert (await client.post("/api/insights/path/hints/nope/seen")).status_code == 404


async def test_the_service_is_built_on_first_use_and_held_for_every_request(
    temp_db: Database, people: dict[str, Viewer], settings: Settings
) -> None:
    """One service for the life of the application, built on the first request."""
    app = FastAPI()
    provide(app, wiring.DATABASE, temp_db)
    provide(app, wiring.WORKBENCH, Workbench())
    # The hub as the keeper of interface state, and NOT as the preferences reader: the path reads
    # and writes a hint's "seen" through the interface part alone.
    provide(app, wiring.INTERFACE_STATE, SettingsService(temp_db))
    provide(app, wiring.LIBRARY, LibraryStore(temp_db, settings))
    app.include_router(router, prefix="/api")
    as_(app, people["admin"])
    assert part_of_app_or_none(app, SERVICE) is None
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://sift.test") as running:
        assert (await running.get("/api/insights/path")).status_code == 200
        built = part_of_app_or_none(app, SERVICE)
        assert isinstance(built, PathService)
        assert (await running.get("/api/insights/path")).status_code == 200
    assert part_of_app_or_none(app, SERVICE) is built
