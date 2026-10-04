# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where the unwrapped master keys live while someone is logged in.

The master key that protects saved site logins exists in the clear only in memory, and only for
as long as a user is logged in. It is derived from the password at login, held here, and
dropped at logout. This store is that "held here": a plain in-process map from a user to their
master key, never written to disk and empty again after a restart.

That emptiness after a restart is a feature, not a gap. A cold start has no password to derive
from, so it has no master key, so a job that needs a saved login waits for someone to log in
rather than running against secrets it cannot read. The alternative (keeping the key somewhere it
survives a restart) is exactly the thing the password-wrapped design exists to avoid.
"""

from __future__ import annotations

from sift.kernel.wiring import Part


class MasterKeyStore:
    """Unwrapped master keys, by user, for the life of the process.

    Held on the application so a request or a job can reach the key for a logged-in user, and so
    a test can hand over its own. Guests have no master key and never appear here.

    Access is from the event loop, which runs one thing at a time, so the plain dictionary needs no
    lock: there is no await between reading it and writing it.
    """

    def __init__(self) -> None:
        self._keys: dict[str, bytes] = {}

    def store(self, user_id: str, master_key: bytes) -> None:
        """Remember a user's master key. Called once the password has unwrapped it at login."""
        self._keys[user_id] = master_key

    def get(self, user_id: str) -> bytes | None:
        """The user's master key, or None if nobody has unwrapped it in this process yet.

        None is the ordinary state after a restart even for a user with a valid session cookie:
        the cookie authenticates, but the key that decrypts secrets is gone until the password is
        entered again.
        """
        return self._keys.get(user_id)

    def forget(self, user_id: str) -> None:
        """Drop a user's master key. Called at logout, once no live session still needs it."""
        self._keys.pop(user_id, None)

    def has(self, user_id: str) -> bool:
        return user_id in self._keys


#: The unwrapped master keys, in memory, for as long as a user is signed in.
MASTER_KEYS: Part[MasterKeyStore] = Part("master_keys")
