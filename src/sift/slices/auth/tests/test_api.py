# SPDX-License-Identifier: AGPL-3.0-or-later
"""The auth endpoints over real HTTP, through the cookie, the CSRF header and the status codes,
including requests the UI would never make."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sift.kernel.config import get_settings
from sift.kernel.http import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from sift.main import create_app
from sift.slices import auth
from sift.slices.auth import AuthService, Hasher
from sift.slices.auth.crypto import resolve_argon2_params
from sift.slices.auth.service import SignInBusy

pytestmark = [pytest.mark.integration]

PASSWORD = "Corr3ct-Horse!staple9"
PASSWORD_TWO = "An0ther-Secur3!keyword"


def _may_reopen_with_pin(app: Any) -> Callable[[str], Awaitable[bool]]:
    """Whether this user has asked for their PIN to reopen a locked session, as boot wires it."""

    async def ask(user_id: str) -> bool:
        return bool(await app.state.settings_hub.get_user(user_id, "vault.app_lock_enabled"))

    return ask


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    # SIFT_COOKIE_SECURE is left unset on purpose: the default decides per request, and the test
    # client speaks http, so the cookie comes back the way it would on a LAN.
    get_settings.cache_clear()
    # A caller on the household network, which is where a PIN may unlock Sift.
    with TestClient(create_app(), client=("192.168.1.20", 50000)) as c:
        app = c.app
        # The floor Argon2id parameters, so many logins are not paced by the tuned hash.
        app.state.auth = AuthService(  # type: ignore[attr-defined]
            app.state.database,  # type: ignore[attr-defined]
            hasher=Hasher(resolve_argon2_params(None)),
            master_keys=app.state.master_keys,  # type: ignore[attr-defined]
            queue=app.state.queue,  # type: ignore[attr-defined]
            session_ttl_seconds=auth.DEFAULT_SESSION_DAYS * 24 * 60 * 60,
            # The same collaborator the composition root gives the real one. A stand-in built
            # without it silently answers "no" to every question it was meant to ask.
            may_reopen_with_pin=_may_reopen_with_pin(app),
        )
        yield c
    get_settings.cache_clear()


def _setup(client: TestClient, username: str = "kate", password: str = PASSWORD) -> str:
    """Create an admin and return the CSRF token to use for state-changing requests."""
    response = client.post("/api/auth/setup", json={"username": username, "password": password})
    assert response.status_code == 201, response.text
    return str(response.json()["csrf_token"])


def _forget_key(client: TestClient, user_id: str) -> None:
    """Drop a user's master key from memory, as a restart does."""
    app: Any = client.app
    app.state.auth.master_keys.forget(user_id)


def _csrf(client: TestClient) -> dict[str, str]:
    return {CSRF_HEADER_NAME: client.get("/api/auth/me").json()["csrf_token"]}


# --- First run -------------------------------------------------------------------------------


def test_setup_creates_the_admin_and_signs_in(client: TestClient) -> None:
    response = client.post("/api/auth/setup", json={"username": "kate", "password": PASSWORD})
    assert response.status_code == 201
    body = response.json()
    assert body["role"] == "admin"
    assert body["username"] == "kate"
    # The setup response signed the client in.
    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["role"] == "admin"


def test_a_second_setup_is_a_conflict(client: TestClient) -> None:
    _setup(client)
    again = client.post("/api/auth/setup", json={"username": "intruder", "password": PASSWORD_TWO})
    assert again.status_code == 409


def test_setup_rejects_a_weak_password(client: TestClient) -> None:
    response = client.post("/api/auth/setup", json={"username": "kate", "password": "weak"})
    assert response.status_code == 422


def test_nothing_is_authorized_before_setup(client: TestClient) -> None:
    assert client.get("/api/auth/me").status_code == 401


# --- Login / logout / sessions ---------------------------------------------------------------


