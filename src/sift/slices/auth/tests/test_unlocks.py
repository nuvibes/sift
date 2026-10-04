# SPDX-License-Identifier: AGPL-3.0-or-later
"""The store of sessions that have the vault open: keyed by session, not user, and bounded."""

from __future__ import annotations

from sift.slices.auth.unlocks import VaultUnlockStore


def test_a_fresh_store_has_nothing_open() -> None:
    """What a restart produces, and the reason the PIN is allowed to be short: a cold process has
    no unlock to inherit, whatever any browser still holds."""
    assert VaultUnlockStore().is_unlocked("anything") is False


def test_unlocking_is_per_session() -> None:
    store = VaultUnlockStore()
    store.unlock("one")

    assert store.is_unlocked("one") is True
    assert store.is_unlocked("two") is False, "a second browser has its own answer"


def test_locking_something_that_was_never_open_is_not_an_error() -> None:
    """Every lock trigger fires whether or not anything was open, and the panic control is pressed
    by somebody with no time to check first."""
    store = VaultUnlockStore()
    store.lock("never-opened")

    assert store.is_unlocked("never-opened") is False


def test_the_store_cannot_grow_without_bound() -> None:
    """The store is bounded: entries of dead sessions are inert but must not accumulate."""
    store = VaultUnlockStore(max_open=3)

    for session in ("a", "b", "c", "d", "e"):
        store.unlock(session)

    assert [store.is_unlocked(s) for s in ("a", "b", "c", "d", "e")] == [
        False,
        False,
        True,
        True,
        True,
    ], "the oldest go first, and only the cap's worth are kept"


def test_the_browser_in_use_is_the_last_one_dropped() -> None:
    """Re-unlocking moves a session to the back of the eviction queue."""
    store = VaultUnlockStore(max_open=2)
    store.unlock("old")
    store.unlock("new")
    store.unlock("old")  # used again

    store.unlock("newest")

    assert store.is_unlocked("old") is True
    assert store.is_unlocked("new") is False, "the one nobody came back to"
    assert store.is_unlocked("newest") is True


def test_dropping_the_oldest_re_locks_rather_than_reveals() -> None:
    """The direction the cap fails in. Being evicted costs somebody a PIN entry; the other way
    round would be a session that kept an open vault because the store ran out of room."""
    store = VaultUnlockStore(max_open=1)
    store.unlock("first")
    store.unlock("second")

    assert store.is_unlocked("first") is False
