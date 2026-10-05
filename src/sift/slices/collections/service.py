# SPDX-License-Identifier: AGPL-3.0-or-later
"""The writes behind a manual collection.

A collection is a set somebody put together by hand. That is what separates it from a tag, which
describes, and from a folder, which is where the file physically is. Nothing here derives
membership from a query: every row in `collection_items` is there because a person put it there.

**Nothing on disk moves.** Adding an item writes one row in a join table. The file keeps its path
and its bytes, exactly as attaching a tag does, and the same test asserts it.

Reads that have to be scoped by viewer are not here. The list, the single collection and a
collection's contents all come out of the access layer, which is the only thing that knows who is
asking. What is here is the writes, plus the unscoped lookups a write needs to check itself.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from sift.kernel.access import ObjectType, Repository, Viewer, bump_stamps_for_object
from sift.kernel.audience import EVERY_ADMIN, Audience
from sift.kernel.cache_stamp import bump_cache_stamp
from sift.kernel.changes import About, announce, telling
from sift.kernel.content.entity_state import opinion_before
from sift.kernel.content.user_state import OpinionKind, record_opinion
from sift.kernel.cover_frame import CoverFrame
from sift.kernel.covers import ChosenCover, chosen_from_row, cover_change
from sift.kernel.db import Database, Row
from sift.kernel.ids import new_id
from sift.kernel.ledger import ACTOR_USER, Actor, Object, record_event
from sift.kernel.log import get_logger
from sift.kernel.sorting import sort_key
from sift.kernel.vocabulary import Subject
from sift.kernel.wiring import Part

log = get_logger(__name__)


def _and_the_actor(told: Audience, actor: Actor) -> Audience:
    """The audience of a membership write, plus whoever made it.

    `bump_stamps_for_object` names the users whose VIEW the write changed, and on an install
    where nothing is shared that is nobody, so the user who put a file in a collection would
    never be told, and their own History threads and other tabs would go on showing the file where
    it was. The writer always hears of its own write; a pass of Sift's has no user to tell.
    """
    if actor.kind == ACTOR_USER and actor.id:
        return told | Audience.of_user(actor.id)
    return told


# The same `tags` table every other kind of thing uses. A tag called "archive" means the same thing
# wherever it is put, and two tag vocabularies that cannot see each other is what one table exists
# to prevent.
#: `added_at` is named and bound, never left to a default: the column is nullable so a writer that
#: forgot it would leave a row saying a tag was put here at no moment, which is indistinguishable
#: from a row written before the column existed.
_TAG = "INSERT OR IGNORE INTO collection_tags (collection_id, tag_id, added_at) VALUES (?, ?, ?)"
_UNTAG = "DELETE FROM collection_tags WHERE collection_id = ? AND tag_id = ?"

_TAGS_OF_COLLECTION = """
SELECT t.* FROM tags t
  JOIN collection_tags ct ON ct.tag_id = t.id
 WHERE ct.collection_id = ?
 ORDER BY COALESCE(t.name_sort, t.name) ASC, t.id ASC
"""


@dataclass(frozen=True, slots=True)
class Collection:
    """A collection as the row holds it, with no scoping applied.

    Distinct from the access layer's view of one on purpose: this is what a write needs to see to
    check itself, and it carries the real cover and no count at all. Nothing built from this is
    ever sent to a client: the views that go out are built from the scoped read.

    It says nothing about whether the collection is hidden, and cannot: hiding is personal to a
    user, so there is no answer to give without one. The scoped read carries that.
    """

    id: str
    name: str
    cover_asset_id: str | None
    owner_id: str | None
    created_at: int


def collection_from_row(row: Row) -> Collection:
    return Collection(
        id=row["id"],
        name=row["name"],
        cover_asset_id=row["cover_asset_id"],
        owner_id=row["owner_id"],
        created_at=int(row["created_at"]),
    )


#: 'user' and the maker named twice, in two columns that mean two things. `owner_id` is whose
#: shelf it IS (it decides who may see it) and `created_by_user_id` is who MADE it, which is
#: the same value today and need not stay so: a shelf handed over keeps its maker. Nothing in Sift
#: makes a shelf on its own, so there is no 'sift' case here.
_INSERT_COLLECTION = """
INSERT INTO collections
  (id, name, name_sort, cover_asset_id, owner_id, created_at, created_by_kind,
   created_by_user_id)
