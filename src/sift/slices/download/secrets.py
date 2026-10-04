# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reaching the master key when there is no session to read it from.

The `secrets` table and the store over it are `sift.kernel.secret_store`, because more than one
slice needs sealed values. `SecretStore` is re-exported here for the callers that take it from
this module.

What is this slice's is `AdminMasterKey`. A route reads the key from the session of whoever is
asking; a background download has no asker. The key exists in memory only while an admin is
signed in, so the answer to "what is the key" is often None (nobody has signed in since the
process started), and None is the signal to wait for a sign-in, not a failure.
"""

from __future__ import annotations

from sift.kernel.db import Database
from sift.kernel.secret_store import SecretStore
from sift.slices.auth import MasterKeyStore

_FIRST_ADMIN = "SELECT id FROM users WHERE role = 'admin' AND disabled = 0 ORDER BY id"


class AdminMasterKey:
    """The job-side view of the master key: an admin's, or None when nobody is signed in.

    Satisfies the system-secrets seam the worker pool hands its jobs. It resolves the one admin
    user and reads their key out of the in-memory store, which is empty until a password sign-in
    fills it and empty again after a restart. None is therefore ordinary, and it means a download
    that needs a site's saved cookies waits rather than running without them.
    """

    def __init__(self, database: Database, master_keys: MasterKeyStore) -> None:
        self._db = database
        self._master_keys = master_keys

    async def master_key(self) -> bytes | None:
        row = await self._db.fetch_one(_FIRST_ADMIN)
        if row is None:
            return None
        return self._master_keys.get(str(row["id"]))


__all__ = ["AdminMasterKey", "SecretStore"]
