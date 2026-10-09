# SPDX-License-Identifier: AGPL-3.0-or-later
"""The writes behind a manual collection; nothing on disk moves, and scoped reads live elsewhere."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from sift.kernel.access import ObjectType, Repository, Viewer, bump_stamps_for_object
from sift.kernel.audience import Audience
from sift.kernel.cache_stamp import bump_cache_stamp
from sift.kernel.changes import About, announce, telling, who_may_see_a_file
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
    """The audience of a membership write, plus whoever made it, who is otherwise never told."""
    if actor.kind == ACTOR_USER and actor.id:
        return told | Audience.of_user(actor.id)
    return told


# The same `tags` table every other kind of thing uses: one vocabulary.
#: `added_at` is bound, never defaulted: a NULL would read as a row from before the column.
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
    """A collection as the row holds it, unscoped, for a write to check itself; never sent out."""

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


#: `owner_id` is whose shelf it is, `created_by_user_id` who made it: equal today, not for ever.
_INSERT_COLLECTION = """
INSERT INTO collections
  (id, name, name_sort, cover_asset_id, owner_id, created_at, created_by_kind,
   created_by_user_id)
VALUES (?, ?, ?, NULL, ?, ?, 'user', ?)
RETURNING *
"""

# Names are not unique: two shortlists can both be "best of".
_RENAME_COLLECTION = "UPDATE collections SET name = ?, name_sort = ? WHERE id = ? RETURNING *"

# An upsert: nothing writes a state row until somebody hides the collection.
_SET_VAULT = """
INSERT INTO collection_user_state (collection_id, user_id, hidden, hidden_at, updated_at)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(collection_id, user_id) DO UPDATE SET
    hidden     = excluded.hidden,
    hidden_at  = excluded.hidden_at,
    updated_at = excluded.updated_at
"""

# Upserts, as the vault write is: most of these rows do not exist yet.
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

# The moment is written with the file, always: one write, both columns, or neither.
#: The file a cover names and which moment of it; visibility is settled either side of this.
_CHOSEN_COVER = (
    "SELECT cover_asset_id, cover_at_ms, cover_upload_id, cover_frame FROM collections WHERE id = ?"
)

#: One statement writes every cover pointer, so an entity can only ever have one cover.
_SET_COVER = (
    "UPDATE collections SET cover_asset_id = ?, cover_at_ms = ?, cover_upload_id = ?, cover_frame = ?,"
    " cover_cleared_at = ?, cover_by_default = NULL WHERE id = ? RETURNING *"
)

_DELETE_COLLECTION = "DELETE FROM collections WHERE id = ? RETURNING id"

#: What one collection is called, for the snapshot an event carries.
_COLLECTION_NAME = "SELECT name FROM collections WHERE id = ?"


def _opinion_of(row: Row) -> tuple[bool, int | None]:
    """The heart and the stars off a row the two writes above hand back."""
    return bool(row["favorite"]), (None if row["rating"] is None else int(row["rating"]))


# `DO NOTHING` rather than a lookup first: the insert is the check.
_ADD_ITEM = """
INSERT INTO collection_items (collection_id, asset_id, added_at) VALUES (?, ?, ?)
ON CONFLICT DO NOTHING
"""

_REMOVE_ITEM = "DELETE FROM collection_items WHERE collection_id = ? AND asset_id = ?"

# The cover cannot outlive its item: cleared whenever it is no longer held.
_DROP_STALE_COVER = """
UPDATE collections SET cover_asset_id = NULL
 WHERE id = ?
   AND cover_asset_id IS NOT NULL
   AND cover_asset_id NOT IN (SELECT asset_id FROM collection_items WHERE collection_id = ?)