VALUES (?, ?, ?, NULL, ?, ?, 'user', ?)
RETURNING *
"""

# Names are not unique. Two shortlists really can be called "best of", and the id is what tells
# them apart, so unlike a tag, a second collection by the same name is allowed rather than a
# conflict.
_RENAME_COLLECTION = "UPDATE collections SET name = ?, name_sort = ? WHERE id = ? RETURNING *"

# Hiding a collection, for one user. An upsert because the state lives in a row of its own and
# most of them do not exist: nothing writes one until somebody hides the collection.
_SET_VAULT = """
INSERT INTO collection_user_state (collection_id, user_id, hidden, hidden_at, updated_at)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(collection_id, user_id) DO UPDATE SET
    hidden     = excluded.hidden,
    hidden_at  = excluded.hidden_at,
    updated_at = excluded.updated_at
"""

# The heart and the stars on a collection, for one user. Upserts for the same reason the vault
# write above is one: the row is this user's own and most of them do not exist until somebody
# says something.
_SET_FAVORITE = """
INSERT INTO collection_user_state (collection_id, user_id, favorite, updated_at)
VALUES (?, ?, ?, ?)
ON CONFLICT(collection_id, user_id) DO UPDATE SET
    favorite   = excluded.favorite,
    updated_at = excluded.updated_at
RETURNING favorite, rating
"""

_SET_RATING = """
INSERT INTO collection_user_state (collection_id, user_id, rating, updated_at)
VALUES (?, ?, ?, ?)
ON CONFLICT(collection_id, user_id) DO UPDATE SET
    rating     = excluded.rating,
    updated_at = excluded.updated_at
RETURNING favorite, rating
"""

# The MOMENT is written by the same statement as the file, always: a moment belongs to the file it
# was taken from, so choosing a different file with the old moment left behind would point at a
# frame of a video nobody chose. One write, both columns, or neither.
#: The file a cover names and WHICH MOMENT of it, read straight off the row.
#:
#: Two columns and nothing else. The route that serves a cover has already resolved the entity
#: against the viewer by the time it asks this, and the picture itself is then read through the
#: permission-scoped derivative read, so this is the middle of a sandwich rather than a place a
#: decision about visibility is taken. Its own statement rather than a field on the entity view,
#: which is read a whole wall at a time to serve one picture.
_CHOSEN_COVER = (
    "SELECT cover_asset_id, cover_at_ms, cover_upload_id, cover_frame FROM collections WHERE id = ?"
)

#: One statement writes EVERY cover pointer, which is what makes "an entity has one cover" a
#: property of the schema's use rather than a rule each writer remembers. A file cover, an uploaded
#: cover, and the moment of a file are three columns and one decision, so choosing any of them
#: clears the other two, and there is no arrangement in which a row claims both kinds at once.
_SET_COVER = (
    "UPDATE collections SET cover_asset_id = ?, cover_at_ms = ?, cover_upload_id = ?, cover_frame = ?,"
    # The clear mark, and the rule's default let go (`kernel/access/default_covers.py`).
    " cover_cleared_at = ?, cover_by_default = NULL WHERE id = ? RETURNING *"
)

_DELETE_COLLECTION = "DELETE FROM collections WHERE id = ? RETURNING id"

#: What one collection is CALLED, for the snapshot an event carries. Its own one-column read
#: because it is asked before a write rather than as part of one, and because what a collection
#: holds is not the question: only what it was named at the moment somebody acted on it.
_COLLECTION_NAME = "SELECT name FROM collections WHERE id = ?"


def _opinion_of(row: Row) -> tuple[bool, int | None]:
    """The heart and the stars off a row the two writes above hand back."""
    return bool(row["favorite"]), (None if row["rating"] is None else int(row["rating"]))


# `DO NOTHING` rather than a lookup first: the insert is the check. Dropping the same clip onto the
# same collection twice is a no-op, which is what somebody doing it means by it.
_ADD_ITEM = """
INSERT INTO collection_items (collection_id, asset_id, added_at) VALUES (?, ?, ?)
ON CONFLICT DO NOTHING
"""

_REMOVE_ITEM = "DELETE FROM collection_items WHERE collection_id = ? AND asset_id = ?"

# A cover is one of the items, and taking that item out has to take the cover with it. Written as
# "clear it if it is no longer held" rather than "clear it if it was the one just removed", so the
# rule is the invariant itself and not a special case of one route: whatever path empties the
# membership row, the cover cannot outlive it. Left alone, a collection goes on showing a picture
# of something it does not contain, and the only place that is refused is the route that sets one.
_DROP_STALE_COVER = """
UPDATE collections SET cover_asset_id = NULL
 WHERE id = ?
   AND cover_asset_id IS NOT NULL
   AND cover_asset_id NOT IN (SELECT asset_id FROM collection_items WHERE collection_id = ?)
