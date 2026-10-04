# SPDX-License-Identifier: AGPL-3.0-or-later
"""The saved site logins, either side of a backup.

The master key is wrapped by a key derived from the password and travels in the backup; the
password travels nowhere. So a restore plus the password brings the logins back, and the file alone
opens nothing. The real auth service and secret store are driven.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from sift.kernel.db import Database
from sift.kernel.jobs import JobQueue
from sift.slices.auth import AuthService, Hasher, MasterKeyStore, VaultUnlockStore
from sift.slices.auth.crypto import resolve_argon2_params
from sift.slices.backup.service import BackupService
from sift.slices.backup.tests.conftest import database_in
from sift.slices.download import SecretStore

pytestmark = pytest.mark.anyio

PASSWORD = "Corr3ct-Horse!staple9"
A_SAVED_LOGIN = b"the-stored-credential-for-a-site-which-is-effectively-a-password"


def build_auth(database: Database, keys: MasterKeyStore) -> AuthService:
    """The real service, at the floor hashing cost so a suite is not paced by Argon2id."""
    return AuthService(
        database,
        hasher=Hasher(resolve_argon2_params(None)),
        master_keys=keys,
        vault_unlocks=VaultUnlockStore(),
        queue=JobQueue(database),
        session_ttl_seconds=7 * 24 * 3600,
    )


async def test_a_saved_site_login_comes_back_after_a_restore_on_a_fresh_install(
    backup: BackupService, prepared_db: Database, tmp_path: Path
) -> None:
    """A saved login decrypts again after a restore on a fresh install with an empty key store."""
    original_keys = MasterKeyStore()
    admin = await build_auth(prepared_db, original_keys).create_first_admin("keeper", PASSWORD)
    original_key = original_keys.get(admin.user_id)
    assert original_key is not None
    secrets = SecretStore(prepared_db)
    secret_id = await secrets.seal(A_SAVED_LOGIN, original_key)

    exported = tmp_path / "before-the-machine-died.sqlite3"
    await backup.export_to(exported)

    # The disaster. Every record gone, including the user and the sealed login.
    await prepared_db.execute("DELETE FROM secrets")
    await prepared_db.execute("DELETE FROM users")
    assert await secrets.open(secret_id, original_key) is None

    await backup.restore(exported)

    # A process that has never seen this key: no store, no session, nothing but the password.
    fresh_keys = MasterKeyStore()
    signed_in = await build_auth(prepared_db, fresh_keys).login("keeper", PASSWORD)

    recovered = fresh_keys.get(signed_in.user_id)
    assert recovered is not None
    assert await SecretStore(prepared_db).open(secret_id, recovered) == A_SAVED_LOGIN


async def test_the_backup_file_alone_does_not_give_up_the_saved_logins(
    backup: BackupService, prepared_db: Database, tmp_path: Path
) -> None:
    """The backup file read as plain SQLite yields no plaintext and no password."""
    keys = MasterKeyStore()
    admin = await build_auth(prepared_db, keys).create_first_admin("keeper", PASSWORD)
    master_key = keys.get(admin.user_id)
    assert master_key is not None
    await SecretStore(prepared_db).seal(A_SAVED_LOGIN, master_key)

    await backup.export_to(tmp_path / "stolen.zip")
    stolen = database_in(tmp_path / "stolen.zip")

    raw = stolen.read_bytes()
    assert A_SAVED_LOGIN not in raw
    assert PASSWORD.encode() not in raw

    thief = sqlite3.connect(f"file:{stolen}?mode=ro", uri=True)
    try:
        ciphertext = thief.execute("SELECT ciphertext FROM secrets").fetchall()
        wrapped = thief.execute("SELECT mk_wrapped FROM users").fetchall()
    finally:
        thief.close()

    # Both halves are in the file, and that is the design: a locked box and a locked key.
    assert len(ciphertext) == 1
    assert len(wrapped) == 1
    assert ciphertext[0][0] != A_SAVED_LOGIN
    # The key as stored is not the key that opens the box. Holding the file gets you neither.
    assert wrapped[0][0] != master_key


async def test_the_backup_carries_no_key_or_recovery_file_beside_it(
    backup: BackupService, prepared_db: Database, tmp_path: Path
) -> None:
    """The backup is one file, with no key or recovery file beside it."""
    folder = tmp_path / "destination"
    folder.mkdir()

    await backup.export_to(folder / "export.sqlite3")

    assert sorted(entry.name for entry in folder.iterdir()) == ["export.sqlite3"]