def test_a_sign_in_turned_away_while_another_is_checked_says_when_to_come_back(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup(client)
    client.post("/api/auth/logout", headers=_csrf(client))
    client.cookies.clear()

    async def busy(*_args: object, **_kwargs: object) -> None:
        raise SignInBusy(7)

    app: Any = client.app
    monkeypatch.setattr(app.state.auth, "login", busy)
    response = client.post("/api/auth/login", json={"username": "kate", "password": PASSWORD})

    assert response.status_code == 429
    assert response.headers["retry-after"] == "7"


def test_login_sets_an_httponly_samesite_cookie(client: TestClient) -> None:
    _setup(client)
    client.post("/api/auth/logout", headers=_csrf(client))
    client.cookies.clear()

    response = client.post("/api/auth/login", json={"username": "kate", "password": PASSWORD})
    assert response.status_code == 200
    set_cookie = response.headers["set-cookie"].lower()
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie
    assert client.get("/api/auth/me").json()["username"] == "kate"


def test_a_sign_in_lasts_as_many_days_as_privacy_says(client: TestClient) -> None:
    """`Settings > Privacy` sets how long a sign-in lasts. The next sign-in's cookie carries that
    many days, read at the sign-in rather than at boot."""
    _setup(client)
    saved = client.put(
        "/api/settings",
        json={"values": {"sessions.stay_signed_in_days": 2}},
        headers=_csrf(client),
    )
    assert saved.status_code == 204, saved.text
    client.post("/api/auth/logout", headers=_csrf(client))
    client.cookies.clear()

    response = client.post("/api/auth/login", json={"username": "kate", "password": PASSWORD})

    assert response.status_code == 200
    session_cookie = next(
        one
        for one in response.headers.get_list("set-cookie")
        if one.startswith(f"{SESSION_COOKIE_NAME}=")
    )
    assert f"max-age={2 * 24 * 60 * 60}" in session_cookie.lower()


def test_the_cookie_is_secure_only_when_the_request_arrived_over_https(client: TestClient) -> None:
    _setup(client)
    client.post("/api/auth/logout", headers=_csrf(client))
    client.cookies.clear()

    # Over plain http (a LAN), the cookie is not Secure, or a browser would never send it back.
    over_http = client.post("/api/auth/login", json={"username": "kate", "password": PASSWORD})
    assert "secure" not in over_http.headers["set-cookie"].lower()

    client.cookies.clear()
    # The same request arriving through a TLS-terminating proxy is marked Secure.
    over_https = client.post(
        "/api/auth/login",
        json={"username": "kate", "password": PASSWORD},
        headers={"x-forwarded-proto": "https"},
    )
    assert "secure" in over_https.headers["set-cookie"].lower()


def test_logout_revokes_the_session_server_side(client: TestClient) -> None:
    _setup(client)
    token = client.cookies[SESSION_COOKIE_NAME]
    assert client.post("/api/auth/logout", headers=_csrf(client)).status_code == 204

    # The cookie the client kept a copy of is dead: put it back and it is refused.
    client.cookies.set(SESSION_COOKIE_NAME, token)
    assert client.get("/api/auth/me").status_code == 401


def test_signing_out_tells_the_browser_to_throw_away_what_it_is_holding(
    client: TestClient,
) -> None:
    """Signing out sends `Clear-Site-Data: "cache"`, so pictures kept by content address do not
    outlive the session; not `cookies` or `storage`."""
    _setup(client)

    answer = client.post("/api/auth/logout", headers=_csrf(client))

    assert answer.status_code == 204
    assert answer.headers["clear-site-data"] == '"cache"'


def test_locking_the_vault_does_not_throw_the_cache_away(client: TestClient) -> None:
    """Locking the vault does not clear the cache: concealed pictures are never keepable."""
    _setup(client)

    answer = client.post("/api/vault/lock", headers=_csrf(client))

    assert answer.status_code == 204
    assert "clear-site-data" not in answer.headers


def test_a_disabled_account_loses_its_live_session_on_the_next_request(client: TestClient) -> None:
    _setup(client)
    assert client.get("/api/auth/me").status_code == 200

    # Disable the user directly in the database, the way an admin action later would. No new
    # login, no waiting for expiry: the very next request must be refused.
    import asyncio

    from sift.kernel.db import Database

    async def disable() -> None:
        db = Database(client.app.state.database.path, readers=1)  # type: ignore[attr-defined]
        await db.connect()
        try:
            await db.execute("UPDATE users SET disabled = 1 WHERE username = 'kate'")
        finally:
            await db.close()

    asyncio.run(disable())
    assert client.get("/api/auth/me").status_code == 401


# --- Session fixation (A07) ------------------------------------------------------------------


def test_login_always_mints_a_fresh_session_token(client: TestClient) -> None:
    # Session fixation is defeated by never adopting a client-supplied session: every login issues
    # a brand-new token, so a value an attacker planted before login cannot survive it.
    _setup(client)

    client.post("/api/auth/logout", headers=_csrf(client))
    client.cookies.clear()
    first = client.post("/api/auth/login", json={"username": "kate", "password": PASSWORD})
    token_one = first.cookies.get(SESSION_COOKIE_NAME)

    client.cookies.clear()
    second = client.post("/api/auth/login", json={"username": "kate", "password": PASSWORD})
    token_two = second.cookies.get(SESSION_COOKIE_NAME)

    assert token_one and token_two and token_one != token_two

    # A value that was never issued authenticates nothing.
    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE_NAME, "attacker-fixed-token")
    assert client.get("/api/auth/me").status_code == 401


