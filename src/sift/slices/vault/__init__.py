# SPDX-License-Identifier: AGPL-3.0-or-later
"""The vault: it conceals from the screen and does not encrypt; the access layer applies it."""

from __future__ import annotations

from sift.kernel.settings_registry import ReadBy, register_setting
from sift.slices.auth import (
    DEFAULT_SESSION_DAYS,
    SESSION_DAYS_KEY,
    VAULT_CONCEALMENT_KEY,
)
from sift.slices.vault.router import router
from sift.slices.vault.tuning import (
    APP_LOCK_AFTER_IDLE_KEY,
    APP_LOCK_ENABLED_KEY,
    LOCK_AFTER_IDLE_KEY,
    LOCK_ON_BLUR_KEY,
    LOCK_ON_CLOSE_KEY,
    LOCK_ON_LAUNCH_KEY,
    MAX_IDLE_MINUTES,
)

# Drawn in Privacy though auth owns the key. App-wide, so a guest cannot choose their own security.
register_setting(
    key=SESSION_DAYS_KEY,
    scope="app",
    default=DEFAULT_SESSION_DAYS,
    minimum=1,
    maximum=365,
    unit="days",
    section="Privacy and Security",
    label="Stay signed in for",
    disclosure="Applies from your next sign-in. A browser already signed in isn't signed out early.",
    help="How long a browser stays signed in before you must sign in again.",
)

# Show nothing is the safer default: a gap on a screen still says something is hidden.
register_setting(
    key=VAULT_CONCEALMENT_KEY,
    scope="user",
    default="fully_gone",
    choices=["fully_gone", "placeholder"],
    choice_labels=("Show nothing", "Show a locked tile"),
    section="Privacy and Security",
    # It changes what a scoped read returns, so placeholders already drawn must be read again.
    changes_visibility=True,
    label="How Hidden hides files",
    disclosure=(
        "With Show a locked tile, you can see that something is there but not what it is. Either "
        "way, Sift never sends a hidden file until you unlock Hidden."
    ),
    help="With Show nothing, hidden files are left out of every list, count and search.",
)

# The triggers default to soon and often. They run in the browser, the only place that sees them.
register_setting(
    key=LOCK_AFTER_IDLE_KEY,
    read_by=ReadBy.CLIENT,
    scope="user",
    default=15,
    minimum=0,
    maximum=MAX_IDLE_MINUTES,
    section="Privacy and Security",
    label="Lock Hidden after",
    automatic_label="Never",
    unit="min",
    help=(
        "Hides your hidden files again after this long without use, and asks for your PIN. "
        "Leave it empty to never lock it automatically."
    ),
)
register_setting(
    key=LOCK_ON_BLUR_KEY,
    read_by=ReadBy.CLIENT,
    scope="user",
    default=False,
    section="Privacy and Security",
    label="Lock Hidden when you switch away",
    help="The strictest choice: you enter your PIN every time you come back to Sift.",
)
register_setting(
    key=LOCK_ON_LAUNCH_KEY,
    read_by=ReadBy.CLIENT,
    scope="user",
    default=True,
    section="Privacy and Security",
    label="Lock Hidden when Sift opens",
    help="When off, opening Sift again leaves Hidden as it was. Restarting Sift always locks it.",
)
register_setting(
    key=LOCK_ON_CLOSE_KEY,
    read_by=ReadBy.CLIENT,
    scope="user",
    default=True,
    section="Privacy and Security",
    label="Lock Hidden when you quit Sift",
    help="The next person to open Sift on this device finds Hidden locked.",
)

# Off by default: a PIN in place of the password is a trade nobody should get unasked.
register_setting(
    key=APP_LOCK_ENABLED_KEY,
    scope="user",
    default=False,
    section="Privacy and Security",
    label="Unlock Sift with your PIN on LAN",
    disclosure=(
        "Locking Sift keeps you signed in either way, and you can still sign out at any time. "
        "Through a tunnel or from outside your network, Sift always asks for your password."
    ),
    help=(
        "When on, your PIN unlocks a locked Sift on your local network. Anywhere else, and when "
        "this is off, your password does."
    ),
)
register_setting(
    key=APP_LOCK_AFTER_IDLE_KEY,
    read_by=ReadBy.CLIENT,
    scope="user",
    default=0,
    minimum=0,
    maximum=MAX_IDLE_MINUTES,
    section="Privacy and Security",
    label="Lock Sift after",
    automatic_label="Never",
    unit="min",
    disclosure="Leave it empty to never lock Sift automatically.",
    help=(
        "Locks all of Sift after this long without use. Lock Hidden after only hides your hidden "
        "files."
    ),
)

__all__ = [
    # For the composition root: auth owns the lock and does not import this slice.
    "APP_LOCK_ENABLED_KEY",
    "router",
]