"""

_ITEMS = "SELECT asset_id FROM collection_items WHERE collection_id = ? ORDER BY asset_id"

_HOLDS_ITEM = "SELECT 1 FROM collection_items WHERE collection_id = ? AND asset_id = ?"


class CollectionService:
    """Collection writes, and the unscoped reads those writes check themselves against.

    Takes the access layer as well as the database because deleting a collection has to forget the
    grants that named it, and grants are the access layer's.
    """

    def __init__(
        self,
        database: Database,
        access: Repository,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._db = database
        self._access = access
        self._clock = clock

    def _now(self) -> int:
        return int(self._clock())

    async def create(self, name: str, *, owner_id: str, actor: Actor) -> Collection:
        """A new, empty collection. Flat: it has no parent, and there is nowhere to put one.

        It starts with no cover, because a cover is one of the items and this has none yet, and it
        starts out of the vault: concealing one is a separate, deliberate act rather than
        something a create can do on the way past.
        """
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            rows = list(
                await connection.execute_fetchall(
                    _INSERT_COLLECTION,
                    (new_id(), name, sort_key(name), owner_id, self._now(), owner_id),
                )
            )
            await record_event(
                connection,
                actor=actor,
                verb="added",
                subject=Subject(kind="collection", id=str(rows[0]["id"]), name=name),
            )
        return collection_from_row(rows[0])

    async def rename(self, collection_id: str, name: str, *, actor: Actor) -> Collection | None:
        """Rename. None when there is no such collection.

        The name it HAD is read first and written into the event's payload, because the column is
        overwritten in place: without it, "renamed" is a line that cannot say what from.
        """
        was_called = await self._name_of(collection_id)
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            rows = list(
                await connection.execute_fetchall(
                    _RENAME_COLLECTION, (name, sort_key(name), collection_id)
                )
            )
            if rows and was_called != name:
                await record_event(
                    connection,
                    actor=actor,
                    verb="renamed",
                    subject=Subject(kind="collection", id=collection_id, name=name),
                    payload=json.dumps({"before": was_called}),
                )
        return collection_from_row(rows[0]) if rows else None

    async def set_favorite(
        self, viewer: Viewer, collection_id: str, *, favorite: bool
    ) -> tuple[bool, int | None]:
        """Heart a collection, or take the heart off. This viewer's, not the collection's.

        Unscoped, exactly as `set_vault` below is: nothing decides here who may do this, and the
        route resolves the collection through the access layer before calling, which is what stops
        a row being written against an id this user may not be shown, or one never minted.
        """
        now = self._now()
        async with telling(self._db, Audience.of_user(viewer.id), About.MINE) as connection:
            # Read before the upsert, which is the statement that destroys it. See
            # `kernel.content.entity_state` for why the upsert's own RETURNING cannot answer this.
            before = await opinion_before(
                connection,
                subject_kind="collection",
                subject_id=collection_id,
                user_id=viewer.id,
                kind=OpinionKind.FAVORITE,
            )
            rows = list(
                await connection.execute_fetchall(
                    _SET_FAVORITE, (collection_id, viewer.id, int(favorite), now)
                )
            )
            await record_opinion(
                connection,
                user_id=viewer.id,
                subject_kind="collection",
                subject_id=collection_id,
                kind=OpinionKind.FAVORITE,
                before=before,
                after=int(favorite),
                at=now,
            )
        return _opinion_of(rows[0])

    async def set_rating(
        self, viewer: Viewer, collection_id: str, *, rating: int | None
    ) -> tuple[bool, int | None]:
        """Set the stars, or clear them with None. Zero is not a rating and never reaches here:
        the model refuses it, so one number means one thing wherever it is stored."""
        now = self._now()
        async with telling(self._db, Audience.of_user(viewer.id), About.MINE) as connection:
            before = await opinion_before(
                connection,
                subject_kind="collection",
                subject_id=collection_id,
                user_id=viewer.id,
                kind=OpinionKind.RATING,
            )
            rows = list(
                await connection.execute_fetchall(
                    _SET_RATING, (collection_id, viewer.id, rating, now)
                )
            )
            await record_opinion(
                connection,
                user_id=viewer.id,
                subject_kind="collection",
                subject_id=collection_id,
                kind=OpinionKind.RATING,
                before=before,
                after=rating,
                at=now,
            )
        return _opinion_of(rows[0])

    async def set_vault(self, viewer: Viewer, collection_id: str, *, vault: bool) -> None:
        """Hide a collection, or bring it back, for this user. Unscoped: the caller resolves it.

        Kept apart from the rename because the two answer differently: a rename describes what it
        wrote, and this cannot: by the time it has finished, the row it changed may be one the
        caller is no longer allowed to be shown.

        Both directions are writable here on purpose. Nothing decides who may hide or unhide at this
        level; the endpoint does, by resolving the collection through the access layer first, which
        means bringing one back is reachable exactly to somebody who has entered their PIN.
        """
        now = self._now()
        # The hide and the note that makes this user's cached pictures unreachable go together.
        # Split apart, there is a moment in which the collection is concealed and every picture in
        # it is still on screen from the browser's own store.
        async with self._db.write() as connection:
            before = await opinion_before(
                connection,
                subject_kind="collection",
                subject_id=collection_id,
                user_id=viewer.id,
                kind=OpinionKind.HIDE,
            )
            await connection.execute(
                _SET_VAULT, (collection_id, viewer.id, int(vault), now if vault else None, now)
            )
            # `hidden_at` says only when the CURRENT concealment began: bringing a collection back
            # clears it, and with it the fact that it was ever hidden. This is what remembers.
            await record_opinion(
                connection,
                user_id=viewer.id,
                subject_kind="collection",
                subject_id=collection_id,
                kind=OpinionKind.HIDE,
                before=before,
                after=int(vault),
                at=now,
            )
            announce(await bump_cache_stamp(connection, viewer.id), About.LIBRARY)

    # --- tags on a collection ------------------------------------------------------------

    async def tags_of(self, collection_id: str) -> list[Row]:
        """The tags on one collection, by name.

        Rows rather than a type, for the reason the same reader on a person gives: the tag's shape
        belongs to the tags slice, and importing it here would couple the two.
        """
        return await self._db.fetch_all(_TAGS_OF_COLLECTION, (collection_id,))

    async def tag(self, collection_id: str, tag_id: str, *, add: bool = True) -> None:
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            if add:
                await connection.execute(_TAG, (collection_id, tag_id, int(time.time())))
            else:
                await connection.execute(_UNTAG, (collection_id, tag_id))

    async def holds(self, collection_id: str, asset_id: str) -> bool:
        """Whether the collection holds this item. Unscoped: the caller resolves the asset first."""
        return await self._db.fetch_one(_HOLDS_ITEM, (collection_id, asset_id)) is not None

    async def chosen_cover(self, collection_id: str) -> ChosenCover:
        """What this collection is drawn as: an uploaded picture, or a file and a moment of it.

        One shape rather than a widening tuple. See `ChosenCover`, and `serve_cover` for which of
        the two wins.
        """
        row = await self._db.fetch_one(_CHOSEN_COVER, (collection_id,))
        if row is None:  # pragma: no cover (the route resolved the collection a line ago)
            return ChosenCover()
        return chosen_from_row(row)

    async def set_cover(
        self,
        collection_id: str,
        asset_id: str | None,
        at_ms: int | None = None,
        upload_id: str | None = None,
        *,
        actor: Actor,
        frame: CoverFrame | None = None,
    ) -> Collection | None:
        """Set or clear the cover. None when there is no such collection.

        The caller checks that the asset is one of the items and that the viewer may see it. This
        writes what it is given.
        """
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            # The cover as it WAS, in the write's own transaction: the same picture with a new
            # window is a reframe and says so. See `kernel/covers.py cover_change`.
            was = list(await connection.execute_fetchall(_CHOSEN_COVER, (collection_id,)))
            change = cover_change(
                chosen_from_row(was[0]) if was else ChosenCover(),
                asset_id=asset_id,
                at_ms=at_ms,
                upload_id=upload_id,
                frame=frame,
                box=None,
            )
            rows = list(
                await connection.execute_fetchall(
                    _SET_COVER,
                    (asset_id, at_ms, upload_id, change.frame, change.cleared_at, collection_id),
                )
            )
            if rows:
                # The still is the OBJECT and the shelf the subject, so a picture chosen as a
                # cover says so on the FILE's own pane too: `history_events` reads an event from
                # both sides. None where the cover was cleared: an act with nothing on the other
                # end of it.
                await record_event(
                    connection,
                    actor=actor,
                    verb="edited",
                    subject=Subject(kind="collection", id=collection_id, name=str(rows[0]["name"])),
                    object=change.object,
                    payload=change.payload,
                )
        return collection_from_row(rows[0]) if rows else None

    async def delete(self, collection_id: str, *, actor: Actor) -> list[str] | None:
        """Delete a collection, its membership rows, and every grant that named it.

        Returns the assets that were in it, or None if there was no such collection. **That list
        is what the caller reindexes.** A collection's name is indexed on every asset that carries
        it and on no other, so those ids are exactly the text this changed, and for an empty
        collection they are none at all. Read one statement before the cascade and inside the same
        write, because afterwards there is nothing left to ask.

        **The assets survive.** `collection_items` names the asset with `ON DELETE CASCADE`
        pointing at the collection, so deleting a collection deletes the rows joining it to files
        and never the files. A cascade pointed the other way here would delete somebody's media
        because they tidied up a shortlist, and there is a test asserting the files are still
        there.

        The grant half is the reason this is not one statement. `acl_grants.object_id` carries no
        foreign key (it names a different table depending on the type beside it), so nothing
        cascades, and a collection deleted on its own leaves its grants behind to apply to whatever
        ends up with that id. A stale share hands somebody a file; a stale restrict is a promise
        that stopped being kept. Neither announces itself.

        Forgotten first, so a failure between the two leaves grants naming a collection that still
        exists rather than grants naming nothing.
        """
        await self._access.forget_object(ObjectType.COLLECTION, collection_id)
        # Read before the cascade, for the reason the ids below are read before it: afterwards
        # there is nothing left to ask, and a deletion the record cannot name is a deletion nobody
        # can look back at.
        was_called = await self._name_of(collection_id)
        async with self._db.write() as connection:
            held = [
                str(row["asset_id"])
                for row in await connection.execute_fetchall(_ITEMS, (collection_id,))
            ]
            rows = list(await connection.execute_fetchall(_DELETE_COLLECTION, (collection_id,)))
            if rows:
                await record_event(
                    connection,
                    actor=actor,
                    verb="deleted",
                    subject=Subject(kind="collection", id=collection_id, name=was_called),
                )
                # Its grants went first and told everybody then; after that only an admin can
                # still be drawing it, and this is the commit that takes it off their screens.
                announce(EVERY_ADMIN, About.LIBRARY)
        if not rows:
            return None
        # No name in the log. What somebody chose to collect says a great deal about what a
        # library is for, which is not something a log file needs to record.
        log.info("collections.deleted", collection_id=collection_id)
        return held

    async def _name_of(self, collection_id: str) -> str | None:
        """What this collection is called, or None when there is no such collection."""
        row = await self._db.fetch_one(_COLLECTION_NAME, (collection_id,))
        return None if row is None else str(row["name"])

    async def members(self, collection_id: str) -> list[str]:
        """The assets a collection holds: what a rename reindexes."""
        rows = await self._db.fetch_all(_ITEMS, (collection_id,))
        return [str(row["asset_id"]) for row in rows]

    async def add(self, collection_id: str, asset_ids: Sequence[str], *, actor: Actor) -> int:
        """Put items in, and touch no file on disk.

        This is the whole promise of organising logically: a clip dragged onto a collection moves
        nothing, is copied nowhere, and its path is the same afterwards. The only table written is
        the join.

        The caller resolves every asset through the access layer first. Nothing here can tell a
        visible asset from a hidden one, which is why it does not try to.
        """
        written = 0
        landed: list[str] = []
        named = await self._name_of(collection_id)
        async with self._db.write() as connection:
            # One moment for the whole drop. Reading the clock per item would date a drag of two
            # hundred files across a second or more, which says they were put here separately.
            now = int(time.time())
            for asset_id in asset_ids:
                cursor = await connection.execute(_ADD_ITEM, (collection_id, asset_id, now))
                if cursor.rowcount > 0:
                    written += cursor.rowcount
                    # Only a row that landed is an act. Membership carries no timestamp at all:
                    # the table has no column for one, and the history reader says so in its own
                    # comment, so this event is the whole of what says when a file was put here.
                    landed.append(asset_id)
            for asset_id in landed:
                await record_event(
                    connection,
                    actor=actor,
                    verb="linked",
                    subject=Subject(kind="asset", id=asset_id),
                    object=Object(kind="collection", id=collection_id, name=named),
                )
            if written:
                # A collection is what a share or a hide is attached to, so what it holds decides
                # what somebody may see. In the same transaction, and not at all when every drop
                # was a duplicate: nothing changed, so nobody should pay a re-fetch.
                moved = await bump_stamps_for_object(
                    connection, ObjectType.COLLECTION, collection_id
                )
                announce(_and_the_actor(moved, actor), About.LIBRARY)
        return written

    async def remove(self, collection_id: str, asset_ids: Sequence[str], *, actor: Actor) -> int:
        """Take items out, and drop the cover if it went with them.

        The cover is cleared in the same transaction when the item it named has gone. A cover is
        one of the items, and that is enforced when one is set, so leaving a stale one behind
        would make the rule true only on the way in, and the collection would go on showing a
        picture of something it no longer holds.
        """
        removed = 0
        named = await self._name_of(collection_id)
        async with self._db.write() as connection:
            for asset_id in asset_ids:
                cursor = await connection.execute(_REMOVE_ITEM, (collection_id, asset_id))
                if not cursor.rowcount or cursor.rowcount <= 0:
                    continue
                removed += cursor.rowcount
                # The half that vanishes without this. The membership row is deleted, so a file
                # taken out of a collection stops ever having been in it.
                await record_event(
                    connection,
                    actor=actor,
                    verb="unlinked",
                    subject=Subject(kind="asset", id=asset_id),
                    object=Object(kind="collection", id=collection_id, name=named),
                )
            if removed:
                await connection.execute(_DROP_STALE_COVER, (collection_id, collection_id))
                # The direction that matters most: a file taken out of a shared collection ends a
                # user's access to it as surely as taking the share away does.
                moved = await bump_stamps_for_object(
                    connection, ObjectType.COLLECTION, collection_id
                )
                announce(_and_the_actor(moved, actor), About.LIBRARY)
        return removed


#: Collections.
SERVICE: Part[CollectionService] = Part("collections")