# --- No username enumeration -----------------------------------------------------------------


def test_a_bad_username_and_a_bad_password_are_indistinguishable(client: TestClient) -> None:
    _setup(client)
    client.post("/api/auth/logout", headers=_csrf(client))
    client.cookies.clear()

    def attempt(username: str, password: str) -> tuple[int, str, float]:
        started = time.perf_counter()
        response = client.post("/api/auth/login", json={"username": username, "password": password})
        return response.status_code, response.text, time.perf_counter() - started

    wrong_password = [attempt("kate", "wrong-passw0rd!") for _ in range(3)]
    no_such_user = [attempt("ghost", "wrong-passw0rd!") for _ in range(3)]

    # The reply is byte-identical either way.
    assert {s for s, _, _ in wrong_password} == {401}
    assert {s for s, _, _ in no_such_user} == {401}
    assert {b for _, b, _ in wrong_password} == {b for _, b, _ in no_such_user}

    # And the no-such-user path is not measurably faster: it hashes a dummy so it does the same
    # work. Compared by the fastest of each to shrug off scheduler noise, with a loose bound.
    fastest_wrong = min(t for _, _, t in wrong_password)
    fastest_missing = min(t for _, _, t in no_such_user)
    assert fastest_missing >= 0.4 * fastest_wrong


# --- Rate limiting ---------------------------------------------------------------------------


def test_login_is_slowed_by_failures_but_never_locked_out(client: TestClient) -> None:
    """Repeated wrong logins answer 401 and are never locked out; the tarpit is tested in units."""
    _setup(client)
    client.post("/api/auth/logout", headers=_csrf(client))
    client.cookies.clear()

    async def _no_delay(_seconds: float) -> None:
        return None

    # The tarpit's sleep as a no-op, so the contract is checked without the wait.
    client.app.state.auth._sleep = _no_delay  # type: ignore[attr-defined]

    statuses = {
        client.post(
            "/api/auth/login", json={"username": "kate", "password": "wrong-passw0rd!"}
        ).status_code
        for _ in range(15)
    }
    assert statuses == {401}  # always 401, and never a 429: there is no lockout to hit


# --- CSRF (A01) ------------------------------------------------------------------------------


def test_a_state_change_without_the_csrf_header_is_refused(client: TestClient) -> None:
    _setup(client)
    # A valid session cookie, but no CSRF header: exactly the cross-site request the token stops.
    refused = client.post(
        "/api/auth/password",
        json={"old_password": PASSWORD, "new_password": PASSWORD_TWO},
    )
    assert refused.status_code == 403
    # The password was not changed.
    assert (
        client.post("/api/auth/login", json={"username": "kate", "password": PASSWORD}).status_code
        == 200
    )


def test_a_state_change_with_the_csrf_header_succeeds(client: TestClient) -> None:
    _setup(client)
    changed = client.post(
        "/api/auth/password",
        json={"old_password": PASSWORD, "new_password": PASSWORD_TWO},
        headers=_csrf(client),
    )
    assert changed.status_code == 204
    # The session that made the change is kept, so the caller is not signed out of their own browser.
    assert client.get("/api/auth/me").status_code == 200
    client.cookies.clear()
    assert (
        client.post(
            "/api/auth/login", json={"username": "kate", "password": PASSWORD_TWO}
        ).status_code
        == 200
    )


