# SPDX-License-Identifier: AGPL-3.0-or-later
"""The vault's fixed names and numbers, in one place, with the reason beside each."""

from __future__ import annotations

LOCK_AFTER_IDLE_KEY = "vault.lock_after_idle_minutes"
LOCK_ON_BLUR_KEY = "vault.lock_on_blur"
LOCK_ON_LAUNCH_KEY = "vault.lock_on_launch"
LOCK_ON_CLOSE_KEY = "vault.lock_on_close"
"""The lock triggers, by the names their values are stored under. Read by the screen, which is what
arms them: a timer and a tab event live in the browser, and what they do when they fire is ask the
server to lock, so the closing is real even though the noticing is not."""

APP_LOCK_ENABLED_KEY = "vault.app_lock_enabled"
APP_LOCK_AFTER_IDLE_KEY = "vault.app_lock_after_idle_minutes"
"""Locking Sift itself, by the names their values are stored under.

Two locks, two sizes, and they are deliberately not one setting. Shutting Hidden takes hidden things
off the screen and leaves the library open; locking Sift makes the session inert until the PIN
arrives. Somebody wants the first often and the second rarely, so the timers are separate and the
Sift one is the longer of the two by default.

Off by default. Until it is on, the lock shortcut means what it has always meant (end the session,
password to return) because turning a password door into a PIN door without being asked is not a
default anybody chose.
"""

MAX_IDLE_MINUTES = 24 * 60
"""A day. Not a meaningful privacy setting at that length, but a bound has to be somewhere, and one
that refuses a plainly absurd number is better than a box that accepts any integer at all."""
