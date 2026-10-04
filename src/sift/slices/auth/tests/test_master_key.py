# SPDX-License-Identifier: AGPL-3.0-or-later
"""The master key: where cookies live or die.

The saved site logins are only ever as safe as this. The tests here prove the properties the design
rests on: the key that wraps the master key is derived independently of the login hash, a stolen
database file opens nothing, a password change keeps the saved logins, and the PIN never brings the
key back on its own. The one place a job waits for a login rather than failing is exercised end to
end against a real worker.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable

import pytest
from argon2.low_level import Type, hash_secret_raw

from sift.kernel.db import Database
from sift.kernel.jobs import JobBlocked, JobQueue, JobState, WorkerPool, register_handler
from sift.kernel.jobs.worker_pool import JobContext
from sift.kernel.log import redact
from sift.kernel.secrets import open_secret, seal_secret
from sift.slices.auth import MasterKeyStore
from sift.slices.auth.crypto import (
    WrappedKey,
    _wrapping_key,
    unwrap_master_key,
)
from sift.slices.auth.service import AuthService
from sift.slices.auth.tests.conftest import PASSWORD, PASSWORD_TWO
from sift.testing.auth import user_of_session

pytestmark = pytest.mark.integration


async def _wrapped_of(db: Database, username: str = "kate") -> WrappedKey:
    row = await db.fetch_one(
        "SELECT mk_wrapped, mk_nonce, mk_kdf_salt FROM users WHERE username = ?", (username,)
    )
    assert row is not None
    return WrappedKey(row["mk_wrapped"], row["mk_nonce"], row["mk_kdf_salt"])


# --- The key that wraps the master key is not the login hash ---------------------------------


async def test_the_wrapping_key_is_derived_independently_of_the_login_hash(
    service: AuthService, auth_db: Database
) -> None:
    await service.create_first_admin("kate", PASSWORD)
    row = await auth_db.fetch_one(
        "SELECT password_hash, mk_kdf_salt FROM users WHERE username = 'kate'"
    )
    assert row is not None

    # Different salts: the wrapping salt is its own random value and does not appear inside the PHC
    # login-hash string, whose salt is a separate random value.
    salt_b64 = base64.b64encode(row["mk_kdf_salt"]).decode().rstrip("=")
    assert salt_b64 not in row["password_hash"]

    # Different context: the domain tag mixed into the wrapping-key derivation changes the output,
    # so even the same password and the same salt would not produce the same bytes as a plain
    # derivation. The login hash is a plain derivation of the password.
    salt = row["mk_kdf_salt"]
    with_domain = _wrapping_key(PASSWORD, salt)
    without_domain = hash_secret_raw(
        secret=PASSWORD.encode(),
        salt=salt,
        time_cost=3,
        memory_cost=64 * 1024,
        parallelism=1,
        hash_len=32,
        type=Type.ID,
    )
    assert with_domain != without_domain


# --- Login unwraps it, logout forgets it -----------------------------------------------------


async def test_login_unwraps_the_master_key_into_the_store(
    service: AuthService, master_keys: MasterKeyStore
) -> None:
    result = await service.create_first_admin("kate", PASSWORD)
    assert master_keys.has(result.user_id)  # setup logs in, so the key is present

    await service.logout(result.session_token)
    assert not master_keys.has(result.user_id)  # forgotten once no session needs it


async def test_a_second_session_keeps_the_key_until_the_last_logout(
    make_service: Callable[..., AuthService], master_keys: MasterKeyStore
) -> None:
    service = make_service()
    result = await service.create_first_admin("kate", PASSWORD)
    second = await service.login("kate", PASSWORD)  # a second browser

    await service.logout(result.session_token)
    assert master_keys.has(result.user_id)  # the other session still needs it
    await service.logout(second.session_token)
    assert not master_keys.has(result.user_id)


async def test_a_cold_store_has_no_key_even_with_a_valid_session(
    make_service: Callable[..., AuthService], master_keys: MasterKeyStore
) -> None:
    service = make_service()
    result = await service.create_first_admin("kate", PASSWORD)
    # A restart empties the in-memory store while the session row survives in the database.
    master_keys.forget(result.user_id)
    assert await user_of_session(service, result.session_token) == result.user_id
    assert master_keys.get(result.user_id) is None


# --- A password change keeps the saved logins ------------------------------------------------


async def test_a_password_change_rewraps_the_key_and_keeps_the_secrets(
    service: AuthService, auth_db: Database, master_keys: MasterKeyStore
) -> None:
    result = await service.create_first_admin("kate", PASSWORD)
    master_key = master_keys.get(result.user_id)
    assert master_key is not None

    # A saved login, sealed under the master key. This stands in for a row in the secrets table.
    sealed, nonce = seal_secret(master_key, b"instagram-session=abc123")
    wrapped_before = await _wrapped_of(auth_db)

    await service.change_password(result.user_id, PASSWORD, PASSWORD_TWO)

    # The wrapped key on disk changed (it was re-wrapped)...
    wrapped_after = await _wrapped_of(auth_db)
    assert wrapped_after.ciphertext != wrapped_before.ciphertext

    # ...but it still holds the SAME master key, now openable with the new password...
    reopened = unwrap_master_key(wrapped_after, PASSWORD_TWO)
    assert reopened == master_key
    # ...and the sealed secret was never touched, so it still decrypts.
    assert open_secret(master_key, sealed, nonce) == b"instagram-session=abc123"


# --- A stolen database opens nothing ---------------------------------------------------------


async def test_the_database_alone_does_not_open_the_secrets(
    service: AuthService, auth_db: Database, master_keys: MasterKeyStore
) -> None:
    result = await service.create_first_admin("kate", PASSWORD)
    master_key = master_keys.get(result.user_id)
    assert master_key is not None
    sealed, nonce = seal_secret(master_key, b"a-real-cookie")

    # Everything an attacker with the file has: the wrapped key and the sealed secret. Without the
    # password there is no way from one to the other.
    wrapped = await _wrapped_of(auth_db)
    assert unwrap_master_key(wrapped, "guessed-passw0rd!") is None
    assert unwrap_master_key(wrapped, PASSWORD_TWO) is None
    # And the secret itself, without the key, is opaque.
    assert open_secret(b"\x00" * 32, sealed, nonce) is None


# --- The master key is not something a log or a redactor lets out -----------------------------


def test_a_master_key_that_reached_a_log_would_be_scrubbed() -> None:
    # The code never logs the key. These are the backstops. A field carrying it redacts by name,
    # which is the shape an accidental log line would most likely take...
    from sift.slices.auth.crypto import generate_master_key

    key = generate_master_key()
    assert redact({"master_key": key}) == {"master_key": "[redacted]"}

    # ...and a hex rendering of it, were it ever concatenated into free text, is long and random
    # enough to trip the credential-shaped value redactor.
    hex_text = key.hex()
    assert "[redacted]" in redact(hex_text)


# --- The PIN unlocks a screen, never the key -------------------------------------------------


async def test_setting_a_pin_does_not_touch_the_key_envelope(
    service: AuthService, auth_db: Database
) -> None:
    result = await service.create_first_admin("kate", PASSWORD)
    before = await _wrapped_of(auth_db)
    await service.set_pin(result.user_id, "246810", PASSWORD)
    after = await _wrapped_of(auth_db)
    # The PIN wrote pin_hash and nothing else. The wrapped key is byte-for-byte what it was.
    assert (after.ciphertext, after.nonce, after.salt) == (
        before.ciphertext,
        before.nonce,
        before.salt,
    )


async def test_the_pin_does_not_restore_the_key_on_a_cold_session(
    service: AuthService, master_keys: MasterKeyStore
) -> None:
    result = await service.create_first_admin("kate", PASSWORD)
    await service.set_pin(result.user_id, "246810", PASSWORD)

    # A restart: the key is gone from memory, the session and PIN survive in the database.
    master_keys.forget(result.user_id)

    # The PIN verifies: it would unlock the lock screen...
    assert await service.verify_pin(result.user_id, "246810")
    # ...but it has not brought the master key back. Only the password can.
    assert master_keys.get(result.user_id) is None
    await service.login("kate", PASSWORD)
    assert master_keys.has(result.user_id)


# --- A job waits for a login rather than failing ---------------------------------------------


async def _wait_for(queue: JobQueue, job_id: str, state: JobState, *, tries: int = 400) -> None:
    last: JobState | None = None
    for _ in range(tries):
        job = await queue.get(job_id)
        assert job is not None
        last = job.state
        if last is state:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"job did not reach {state} in time; last state was {last}")


async def test_a_job_needing_a_login_blocks_then_resumes_when_someone_logs_in(
    make_service: Callable[..., AuthService],
    auth_db: Database,
    master_keys: MasterKeyStore,
    clean_handlers: None,
) -> None:
    queue = JobQueue(auth_db)
    service = make_service(queue=queue)
    result = await service.create_first_admin("kate", PASSWORD)
    user_id = result.user_id
    # Log out: nobody is logged in, so the master key is not available to a job.
    await service.logout(result.session_token)
    assert not master_keys.has(user_id)

    ran = {"count": 0}

    async def needs_login(ctx: JobContext) -> None:
        ran["count"] += 1
        if master_keys.get(user_id) is None:
            raise JobBlocked("needs a saved login")
        # The key is here now; the work can proceed.

    register_handler("needs-login", needs_login, name="Test job")

    # max_attempts=1: if blocking consumed the one attempt, the job would fail instead of waiting.
    pool = WorkerPool(queue, concurrency=1, watchdog=False, poll_interval=0.02)
    await pool.start()
    try:
        job_id = await queue.enqueue("needs-login", {"user_id": user_id}, max_attempts=1)
        await _wait_for(queue, job_id, JobState.BLOCKED)
        blocked = await queue.get(job_id)
        assert blocked is not None and blocked.attempts == 0  # the attempt was handed back

        # Logging in derives the master key and releases the waiting job in one step.
        await service.login("kate", PASSWORD)
        await _wait_for(queue, job_id, JobState.DONE)
    finally:
        await pool.stop()

    assert ran["count"] >= 2  # it ran once, blocked, and ran again after the login


async def test_an_unlock_tells_every_admin_window_even_with_nothing_parked(
    make_service: Callable[..., AuthService], master_keys: MasterKeyStore
) -> None:
    """The unlock bar stands in every window of a restarted session. Unlocked in one, it must go
    from the others without a reload, and with nothing parked no queue write would say so."""
    from sift.kernel import changes
    from sift.kernel.changes import About, ChangeBus

    service = make_service()
    result = await service.create_first_admin("kate", PASSWORD)
    master_keys.forget(result.user_id)
    bus = ChangeBus()
    changes.listens(bus)
    try:
        window = bus.subscribe("01HX00000000000000000WIN4")
        assert await service.unlock_secrets(result.user_id, PASSWORD) is True
        assert About.JOBS in window.take(as_admin=True).about
    finally:
        changes.listens(None)
