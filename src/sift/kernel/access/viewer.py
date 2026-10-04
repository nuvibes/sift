# SPDX-License-Identifier: AGPL-3.0-or-later
"""Who is asking.

Every read of an asset takes one of these. Not "is someone logged in": that question has the
same answer for an admin and for a guest who was shared two files, and answering it is not
authorization.

A `Viewer` is built fresh from the database on each request and thrown away with it. It holds no
grants and caches no answer: what it carries is an identity, and every permission is looked up
from that identity at the moment it is needed. Revoking a share therefore denies the very next
request rather than the next login, which for a session that lasts a month is the whole point.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Role(StrEnum):
    ADMIN = "admin"
    GUEST = "guest"


class Effect(StrEnum):
    """What a grant does.

    Two effects, three states. The third state is the absence of a row, and it is not the same
    as RESTRICT: it means private, which keeps a guest out by default but can still be overridden
    by a share made somewhere else. RESTRICT means never, and nothing overrides it.
    """

    SHARE = "share"
    RESTRICT = "restrict"


class ObjectType(StrEnum):
    """What a grant can be attached to.

    The physical objects nest (an asset sits in a folder, inside a root, inside everything),
    so a grant on one is inherited by what is under it. A restrict anywhere above is absolute;
    among shares, the nearest one wins.

    The logical objects do not nest and do not inherit, with one exception: a Site can sit under
    a network Site, and a grant on the network reaches the label (`sites.SITE_REACH`). An asset
    either belongs to the tag or it does not.
    """

    GLOBAL = "global"
    ROOT = "root"
    FOLDER = "folder"
    ITEM = "item"

    TAG = "tag"
    PERSON = "person"
    COLLECTION = "collection"
    SITE = "site"
    #: A shoot: the pictures that arrived together. A grant on one reaches its pictures through
    #: the membership rows, exactly as a collection's does.
    PHOTO_SET = "photo_set"
    #: One piece of music: a grant on it reaches the files that carry it, as a Photo Set's does.
    SONG = "song"


class ConcealerType(StrEnum):
    """What kind of thing is hiding something from the user who hid it.

    Not `ObjectType`, and the difference is the point rather than an inconvenience. `ObjectType` is
    what a GRANT can name; this is what a HIDE can be attached to, and the two lists differ. A photo
    set is one and not the other (it can be hidden and nothing can be shared on it), so a single
    enum could only carry it by implying a grant that does not exist, and the mismatch would raise
    deep inside the read that answers "why can I not see this", which is a screen somebody reaches
    precisely when they are already confused.
    """

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
    """How the vault hides what is in it.

    FULLY_GONE is the default and means what it says: the item is absent from the grid, from
    search, from counts, from recently-viewed. Not greyed out, not a padlock: absent, so that
    nothing on screen says there is something you are not being shown.

    PLACEHOLDER keeps the tile and blurs the contents, which is the right answer for someone who
    would rather see a gap than wonder whether one is there. Either way the bytes are not served
    until the vault is unlocked.
    """

    FULLY_GONE = "fully_gone"
    PLACEHOLDER = "placeholder"


@dataclass(frozen=True, slots=True)
class Viewer:
    """An identity, resolved from the database for this one request.

    `show_hidden` is not a permission and is not stored: it is a fact about this session, set
    when the vault was unlocked and gone when the session ends. The defaults are the closed ones,
    so a `Viewer` built without thinking about the vault conceals it.
    """

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
    """Whether a concealed FILE comes back for this viewer at all, as a locked tile or in full.

    Unlocking the vault reveals it; so does asking for placeholders, which keeps the tile and none
    of its content. It is the flag every count of files binds as `:reveal`, so a number beside a
    list counts exactly the tiles the list draws. And it is NOT the answer for anything that is
    nothing but a name (a person, a tag, a Site), which only an unlocked vault brings back.

    Published here so the repository and the faces service read one answer rather than a copy each.
    """
    return viewer.show_hidden or viewer.concealment is Concealment.PLACEHOLDER
