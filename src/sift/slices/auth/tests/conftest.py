# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fixtures for the auth tests.

The service is built with the floor hashing parameters rather than the ones tuned to this machine:
they are the fastest a real Argon2id hash is allowed to be, which keeps a suite that hashes a lot
of passwords from being paced by a deliberately expensive function, while still exercising a real
hash that meets the floor.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

import pytest

from sift.kernel.db import Database
from sift.kernel.jobs import JobQueue
from sift.slices.auth import AuthService, Hasher, MasterKeyStore
from sift.slices.auth.crypto import resolve_argon2_params
from sift.slices.auth.throttle import Tarpit, Throttle

# Strong enough to pass the policy, and absent from the bundled breach list.
PASSWORD = "Corr3ct-Horse!staple9"
PASSWORD_TWO = "An0ther-Secur3!keyword"


@pytest.fixture
def hasher() -> Hasher:
    return Hasher(resolve_argon2_params(None))


@pytest.fixture
def master_keys() -> MasterKeyStore:
    return MasterKeyStore()


@pytest.fixture
async def auth_db(temp_db: Database) -> Database:
    await temp_db.initialize_schema()
    return temp_db


@pytest.fixture
def make_service(
    auth_db: Database, hasher: Hasher, master_keys: MasterKeyStore
) -> Callable[..., AuthService]:
    """Build an AuthService, overriding the clock, queue or throttles a test wants to drive."""

    def _make(
        *,
        queue: JobQueue | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        login_tarpit: Tarpit | None = None,
        pin_throttle: Throttle | None = None,
        service_hasher: Hasher | None = None,
        on_key_available: Callable[[bytes], Awaitable[None]] | None = None,
    ) -> AuthService:
        return AuthService(
            auth_db,
            hasher=service_hasher or hasher,
            master_keys=master_keys,
            queue=queue,
            session_ttl_seconds=7 * 24 * 3600,
            on_key_available=on_key_available,
            clock=clock,
            sleep=sleep,
            login_tarpit=login_tarpit,
            pin_throttle=pin_throttle,
        )

    return _make


@pytest.fixture
async def service(make_service: Callable[..., AuthService]) -> AuthService:
    return make_service()
