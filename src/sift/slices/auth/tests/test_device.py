# SPDX-License-Identifier: AGPL-3.0-or-later
"""The device a browser signs in from: a cookie of its own, kept on the session and on every act.

Over real HTTP, because what is under test is the boundary: what the browser is handed, what it
sends back, and what the server writes from it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sift.kernel.client import CLIENT_HEADER, DEVICE_COOKIE_NAME, device_from
from sift.kernel.config import get_settings
from sift.kernel.db import Database
from sift.kernel.http import CSRF_HEADER_NAME
from sift.main import create_app
from sift.slices import auth
from sift.slices.auth import AuthService, Hasher
from sift.slices.auth import schema as auth_schema
from sift.slices.auth.crypto import resolve_argon2_params

pytestmark = [pytest.mark.integration]

PASSWORD = "Corr3ct-Horse!staple9"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        app = c.app
        # The floor hasher, so signing in is not paced by the machine's tuned one.
        app.state.auth = AuthService(  # type: ignore[attr-defined]
            app.state.database,  # type: ignore[attr-defined]
            hasher=Hasher(resolve_argon2_params(None)),
            master_keys=app.state.master_keys,  # type: ignore[attr-defined]
            queue=app.state.queue,  # type: ignore[attr-defined]
            session_ttl_seconds=auth.DEFAULT_SESSION_DAYS * 24 * 60 * 60,
        )
        yield c
    get_settings.cache_clear()


def _read(client: TestClient, sql: str) -> list[dict[str, Any]]:
    """Read on a connection of the test's own: the app's handle lives on the client's loop."""
    path = client.app.state.database.path  # type: ignore[attr-defined]

    async def run() -> list[dict[str, Any]]:
        database = Database(path, readers=1)
        await database.connect()
        try:
            return [dict(row) for row in await database.fetch_all(sql)]
        finally:
            await database.close()

    return asyncio.run(run())


def _setup(client: TestClient) -> None:
    response = client.post(
        "/api/auth/setup",
        json={"username": "wren", "password": PASSWORD},
        headers={CLIENT_HEADER: "app"},
    )
    assert response.status_code == 201, response.text
    client.headers[CSRF_HEADER_NAME] = str(response.json()["csrf_token"])


def test_signing_in_gives_the_browser_a_device_and_the_session_keeps_it(
    client: TestClient,
) -> None:
    _setup(client)
    device = client.cookies.get(DEVICE_COOKIE_NAME)
    assert device is not None and device_from(device) == device
    (session,) = _read(client, "SELECT device_id, client_kind FROM sessions")
    assert session == {"device_id": device, "client_kind": "app"}

    # Signing in again on the same browser is the same device.
    again = client.post(
        "/api/auth/login",
        json={"username": "wren", "password": PASSWORD},
        headers={CLIENT_HEADER: "phone"},
    )
    assert again.status_code == 200, again.text
    assert client.cookies.get(DEVICE_COOKIE_NAME) == device
    rows = _read(client, "SELECT device_id, client_kind FROM sessions ORDER BY created_at, id")
    assert {row["device_id"] for row in rows} == {device}
    assert {row["client_kind"] for row in rows} == {"app", "phone"}


def test_a_browser_signed_in_before_devices_were_kept_is_given_one_when_it_asks_who_it_is(
    client: TestClient,
) -> None:
    _setup(client)
    client.cookies.delete(DEVICE_COOKIE_NAME)
    answered = client.get("/api/auth/me", headers={CLIENT_HEADER: "tablet"})
    assert answered.status_code == 200
    device = answered.cookies.get(DEVICE_COOKIE_NAME)
    assert device is not None and device_from(device) == device
    (session,) = _read(client, "SELECT device_id, client_kind FROM sessions")
    assert session == {"device_id": device, "client_kind": "tablet"}
    # And asked again with one, it is left as it is.
    assert DEVICE_COOKIE_NAME not in client.get("/api/auth/me").cookies


def test_an_act_taken_from_a_window_carries_its_kind_and_device(client: TestClient) -> None:
    _setup(client)
    device = client.cookies.get(DEVICE_COOKIE_NAME)
    made = client.post(
        "/api/auth/users",
        json={"username": "fennick", "password": PASSWORD},
        headers={CLIENT_HEADER: "computer"},
    )
    assert made.status_code == 201, made.text
    (event,) = _read(
        client,
        "SELECT client_kind, device_id FROM workbench_decisions"
        " WHERE verb = 'added' AND actor_kind = 'user'",
    )
    assert event == {"client_kind": "computer", "device_id": device}


async def test_the_sessions_step_adds_the_device_once(temp_db: Database) -> None:
    async with temp_db.write() as connection:
        await connection.execute(
            "CREATE TABLE sessions (id TEXT PRIMARY KEY, user_id TEXT, token_hash TEXT,"
            " created_at INTEGER, last_seen_at INTEGER, expires_at INTEGER)"
        )
        await auth_schema.initialize_sessions(connection, 2)
        await auth_schema.initialize_sessions(connection, 2)
    columns = {
        str(row["name"])
        for row in await temp_db.fetch_all("SELECT name FROM pragma_table_info('sessions')")
    }
    assert {"device_id", "client_kind"} <= columns
