# SPDX-License-Identifier: AGPL-3.0-or-later
"""The secret store and the job-side master key.

The properties that matter here are the ones a backup depends on: the row on disk is ciphertext,
and without the key derived from the password there is no way back to the cookie.
"""

from __future__ import annotations

from sift.kernel.access import Role
from sift.kernel.db import Database
from sift.slices.auth.crypto import generate_master_key
from sift.slices.auth.keys import MasterKeyStore
from sift.slices.download.secrets import AdminMasterKey, SecretStore
from sift.testing.fixtures import create_user

_COOKIE = b"sessionid=super-secret-value; Domain=.instagram.com"


async def test_a_sealed_secret_opens_again_with_the_same_key(
    secret_store: SecretStore, master_key: bytes
) -> None:
    secret_id = await secret_store.seal(_COOKIE, master_key)
    assert await secret_store.open(secret_id, master_key) == _COOKIE


async def test_the_stored_row_is_ciphertext_not_the_cookie(
    secret_store: SecretStore, master_key: bytes, temp_db: Database
) -> None:
    secret_id = await secret_store.seal(_COOKIE, master_key)
    row = await temp_db.fetch_one(
        "SELECT ciphertext, nonce FROM secrets WHERE id = ?", (secret_id,)
    )
    assert row is not None
    assert _COOKIE not in bytes(row["ciphertext"])
    assert b"super-secret-value" not in bytes(row["ciphertext"])


async def test_a_stolen_backup_without_the_key_is_worthless(
    secret_store: SecretStore, master_key: bytes
) -> None:
    secret_id = await secret_store.seal(_COOKIE, master_key)
    # The whole database is here; the key (derived from a password held nowhere) is not.
    assert await secret_store.open(secret_id, generate_master_key()) is None


async def test_opening_a_missing_secret_is_none(
    secret_store: SecretStore, master_key: bytes
) -> None:
    assert await secret_store.open("01HXnope", master_key) is None


async def test_a_forgotten_secret_cannot_be_opened(
    secret_store: SecretStore, master_key: bytes
) -> None:
    secret_id = await secret_store.seal(_COOKIE, master_key)
    await secret_store.forget(secret_id)
    assert await secret_store.open(secret_id, master_key) is None


async def test_the_admin_key_is_none_with_no_admin(
    secret_store: SecretStore, temp_db: Database
) -> None:
    keys = MasterKeyStore()
    assert await AdminMasterKey(temp_db, keys).master_key() is None


async def test_the_admin_key_is_none_until_the_admin_logs_in(
    secret_store: SecretStore, temp_db: Database
) -> None:
    admin = await create_user(temp_db, Role.ADMIN)
    keys = MasterKeyStore()
    secrets = AdminMasterKey(temp_db, keys)

    # Nobody has entered the password since the process started.
    assert await secrets.master_key() is None

    key = generate_master_key()
    keys.store(admin.id, key)
    assert await secrets.master_key() == key
