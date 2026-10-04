# SPDX-License-Identifier: AGPL-3.0-or-later
"""The dependencies every other slice reads auth through: `current_viewer`, `require_admin`,
`master_key` and `require_vault_pin`, exercised through routes mounted on the real application so
the dependency resolution itself is tested.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

import pytest
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.testclient import TestClient

from sift.kernel.access import Viewer
from sift.kernel.config import get_settings
from sift.kernel.http import CSRF_HEADER_NAME
from sift.main import create_app
from sift.slices import auth
from sift.slices.auth import AuthService, Hasher
from sift.slices.auth.crypto import resolve_argon2_params
from sift.slices.auth.router import (
    current_viewer,
    master_key,
    optional_viewer,
    require_admin,
    require_vault_pin,
    session_key,
)

pytestmark = [pytest.mark.integration]

PASSWORD = "Corr3ct-Horse!staple9"
GUEST_PASSWORD = "An0ther-Secur3!keyword"

#: Routes mounted only in this file, after the app is built, so the authorization matrix never sees
#: them.
probes = APIRouter(prefix="/probe")


@probes.get("/optional")
async def _optional(viewer: Annotated[Viewer | None, Depends(optional_viewer)]) -> dict[str, str]:
    return {"who": "nobody" if viewer is None else viewer.role.value}


@probes.get("/admin")
async def _admin(viewer: Annotated[Viewer, Depends(require_admin)]) -> dict[str, str]:
    return {"who": viewer.id}


@probes.get("/key")
async def _key(key: Annotated[bytes | None, Depends(master_key)]) -> dict[str, bool]:
    return {"held": key is not None}


@probes.get("/session")
async def _session(handle: Annotated[str | None, Depends(session_key)]) -> dict[str, bool]:
    return {"named": handle is not None}


@probes.get("/vault-pin")
async def _vault_pin(
    request: Request, viewer: Annotated[Viewer, Depends(current_viewer)]
) -> dict[str, bool]:
    await require_vault_pin(request, viewer)
    return {"allowed": True}


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()
    app: FastAPI = create_app()
    # Ahead of everything, because the application ends with a catch-all that serves the browser
    # client: a route added on the end is a route nothing ever reaches.
    already = len(app.routes)
    app.include_router(probes)
    added = app.routes[already:]
    del app.routes[already:]
    app.routes[0:0] = added
    with TestClient(app) as c:
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


def _admin_session(client: TestClient) -> None:
    response = client.post("/api/auth/setup", json={"username": "kate", "password": PASSWORD})
    assert response.status_code == 201, response.text


def _guest_session(client: TestClient) -> None:
    """Make a guest as an admin, then become them."""
    client.post(
        "/api/auth/users",
        json={"username": "sam", "password": GUEST_PASSWORD},
        headers=_csrf(client),
    )
    client.cookies.clear()
    assert (
        client.post(
            "/api/auth/login", json={"username": "sam", "password": GUEST_PASSWORD}
        ).status_code
        == 200
    )


# --- who is asking, when nobody is ---------------------------------------------------------------


def test_the_optional_viewer_is_nobody_without_a_session(client: TestClient) -> None:
    """The optional viewer is None with no cookie, a dead session or a deleted user."""
    _admin_session(client)
    client.cookies.clear()

    assert client.get("/probe/optional").json() == {"who": "nobody"}


def test_the_optional_viewer_is_the_account_when_there_is_one(client: TestClient) -> None:
    _admin_session(client)

    assert client.get("/probe/optional").json() == {"who": "admin"}


# --- admin-only routes ---------------------------------------------------------------------------


def test_an_admin_passes_the_admin_gate(client: TestClient) -> None:
    _admin_session(client)

    assert client.get("/probe/admin").status_code == 200


def test_a_guest_is_refused_by_it(client: TestClient) -> None:
    """403 and not 404. The route is not being asked about a thing that might not exist: it is
    being asked to do something, and the answer is that this user may not."""
    _admin_session(client)
    _guest_session(client)

    assert client.get("/probe/admin").status_code == 403


def test_nobody_is_refused_by_it_as_unauthenticated(client: TestClient) -> None:
    _admin_session(client)
    client.cookies.clear()

    assert client.get("/probe/admin").status_code == 401


# --- the master key ------------------------------------------------------------------------------


def test_the_master_key_is_held_after_an_admin_signs_in(client: TestClient) -> None:
    """It is unwrapped by the password at login and lives in memory only. A job that needs a saved
    site login reaches it through this."""
    _admin_session(client)

    assert client.get("/probe/key").json() == {"held": True}


def test_there_is_no_master_key_for_a_guest(client: TestClient) -> None:
    """A guest holds no master key: the saved logins are an admin's."""
    _admin_session(client)
    _guest_session(client)

    assert client.get("/probe/key").json() == {"held": False}


def test_there_is_no_master_key_for_nobody(client: TestClient) -> None:
    """None means wait for a login, not fail. It is the same answer a cold start gives before the
    password has been entered again."""
    _admin_session(client)
    client.cookies.clear()

    assert client.get("/probe/key").json() == {"held": False}


# --- naming this session without handing the cookie around ---------------------------------------


def test_a_session_can_be_named_by_its_handle(client: TestClient) -> None:
    """The hashed token, never the token. What the vault feature passes around to open and close
    itself for one browser cannot be replayed as a cookie."""
    _admin_session(client)

    assert client.get("/probe/session").json() == {"named": True}


def test_there_is_no_handle_without_a_cookie(client: TestClient) -> None:
    _admin_session(client)
    client.cookies.clear()

    assert client.get("/probe/session").json() == {"named": False}


# --- no PIN, no vault ----------------------------------------------------------------------------


def test_concealing_anything_is_refused_before_a_pin_exists(client: TestClient) -> None:
    """Concealing anything is refused server-side before a PIN exists."""
    _admin_session(client)

    refused = client.get("/probe/vault-pin")

    assert refused.status_code == 409
    assert "PIN" in refused.json()["detail"]


def test_and_allowed_once_there_is_one(client: TestClient) -> None:
    _admin_session(client)
    client.put(
        "/api/auth/pin",
        json={"pin": "246810", "current_password": PASSWORD},
        headers=_csrf(client),
    )

    assert client.get("/probe/vault-pin").status_code == 200


def test_a_missing_session_says_so_in_a_header(client: TestClient) -> None:
    """A missing session is marked in a header, the one 401 meaning "sign in again"."""
    refused = client.get("/api/assets")

    assert refused.status_code == 401
    assert refused.headers.get("www-authenticate") == "Session"


def test_a_wrong_password_does_not_carry_it(client: TestClient) -> None:
    """A wrong password does not carry that header."""
    _admin_session(client)

    refused = client.post("/api/auth/login", json={"username": "kate", "password": "wrong-one-9!"})

    assert refused.status_code == 401
    assert refused.headers.get("www-authenticate") is None
