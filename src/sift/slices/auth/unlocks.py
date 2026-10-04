# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which live sessions have unlocked the vault.

An unlocked vault is a fact about one browser right now, not a permission or a user property, so
it lives in the process keyed by session and never in a row. A restart empties it, so every
session comes back locked, which is what lets the PIN be short: it never opens what a restart
closed. Each browser unlocks for itself, so a vault opened here conceals nothing less on the
screen somebody else is looking at. Keys are hashes of session tokens, nothing replayable as a
cookie. Only the event loop touches it, with no await between read and write, so no lock.
"""

from __future__ import annotations

MAX_OPEN_SESSIONS = 256
"""A cap on sessions holding an open vault at once.

Entries leave on lock and logout but not when a session expires or is revoked; those leftovers are
inert (their token hash matches nothing) but unbounded, so the oldest is dropped at the cap. Far
above a household, and evicting costs a PIN entry: this store's failure must be a re-prompt, never
a reveal.
"""


class VaultUnlockStore:
    """The set of sessions whose vault is open, for the life of the process.

    Held on the application so the viewer dependency asks it every request and a test can supply
    its own. A dict, never a set: insertion order is what makes "drop the oldest" mean something.
    """

    def __init__(self, max_open: int = MAX_OPEN_SESSIONS) -> None:
        self._open: dict[str, None] = {}
        self._max_open = max_open

    def unlock(self, token_hash: str) -> None:
        """Open the vault for one session. Called only after the PIN has been verified.

        Re-unlocking moves a session to the back, so the browser in use is evicted last.
        """
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