"""

_ITEMS = "SELECT asset_id FROM collection_items WHERE collection_id = ? ORDER BY asset_id"

_HOLDS_ITEM = "SELECT 1 FROM collection_items WHERE collection_id = ? AND asset_id = ?"


class CollectionService:
    """Collection writes, and the unscoped reads they check themselves against."""

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
        """A new, empty collection: flat, with no cover, and out of the vault."""
        async with telling(self._db, who_may_see_a_file, About.LIBRARY) as connection:
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
        """Rename, recording the old name; None when there is no such collection."""
        was_called = await self._name_of(collection_id)
        async with telling(self._db, who_may_see_a_file, About.LIBRARY) as connection:
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
        """Heart a collection, or take the heart off, for this viewer; the route resolves it."""
        now = self._now()
        async with telling(self._db, Audience.of_user(viewer.id), About.MINE) as connection:
            # Read before the upsert, which is the statement that destroys it.
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
        """Set the stars, or clear them with None."""
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
        """Hide a collection, or bring it back, for this user; the caller resolves it."""
        now = self._now()
        # The hide and the note that makes cached pictures unreachable go together.
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
            # `hidden_at` is cleared on bringing back; this is what remembers it was ever hidden.
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
        """The tags on one collection, by name."""
        return await self._db.fetch_all(_TAGS_OF_COLLECTION, (collection_id,))

    async def tag(self, collection_id: str, tag_id: str, *, add: bool = True) -> None:
        async with telling(self._db, who_may_see_a_file, About.LIBRARY) as connection:
            if add:
                await connection.execute(_TAG, (collection_id, tag_id, int(time.time())))
            else:
                await connection.execute(_UNTAG, (collection_id, tag_id))

    async def holds(self, collection_id: str, asset_id: str) -> bool:
        """Whether the collection holds this item. Unscoped: the caller resolves the asset first."""
        return await self._db.fetch_one(_HOLDS_ITEM, (collection_id, asset_id)) is not None

    async def chosen_cover(self, collection_id: str) -> ChosenCover:
        """What this collection is drawn as: an uploaded picture, or a file and a moment of it."""
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
        """Set or clear the cover; the caller checks the asset. None when there is no such one."""
        async with telling(self._db, who_may_see_a_file, About.LIBRARY) as connection:
            # The cover as it was, so a new window on the same picture reads as a reframe.
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
                # The still is the object, so a chosen cover shows on the file's own pane too.
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
        """Delete a collection, its rows and its grants (grants first); returns its assets."""
        await self._access.forget_object(ObjectType.COLLECTION, collection_id)
        # Read before the cascade: afterwards there is nothing left to name.
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
                announce(await who_may_see_a_file(connection), About.LIBRARY)
        if not rows:
            return None
        # No name in the log: what somebody collects says a great deal about a library.
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
        """Put items in, and touch no file on disk; the caller resolves every asset first."""
        written = 0
        landed: list[str] = []
        named = await self._name_of(collection_id)
        async with self._db.write() as connection:
            # One moment for the whole drop.
            now = int(time.time())
            for asset_id in asset_ids:
                cursor = await connection.execute(_ADD_ITEM, (collection_id, asset_id, now))
                if cursor.rowcount > 0:
                    written += cursor.rowcount
                    # Only a row that landed is an act; this event is the only record of when.
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
                # What it holds decides what somebody may see, so a change rings visibility.
                moved = await bump_stamps_for_object(
                    connection, ObjectType.COLLECTION, collection_id
                )
                announce(_and_the_actor(moved, actor), About.LIBRARY)
        return written

    async def remove(self, collection_id: str, asset_ids: Sequence[str], *, actor: Actor) -> int:
        """Take items out, and drop the cover if it went with them."""
        removed = 0
        named = await self._name_of(collection_id)
        async with self._db.write() as connection:
            for asset_id in asset_ids:
                cursor = await connection.execute(_REMOVE_ITEM, (collection_id, asset_id))
                if not cursor.rowcount or cursor.rowcount <= 0:
                    continue
                removed += cursor.rowcount
                # The membership row is deleted, so this event is what remembers it was there.
                await record_event(
                    connection,
                    actor=actor,
                    verb="unlinked",
                    subject=Subject(kind="asset", id=asset_id),
                    object=Object(kind="collection", id=collection_id, name=named),
                )
            if removed:
                await connection.execute(_DROP_STALE_COVER, (collection_id, collection_id))
                # Taking a file out of a shared collection ends a user's access to it.
                moved = await bump_stamps_for_object(
                    connection, ObjectType.COLLECTION, collection_id
                )
                announce(_and_the_actor(moved, actor), About.LIBRARY)
        return removed


SERVICE: Part[CollectionService] = Part("collections")
