# SPDX-License-Identifier: AGPL-3.0-or-later
"""Making and unmaking the grants the resolver already reads.

This slice enforces nothing. That is worth saying first, because a module named for sharing looks
exactly like the place somebody would add an access check, and adding one here would be adding a
second answer to a question that already has one. Whether a guest may see a file is decided in the
resolver, compiled into the query, on every request: guests see nothing unless a share reaches
them, and any restrict anywhere beats every share. What this slice adds is the way to *make*
the rows it reads.

So what is here is production and nothing else: write a grant, remove a grant, list the grants on
one thing. The one piece of judgement it does exercise is refusing grants that could never mean
anything, and it refuses them for the same reason the engine refuses a folder grant with no folder:
an inert row is worse than no row, because the screen lists it as being in force.
"""

from __future__ import annotations

from dataclasses import dataclass

from sift.kernel.access import (
    AccessError,
    Effect,
    Named,
    ObjectType,
    Recording,
    Repository,
    Viewer,
)
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
    """The thing to share isn't there: a grant on it would record an act about nothing.

    Checked before the write, in the sharer's words, because the record refuses to write a subject
    it can neither be handed a name for nor look one up, and that refusal would surface as a
    crash where the answer is that the thing has gone."""


class SubjectNotAGuest(SharingError):
    """The user is an admin, and an admin already sees everything.

    Refused rather than stored, because the row would do nothing whatsoever: the resolver lets an
    admin past the access rules entirely, so neither a share nor a restrict made to one has any
    effect. A restrict especially: an admin would read "restricted" on the screen and believe
    something had been walled off from them that had not been.
    """


class InertGrant(SharingError):
    """The object named cannot carry a grant of that shape.

    The global object names nothing and everything else names something. Stored the wrong way
    round the row sits in the table doing nothing while the sharing panel reports it as in force.
    """


_USER_ROLE = "SELECT role FROM users WHERE id = ?"

#: What each kind is called in a refusal, in the words a person uses.
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

#: One user's name, for the snapshot a grant's event carries. By id and never by the list above:
#: the list is a page of everybody and this is one seek, on a route that runs on every press.
_USERNAME = "SELECT username FROM users WHERE id = ?"

#: What a grant's object is called in the record's own vocabulary.
#:
#: Two lists meet here and they are not the same length. `ObjectType` is what a grant can be
#: attached to; `SubjectKind` is what an event can be about. Six of the ten line up word for
#: word, `item` is what the record calls an `asset`, and three (global, root and the site)
#: have no word at all.
#:
#: A missing word is not a missing event. Those three are recorded against the USER instead,
#: with what was shared written into the payload: an event with no subject is an event nobody can
#: ever reach, and "everything" or "this library" is a share whose only nameable party is the
#: person it was made to. A word for a library would be a change to the kernel's vocabulary, not
#: something a slice decides for itself.
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

#: The names to put beside the grants. Read here rather than joined into the grants query because
#: the grants query belongs to the kernel, and who a user is by name is this feature's
#: business: the resolver has never needed a username and should not start.
# By ID, for the reason the user list gives: a wall-clock second can go backwards and a
# ULID cannot. The two lists name the same users and must not disagree about their order.
_USERNAMES = "SELECT id, username, role FROM users ORDER BY id"


