# SPDX-License-Identifier: AGPL-3.0-or-later
"""The `secrets` table: seal a value in, open it back out, forget it.

A secret here is something Sift holds on somebody's behalf and must never be able to read on its
own: a site login, a key for a stash-box. It is sealed with a master key that only the user's
password unwraps, and the sealed bytes and the public nonce are all that reach the database. A
backup of that database is a locked box next to no key.

The sealing and unsealing are not implemented here. There is one authenticated-encryption routine
in Sift and it lives with the rest of the sign-in crypto, in `kernel/secrets.py`; this calls it.

## Why this is in the kernel

More than one slice needs sealed values, two slices may not import each other, and the honest
owner of "seal a blob under the master key" is the kernel.

Every method that opens or seals takes the master key as an argument rather than reaching for it.
Reaching for it is exactly what is impossible: the key belongs to a session, or is handed to a job
by whoever has one. Getting hold of it with no session to read it from is `AdminMasterKey`, which
stays in the download slice because it knows about users and the kernel does not.
"""

from __future__ import annotations

import time

from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.secrets import open_secret, seal_secret

_SEAL_SQL = (
    "INSERT INTO secrets (id, ciphertext, nonce, created_at, updated_at) VALUES (?, ?, ?, ?, ?)"
)
_OPEN_SQL = "SELECT ciphertext, nonce FROM secrets WHERE id = ?"
_FORGET_SQL = "DELETE FROM secrets WHERE id = ?"


class SecretStore:
    """The `secrets` table: seal a value in, open it back out, forget it.

    Every method that opens or seals takes the master key as an argument rather than reaching for
    it. Reaching for it is exactly the thing that is impossible here: the key belongs to a session
    or is handed to a job, so it is always passed in by whoever has it.
    """

    def __init__(self, database: Database) -> None:
        self._db = database

    async def seal(self, plaintext: bytes, master_key: bytes) -> str:
        """Seal a value under the master key and store it. Returns the new secret's id."""
        ciphertext, nonce = seal_secret(master_key, plaintext)
        secret_id = new_id()
        now = int(time.time())
        await self._db.execute(_SEAL_SQL, (secret_id, ciphertext, nonce, now, now))
        return secret_id

    async def open(self, secret_id: str, master_key: bytes) -> bytes | None:
        """Recover a stored value with the master key, or None if it is gone or the key is wrong."""
        row = await self._db.fetch_one(_OPEN_SQL, (secret_id,))
        if row is None:
            return None
        return open_secret(master_key, row["ciphertext"], row["nonce"])

    async def forget(self, secret_id: str) -> None:
        """Delete a stored secret. Used when a site login is replaced or removed."""
        await self._db.execute(_FORGET_SQL, (secret_id,))
