# SPDX-License-Identifier: AGPL-3.0-or-later
"""Share and Restrict: the surface over the access engine.

Sift's permission model is three-valued and fail-closed. A guest sees nothing by default; a share
reaches things; a restrict beats every share, everywhere, always. It is compiled into the queries
and re-resolved on every request, so taking a share back denies the next request rather than the
next sign-in. None of that is here.

What is here is the way to make a grant. Without it guests would be a role nobody could hold,
over a library nobody could be shown anything of. This slice is the door: pick a thing, pick a
guest, Share it or Restrict it, see who else has it, take it back.

**Two words, and they are not opposites of each other.** Private is the default and means no row:
a guest cannot reach it, but a share made somewhere else still might. Restrict is a row that says
never: nothing overrides it, and it is the tool for "not this, whatever else I share". That
distinction is the whole reason the model has three states rather than two, and it is why the panel
shows both effects rather than a switch.

This slice owns no table and enforces nothing. It writes rows the resolver reads.
"""

from __future__ import annotations

from sift.slices.sharing.router import router
from sift.slices.sharing.service import (
    SERVICE,
    GrantView,
    InertGrant,
    NoSuchSubject,
    ShareableUser,
    SharingError,
    SharingService,
    SubjectNotAGuest,
)

__all__ = [
    "SERVICE",
    "GrantView",
    "InertGrant",
    "NoSuchSubject",
    "ShareableUser",
    "SharingError",
    "SharingService",
    "SubjectNotAGuest",
    "router",
]
