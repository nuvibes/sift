# SPDX-License-Identifier: AGPL-3.0-or-later
"""Locking Sift itself, and the rules that make the PIN safe to reopen it with.

The session row carries the lock, so a locked session is refused whatever holds its credential;
the authorization gate asserts the refusal on every route. Here is what opens the lock, what
destroys it, and what it deliberately does not do.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sift.kernel.config import get_settings
from sift.kernel.db import Database
from sift.kernel.http import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from sift.main import create_app
from sift.slices import auth
from sift.slices.auth import AuthService, Hasher
from sift.slices.auth.crypto import resolve_argon2_params
from sift.slices.auth.service import LockOutcome
from sift.slices.auth.tuning import MAX_UNLOCK_FAILURES
from sift.testing.auth import TEST_PIN, establish_session, give_pin
from sift.testing.library import hide_for, seed_asset, seed_root, share_folder

pytestmark = [pytest.mark.integration]

A_ROOT = "01HX0000000000000000000201"
A_FOLDER = "01HX0000000000000000000202"
AN_ASSET = "01HX0000000000000000000203"

PASSWORD = "Corr3ct-Horse!staple9"

#: A direct caller on the household network, and one that arrived from outside it.
HOME_ADDRESS = "192.168.1.20"
OUTSIDE_ADDRESS = "203.0.113.7"


def _may_reopen_with_pin(c: TestClient):  # type: ignore[no-untyped-def]
    """Whether this user has asked for their PIN to reopen a locked session, as boot wires it."""

    async def ask(user_id: str) -> bool:
        hub = c.app.state.settings_hub  # type: ignore[attr-defined]
        return bool(await hub.get_user(user_id, "vault.app_lock_enabled"))

    return ask


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()
    # A caller on the household network, which is where a PIN may unlock Sift.
    with TestClient(create_app(), client=(HOME_ADDRESS, 50000)) as c:
        c.app.state.auth = AuthService(  # type: ignore[attr-defined]
            c.app.state.database,  # type: ignore[attr-defined]
            hasher=Hasher(resolve_argon2_params(None)),
            master_keys=c.app.state.master_keys,  # type: ignore[attr-defined]
            queue=c.app.state.queue,  # type: ignore[attr-defined]
            session_ttl_seconds=auth.DEFAULT_SESSION_DAYS * 24 * 60 * 60,
            # The collaborator the composition root gives the real service.
            may_reopen_with_pin=_may_reopen_with_pin(c),
        )
        db = db_path(c)
        seed_root(db, A_ROOT, folder_id=A_FOLDER, path=tmp_path / "library")
        seed_asset(
            db,
            AN_ASSET,
            root_id=A_ROOT,
            folder_id=A_FOLDER,
            root_path=tmp_path / "library",
            cache_dir=tmp_path / "cache",
        )
        yield c
    get_settings.cache_clear()


def db_path(client: TestClient) -> Path:
    return Path(client.app.state.database.path)  # type: ignore[attr-defined]


def sign_in(client: TestClient, role: str = "admin", *, who: str = "one") -> str:
    """Become somebody with a PIN. Returns their user id."""
    user_id, token, csrf = establish_session(
        db_path(client), role=role, username=f"lock-{role}-{who}", password=PASSWORD
    )
    client.cookies.set(SESSION_COOKIE_NAME, token)
    client.headers[CSRF_HEADER_NAME] = csrf
    give_pin(db_path(client), user_id)
    share_folder(db_path(client), A_FOLDER, user_id, grant_id=f"01HX{user_id[4:]}")
    return user_id


def allow_pin_unlock(client: TestClient) -> None:
    """Turn on "let the PIN unlock Sift itself"; with it off, the password reopens a lock."""
    response = client.put("/api/settings", json={"values": {"vault.app_lock_enabled": True}})
    assert response.status_code == 204, response.text


def lock_now(client: TestClient):  # type: ignore[no-untyped-def]
    """Lock this session, having first asked for the shape of lock that keeps it alive."""
    allow_pin_unlock(client)
    return client.post("/api/auth/lock")


def lock_the_row_only(client: TestClient) -> None:
    """Mark the session locked in the database only, bypassing the route that also shuts Hidden,
    so the independent rule below is reached."""

    async def run() -> None:
        database = Database(db_path(client), readers=1)
        await database.connect()
        try:
            await database.execute("UPDATE sessions SET locked_at = 1")
        finally:
            await database.close()

    asyncio.run(run())


def expire_every_session(client: TestClient) -> None:
    """Push every session's expiry into the past, as a month would."""

    async def run() -> None:
        database = Database(db_path(client), readers=1)
        await database.connect()
        try:
            await database.execute("UPDATE sessions SET expires_at = 1")
        finally:
            await database.close()

    asyncio.run(run())


