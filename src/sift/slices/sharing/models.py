# SPDX-License-Identifier: AGPL-3.0-or-later
"""The shapes the sharing panel sends and receives."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from sift.kernel.access import ConcealerType, Effect, ObjectType
from sift.kernel.wire import Wire

# Bounded so nothing absurd reaches a query; absent for the global object, which names nothing.
_ObjectId = Field(default=None, max_length=64)
_UserId = Field(min_length=1, max_length=64)


class GrantWrite(Wire):
    """One share or restrict, made or taken back."""

    object_type: ObjectType
    object_id: str | None = _ObjectId
    subject_user_id: str = _UserId
    effect: Effect


class GrantResponse(Wire):
    """One grant as the panel draws it."""

    subject_user_id: str
    username: str
    effect: Effect
    created_at: int


class GrantSourceResponse(Wire):
    """One grant that reaches this thing, and what it was made on."""

    subject_user_id: str
    username: str
    effect: Effect
    source_type: ObjectType
    source_id: str | None
    source_name: str | None
    here: bool
    #: A loser (a share beaten by a restrict) is shown but must not read as in force.
    decides: bool


class VaultSourceResponse(Wire):
    """One thing the caller has hidden that is concealing this, and what to call it."""

    source_type: ConcealerType
    source_id: str | None
    source_name: str | None
    here: bool


class ShareableUserResponse(Wire):
    """A user the share control can offer."""

    id: str
    username: str
    role: str


class ReachThrough(Wire):
    """One decision behind a user's answer, reshaped from the panel's rows so the two agree."""

    kind: ObjectType
    id: str | None
    name: str | None
    how: Literal["shared", "restricted", "inherited"]
    decides: bool


class ReachUser(Wire):
    """One user, whether they can see the subject, and what puts them there."""

    id: str
    name: str
    role: str
    disabled: bool
    sees: bool
    through: list[ReachThrough]


class OutsideReach(Wire):
    """The two ways something about a thing can leave this device, and where each stands."""

    enrich_refused_here: bool
    enrich_refused: bool
    enriched_at: int | None = None
    enriched_by: str | None = None
    swap_refused_here: bool
    swap_refused: bool


class ReachReport(Wire):
    """Who can see one thing, and through what; hides are the caller's own and false while shut."""

    subject_type: ObjectType
    subject_id: str | None
    hidden: bool
    concealed: bool
    users: list[ReachUser]
    outside: OutsideReach | None = None


class ReachReasonResponse(Wire):
    """One reason a page of an entity's files is reachable, and how many of them it explains."""

    kind: ObjectType
    id: str | None = _ObjectId
    name: str | None = None
    files: int


class ReachThroughReport(Wire):
    """Why one user can see one entity when nothing was said about the entity itself."""

    reasons: list[ReachReasonResponse]
    files: int
    complete: bool
