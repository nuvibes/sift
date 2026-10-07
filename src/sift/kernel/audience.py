# SPDX-License-Identifier: AGPL-3.0-or-later
"""Whose view a change moved.

The picture-address stamp (so a browser's copies stop being reachable) and the change bus (so an
open connection asks again) both need this answer, so it has its own home rather than the bus
riding on the stamp's call sites, where a change to caching would silently change who is told. An
`Audience` holds only users a write RESOLVED, or one of the reaches below: telling a connection that
a person changed tells it the person exists, so a guessed list is never allowed.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace

from sift.kernel.db import Row


@dataclass(frozen=True, slots=True)
class Audience:
    """The users whose view of the library one change moved.

    Often empty, the ordinary case: a tag on a file nobody shared or hid moves nobody, which is
    why resolving it is affordable per file of a scan.
    """

    #: Users a statement named, one at a time.
    users: frozenset[str]

    #: Every admin, resolved at the moment of sending (a connection re-reads its user every beat),
    #: never a stale second copy of who is an admin. For changes that name no user, such as the
    #: queue moving or a collection made, which are admin business.
    every_admin: bool = False

    def __bool__(self) -> bool:
        return bool(self.users) or self.every_admin

    def __or__(self, other: Audience) -> Audience:
        """Both audiences together.

        For a write that changes several things together: one announcement to everybody moved,
        not the same message per row.
        """
        return Audience(
            self.users | other.users,
            every_admin=self.every_admin or other.every_admin,
        )

    def widened_to_admins(self) -> Audience:
        """This audience, and every admin as well. Reads better than an `or` at a call site."""
        return replace(self, every_admin=True)

    @classmethod
    def of(cls, rows: Iterable[Row]) -> Audience:
        """The users a statement reported touching.

        Read by position: these rows come straight off the driver, and every statement making one
        returns the single id column.
        """
        return cls(frozenset(str(row[0]) for row in rows))

    @classmethod
    def of_user(cls, user_id: str) -> Audience:
        """The one user who made a change, told about their own.

        The one honest case for naming rather than resolving: their own opinion (a heart, a star,
        a saved search), written by them a moment ago, tells them nothing new.
        """
        return cls(frozenset({user_id}))


#: What a change that moved nobody's view resolved to. Named so a caller can say so.
NOBODY = Audience(frozenset())

#: Every admin, whoever that is at the moment of sending. See `Audience.every_admin`.
EVERY_ADMIN = Audience(frozenset(), every_admin=True)
