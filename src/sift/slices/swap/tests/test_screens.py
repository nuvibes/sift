# SPDX-License-Identifier: AGPL-3.0-or-later
"""The screens' routes: the guest's Take answers the offer with what was skipped and unticked, is
refused where there is no offer to answer, the tunnels list says which can host a swap, and the
face descriptions a swap brought wait on a person's page until they are added.

Who may call them is the authz matrix's; here `require_admin` and the CSRF check are stood in for.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from sift.kernel import wiring
from sift.kernel.tunnels import TUNNELS
from sift.kernel.wiring import RECOGNITION, provide
from sift.slices.auth import csrf_protect, require_admin
from sift.slices.swap.models import SwapSession, SwapTunnel
from sift.slices.swap.router import router
from sift.slices.swap.session import SESSIONS, SwapSessions, Taken
from sift.slices.swap.tests.test_session import _database, _sessions

pytestmark = pytest.mark.integration


class _Access:
    """Two people, one of them out of this viewer's sight."""

    async def visible_person(self, viewer: object, person_id: str) -> SimpleNamespace | None:
        return SimpleNamespace(name="Cassia Lynn") if person_id == "p-seen" else None


class _Recognition:
    """What the face feature holds under a name, spent by a claim, as `claim_for` spends it."""

    def __init__(self) -> None:
        self.held = {"Cassia Lynn": 3}
        self.claims: list[tuple[str, str]] = []

    async def held_for(self, person_id: str, name: str) -> int:
        return self.held.get(name, 0)

    async def claim_for(self, person_id: str, name: str) -> int:
        self.claims.append((person_id, name))
        return self.held.pop(name, 0)


class _Tunnels:
    async def list(self) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(
                id="t1", name="Tunnel one", can_host=True, up=True, endpoint="198.51.100.7"
            ),
            SimpleNamespace(
                id="t2", name="Tunnel two", can_host=False, up=False, endpoint="198.51.100.8"
            ),
            SimpleNamespace(id="t3", name="Tunnel three", can_host=None, up=False, endpoint=None),
        ]


@pytest.fixture
async def world(tmp_path: Path) -> AsyncIterator[tuple[httpx.AsyncClient, SwapSessions]]:
    database = await _database(tmp_path / "swap.sqlite3")
    sessions = _sessions(database, tmp_path)
    app = FastAPI()
    provide(app, SESSIONS, sessions)
    provide(app, TUNNELS, _Tunnels())
    app.include_router(router, prefix="/api")
    app.dependency_overrides[require_admin] = lambda: SimpleNamespace(id="admin")
    app.dependency_overrides[csrf_protect] = lambda: None
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://sift.test") as client:
            yield client, sessions
    finally:
        await database.close()


async def test_take_hands_the_session_what_was_skipped_and_unticked(
    world: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, sessions = world
    await sessions.store.create(
        "S1", role="guest", started_at=1, started_by="admin", dest_folder_id="folder"
    )
    heard: list[tuple[str, Taken]] = []

    async def take(session_id: str, taken: Taken) -> None:
        heard.append((session_id, taken))

    monkeypatch.setattr(sessions, "take", take)
    answer = await client.post(
        "/api/swap/sessions/S1/take", json={"skipped": [2, 0], "unticked": ["k9"]}
    )
    assert answer.status_code == 200, answer.text
    assert answer.json()["id"] == "S1"
    assert heard == [("S1", Taken(skipped=frozenset({0, 2}), unticked=frozenset({"k9"})))]


async def test_take_is_refused_without_an_offer_and_for_no_such_swap(world: Any) -> None:
    client, sessions = world
    await sessions.store.create(
        "S2", role="guest", started_at=1, started_by="admin", dest_folder_id="folder"
    )
    early = await client.post("/api/swap/sessions/S2/take", json={})
    assert early.status_code == 409
    assert early.json()["detail"] == "There's no offer to answer yet."
    assert (await client.post("/api/swap/sessions/nope/take", json={})).status_code == 404
    negative = await client.post("/api/swap/sessions/S2/take", json={"skipped": [-1]})
    assert negative.status_code == 422


async def test_the_tunnels_say_whether_each_can_host(world: Any) -> None:
    client, _ = world
    answer = (await client.get("/api/swap/tunnels")).json()
    assert answer == [
        {"id": "t1", "name": "Tunnel one", "can_host": True, "endpoint": "198.51.100.7"},
        {"id": "t2", "name": "Tunnel two", "can_host": False, "endpoint": None},
        {"id": "t3", "name": "Tunnel three", "can_host": None, "endpoint": None},
    ]


def test_nothing_the_screens_read_can_carry_an_address() -> None:
    """The session read and the tunnels list are what the swap screens draw, so neither may have a
    field an address, a port or the secret could travel in. The token is the one place an address
    is, sealed inside it, and it is only on the host's own read while nobody has joined (or again
    while a tunnel has cut the session off, to be sent again).

    The one server named is the tunnel's own, `endpoint`: the VPN provider's machine Sites and
    Tunnels already shows beside every tunnel, drawn beside the chooser
    so a swap's tunnel is chosen with its server in view. Never the hosting's public
    address or port, which only the token carries."""
    carrying = {"address", "ip", "ipv4", "public_ipv4", "port", "external_port", "secret"}
    for model in (SwapSession, SwapTunnel):
        named = {part for field in model.model_fields for part in field.split("_")}
        assert not carrying & (set(model.model_fields) | named), model.__name__


@pytest.fixture
async def faces() -> AsyncIterator[tuple[httpx.AsyncClient, _Recognition]]:
    recognition = _Recognition()
    app = FastAPI()
    provide(app, RECOGNITION, recognition)
    app.include_router(router, prefix="/api")
    app.dependency_overrides[wiring.access] = _Access
    app.dependency_overrides[require_admin] = lambda: SimpleNamespace(id="admin")
    app.dependency_overrides[csrf_protect] = lambda: None
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://sift.test") as client:
        yield client, recognition


async def test_what_a_swap_brought_is_offered_and_only_the_press_adds_it(faces: Any) -> None:
    """The read counts and writes nothing; the press adds, answers what it added and what is left,
    and a second press adds nothing."""
    client, recognition = faces

    read = await client.get("/api/swap/people/p-seen/held-faces")
    assert read.status_code == 200, read.text
    assert read.json() == {"waiting": 3, "added": 0}
    assert recognition.claims == []

    pressed = await client.post("/api/swap/people/p-seen/held-faces")
    assert pressed.status_code == 200, pressed.text
    assert pressed.json() == {"waiting": 0, "added": 3}
    assert recognition.claims == [("p-seen", "Cassia Lynn")]

    again = await client.post("/api/swap/people/p-seen/held-faces")
    assert again.json() == {"waiting": 0, "added": 0}


async def test_somebody_out_of_sight_is_not_there_to_offer_faces_for(faces: Any) -> None:
    client, recognition = faces

    assert (await client.get("/api/swap/people/p-hidden/held-faces")).status_code == 404
    assert (await client.post("/api/swap/people/p-hidden/held-faces")).status_code == 404
    assert recognition.claims == []
