# SPDX-License-Identifier: AGPL-3.0-or-later
"""The shapes that cross the wire; password policy lives once, in the service."""

from __future__ import annotations

from pydantic import Field

from sift.kernel.when import zone_name
from sift.kernel.wire import Wire

# The NOCASE column folds case, so "Kate" and "kate" are one user.
_Username = Field(min_length=1, max_length=64)
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
    # Re-proved, so an unattended unlocked screen cannot plant a PIN.
    current_password: str = _Password


class PinVerifyRequest(Wire):
    pin: str = _Pin


class GuestCreateRequest(Wire):
    """A new guest, named and given a first password by an admin; users are local, so no email."""

    username: str = _Username
    password: str = _Password


class UserDisabledRequest(Wire):
    """Whether the user may sign in, as a state, so pressing twice is one request."""

    disabled: bool


class LockResponse(Wire):
    """Which way locking shut the session: `locked`, `signed_out` or `no_session`."""

    outcome: str


class PasswordUnlockRequest(Wire):
    """The user's password, to reopen a session that is locked rather than ended."""

    password: str


class PasswordResetRequest(Wire):
    """A password set for somebody else by an admin, who does not have the old one."""

    new_password: str = _Password


class UsernameChangeRequest(Wire):
    """A new name for a user; no password, since the key is never wrapped by the name."""

    username: str = _Username


class UserResponse(Wire):
    """One user on the management screen; nothing derived from the password or PIN."""

    id: str
    username: str
    role: str
    disabled: bool
    created_at: int


class GeneratedUserResponse(Wire):
    """A guest Sift invented, with the only clear copy of its password."""

    user: UserResponse
    password: str


class ViewerResponse(Wire):
    """Who the caller is and the CSRF token; the role is a layout courtesy, never trusted back."""

    id: str
    username: str
    role: str
    csrf_token: str
    can_save_to_device: bool
    secrets_locked: bool = False
    pin_unlock_offered: bool = False
    locked: bool = False
    zone: str | None = Field(default_factory=zone_name)
    boot: str | None = None


class PasswordCheckRequest(Wire):
    """A password being typed, judged by the policy without being used."""

    password: str = Field(max_length=1024)


class PasswordCheckResponse(Wire):
    """What the policy makes of it. `reason` is the sentence the refusal would carry."""

    acceptable: bool
    breached: bool
    reason: str | None = None


class SetupStatusResponse(Wire):
    """Whether this instance still needs its one admin created; public by necessity."""

    needs_setup: bool
