# SPDX-License-Identifier: AGPL-3.0-or-later
"""Whose view a change moved: only users a write resolved, since naming one reveals them."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace

from sift.kernel.db import Row


@dataclass(frozen=True, slots=True)
class Audience:
    """The users whose view of the library one change moved; often empty."""

    users: frozenset[str]

    #: Resolved when sent, never a stale copy of who is an admin.
    every_admin: bool = False

    def __bool__(self) -> bool:
        return bool(self.users) or self.every_admin

    def __or__(self, other: Audience) -> Audience:
        """Both audiences together, for one announcement per write."""
        return Audience(
            self.users | other.users,
            every_admin=self.every_admin or other.every_admin,
        )

    def widened_to_admins(self) -> Audience:
        return replace(self, every_admin=True)

    @classmethod
    def of(cls, rows: Iterable[Row]) -> Audience:
        """The users a statement reported touching, read by position from its one id column."""
        return cls(frozenset(str(row[0]) for row in rows))

    @classmethod
    def of_user(cls, user_id: str) -> Audience:
        """The one user who made a change, told about their own."""
        return cls(frozenset({user_id}))


NOBODY = Audience(frozenset())

EVERY_ADMIN = Audience(frozenset(), every_admin=True)
