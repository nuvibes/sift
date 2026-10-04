# SPDX-License-Identifier: AGPL-3.0-or-later
"""The shapes the sharing panel sends and receives.

The object type is an enumeration rather than a string, so a typo is a 422 at the edge instead of
a row naming a kind of thing that does not exist. The same goes for the effect: there are two, and
which one it is decides whether a row is a favour or a promise.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from sift.kernel.access import ConcealerType, Effect, ObjectType
from sift.kernel.wire import Wire

# An object id is an identifier, not free text. Bounded so nothing absurd reaches a query; absent
# entirely for the global object, which names nothing.
_ObjectId = Field(default=None, max_length=64)
_UserId = Field(min_length=1, max_length=64)


class GrantWrite(Wire):
    """One share or restrict, made or taken back.

    The same shape for both directions, because the caller is describing a decision rather than an
    operation: which thing, for whom, and which of the two effects.
    """

    object_type: ObjectType
    object_id: str | None = _ObjectId
    subject_user_id: str = _UserId
    effect: Effect


class GrantResponse(Wire):
    """One grant as the panel draws it.

    The username rides along because the panel's job is recognizing who you shared with, and a
    user id is not something anybody recognizes.
    """

    subject_user_id: str
    username: str
    effect: Effect
    created_at: int


class GrantSourceResponse(Wire):
    """One grant that reaches this thing, and what it was made on.

    `source_type` is the kind of thing the decision was made on, and `source_name` is what to call
    it: absent for the global grant, which names nothing, and for a grant on the thing being asked
    about, which the panel is already showing the name of.
    """

    subject_user_id: str
    username: str
    effect: Effect
    #: The server's list of what a grant can name, typed so a kind the panel has no word for fails
    #: loudly rather than leaving a hole in a sentence.
    source_type: ObjectType
    source_id: str | None
    source_name: str | None
    #: Whether it was made on the very thing asked about, rather than on something above it.
    here: bool
    #: Whether this grant decided the answer; a loser (a share beaten by a restrict) is shown but
    #: must not read as in force.
    decides: bool


class VaultSourceResponse(Wire):
    """One thing the caller has hidden that is concealing this, and what to call it.

    Beside the grants, not among them: a hide is about the caller's own screen, withheld from them
    alone, so the panel draws it as its own block.
    """

    #: What a HIDE can attach to (`ConcealerType`): `ObjectType` less "everything in Sift", which
    #: nobody hides.
    source_type: ConcealerType
    source_id: str | None
    source_name: str | None
    #: Set on the thing asked about rather than above it: a solid mark here, hollow above.
    here: bool


class ShareableUserResponse(Wire):
    """A user the share control can offer.

    The role lets the control grey out an admin with a reason, where leaving them off would read as
    "no such user".
    """

    id: str
    username: str
    role: str


class ReachThrough(Wire):
    """One decision that explains a user's answer, worded as the chain the panel reads out.

    A RESHAPING of the `GrantSourceResponse` rows, never a second read, so it cannot disagree with
    the sharing panel. `decides` keeps the losers honest: a share under a restrict is worth seeing,
    but must not read as in force.
    """

    kind: ObjectType
    id: str | None
    name: str | None
    #: Shared on the thing, inherited from what it belongs to or sits in, or restricted: the server
    #: says both halves so the report decides nothing itself. A restrict is one word either way.
    how: Literal["shared", "restricted", "inherited"]
    decides: bool


class ReachUser(Wire):
    """One user, whether they can see the subject, and what puts them there."""

    id: str
    name: str
    #: admin or guest: an admin sees by role, which the report must not draw as a share.
    role: str
    #: Switched off: keeps its grants but can use none, so `sees` is false; this says which reason.
    disabled: bool
    sees: bool
    through: list[ReachThrough]


class OutsideReach(Wire):
    """The two ways something about a thing can leave this device, and where each stands.

    A stash-box lookup and another Sift in a swap. `*_here` is the switch on the thing itself; the
    plain word is the whole rule (for a file, anything it is filed under; a swap also honours "Do
    not enrich"), both through the kernel's one refusal (`catalog.refused_here` / `refused_over`).
    """

    enrich_refused_here: bool
    enrich_refused: bool
    #: The last time a stash-box filled anything in about it, and which, where one has.
    enriched_at: int | None = None
    enriched_by: str | None = None
    swap_refused_here: bool
    swap_refused: bool


class ReachReport(Wire):
    """Who can see one thing, and through what.

    The panel behind "can anybody but me see this": a file is often handed over by a share on a
    tag, collection, folder or network above it, none of which shows on the thing itself.
    `hidden` and `concealed` are the caller's OWN vault, deliberately: hiding is personal, so
    another user's hides are not an answer about permission and are theirs to keep. Both are false
    while the caller's Hidden is shut, since the names of what conceals a thing are the concealment.
    """

    subject_type: ObjectType
    subject_id: str | None
    #: The caller hid this very thing.
    hidden: bool
    #: ...or something it belongs to, or sits in, is hiding it from them.
    concealed: bool
    users: list[ReachUser]
    #: Who OUTSIDE this device can learn of it; None for a kind neither is ever told about.
    outside: OutsideReach | None = None


class ReachReasonResponse(Wire):
    """One reason a page of an entity's files is reachable, and how many of them it explains."""

    kind: ObjectType
    id: str | None = _ObjectId
    name: str | None = None
    #: How many files of the page this reason covers. Out of `ReachThroughReport.files`.
    files: int


class ReachThroughReport(Wire):
    """WHY one user can see one entity, when nothing was ever said about the entity.

    Asked per user where the first half says yes with no grant behind it: an entity is on somebody's
    wall because ONE file under it is reachable, through a folder or another tag never said about
    the entity, so this names the files' reasons. `files` and `complete` say how many were looked at
    and whether that was all, since a reason's count reads only beside its denominator.
    """

    reasons: list[ReachReasonResponse]
    files: int
    complete: bool