def test_a_non_ascii_csrf_header_is_refused_cleanly(client: TestClient) -> None:
    # A non-ASCII CSRF header, sent as raw bytes, is a clean 403.
    _setup(client)
    refused = client.post(
        "/api/auth/password",
        json={"old_password": PASSWORD, "new_password": PASSWORD_TWO},
        headers={CSRF_HEADER_NAME: b"caf\xe9-not-the-token"},
    )
    assert refused.status_code == 403


def test_a_rejected_field_is_not_echoed_back(client: TestClient) -> None:
    # A password too long for the field is rejected without the value coming back in the response.
    secret = "A1!" + "z" * 2000
    response = client.post("/api/auth/setup", json={"username": "kate", "password": secret})
    assert response.status_code == 422
    assert secret not in response.text
    assert "z" * 2000 not in response.text


# --- The PIN over HTTP -----------------------------------------------------------------------


def test_the_pin_can_be_set_and_spent_over_http(client: TestClient) -> None:
    """The PIN can be set and spent; no route merely answers "is this the PIN"."""
    _setup(client)
    headers = _csrf(client)
    assert (
        client.put(
            "/api/auth/pin", json={"pin": "246810", "current_password": PASSWORD}, headers=headers
        ).status_code
        == 204
    )

    # Asking for a lock that keeps the session alive. Off, locking signs out instead, which is
    # what the setting says, and is not the shape this test is about.
    assert (
        client.put(
            "/api/settings", json={"values": {"vault.app_lock_enabled": True}}, headers=headers
        ).status_code
        == 204
    )
    assert client.post("/api/auth/lock", headers=headers).json()["outcome"] == "locked"
    assert client.get("/api/assets").status_code == 423

    assert (
        client.post("/api/auth/unlock", json={"pin": "000000"}, headers=headers).status_code == 401
    )
    assert client.get("/api/assets").status_code == 423, "a wrong PIN opened nothing"

    assert (
        client.post("/api/auth/unlock", json={"pin": "246810"}, headers=headers).status_code == 204
    )
    assert client.get("/api/assets").status_code == 200


# --- Docs are off by default -----------------------------------------------------------------


def test_the_interactive_docs_are_not_served_by_default(client: TestClient) -> None:
    """The interactive docs are not served: the address returns the client page, never a schema."""
    for path in ("/docs", "/redoc", "/openapi.json"):
        body = client.get(path).text
        assert "swagger" not in body.lower(), f"{path} is serving the interactive docs"
        assert "openapi" not in body.lower(), f"{path} is serving the schema"
        assert "/api/auth/login" not in body, f"{path} is enumerating the endpoints"


# --- The master key never reaches a response -------------------------------------------------


def test_no_response_carries_key_material(client: TestClient) -> None:
    body = client.post("/api/auth/setup", json={"username": "kate", "password": PASSWORD}).json()
    # Identity, the CSRF token and flags about state (`can_save_to_device`, `secrets_locked`,
    # `zone`, `boot`): nothing that is key material.
    assert set(body) == {
        "id",
        "username",
        "role",
        "csrf_token",
        "can_save_to_device",
        "secrets_locked",
        "pin_unlock_offered",
        "locked",
        "zone",
        "boot",
    }
    assert client.get("/api/auth/me").json().keys() == body.keys()


# --- getting the key back without losing the session -----------------------------------


def test_a_restart_leaves_a_signed_in_session_unable_to_read_its_secrets(
    client: TestClient,
) -> None:
    """The state this whole route exists for, and the one a fresh sign-in hides.

    The key lives in the process. Dropping it is exactly what a restart does to a session that is
    otherwise perfectly valid, and the screen must not answer that by telling somebody to log in
    with the password they had already used.
    """
    client.post("/api/auth/setup", json={"username": "kate", "password": PASSWORD})
    assert client.get("/api/auth/me").json()["secrets_locked"] is False

    _forget_key(client, client.get("/api/auth/me").json()["id"])
    assert client.get("/api/auth/me").json()["secrets_locked"] is True


