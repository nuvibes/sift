# SPDX-License-Identifier: AGPL-3.0-or-later
"""The tunnel and routing endpoints, driven against the real application.

The store behind them is stood in for, because what these routes decide is not what a tunnel does
(that is tested against a real process elsewhere) but what an admin gets back: a refusal that names
the tunnel, a 409 before there is a key to encrypt a configuration with, and a listing that carries
a tunnel's name and health and nothing about the provider behind it.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sift.kernel.config import get_settings
from sift.kernel.http import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from sift.kernel.jobs import worker_pool
from sift.kernel.tunnels import TunnelError, TunnelView
from sift.main import create_app
from sift.slices.auth.crypto import generate_master_key
from sift.testing.auth import establish_session

pytestmark = [pytest.mark.integration]

_CONFIG = "[Interface]\nPrivateKey = k\n"


def _a_tunnel() -> TunnelView:
    return TunnelView(
        id="t1",
        name="Sweden",
        enabled=True,
        running=True,
        up=True,
        draining=False,
        last_handshake_at=1700,
    )


class _Store:
    """Stands in for the tunnel store. Records what it was asked, and can be told to refuse."""

    def __init__(self, *, refuse: str | None = None) -> None:
        self.refuse = refuse
        self.calls: list[tuple[str, object]] = []
        self.views: list[TunnelView] = []
        self.route_rows: dict[str, str] = {}

    def _maybe_refuse(self) -> None:
        if self.refuse is not None:
            raise TunnelError(self.refuse)

    async def add(self, *, name: str, config: str, master_key: bytes) -> str:
        self.calls.append(("add", name))
        self._maybe_refuse()
        return "t1"

    async def replace_config(self, tunnel_id: str, *, config: str, master_key: bytes) -> None:
        self.calls.append(("replace_config", tunnel_id))
        self._maybe_refuse()

    async def rename(self, tunnel_id: str, name: str) -> None:
        self.calls.append(("rename", name))

    async def start(self, tunnel_id: str, master_key: bytes) -> None:
        self.calls.append(("start", tunnel_id))
        self._maybe_refuse()

    async def stop(self, tunnel_id: str, *, drain: bool = True) -> None:
        self.calls.append(("stop", drain))

    async def remove(self, tunnel_id: str) -> None:
        self.calls.append(("remove", tunnel_id))

    async def list(self) -> list[TunnelView]:
        return self.views

    async def routes(self) -> dict[str, str]:
        return dict(self.route_rows)

    async def set_route(self, scope: str, route: str) -> None:
        self.calls.append(("set_route", (scope, route)))

    async def clear_route(self, scope: str) -> None:
        self.calls.append(("clear_route", scope))

    async def stop_all(self) -> None:
        # Shutdown calls this. A tunnel is a process, and leaving one running after the app has
        # gone is a proxy on a loopback port with nothing left that knows about it.
        self.calls.append(("stop_all", None))


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()
    yield create_app()
    get_settings.cache_clear()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as started:

        async def noop(_ctx: object) -> None:
            return None

        worker_pool._HANDLERS["download"] = noop
        yield started


def _store(client: TestClient, store: _Store) -> _Store:
    client.app.state.tunnels = store  # type: ignore[attr-defined]
    return store


def _admin(client: TestClient, *, with_key: bool = True) -> None:
    db_path = client.app.state.database.path  # type: ignore[attr-defined]
    user_id, token, csrf = establish_session(
        db_path, role="admin", username="tunnel-admin", password="Dl-Test-Passw0rd!"
    )
    client.cookies.set(SESSION_COOKIE_NAME, token)
    client.headers[CSRF_HEADER_NAME] = csrf
    if with_key:
        client.app.state.master_keys.store(user_id, generate_master_key())  # type: ignore[attr-defined]


def test_a_tunnel_is_imported_named_and_never_handed_back(client: TestClient) -> None:
    _admin(client)
    store = _store(client, _Store())
    store.views = [_a_tunnel()]

    created = client.post("/api/tunnels", json={"name": "Sweden", "config": _CONFIG})
    assert created.status_code == 201
    assert created.json()["id"] == "t1"

    listing = client.get("/api/tunnels")
    assert listing.status_code == 200
    (row,) = listing.json()
    assert row["name"] == "Sweden" and row["up"] is True
    # The configuration carries a private key, so nothing about the provider comes back: not the
    # exit address, not the key, not the file.
    assert "PrivateKey" not in listing.text


def test_the_listing_says_whether_each_tunnel_can_host_a_swap(client: TestClient) -> None:
    """The tunnels screen draws "Can host a swap", "Can't host a swap" or "Not tried for a swap
    yet" from one field, so all three answers have to arrive, and "not tried" as null, never as
    false, or a tunnel nobody has tried reads as one that failed."""
    _admin(client)
    store = _store(client, _Store())
    base = _a_tunnel()
    store.views = [
        replace(base, id="t1", can_host=True),
        replace(base, id="t2", can_host=False),
        replace(base, id="t3", can_host=None),
    ]

    listing = client.get("/api/tunnels")
    assert listing.status_code == 200
    assert [(row["id"], row["can_host"]) for row in listing.json()] == [
        ("t1", True),
        ("t2", False),
        ("t3", None),
    ]
    # Only the answer: nothing about the port a provider hands out is listed.
    assert all("port" not in key for row in listing.json() for key in row)


def test_a_configuration_that_will_not_work_is_refused_where_it_is_imported(
    client: TestClient,
) -> None:
    _admin(client)
    _store(client, _Store(refuse="That configuration is missing a private key."))

    refused = client.post("/api/tunnels", json={"name": "Sweden", "config": "nonsense"})

    assert refused.status_code == 400
    assert "private key" in refused.json()["detail"]


def test_setting_up_a_tunnel_asks_for_a_password_login_first(client: TestClient) -> None:
    """A configuration is sealed under a key that exists only after a password login, so a session
    resumed from a cookie is asked to sign in rather than having something stored unprotected."""
    _admin(client, with_key=False)
    _store(client, _Store())

    response = client.post("/api/tunnels", json={"name": "Sweden", "config": _CONFIG})

    assert response.status_code == 409
    assert "password" in response.json()["detail"]


def test_a_reissued_configuration_replaces_the_old_one(client: TestClient) -> None:
    _admin(client)
    store = _store(client, _Store())
    assert client.post("/api/tunnels/t1/config", json={"config": _CONFIG}).status_code == 204
    assert ("replace_config", "t1") in store.calls


def test_a_configuration_that_will_not_work_is_refused_on_replacement_too(
    client: TestClient,
) -> None:
    _admin(client)
    _store(client, _Store(refuse="That configuration is missing a private key."))
    assert client.post("/api/tunnels/t1/config", json={"config": "nonsense"}).status_code == 400


def test_a_tunnel_can_be_renamed_without_moving_any_site_off_it(client: TestClient) -> None:
    _admin(client)
    store = _store(client, _Store())
    assert client.patch("/api/tunnels/t1", json={"name": "Norway"}).status_code == 204
    assert ("rename", "Norway") in store.calls


def test_starting_a_tunnel_that_does_not_come_up_says_so(client: TestClient) -> None:
    """Answering yes before the far end has replied would put a green control over a tunnel
    carrying nothing, which is worse than an obviously broken one."""
    _admin(client)
    _store(client, _Store(refuse="Sweden did not connect."))

    refused = client.post("/api/tunnels/t1/start", json={})

    assert refused.status_code == 400
    assert "Sweden" in refused.json()["detail"]


def test_starting_a_tunnel_asks_for_a_password_login_first(client: TestClient) -> None:
    _admin(client, with_key=False)
    _store(client, _Store())
    assert client.post("/api/tunnels/t1/start", json={}).status_code == 409


def test_a_tunnel_is_started_stopped_and_removed(client: TestClient) -> None:
    _admin(client)
    store = _store(client, _Store())

    assert client.post("/api/tunnels/t1/start", json={}).status_code == 204
    # Off by default lets the downloads already on it finish; "now" is for stopping the bleeding.
    assert client.post("/api/tunnels/t1/stop", json={}).status_code == 204
    assert client.post("/api/tunnels/t1/stop", json={"now": True}).status_code == 204
    assert client.delete("/api/tunnels/t1").status_code == 204

    assert ("start", "t1") in store.calls
    assert ("stop", True) in store.calls and ("stop", False) in store.calls
    assert ("remove", "t1") in store.calls


def test_a_tunnel_hosting_a_swap_answers_its_switch_and_its_removal_with_the_words(
    client: TestClient,
) -> None:
    """The switch and Remove are refused while the tunnel hosts a swap: a 409 with the sentence
    the screen shows, and the tunnel left as it was."""
    from sift.kernel.tunnels import END_THE_SWAP_FIRST, TunnelHosting

    class _Hosting(_Store):
        async def stop(self, tunnel_id: str, *, drain: bool = True) -> None:
            raise TunnelHosting(END_THE_SWAP_FIRST)

        async def remove(self, tunnel_id: str) -> None:
            raise TunnelHosting(END_THE_SWAP_FIRST)

    _admin(client)
    _store(client, _Hosting())
    for answer in (
        client.post("/api/tunnels/t1/stop", json={}),
        client.post("/api/tunnels/t1/stop", json={"now": True}),
        client.delete("/api/tunnels/t1"),
    ):
        assert answer.status_code == 409
        assert answer.json()["detail"] == "End the swap first."


def test_the_routes_read_back_with_every_site_that_can_be_given_one(client: TestClient) -> None:
    _admin(client)
    store = _store(client, _Store())
    store.route_rows = {"*": "t1", "reddit": "direct"}

    routes = client.get("/api/download-routes")

    assert routes.status_code == 200
    body = routes.json()
    assert body["default"] == "t1"
    assert body["sites"] == {"reddit": "direct"}
    # The choices come with the answer, so a screen never has to keep its own list of sites.
    assert any(site["key"] == "youtube" for site in body["available"])


def test_a_site_is_pointed_at_a_way_out_and_put_back(client: TestClient) -> None:
    _admin(client)
    store = _store(client, _Store())
    store.views = [_a_tunnel()]

    assert client.put("/api/download-routes/reddit", json={"route": "t1"}).status_code == 204
    assert client.delete("/api/download-routes/reddit").status_code == 204

    assert ("set_route", ("reddit", "t1")) in store.calls
    assert ("clear_route", "reddit") in store.calls


def test_a_route_is_refused_when_either_half_names_nothing(client: TestClient) -> None:
    """Both halves are checked. A scope naming no site stores a route nothing reads, and a route
    naming no tunnel refuses every download from that site with nothing on screen explaining it."""
    _admin(client)
    store = _store(client, _Store())
    store.views = [_a_tunnel()]

    assert client.put("/api/download-routes/not-a-site", json={"route": "t1"}).status_code == 404
    assert client.put("/api/download-routes/reddit", json={"route": "t9"}).status_code == 404
    assert store.calls == []


def test_the_default_cannot_be_cleared_because_everything_follows_it(client: TestClient) -> None:
    _admin(client)
    _store(client, _Store())
    assert client.delete("/api/download-routes/*").status_code == 400


def test_a_route_chosen_or_put_back_tells_every_admin_s_open_screens(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`Settings > Tunnels` and a download's choices draw where each Site's downloads go. A route
    chosen in one window is told to every other, or another would keep the old one until it was
    reloaded."""
    import importlib

    from sift.kernel.audience import EVERY_ADMIN
    from sift.kernel.changes import About

    download_router = importlib.import_module("sift.slices.download.router")
    told: list[tuple[object, object]] = []
    monkeypatch.setattr(
        download_router, "announce_now", lambda who, about: told.append((who, about))
    )
    _admin(client)
    store = _store(client, _Store())
    store.views = [_a_tunnel()]

    assert client.put("/api/download-routes/reddit", json={"route": "t1"}).status_code == 204
    assert told == [(EVERY_ADMIN, About.SETTINGS)]
    assert client.delete("/api/download-routes/reddit").status_code == 204
    assert told == [(EVERY_ADMIN, About.SETTINGS)] * 2
