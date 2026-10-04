# SPDX-License-Identifier: AGPL-3.0-or-later
"""What each route says when it refuses, so a person can act on it; and the two refusals that
resist carrying on: a damaged key and the PIN lockout."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sift.kernel.config import get_settings
from sift.kernel.db import Database
from sift.kernel.http import CSRF_HEADER_NAME
from sift.main import create_app
from sift.slices import auth
from sift.slices.auth import AuthService, Hasher
from sift.slices.auth.crypto import resolve_argon2_params

pytestmark = [pytest.mark.integration]

PASSWORD = "Corr3ct-Horse!staple9"
PASSWORD_TWO = "An0ther-Secur3!keyword"
PIN = "246810"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        c.app.state.auth = AuthService(  # type: ignore[attr-defined]
            c.app.state.database,  # type: ignore[attr-defined]
            hasher=Hasher(resolve_argon2_params(None)),
            master_keys=c.app.state.master_keys,  # type: ignore[attr-defined]
            queue=c.app.state.queue,  # type: ignore[attr-defined]
            session_ttl_seconds=auth.DEFAULT_SESSION_DAYS * 24 * 60 * 60,
        )
        yield c
    get_settings.cache_clear()


def _csrf(client: TestClient) -> dict[str, str]:
    return {CSRF_HEADER_NAME: client.get("/api/auth/me").json()["csrf_token"]}


def _setup(client: TestClient) -> str:
    response = client.post("/api/auth/setup", json={"username": "kate", "password": PASSWORD})
    assert response.status_code == 201, response.text
    return str(client.get("/api/auth/me").json()["id"])


def _damage_the_key(client: TestClient, user_id: str) -> None:
    """Overwrite the wrapped master key with bytes that cannot be unwrapped, on its own loop."""
    path = client.app.state.database.path  # type: ignore[attr-defined]

    async def run() -> None:
        database = Database(path, readers=1)
        await database.connect()
        try:
            await database.execute(
                "UPDATE users SET mk_wrapped = ? WHERE id = ?", (b"not-a-wrapped-key", user_id)
            )
        finally:
            await database.close()

    asyncio.run(run())


def _sign_in_again(client: TestClient, password: str) -> int:
    client.cookies.clear()
    return int(
        client.post("/api/auth/login", json={"username": "kate", "password": password}).status_code
    )


# --- the password change -------------------------------------------------------------------------


def test_a_password_change_says_which_half_was_wrong(client: TestClient) -> None:
    """Two refusals, two places on the form, and they are not interchangeable."""
    _setup(client)

    wrong_old = client.post(
        "/api/auth/password",
        json={"old_password": "not-the-password-9", "new_password": PASSWORD_TWO},
        headers=_csrf(client),
    )
    weak_new = client.post(
        "/api/auth/password",
        json={"old_password": PASSWORD, "new_password": "weak"},
        headers=_csrf(client),
    )

    assert wrong_old.status_code == 401
    assert weak_new.status_code == 422


def test_a_password_change_over_a_damaged_key_fails_rather_than_half_working(
    client: TestClient,
) -> None:
    """A password change over a damaged key writes nothing, rather than minting a fresh key."""
    user_id = _setup(client)
    _damage_the_key(client, user_id)

    answer = client.post(
        "/api/auth/password",
        json={"old_password": PASSWORD, "new_password": PASSWORD_TWO},
        headers=_csrf(client),
    )

    assert answer.status_code == 500
    # The old password still signs in, because the refusal wrote nothing.
    assert _sign_in_again(client, PASSWORD) == 200


# --- the PIN --------------------------------------------------------------------------------------


def test_setting_a_pin_says_which_half_was_wrong(client: TestClient) -> None:
    """The current password is asked for so an unattended unlocked screen cannot be used to plant a
    PIN. Getting that wrong is a different problem from typing a PIN that is not six digits, and
    they land in different places on the form."""
    _setup(client)

    wrong_password = client.put(
        "/api/auth/pin",
        json={"pin": PIN, "current_password": "not-the-password-9"},
        headers=_csrf(client),
    )
    bad_pin = client.put(
        "/api/auth/pin",
        json={"pin": "12", "current_password": PASSWORD},
        headers=_csrf(client),
    )

    assert wrong_password.status_code == 401
    assert bad_pin.status_code == 422


def test_guessing_at_the_pin_locks_out_rather_than_slowing_down(client: TestClient) -> None:
    """Guessing at the PIN locks out rather than slowing down: a PIN fits inside one delay, and the
    locked-out user is still signed in."""
    _setup(client)
    client.put(
        "/api/auth/pin", json={"pin": PIN, "current_password": PASSWORD}, headers=_csrf(client)
    )
    headers = _csrf(client)

    statuses = [
        client.post("/api/vault/unlock", json={"pin": "000000"}, headers=headers).status_code
        for _ in range(12)
    ]

    assert 401 in statuses, "the first wrong guesses are simply wrong"
    assert 429 in statuses, "a run of them locks out"
    # The correct PIN is refused too while the lockout stands, which is what makes it a lockout
    # rather than a slow-down.
    assert client.post("/api/vault/unlock", json={"pin": PIN}, headers=headers).status_code == 429