# --- what the lock is ----------------------------------------------------------------------------


def test_locking_shuts_the_session_and_the_pin_opens_it(client: TestClient) -> None:
    """Locking shuts the session and the PIN opens it."""
    sign_in(client)
    assert client.get("/api/assets").status_code == 200

    assert lock_now(client).json()["outcome"] == "locked"
    assert client.get("/api/assets").status_code == 423

    assert client.post("/api/auth/unlock", json={"pin": TEST_PIN}).status_code == 204
    assert client.get("/api/assets").status_code == 200


def test_a_guest_can_lock_and_unlock_their_own_session(client: TestClient) -> None:
    """A guest can lock and unlock their own session."""
    sign_in(client, "guest", who="visitor")

    assert lock_now(client).json()["outcome"] == "locked"
    assert client.get("/api/assets").status_code == 423
    assert client.post("/api/auth/unlock", json={"pin": TEST_PIN}).status_code == 204
    assert client.get("/api/assets").status_code == 200


def test_unlocking_sift_leaves_hidden_shut(client: TestClient) -> None:
    """An unlock of Sift leaves Hidden shut: the two locks share a PIN but open one at a time."""
    user_id = sign_in(client)
    hide_for(db_path(client), "asset", AN_ASSET, user_id)

    assert client.post("/api/vault/unlock", json={"pin": TEST_PIN}).status_code == 200
    assert client.get("/api/vault").json()["unlocked"] is True
    assert AN_ASSET in [item["id"] for item in client.get("/api/assets").json()["items"]]

    lock_now(client)
    assert client.post("/api/auth/unlock", json={"pin": TEST_PIN}).status_code == 204

    assert client.get("/api/vault").json()["unlocked"] is False
    assert AN_ASSET not in [item["id"] for item in client.get("/api/assets").json()["items"]]


def test_a_locked_session_hides_hidden_things_even_before_it_is_unlocked(
    client: TestClient,
) -> None:
    """Hidden is shut the moment the lock lands, as `me`, which answers while locked, shows."""
    user_id = sign_in(client)
    hide_for(db_path(client), "asset", AN_ASSET, user_id)
    client.post("/api/vault/unlock", json={"pin": TEST_PIN})

    lock_now(client)

    assert client.get("/api/auth/me").status_code == 200
    assert client.get("/api/vault").status_code == 423


def test_a_locked_session_shows_nothing_hidden_even_if_it_was_open(client: TestClient) -> None:
    """A session marked locked by any means is built with Hidden shut, through the unlock too."""
    user_id = sign_in(client)
    hide_for(db_path(client), "asset", AN_ASSET, user_id)
    assert client.post("/api/vault/unlock", json={"pin": TEST_PIN}).status_code == 200
    assert AN_ASSET in [item["id"] for item in client.get("/api/assets").json()["items"]]

    # The PIN is offered only when asked for.
    allow_pin_unlock(client)
    lock_the_row_only(client)
    assert client.get("/api/assets").status_code == 423

    assert client.post("/api/auth/unlock", json={"pin": TEST_PIN}).status_code == 204
    assert client.get("/api/vault").json()["unlocked"] is False
    assert AN_ASSET not in [item["id"] for item in client.get("/api/assets").json()["items"]]


# --- what destroys it ----------------------------------------------------------------------------


def test_a_run_of_wrong_pins_destroys_the_session(client: TestClient) -> None:
    """A run of wrong PINs destroys the session at exactly the threshold, not one early or late."""
    sign_in(client)
    lock_now(client)

    for _ in range(MAX_UNLOCK_FAILURES - 1):
        assert client.post("/api/auth/unlock", json={"pin": "000000"}).status_code == 401
    # Locked, not gone.
    assert client.get("/api/auth/me").status_code == 200

    assert client.post("/api/auth/unlock", json={"pin": "000000"}).status_code == 401
    assert client.get("/api/auth/me").status_code == 401


def test_the_destroyed_session_cannot_then_be_unlocked_by_the_right_pin(
    client: TestClient,
) -> None:
    """The destroyed session cannot be unlocked by the right PIN; the password signs in again."""
    sign_in(client)
    lock_now(client)
    for _ in range(MAX_UNLOCK_FAILURES):
        client.post("/api/auth/unlock", json={"pin": "000000"})

    assert client.post("/api/auth/unlock", json={"pin": TEST_PIN}).status_code == 401
    assert client.get("/api/auth/me").status_code == 401

    # The password still works: the user was not destroyed.
    signed_in_again = client.post(
        "/api/auth/login", json={"username": "lock-admin-one", "password": PASSWORD}
    )
    assert signed_in_again.status_code == 200