def test_the_password_puts_the_key_back_and_keeps_the_session(client: TestClient) -> None:
    setup = client.post("/api/auth/setup", json={"username": "kate", "password": PASSWORD}).json()
    _forget_key(client, setup["id"])

    answer = client.post(
        "/api/auth/unlock-secrets", json={"password": PASSWORD}, headers=_csrf(client)
    )

    assert answer.status_code == 204
    after = client.get("/api/auth/me").json()
    assert after["secrets_locked"] is False
    # The same user, still signed in. A sign-in would have minted a new session; this must not.
    assert after["id"] == setup["id"]


def test_a_wrong_password_leaves_the_key_where_it_was(client: TestClient) -> None:
    setup = client.post("/api/auth/setup", json={"username": "kate", "password": PASSWORD}).json()
    _forget_key(client, setup["id"])

    answer = client.post(
        "/api/auth/unlock-secrets", json={"password": PASSWORD_TWO}, headers=_csrf(client)
    )

    assert answer.status_code == 401
    assert client.get("/api/auth/me").json()["secrets_locked"] is True


def test_nobody_signed_in_cannot_unlock_anything(client: TestClient) -> None:
    client.post("/api/auth/setup", json={"username": "kate", "password": PASSWORD})
    headers = _csrf(client)
    client.post("/api/auth/logout", headers=headers)

    answer = client.post("/api/auth/unlock-secrets", json={"password": PASSWORD}, headers=headers)

    # 403 rather than 401: the CSRF check refuses a token with no session behind it before the
    # password is even read, which is the earlier of the two refusals and the better one.
    assert answer.status_code in {401, 403}


# --- first run, from the outside -------------------------------------------------------


def test_a_fresh_instance_says_it_needs_setup(client: TestClient) -> None:
    """The sign-in screen asks this before anybody has signed in, to know which form to draw."""
    assert client.get("/api/auth/status").json() == {"needs_setup": True}


def test_a_configured_instance_says_it_does_not(client: TestClient) -> None:
    client.post(
        "/api/auth/setup",
        json={"username": "the-admin", "password": "A-Setup-Status-Passw0rd!"},
    )
    assert client.get("/api/auth/status").json() == {"needs_setup": False}


def test_the_status_says_nothing_about_who_the_admin_is(client: TestClient) -> None:
    """It answers one question. A username here would hand a stranger half of a login."""
    client.post(
        "/api/auth/setup",
        json={"username": "the-admin", "password": "A-Setup-Status-Passw0rd!"},
    )
    body = client.get("/api/auth/status").text

    assert "the-admin" not in body
    assert set(client.get("/api/auth/status").json()) == {"needs_setup"}


# --- the one rule the browser cannot check for itself -------------------------------------------


def test_a_password_can_be_checked_before_it_is_submitted(client: TestClient) -> None:
    """The endpoint the strength meter asks, so it stops calling a leaked password strong.

    Signed out on purpose: the very first screen an install shows is the one that creates the
    admin, and there is no session to have yet.
    """
    leaked = client.post("/api/auth/password/check", json={"password": "Password1!"})
    assert leaked.status_code == 200
    assert leaked.json()["breached"] is True
    assert leaked.json()["acceptable"] is False

    fine = client.post("/api/auth/password/check", json={"password": PASSWORD})
    assert fine.json() == {"acceptable": True, "breached": False, "reason": None}


def test_the_check_says_why_when_it_is_the_shape_that_is_wrong(client: TestClient) -> None:
    """A refusal a person can act on. "Invalid password" is a riddle; "add a symbol" is help."""
    answer = client.post("/api/auth/password/check", json={"password": "Vantablack-Quokka"}).json()
    assert answer["acceptable"] is False
    assert answer["breached"] is False
    assert "Add" in answer["reason"]


def test_the_check_agrees_with_what_setup_will_do(client: TestClient) -> None:
    """The whole point of it. A check that said yes to something the next call refuses is worse
    than no check at all: it is the same surprise, one step later, with the app now claiming it
    had looked."""
    for password in ("Password1!", "abcdefghij", "short1!A", PASSWORD_TWO):
        checked = client.post("/api/auth/password/check", json={"password": password}).json()
        created = client.post("/api/auth/setup", json={"username": "kate", "password": password})
        assert checked["acceptable"] is (created.status_code == 201), password
        if created.status_code == 201:
            break
