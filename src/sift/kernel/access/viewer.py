# SPDX-License-Identifier: AGPL-3.0-or-later
"""Who is asking: an identity resolved per request, holding no grants, so a revoke denies the next
request."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Role(StrEnum):
    ADMIN = "admin"
    GUEST = "guest"


class Effect(StrEnum):
    """What a grant does. No row is private, which a share elsewhere can override; RESTRICT never
    can be."""

    SHARE = "share"
    RESTRICT = "restrict"


class ObjectType(StrEnum):
    """What a grant can be attached to. Physical objects nest and inherit; logical ones do not, but
    a network Site reaches its labels."""

    GLOBAL = "global"
    ROOT = "root"
    FOLDER = "folder"
    ITEM = "item"

    TAG = "tag"
    PERSON = "person"
    COLLECTION = "collection"
    SITE = "site"
    PHOTO_SET = "photo_set"
    SONG = "song"


class ConcealerType(StrEnum):
    """What kind of thing is hiding something: what a hide can attach to, which is not what a grant
    can name."""

    ROOT = "root"
    FOLDER = "folder"
    ITEM = "item"

    TAG = "tag"
    PERSON = "person"
    COLLECTION = "collection"
    SITE = "site"
    PHOTO_SET = "photo_set"
    SONG = "song"


PHYSICAL_TYPES = frozenset({ObjectType.GLOBAL, ObjectType.ROOT, ObjectType.FOLDER, ObjectType.ITEM})
LOGICAL_TYPES = frozenset(
    {
        ObjectType.TAG,
        ObjectType.PERSON,
        ObjectType.COLLECTION,
        ObjectType.SITE,
        ObjectType.PHOTO_SET,
        ObjectType.SONG,
    }
)


class Concealment(StrEnum):
    """How the vault hides what is in it: absent everywhere, or a blurred tile. Its bytes are never
    served while it is shut."""

    FULLY_GONE = "fully_gone"
    PLACEHOLDER = "placeholder"


@dataclass(frozen=True, slots=True)
class Viewer:
    """An identity resolved for this one request; `show_hidden` is this session's and defaults
    closed."""

    id: str
    role: Role
    show_hidden: bool = False
    concealment: Concealment = Concealment.FULLY_GONE

    cache_stamp: int = 0
    """How many times what this user may see has changed.

    Unlike the two fields above this one IS stored, on the user rather than on the session, and
    it is read fresh here on every request for the same reason the role is. It rides in the address
    of every picture Sift generates, so raising it makes each address the user was given
    unreachable, which is what stops a copy already in a browser outliving the permission that
    put it there. Never a permission itself: the address is a cache key, and the check still runs
    on every request that reaches the server."""

    @property
    def is_admin(self) -> bool:
        return self.role is Role.ADMIN


def reveals_existence(viewer: Viewer) -> bool:
    """Whether a concealed file comes back for this viewer, as a locked tile or in full: the
    `:reveal` every count binds."""
    return viewer.show_hidden or viewer.concealment is Concealment.PLACEHOLDER
