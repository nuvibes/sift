# SPDX-License-Identifier: AGPL-3.0-or-later
"""Making and unmaking the grants the resolver already reads; this slice enforces nothing."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from sift.kernel.access import (
    AccessError,
    Effect,
    Named,
    ObjectType,
    Recording,
    Repository,
    Viewer,
    visibility_settled,
)
from sift.kernel.audience import Audience
from sift.kernel.changes import About, announce
from sift.kernel.db import Database, in_clause
from sift.kernel.ledger import NAME_NOW
from sift.kernel.log import get_logger, security_event
from sift.kernel.wiring import Part

log = get_logger(__name__)


class SharingError(Exception):
    """A grant will not be made or removed, and the message says why."""


class NoSuchSubject(SharingError):
    """There is no user with that id to share with."""


class NoSuchObject(SharingError):
    """The thing to share isn't there: a grant on it would record an act about nothing."""


class SubjectNotAGuest(SharingError):
    """The user is an admin, who sees everything: a grant would read as in force and do nothing."""


class InertGrant(SharingError):
    """The object named cannot carry a grant of that shape: an inert row would read as in force."""


_USER_ROLE = "SELECT role FROM users WHERE id = ?"

_WORDS: dict[ObjectType, str] = {
    ObjectType.ITEM: "file",
    ObjectType.FOLDER: "folder",
    ObjectType.TAG: "tag",
    ObjectType.PERSON: "person",
    ObjectType.COLLECTION: "collection",
    ObjectType.PHOTO_SET: "photo set",
    ObjectType.SITE: "site",
    ObjectType.SONG: "song",
}

#: One seek by id, never the whole user list: this runs on every press.
_USERNAME = "SELECT username FROM users WHERE id = ?"

#: The record's word for a grant's object; global, root and Site go against the user instead.
_SHARED_KINDS: dict[ObjectType, str] = {
    ObjectType.ITEM: "asset",
    ObjectType.FOLDER: "folder",
    ObjectType.TAG: "tag",
    ObjectType.PERSON: "person",
    ObjectType.COLLECTION: "collection",
    ObjectType.PHOTO_SET: "photo_set",
    ObjectType.SITE: "site",
    ObjectType.SONG: "song",
}

#: Names come from here, not the kernel's grants query: the resolver needs no usernames.
# By id, as the user list orders: a wall-clock second can go backwards and a ULID cannot.
_USERNAMES = "SELECT id, username, role FROM users ORDER BY id"

_ANY_OWED = "SELECT 1 AS owed FROM visibility_owed WHERE owed = 1 LIMIT 1"


@dataclass(frozen=True, slots=True)
class GrantView:
    """One grant, with the name of the user it was made to."""

    subject_user_id: str
    username: str
    effect: Effect
    created_at: int


@dataclass(frozen=True, slots=True)
class ShareableUser:
    """A user a grant could name, for the picker beside the share control."""

    id: str
    username: str
    role: str