def test_a_locked_session_still_expires(client: TestClient) -> None:
    """A locked session still expires; expiry outranks the lock."""
    sign_in(client)
    lock_now(client)

    expire_every_session(client)

    assert client.post("/api/auth/unlock", json={"pin": TEST_PIN}).status_code == 401
    assert client.get("/api/auth/me").status_code == 401


def test_the_failure_count_starts_again_after_a_successful_unlock(client: TestClient) -> None:
    """A successful unlock resets the failure count: the cap bounds one sitting."""
    sign_in(client)
    lock_now(client)
    for _ in range(MAX_UNLOCK_FAILURES - 1):
        client.post("/api/auth/unlock", json={"pin": "000000"})
    assert client.post("/api/auth/unlock", json={"pin": TEST_PIN}).status_code == 204

    lock_now(client)
    for _ in range(MAX_UNLOCK_FAILURES - 1):
        assert client.post("/api/auth/unlock", json={"pin": "000000"}).status_code == 401
    assert client.get("/api/auth/me").status_code == 200


# --- the shapes with no session behind them ------------------------------------------------------


async def test_locking_and_unlocking_a_session_that_is_not_there_answer_false(
    client: TestClient,
) -> None:
    """Locking or unlocking a session that is not there answers no-session, not an error."""
    service = client.app.state.auth  # type: ignore[attr-defined]

    assert await service.lock_app("not-a-real-token") is LockOutcome.NO_SESSION
    assert await service.unlock_app("not-a-real-token", TEST_PIN) is False


def test_unlocking_while_the_pin_is_locked_out_says_so(client: TestClient) -> None:
    """While the PIN is locked out, unlocking answers 429 rather than 401. The budget is spent
    opening Hidden, which destroys nothing, so the session survives to be refused."""
    sign_in(client)
    for _ in range(12):
        client.post("/api/vault/unlock", json={"pin": "000000"})

    lock_now(client)

    assert client.post("/api/auth/unlock", json={"pin": TEST_PIN}).status_code == 429


def test_destroying_one_session_leaves_the_accounts_other_one_holding_the_key(
    client: TestClient,
) -> None:
    """Destroying one session keeps the master key while another session of the user remains."""
    setup = client.post("/api/auth/setup", json={"username": "twice", "password": PASSWORD})
    assert setup.status_code == 201
    client.headers[CSRF_HEADER_NAME] = client.get("/api/auth/me").json()["csrf_token"]
    user_id = client.get("/api/auth/me").json()["id"]
    give_pin(db_path(client), user_id)
    keys = client.app.state.master_keys  # type: ignore[attr-defined]
    assert keys.get(user_id) is not None

    # Two browsers are two sessions on one client: a second `TestClient` would run on a second
    # event loop, which the database's lock refuses.
    first = client.cookies[SESSION_COOKIE_NAME]
    assert (
        client.post("/api/auth/login", json={"username": "twice", "password": PASSWORD}).status_code
        == 200
    )
    second = client.cookies[SESSION_COOKIE_NAME]
    assert second != first, "the second sign-in reused the first session"

    client.cookies.set(SESSION_COOKIE_NAME, first)
    client.headers[CSRF_HEADER_NAME] = client.get("/api/auth/me").json()["csrf_token"]
    lock_now(client)
    for _ in range(MAX_UNLOCK_FAILURES):
        client.post("/api/auth/unlock", json={"pin": "000000"})
    assert client.get("/api/auth/me").status_code == 401

    client.cookies.set(SESSION_COOKIE_NAME, second)
    assert client.get("/api/auth/me").status_code == 200
    assert keys.get(user_id) is not None


def test_locking_never_ends_a_session_and_the_password_reopens_it(
    client: TestClient,
) -> None:
    """Locking never ends a session, and the password always reopens it, PIN or not."""
    sign_in(client)

    assert client.post("/api/auth/lock").json()["outcome"] == "locked"
    assert client.get("/api/assets").status_code == 423, "locking did not shut the session"

    assert client.post("/api/auth/unlock/password", json={"password": PASSWORD}).status_code == 204
    assert client.get("/api/assets").status_code == 200


def test_a_wrong_password_does_not_open_a_locked_session(client: TestClient) -> None:
    sign_in(client)
    client.post("/api/auth/lock")

    refused = client.post("/api/auth/unlock/password", json={"password": "not-the-password"})

    assert refused.status_code == 401
    assert client.get("/api/assets").status_code == 423


def test_the_pin_does_not_open_a_session_when_that_option_is_off(client: TestClient) -> None:
    """With the PIN option off the PIN opens nothing; the password still does."""
    sign_in(client)
    client.post("/api/auth/lock")

    assert client.post("/api/auth/unlock", json={"pin": TEST_PIN}).status_code == 401
    assert client.get("/api/assets").status_code == 423

    assert client.post("/api/auth/unlock/password", json={"password": PASSWORD}).status_code == 204


