# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sign a test client in by seeding the user and session directly, as the service would."""

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
    # The floor parameters: the fastest hash that still verifies.
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
    # The service compares expires_at with time.time().
    now = int(time.time())
    await database.execute(
        _INSERT_SESSION,
        (new_id(), user_id, hash_token(token), now, now, now + _TTL),
    )


TEST_PIN = "246810"

_SET_PIN = "UPDATE users SET pin_hash = ? WHERE id = ?"


def give_pin(db_path: Path, user_id: str, pin: str = TEST_PIN) -> str:
    """Give a seeded user a PIN, which concealing anything needs first. Returns the PIN."""

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
    """Seed a user (if absent) and a session: (user_id, session_token, csrf_token)."""
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
    """The id the server believes the client is signed in as."""
    return str(client.get("/api/auth/me").json()["id"])  # type: ignore[attr-defined]


def hide_for_caller(client: object, kind: str, object_id: str, *, hidden: bool = True) -> None:
    """Hide one thing from the signed-in user, in the row itself: the route needs a PIN."""
    app: FastAPI = client.app  # type: ignore[attr-defined]
    database = part_of_app(app, DATABASE)
    hide_for(Path(database.path), kind, object_id, signed_in_id(client), hidden=hidden)


async def user_of_session(service: object, token: str) -> str | None:
    """The user behind a session token, or None; here, since only tests want it alone."""
    resolved = await service.resolve_session_state(token)  # type: ignore[attr-defined]
    return None if resolved is None else str(resolved.user_id)