class SharingService:
    """Grant production. One per application, reached at `app.state.sharing`."""

    def __init__(self, database: Database, access: Repository) -> None:
        self._db = database
        self._access = access
        self._folding: asyncio.Task[None] | None = None
        self._fold_asked = False

    async def users(self) -> list[ShareableUser]:
        """Everybody a grant could be made to, admins included and marked, in list order."""
        rows = await self._db.fetch_all(_USERNAMES)
        return [
            ShareableUser(id=str(row["id"]), username=str(row["username"]), role=str(row["role"]))
            for row in rows
        ]

    async def grants_on(self, object_type: ObjectType, object_id: str | None) -> list[GrantView]:
        """Who this one thing is shared with, and who it is restricted from."""
        try:
            grants = await self._access.grants_on(object_type, object_id)
        except AccessError as exc:
            raise InertGrant(str(exc)) from exc
        names = {user.id: user.username for user in await self.users()}
        return [
            GrantView(
                subject_user_id=grant.subject_user_id,
                # A missing name means a deleted user raced the read; kept so it can be revoked.
                username=names.get(grant.subject_user_id, "(removed user)"),
                effect=grant.effect,
                created_at=grant.created_at,
            )
            for grant in grants
        ]

    async def share(
        self,
        viewer: Viewer,
        object_type: ObjectType,
        object_id: str | None,
        subject_user_id: str,
        effect: Effect,
    ) -> list[GrantView]:
        """Make one grant, replacing the opposite effect, and hand back the whole panel."""
        await self._require_guest(subject_user_id)
        await self._require_object(object_type, object_id)
        opposite = Effect.RESTRICT if effect is Effect.SHARE else Effect.SHARE
        made = await self._recording(viewer, "shared", object_type, object_id, subject_user_id)
        await self._db.execute(visibility_settled.MAY_DEFER)
        try:
            # The opposite is cleared with no event: the share's own event says what happened.
            await self._access.revoke(object_type, object_id, subject_user_id, opposite)
            await self._access.grant(object_type, object_id, subject_user_id, effect, event=made)
        except AccessError as exc:
            raise InertGrant(str(exc)) from exc
        finally:
            await self._db.execute(visibility_settled.NO_DEFER)
        security_event(
            "sharing.granted",
            object_type=object_type.value,
            object_id=object_id,
            subject_user_id=subject_user_id,
            effect=effect.value,
        )
        await self._fold_soon()
        return await self.grants_on(object_type, object_id)

    async def revoke(
        self,
        viewer: Viewer,
        object_type: ObjectType,
        object_id: str | None,
        subject_user_id: str,
        effect: Effect,
    ) -> list[GrantView]:
        """Take one grant back, effective on the subject's very next request."""
        await self._require_object(object_type, object_id)
        taken = await self._recording(viewer, "unshared", object_type, object_id, subject_user_id)
        await self._db.execute(visibility_settled.MAY_DEFER)
        try:
            await self._access.revoke(object_type, object_id, subject_user_id, effect, event=taken)
        except AccessError as exc:
            raise InertGrant(str(exc)) from exc
        finally:
            await self._db.execute(visibility_settled.NO_DEFER)
        security_event(
            "sharing.revoked",
            object_type=object_type.value,
            object_id=object_id,
            subject_user_id=subject_user_id,
            effect=effect.value,
        )
        await self._fold_soon()
        return await self.grants_on(object_type, object_id)

    async def _fold_soon(self) -> None:
        """File a large widening's pairs and fold its counts after the press, a page at a time."""
        if (
            await self._db.fetch_one(visibility_settled.ANY_FILING) is None
            and await self._db.fetch_one(_ANY_OWED) is None
        ):
            return
        self._fold_asked = True
        if self._folding is None or self._folding.done():
            self._folding = asyncio.get_running_loop().create_task(self._fold())

    async def _fold(self) -> None:
        try:
            while self._fold_asked:
                self._fold_asked = False
                while await self._file_page() or await self._fold_page():
                    pass
        except Exception:
            log.exception("sharing.fold_failed")

    async def _file_page(self) -> bool:
        """One page of a deferred widening filed, its files read first off the writer."""
        filing = await self._db.fetch_one(visibility_settled.NEXT_FILING)
        if filing is None:
            return False
        limit = visibility_settled.FILING_PAGE
        sql, values = visibility_settled.filing_page(filing, limit)
        files = [str(row["asset_id"]) for row in await self._db.fetch_all(sql, values)]
        async with self._db.write() as connection:
            await visibility_settled.file_page(connection, filing, files, limit)
            announce(Audience.of_user(str(filing["user_id"])), About.LIBRARY)
        return True

    async def _fold_page(self) -> bool:
        async with self._db.write() as connection:
            moved = await visibility_settled.fold_owed(
                connection, visibility_settled.OWED_FOLD_PAGE
            )
            if moved:
                announce(moved, About.LIBRARY)
                log.info("sharing.counts_folded", users=len(moved.users))
        return bool(moved)

    async def _recording(
        self,
        viewer: Viewer,
        verb: str,
        object_type: ObjectType,
        object_id: str | None,
        subject_user_id: str,
    ) -> Recording:
        """What to write down, built before the write so a revoke still names the user."""
        named = _SHARED_KINDS.get(object_type)
        # `login` (a user who signs in) and not `username`, which is a name on a site.
        user = Named("login", subject_user_id, await self._username(subject_user_id))
        if named is None or object_id is None:
            return Recording(
                user_id=viewer.id,
                verb=verb,
                subjects=(user,),
                object=Named("grant", object_type.value),
            )
        return Recording(
            user_id=viewer.id,
            verb=verb,
            subjects=(Named(named, object_id),),
            object=user,
        )

    async def _username(self, subject_user_id: str) -> str | None:
        """What the user is called, for the snapshot. None when it has gone between the two."""
        row = await self._db.fetch_one(_USERNAME, (subject_user_id,))
        return None if row is None else str(row["username"])

    async def _require_object(self, object_type: ObjectType, object_id: str | None) -> None:
        """The thing a grant is about, proved to be there; a grant on a whole kind names nothing."""
        kind = _SHARED_KINDS.get(object_type)
        if object_id is None or kind is None:
            return
        asked, values = in_clause(NAME_NOW[kind], [object_id])
        if await self._db.fetch_one(asked, values) is None:
            raise NoSuchObject(f"there's no such {_WORDS[object_type]}")

    async def _require_guest(self, subject_user_id: str) -> None:
        """The user a grant is about to name, proved to be one a grant can mean something to."""
        row = await self._db.fetch_one(_USER_ROLE, (subject_user_id,))
        if row is None:
            raise NoSuchSubject("there is no such user")
        if str(row["role"]) != "guest":
            raise SubjectNotAGuest("an admin already sees everything, so a grant would do nothing")


SERVICE: Part[SharingService] = Part("sharing")
