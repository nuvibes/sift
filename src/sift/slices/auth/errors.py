# SPDX-License-Identifier: AGPL-3.0-or-later
"""The failures the auth service raises, each mapped to one HTTP status by the router."""

from __future__ import annotations

# The login errors are deliberately interchangeable.


class AuthError(Exception):
    """Base for the failures this slice raises."""


class SetupAlreadyDone(AuthError):
    """An admin already exists; setup running once is the anti-takeover guard."""


class InvalidCredentials(AuthError):
    """A username or password did not match: one error, so nothing says which part."""


class LockedOut(AuthError):
    """Too many failures in a row. The target is locked for a few minutes."""


class SignInBusy(AuthError):
    """A sign-in for this username from this address is already being checked. Try again later."""

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(
            "A sign-in for that username is already being checked. Try again in a moment."
        )
        self.retry_after_seconds = retry_after_seconds


class UsernameTaken(AuthError):
    """The name is already a user's, said plainly to the admin who typed it."""


class TooManyRenames(AuthError):
    """This user has spent their name changes for the window. Applies to renaming yourself only."""


class NoSuchUser(AuthError):
    """No user has that id. Either it never existed or it has since been deleted."""


class NotAGuestUser(AuthError):
    """The user exists and is not a guest; refusing every admin here keeps the last admin."""


class MasterKeyCorrupted(AuthError):
    """The password verified but did not open the wrapped key: refuse rather than mint a new one."""