def test_a_password_unlock_needs_a_session_to_unlock(client: TestClient) -> None:
    """A password unlock opens an existing session and never mints one."""
    # Signed out by dropping the session, not by a second client (see the two-browser test).
    client.cookies.clear()

    refused = client.post("/api/auth/unlock/password", json={"password": PASSWORD})

    assert refused.status_code in (401, 403)


def test_a_session_can_be_locked_again_after_it_was_unlocked(client: TestClient) -> None:
    """A session can be locked again after an unlock: the unlock clears the lock and the count."""
    sign_in(client)
    allow_pin_unlock(client)

    assert client.post("/api/auth/lock").json()["outcome"] == "locked"
    assert client.post("/api/auth/unlock", json={"pin": TEST_PIN}).status_code == 204
    assert client.get("/api/assets").status_code == 200, "the first unlock did not take"

    second = client.post("/api/auth/lock")
    assert second.status_code == 200, f"the second lock was refused: {second.status_code}"
    assert second.json()["outcome"] == "locked"
    assert client.get("/api/assets").status_code == 423


def test_locking_a_session_that_is_already_locked_is_not_a_refusal(client: TestClient) -> None:
    """Locking an already locked session is not a refusal: a panic control pressed twice."""
    sign_in(client)
    allow_pin_unlock(client)

    assert client.post("/api/auth/lock").json()["outcome"] == "locked"

    again = client.post("/api/auth/lock")
    assert again.status_code == 200, f"pressing it twice answered {again.status_code}"
    assert again.json()["outcome"] == "locked"


def test_a_locked_session_is_told_it_is_locked_when_it_asks_who_it_is(client: TestClient) -> None:
    """`me` tells a locked session it is locked, so the shell never draws the library first."""
    sign_in(client)
    allow_pin_unlock(client)

    assert client.get("/api/auth/me").json()["locked"] is False

    client.post("/api/auth/lock")

    me = client.get("/api/auth/me")
    assert me.status_code == 200, "the lock screen cannot ask who it is asking for"
    assert me.json()["locked"] is True, "the shell has no way to know before it draws"


# --- the PIN is for the local network ------------------------------------------------------


def test_the_pin_unlocks_from_the_local_network(client: TestClient) -> None:
    sign_in(client)
    lock_now(client)

    assert client.get("/api/auth/me").json()["pin_unlock_offered"] is True
    assert client.post("/api/auth/unlock", json={"pin": TEST_PIN}).status_code == 204
    assert client.get("/api/auth/me").json()["locked"] is False


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({"X-Forwarded-For": OUTSIDE_ADDRESS}, id="through_a_tunnel"),
        pytest.param({"CF-Connecting-IP": OUTSIDE_ADDRESS}, id="through_a_hosted_tunnel"),
        pytest.param({"Forwarded": f"for={OUTSIDE_ADDRESS}"}, id="through_a_proxy"),
    ],
)
def test_the_pin_is_refused_through_a_tunnel_with_the_sentence(
    client: TestClient, headers: dict[str, str]
) -> None:
    """Through a tunnel the PIN is refused before it is read, so nothing is counted; the password
    still opens the session."""
    sign_in(client)
    lock_now(client)

    assert client.get("/api/auth/me", headers=headers).json()["pin_unlock_offered"] is False
    for _ in range(MAX_UNLOCK_FAILURES + 1):
        refused = client.post("/api/auth/unlock", json={"pin": TEST_PIN}, headers=headers)
        assert refused.status_code == 403
        assert refused.json()["detail"] == (
            "Your PIN only unlocks Sift on your local network. From here, use your password."
        )
    assert client.get("/api/auth/me").json()["locked"] is True

    opened = client.post("/api/auth/unlock/password", json={"password": PASSWORD}, headers=headers)
    assert opened.status_code == 204


def test_the_pin_is_refused_from_an_address_outside_the_network(client: TestClient) -> None:
    """A port forwarded straight to Sift adds no header; the address itself is the fact."""
    sign_in(client)
    lock_now(client)
    outside = TestClient(client.app, client=(OUTSIDE_ADDRESS, 50000))
    outside.cookies.set(SESSION_COOKIE_NAME, client.cookies[SESSION_COOKIE_NAME])
    outside.headers[CSRF_HEADER_NAME] = client.headers[CSRF_HEADER_NAME]

    assert outside.post("/api/auth/unlock", json={"pin": TEST_PIN}).status_code == 403
    assert client.post("/api/auth/unlock", json={"pin": TEST_PIN}).status_code == 204