@dataclass(frozen=True, slots=True)
class GrantView:
    """One grant, with the name of the user it was made to.

    The name is here because a list of user ids is not something anybody can act on, and the
    action this list exists for is revoking, which means recognizing who you shared with.
    """

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

    async def users(self) -> list[ShareableUser]:
        """Everybody a grant could be made to, in the order the user list shows them.

        Admins are included and marked. The picker greys them out rather than hiding them, because
        an admin looking for somebody who is not in the list needs to be able to tell "not a
        user here" from "a user that this control cannot do anything about".
        """
        rows = await self._db.fetch_all(_USERNAMES)
        return [
            ShareableUser(id=str(row["id"]), username=str(row["username"]), role=str(row["role"]))
            for row in rows
        ]

    async def grants_on(self, object_type: ObjectType, object_id: str | None) -> list[GrantView]:
        """Who this one thing is shared with, and who it is restricted from.

        Both effects, because both are decisions somebody made and both are decisions somebody may
        want to take back. A panel showing only the shares would quietly hide the restricts, which
        are the ones that were meant to be a promise.
        """
        try:
            grants = await self._access.grants_on(object_type, object_id)
        except AccessError as exc:
            raise InertGrant(str(exc)) from exc
        names = {user.id: user.username for user in await self.users()}
        return [
            GrantView(
                subject_user_id=grant.subject_user_id,
                # A user deleted between the two reads would have taken its grants with it by
                # foreign key, so a missing name here means a race rather than a leak. Named for
                # what it is instead of dropped, so the row can still be revoked by hand.
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
        """Make one grant, and hand back the whole panel afterwards.

        The whole panel rather than the one row, because that is what the screen has to redraw and
        because a share and a restrict on the same thing are separate rows that resolve against
        each other: somebody who has just added a restrict inside a share needs to see both, not
        a confirmation of the half they typed.

        Re-granting what is already granted is not an error. The control says what the state should
        be, so pressing it twice is one decision made twice.

        **The two effects are mutually exclusive on one object for one user, and the new one
        replaces the old.** Both at once resolves to Restricted (a restrict beats every share),
        so a share stored underneath one is a row that does nothing, for as long as it sits there,
        and then quietly takes effect the day the restrict is lifted. That is a decision nobody
        made showing up much later. Saying "share this with them" now means exactly that, whatever
        was said before.
        """
        await self._require_guest(subject_user_id)
        await self._require_object(object_type, object_id)
        opposite = Effect.RESTRICT if effect is Effect.SHARE else Effect.SHARE
        made = await self._recording(viewer, "shared", object_type, object_id, subject_user_id)
        try:
            # The opposite is cleared first and carries NO event: nothing was decided about it, the
            # whole point of the pair is that saying "share this" means exactly that whatever was
            # said before, and a "unshared" row here would put an act in the record that nobody
            # took. The share's own event says what happened.
            await self._access.revoke(object_type, object_id, subject_user_id, opposite)
            await self._access.grant(object_type, object_id, subject_user_id, effect, event=made)
        except AccessError as exc:
            raise InertGrant(str(exc)) from exc
        security_event(
            "sharing.granted",
            object_type=object_type.value,
            object_id=object_id,
            subject_user_id=subject_user_id,
            effect=effect.value,
        )
        return await self.grants_on(object_type, object_id)

    async def revoke(
        self,
        viewer: Viewer,
        object_type: ObjectType,
        object_id: str | None,
        subject_user_id: str,
        effect: Effect,
    ) -> list[GrantView]:
        """Take one grant back. It applies to the subject's very next request, not their next
        sign-in: nothing about a permission is carried in a session.

        Removing a grant that is not there is not an error either, for the same reason making one
        twice is not: the caller is saying what the state should be.
        """
        await self._require_object(object_type, object_id)
        taken = await self._recording(viewer, "unshared", object_type, object_id, subject_user_id)
        try:
            await self._access.revoke(object_type, object_id, subject_user_id, effect, event=taken)
        except AccessError as exc:
            raise InertGrant(str(exc)) from exc
        security_event(
            "sharing.revoked",
            object_type=object_type.value,
            object_id=object_id,
            subject_user_id=subject_user_id,
            effect=effect.value,
        )
        return await self.grants_on(object_type, object_id)

    async def _recording(
        self,
        viewer: Viewer,
        verb: str,
        object_type: ObjectType,
        object_id: str | None,
        subject_user_id: str,
    ) -> Recording:
        """What to write down, built BEFORE the write and with the user's name in it.

        Before, because a revoke deletes the row: afterwards there is nothing left to read, and the
        one question somebody brings to a record of shares (who could see this, and when did they
        stop) is answered by a name or by nothing at all. The user still exists, but a user
        can be deleted too, and the record is not allowed to depend on it.

        The user is the OBJECT and the thing shared is the subject, which is the way round the
        sentence reads: "shared this collection with Wren Aldabry". A reader on the collection's own
        page wants the name of the person in the line; a reader on the user wants the grant.
        """
        named = _SHARED_KINDS.get(object_type)
        # `login` (a user who signs in) and not `username`, which is a name on a site. See
        # `SubjectKind`.
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
        """The thing a grant is about, proved to be there. A grant on a whole kind names nothing.

        Read through the same statement the record reads a name with, so the two can never
        disagree about what "there" means.
        """
        kind = _SHARED_KINDS.get(object_type)
        if object_id is None or kind is None:
            return
        asked, values = in_clause(NAME_NOW[kind], [object_id])
        if await self._db.fetch_one(asked, values) is None:
            raise NoSuchObject(f"there's no such {_WORDS[object_type]}")

    async def _require_guest(self, subject_user_id: str) -> None:
        """The user a grant is about to name, proved to be one a grant can mean something to.

        Checked on the way in rather than left to the foreign key. The key would catch an id that
        names nobody, as a database error at write time; it would not catch an admin, and that is
        the one that reads as working.
        """
        row = await self._db.fetch_one(_USER_ROLE, (subject_user_id,))
        if row is None:
            raise NoSuchSubject("there is no such user")
        if str(row["role"]) != "guest":
            raise SubjectNotAGuest("an admin already sees everything, so a grant would do nothing")


#: Grants, and who holds them.
SERVICE: Part[SharingService] = Part("sharing")
