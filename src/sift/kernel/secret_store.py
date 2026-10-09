# SPDX-License-Identifier: AGPL-3.0-or-later
"""The `secrets` table: values sealed under a master key only a password unwraps."""

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
    """Seal, open and forget secrets; the master key is always passed in, never reached for."""

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
