# SPDX-License-Identifier: AGPL-3.0-or-later
"""The shapes that cross the wire.

Passwords and PINs are bounded strings here; their policy (length, variety, breach) lives once,
in the service, so no copy in a model can drift from it. These keep a megabyte of "username" from
reaching the database.
"""

from __future__ import annotations

from pydantic import Field

from sift.kernel.when import zone_name
from sift.kernel.wire import Wire

# A username is a label, not a secret. Bounded so nothing absurd reaches the database; the case is
# folded by the column's NOCASE collation, so "Kate" and "kate" are one user.
_Username = Field(min_length=1, max_length=64)

# Passwords are bounded only so a huge string is not handed to the hasher; the rest is policy.
_Password = Field(min_length=1, max_length=1024)
_Pin = Field(min_length=1, max_length=32)


class SetupRequest(Wire):
    username: str = _Username
    password: str = _Password


class LoginRequest(Wire):
    username: str = _Username
    password: str = _Password


class UnlockSecretsRequest(Wire):
    """A password offered to put the master key back in memory, not to start a session."""

    password: str = _Password


class PasswordChangeRequest(Wire):
    old_password: str = _Password
    new_password: str = _Password


class PinSetRequest(Wire):
    pin: str = _Pin
    # Setting the PIN re-proves the password, so an unattended unlocked screen cannot plant one.
    current_password: str = _Password


class PinVerifyRequest(Wire):
    pin: str = _Pin


class GuestCreateRequest(Wire):
    """A new guest, named by an admin and given a first password by that admin.

    No email field: users are local, so there is nowhere to send an invitation or a reset. The
    admin tells the person the password, and they change it from their profile.
    """

    username: str = _Username
    password: str = _Password


class UserDisabledRequest(Wire):
    """Whether the user may sign in. Sent as the state it should be in rather than as a verb,
    so pressing the same switch twice is not two different requests."""

    disabled: bool


class LockResponse(Wire):
    """Which way locking shut the session.

    `locked`: alive, the PIN reopens it. `signed_out`: ended, the password comes back. `no_session`:
    nothing to shut, not a failure, as a panic control must not need reading. Named, so the screen
    does not re-decide where to land.
    """

    outcome: str


class PasswordUnlockRequest(Wire):
    """The user's password, to reopen a session that is locked rather than ended."""

    password: str


class PasswordResetRequest(Wire):
    """A password set for somebody else. The old one is not asked for, because an admin does not
    have it, which is the whole reason this exists and is admin-only."""

    new_password: str = _Password


class UsernameChangeRequest(Wire):
    """A new name for a user. Nothing else about it changes.

    No password field: the master key is wrapped by the password against a stored salt, never the
    name, so nothing is re-proved, and an admin renaming somebody else does not have it.
    """

    username: str = _Username


class UserResponse(Wire):
    """One user on the management screen.

    Nothing derived from the password or PIN: whether somebody set a PIN is their business, and
    the vault it opens is theirs.
    """

    id: str
    username: str
    role: str
    disabled: bool
    created_at: int


class GeneratedUserResponse(Wire):
    """A guest Sift invented, and the password that goes with it.

    The only time the password exists in the clear: never stored that way, returned or logged, so
    a lost one is reset. Its own shape so no user listing is ever built from one carrying it.
    """

    user: UserResponse
    password: str


class ViewerResponse(Wire):
    """Who the caller is, plus the token they must echo on state-changing requests.

    The role and `can_save_to_device` are courtesies for laying out the screen (an admin may always
    save, a guest when an admin allows), never trusted back: the server re-reads the role on every
    request and the save endpoint decides for itself.
    """

    id: str
    username: str
    role: str
    csrf_token: str
    can_save_to_device: bool
    #: Whether this user's saved cookies and tunnels cannot be read right now: true after a restart
    #: for a valid session, since the decrypting key was only in memory, so a screen asks for the
    #: password before a form rather than refusing after it.
    secrets_locked: bool = False
    #: Whether the lock screen offers the PIN beside the password (the setting on and a PIN set),
    #: resolved on the server. The password reopens a lock either way.
    pin_unlock_offered: bool = False
    #: Whether Sift is locked on THIS session: `me` is one of the few routes a locked session may
    #: call, so the lock screen shows before any library appears.
    locked: bool = False
    #: The server machine's IANA zone, or null: every date the client writes is in it, never the
    #: browser's, so a phone elsewhere reads the library's days (`kernel/when.py`).
    zone: str | None = Field(default_factory=zone_name)
    #: Which run of the server answered (`lifecycle.BOOT_ID`), for an admin only, as `/health` gives
    #: it: the unlock bar's Not now holds across a page reload but not a restart, which reseals the
    #: keys. Says only that a restart happened.
    boot: str | None = None


class PasswordCheckRequest(Wire):
    """A password somebody is typing, sent to be judged rather than to be used.

    No user named, nothing written: the policy's answer as it would be on submit, since the
    browser's meter cannot check leaked passwords and would call `Password123!` strong.
    """

    password: str = Field(max_length=1024)


class PasswordCheckResponse(Wire):
    """What the policy makes of it. `reason` is the sentence the refusal would carry."""

    acceptable: bool
    breached: bool
    reason: str | None = None


class SetupStatusResponse(Wire):
    """Whether this instance still needs its one admin created.

    Public: the sign-in screen must choose its form before anyone signs in, and posting to setup
    reveals the same fact anyway.
    """

    needs_setup: bool
