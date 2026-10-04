# SPDX-License-Identifier: AGPL-3.0-or-later
"""The vault: putting things out of sight, and the PIN that brings them back.

What the vault is, stated plainly, because building the wrong thing here is easy: it conceals, it
does not encrypt. Sift reads files where they already are and never moves them, so it cannot
scramble the bytes on disk without copying them somewhere else, and it does not pretend to. What it
defends is the screen: somebody glancing over your shoulder, a call you are sharing, a guest
sitting at your desk. It is not a defence against somebody holding the disk, and nothing here or in
the interface may suggest that it is. A promise of secrecy that the code cannot keep is worse than
no promise at all.

The concealing itself is not written here. The access layer applies it to every query in the
application, for everybody, admins included: one rule, in one place, so no screen can hold a
second opinion about what is hidden. This feature owns the other half: the flag that puts something
in, the PIN check that opens it for one browser, and the triggers that shut it again.

The settings this registers are the vault's behaviour, which is why they are declared here and not
in the feature that draws the settings screen. That screen is generated from what has been
declared.
"""

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

# How long a sign-in lasts.
#
# HERE rather than in the auth slice, because the auth package declares no settings and this pane
# is where somebody looks for it: Privacy is already the home of the PIN, the idle timers and what
# a guest may do. The key and the conversion belong to auth, which owns sessions (see
# `auth.tuning`) and this only says where the control is drawn.
#
# App-wide and admin-only. It is a property of the installation rather than a personal preference:
# a guest shortening their own session would be choosing their own security, and lengthening it
# would be choosing an admin's.
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

# How the vault hides things. Two answers, and which is right depends on who else is in the room.
# Showing nothing is the safer default: a gap on a screen still says there is something you
# are not being shown, and someone who noticed the gap would know to ask.
register_setting(
    key=VAULT_CONCEALMENT_KEY,
    scope="user",
    default="fully_gone",
    choices=["fully_gone", "placeholder"],
    choice_labels=("Show nothing", "Show a locked tile"),
    section="Privacy and Security",
    # It decides what a scoped read RETURNS, not how something looks, so a change to it rings the
    # bell that means "what you may see has moved" as well as the one that means "a setting has".
    # Without it the placeholders already drawn would stay on screen until somebody navigated,
    # which reads exactly like the setting not working. See `Setting.changes_visibility`.
    changes_visibility=True,
    label="How Hidden hides files",
    disclosure=(
        "With Show a locked tile, you can see that something is there but not what it is. Either "
        "way, Sift never sends a hidden file until you unlock Hidden."
    ),
    help="With Show nothing, hidden files are left out of every list, count and search.",
)

# The triggers. Each one answers the same question (when should the vault shut by itself?) and
# the honest default is "soon, and often", because the whole feature is for the moment somebody
# else can see the screen and you did not plan for it.
register_setting(
    key=LOCK_AFTER_IDLE_KEY,
    # Locking a screen is the browser's job: the timer runs there, the blur
    # happens there, and nothing on the server can observe either.
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
    # Locking a screen is the browser's job: the timer runs there, the blur
    # happens there, and nothing on the server can observe either.
    read_by=ReadBy.CLIENT,
    scope="user",
    default=False,
    section="Privacy and Security",
    label="Lock Hidden when you switch away",
    help="The strictest choice: you enter your PIN every time you come back to Sift.",
)
register_setting(
    key=LOCK_ON_LAUNCH_KEY,
    # Locking a screen is the browser's job: the timer runs there, the blur
    # happens there, and nothing on the server can observe either.
    read_by=ReadBy.CLIENT,
    scope="user",
    default=True,
    section="Privacy and Security",
    label="Lock Hidden when Sift opens",
    help="When off, opening Sift again leaves Hidden as it was. Restarting Sift always locks it.",
)
register_setting(
    key=LOCK_ON_CLOSE_KEY,
    # Locking a screen is the browser's job: the timer runs there, the blur
    # happens there, and nothing on the server can observe either.
    read_by=ReadBy.CLIENT,
    scope="user",
    default=True,
    section="Privacy and Security",
    label="Lock Hidden when you quit Sift",
    help="The next person to open Sift on this device finds Hidden locked.",
)

# Locking Sift itself, as opposed to shutting Hidden. Off by default, and the reason is that turning
# it on swaps a password for a PIN on the way back in: a deliberate trade of strength for speed,
# which is exactly the kind of thing that must not happen because nobody looked at a settings page.
# With it off, the lock shortcut still keeps the session, and only the password reopens it.
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
    # Locking a screen is the browser's job: the timer runs there, the blur
    # happens there, and nothing on the server can observe either.
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
    # Named so the composition root can ask whether a user has turned this on. The lock
    # itself belongs to auth, which does not import this slice.
    "APP_LOCK_ENABLED_KEY",
    "router",
]
