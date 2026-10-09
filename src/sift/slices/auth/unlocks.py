# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which live sessions have unlocked the vault: in memory, keyed by hashed token, empty at restart.

Only the event loop touches it, with no await between read and write, so it needs no lock.
"""

from __future__ import annotations

#: Sessions holding an open vault; past it the oldest is dropped, which costs a PIN, never a reveal.
MAX_OPEN_SESSIONS = 256


class VaultUnlockStore:
    """The sessions whose vault is open; a dict, so insertion order says which is oldest."""

    def __init__(self, max_open: int = MAX_OPEN_SESSIONS) -> None:
        self._open: dict[str, None] = {}
        self._max_open = max_open

    def unlock(self, token_hash: str) -> None:
        """Open the vault for one session, after the PIN; the browser in use is evicted last."""
        self._open.pop(token_hash, None)
        self._open[token_hash] = None
        while len(self._open) > self._max_open:
            # `next(iter(...))` is the oldest key: dicts iterate in insertion order.
            self._open.pop(next(iter(self._open)))

    def lock(self, token_hash: str) -> None:
        """Close it again. Called by a lock trigger, by the panic control, and at logout.

        Locking a session never unlocked is not an error: every lock trigger fires regardless, and a
        control safe to press only in some states would sometimes be pressed wrongly.
        """
        self._open.pop(token_hash, None)

    def is_unlocked(self, token_hash: str) -> bool:
        """Whether this session has the vault open. Asked on every request, and false is the answer
        for every session this process has not been asked to unlock, including every session that
        was unlocked before a restart."""
        return token_hash in self._open
