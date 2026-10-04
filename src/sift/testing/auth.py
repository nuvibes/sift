# SPDX-License-Identifier: AGPL-3.0-or-later
"""Helpers for signing a test client in.

Some tests need a real session cookie against a running application: the authorization matrix, in
particular, has to call protected routes as a real admin and a real guest. Creating one through the
`/api/auth/users` endpoints needs a session to make the call with, which is the thing being
set up, so these seed the user and the session directly, the way the service would, using the same
hashing and the same tables.

They open their own short-lived connection to the application's database file rather than reaching
into the running app's connections, because those belong to the app's event loop and are not safe to
touch from a synchronous test thread. WAL mode makes a second writer to the same file safe.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from fastapi import FastAPI

from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.wiring import DATABASE, part_of_app
from sift.slices.auth.crypto import (
    Hasher,
    derive_csrf_token,
    generate_master_key,
    hash_token,
    new_token,
    resolve_argon2_params,
    wrap_master_key,
)
from sift.testing.library import hide_for

_EPOCH = 1_700_000_000
_TTL = 7 * 24 * 3600

_FIND_USER = "SELECT id FROM users WHERE username = ?"
_INSERT_USER = """
INSERT INTO users
  (id, username, password_hash, pin_hash, role, mk_wrapped, mk_nonce, mk_kdf_salt,
   created_at, disabled)
VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, 0)
"""
_INSERT_SESSION = """
INSERT INTO sessions (id, user_id, token_hash, created_at, last_seen_at, expires_at)
VALUES (?, ?, ?, ?, ?, ?)
"""


def _hasher() -> Hasher:
    # Floor parameters: the fastest a real hash is allowed to be, so seeding many test users does
    # not dominate a run while still producing a hash that verifies.
    return Hasher(resolve_argon2_params(None))


async def _ensure_user(database: Database, role: str, username: str, password: str) -> str:
    existing = await database.fetch_one(_FIND_USER, (username,))
    if existing is not None:
        return str(existing["id"])

    user_id = new_id()
    password_hash = _hasher().hash(password)
    mk: tuple[bytes | None, bytes | None, bytes | None]
    if role == "admin":
        wrapped = wrap_master_key(generate_master_key(), password)
        mk = (wrapped.ciphertext, wrapped.nonce, wrapped.salt)
    else:
        mk = (None, None, None)
    await database.execute(_INSERT_USER, (user_id, username, password_hash, role, *mk, _EPOCH))
    return user_id


async def _open_session(database: Database, user_id: str, token: str) -> None:
    # Real wall-clock time: the service compares expires_at against time.time(), so a session dated
    # from any other clock would read as either already expired or good for a century.
    now = int(time.time())
    await database.execute(
        _INSERT_SESSION,
        (new_id(), user_id, hash_token(token), now, now, now + _TTL),
    )


TEST_PIN = "246810"

_SET_PIN = "UPDATE users SET pin_hash = ? WHERE id = ?"


def give_pin(db_path: Path, user_id: str, pin: str = TEST_PIN) -> str:
    """Give a seeded user a PIN, the way setting one would. Returns the PIN.

    Anything that goes into the vault needs one first: the PIN is the only thing that opens the
    vault again, so concealing something without one would be losing it, and the server refuses.
    A test that is about what concealment *does* therefore has to get past that refusal, and this
    is how, without spending a password round trip on a precondition it is not testing.
    """

    async def run() -> None:
        database = Database(db_path, readers=1)
        await database.connect()
        try:
            await database.execute(_SET_PIN, (_hasher().hash(pin), user_id))
        finally:
            await database.close()

    asyncio.run(run())
    return pin


def establish_session(
    db_path: Path, *, role: str, username: str, password: str
) -> tuple[str, str, str]:
    """Seed a user (if absent) and a fresh session for it. Sync, for use inside a TestClient.

    Returns (user_id, session_token, csrf_token). Set the token as the session cookie and send the
    CSRF token in the header on state-changing requests.
    """
    token = new_token()

    async def run() -> str:
        database = Database(db_path, readers=1)
        await database.connect()
        try:
            user_id = await _ensure_user(database, role, username, password)
            await _open_session(database, user_id, token)
            return user_id
        finally:
            await database.close()

    user_id = asyncio.run(run())
    return user_id, token, derive_csrf_token(token)


def signed_in_id(client: object) -> str:
    """The id of whoever the client is currently signed in as.

    Read back over HTTP rather than remembered from the sign-in, so it is the user the server
    believes is calling. Every test that hides something needs it, because hiding is per user and
    there is no such thing as hiding a row for nobody in particular.
    """
    return str(client.get("/api/auth/me").json()["id"])  # type: ignore[attr-defined]


def hide_for_caller(client: object, kind: str, object_id: str, *, hidden: bool = True) -> None:
    """Hide one thing from whoever the client is signed in as.

    Straight into the row the resolver reads, rather than through the route: hiding through the
    route needs a PIN, and most tests that need something hidden are not about the PIN. What is
    being set up is the state.
    """
    app: FastAPI = client.app  # type: ignore[attr-defined]
    database = part_of_app(app, DATABASE)
    hide_for(Path(database.path), kind, object_id, signed_in_id(client), hidden=hidden)


async def user_of_session(service: object, token: str) -> str | None:
    """The user behind a session token, or None. For tests that only want the id.

    The service answers with the user AND whether the app is locked on that session, because
    every request needs both and the second is useless without the first. A convenience wrapper
    that dropped the lock state would, on the service, be a public method nothing in the
    application calls, kept alive entirely by the tests below: the shape a reachability gate exists
    to refuse. So the convenience lives here, with the tests that want it.
    """
    resolved = await service.resolve_session_state(token)  # type: ignore[attr-defined]
    return None if resolved is None else str(resolved.user_id)
