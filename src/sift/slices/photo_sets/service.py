# SPDX-License-Identifier: AGPL-3.0-or-later
"""Photo-set writes, unscoped, ordered, and possibly derived, and the reads they check against."""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from sift.kernel.access import ObjectType, Repository, Viewer
from sift.kernel.access.stamps import bump_stamps_for_object
from sift.kernel.audience import EVERY_ADMIN, Audience
from sift.kernel.changes import About, announce, telling
from sift.kernel.content.entity_state import opinion_before
from sift.kernel.content.user_state import OpinionKind, record_opinion
from sift.kernel.cover_frame import CoverFrame
from sift.kernel.covers import ChosenCover, chosen_from_row, cover_change
from sift.kernel.db import Database, IntegrityError, Row
from sift.kernel.ids import new_id
from sift.kernel.ledger import Actor, Object, record_event
from sift.kernel.sorting import sort_key
from sift.kernel.vocabulary import VIA_ARCHIVE, VIA_FOLDER, VIA_STASH_LIBRARY, Subject
from sift.kernel.wiring import Part


class UnknownItem(ValueError):
    """An asset id that names nothing, or nothing this viewer may put in a set."""


@dataclass(frozen=True, slots=True)
class PhotoSet:
    """A set as the table holds it, unscoped. What a screen is shown is `PhotoSetView`."""

    id: str
    name: str
    cover_asset_id: str | None
    origin: str
    origin_url: str | None
    folder_id: str | None
    #: The archive it was derived from, so a rescan finds the set it made last time.
    archive_root_id: str | None
    archive_rel_path: str | None
    notes: str | None
    created_at: int


def photo_set_from_row(row) -> PhotoSet:  # type: ignore[no-untyped-def]
    return PhotoSet(
        id=str(row["id"]),
        name=str(row["name"]),
        cover_asset_id=row["cover_asset_id"],
        origin=str(row["origin"]),
        origin_url=row["origin_url"],
        folder_id=row["folder_id"],
        archive_root_id=row["archive_root_id"],
        archive_rel_path=row["archive_rel_path"],
        notes=row["notes"],
        created_at=int(row["created_at"]),
    )


#: Who made it and which pass are both read off `origin`, so no second answer can disagree.
_INSERT = """
INSERT INTO photo_sets
  (id, name, name_sort, cover_asset_id, origin, origin_url, folder_id,
   archive_root_id, archive_rel_path, created_at, created_by_kind, created_by_via,
   created_by_user_id)
VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)
RETURNING *
"""

# Names are not unique: two shoots can both be called "beach".
_RENAME = "UPDATE photo_sets SET name = ?, name_sort = ? WHERE id = ? RETURNING *"
_SET_NOTES = "UPDATE photo_sets SET notes = ? WHERE id = ? RETURNING *"
# The moment is written with the file, always: one write, both columns, or neither.
#: The file a cover names and which moment of it; visibility is settled either side of this.
_CHOSEN_COVER = (
    "SELECT cover_asset_id, cover_at_ms, cover_upload_id, cover_frame FROM photo_sets WHERE id = ?"
)

#: One statement writes every cover pointer, so an entity can only ever have one cover.
_SET_COVER = (
    "UPDATE photo_sets SET cover_asset_id = ?, cover_at_ms = ?, cover_upload_id = ?, cover_frame = ?,"
    " cover_cleared_at = ?, cover_by_default = NULL WHERE id = ? RETURNING *"
)
_DELETE = "DELETE FROM photo_sets WHERE id = ?"
# Correlated, not grouped: a set whose files have all gone has no row to group yet must answer.
_UNDER_FLOOR = (
    "SELECT ps.id AS id FROM photo_sets ps WHERE ps.origin != 'manual'"
    " AND (SELECT COUNT(*) FROM photo_set_items psi WHERE psi.photo_set_id = ps.id) < ?"
    " ORDER BY ps.id"
)

_NAME = "SELECT name FROM photo_sets WHERE id = ?"
_ONE = "SELECT * FROM photo_sets WHERE id = ?"

#: `MAX + 1`, not a count: a removal leaves a gap a count would hand out twice.
_NEXT_POSITION = (
    "SELECT COALESCE(MAX(position), -1) + 1 AS next FROM photo_set_items WHERE photo_set_id = ?"
)

#: `added_at` is bound, never defaulted: a NULL would read as a row from before the column.
_ADD_ITEM = """
INSERT INTO photo_set_items (photo_set_id, asset_id, position, added_at)
VALUES (?, ?, ?, ?)
ON CONFLICT(photo_set_id, asset_id) DO NOTHING
"""
_REMOVE_ITEM = "DELETE FROM photo_set_items WHERE photo_set_id = ? AND asset_id = ?"
_SET_POSITION = "UPDATE photo_set_items SET position = ? WHERE photo_set_id = ? AND asset_id = ?"

_SET_FAVORITE = """
INSERT INTO photo_set_user_state (photo_set_id, user_id, favorite, updated_at)
VALUES (?, ?, ?, ?)
ON CONFLICT(photo_set_id, user_id) DO UPDATE SET
  favorite = excluded.favorite, updated_at = excluded.updated_at
RETURNING favorite, rating
"""

_SET_RATING = """
INSERT INTO photo_set_user_state (photo_set_id, user_id, rating, updated_at)
VALUES (?, ?, ?, ?)
ON CONFLICT(photo_set_id, user_id) DO UPDATE SET
  rating = excluded.rating, updated_at = excluded.updated_at
RETURNING favorite, rating
"""

_SET_VAULT = """
INSERT INTO photo_set_user_state (photo_set_id, user_id, hidden, hidden_at, updated_at)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(photo_set_id, user_id) DO UPDATE SET
  hidden = excluded.hidden, hidden_at = excluded.hidden_at, updated_at = excluded.updated_at
"""

_SET_TAG = """
INSERT INTO photo_set_tags (photo_set_id, tag_id, added_at) VALUES (?, ?, ?)
ON CONFLICT(photo_set_id, tag_id) DO NOTHING
"""
_UNSET_TAG = "DELETE FROM photo_set_tags WHERE photo_set_id = ? AND tag_id = ?"
_TAGS_ON = """
SELECT t.id, t.name
  FROM tags t JOIN photo_set_tags pst ON pst.tag_id = t.id
 WHERE pst.photo_set_id = ?
 ORDER BY COALESCE(t.name_sort, t.name), t.id
"""

#: What makes a second pass over a folder idempotent.
_BY_FOLDER = "SELECT * FROM photo_sets WHERE folder_id = ? LIMIT 1"

# Library and path together: two libraries can hold the same archive path.
_BY_ARCHIVE = """
SELECT * FROM photo_sets
 WHERE archive_root_id = ? AND archive_rel_path = ?
 LIMIT 1
"""

#: An unlinked set holding most of these pictures: a library taken out and added back.
_ORPHAN_HOLDING = """
SELECT ps.*,
       COUNT(*) AS shared,
       (SELECT COUNT(*) FROM photo_set_items every WHERE every.photo_set_id = ps.id) AS held
  FROM photo_sets ps
  JOIN photo_set_items psi ON psi.photo_set_id = ps.id
 WHERE ps.origin = ?
   AND ps.folder_id IS NULL
   AND ps.archive_root_id IS NULL
   AND psi.asset_id IN (SELECT value FROM json_each(?))
 GROUP BY ps.id
 ORDER BY shared DESC, ps.id
 LIMIT 1
"""

#: The set holding every one of these pictures where they are most of it, fullest then oldest.
_HOLDING = """
SELECT ps.id AS id,
       COUNT(*) AS shared,
       (SELECT COUNT(*) FROM photo_set_items every WHERE every.photo_set_id = ps.id) AS held
  FROM photo_sets ps
  JOIN photo_set_items psi ON psi.photo_set_id = ps.id
 WHERE psi.asset_id IN (SELECT value FROM json_each(?))
 GROUP BY ps.id
HAVING shared = ? AND shared * 2 > held
 ORDER BY shared DESC, ps.id
 LIMIT 1
"""

#: Guarded on still being an orphan, so a racing pass cannot move a set just tied.
_TIE_TO_FOLDER = (
    "UPDATE photo_sets SET folder_id = ? WHERE id = ? AND folder_id IS NULL RETURNING *"
)
_TIE_TO_ARCHIVE = (
    "UPDATE photo_sets SET archive_root_id = ?, archive_rel_path = ?"
    " WHERE id = ? AND archive_root_id IS NULL RETURNING *"
)


def _opinion_of(row) -> tuple[bool, int | None]:  # type: ignore[no-untyped-def]
    return bool(row["favorite"]), None if row["rating"] is None else int(row["rating"])


class PhotoSetService:
    """Photo-set writes, given the access layer for grants on delete and the stamp on hide."""

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

    async def create(
        self,
        name: str,
        *,
        origin: str = "manual",
        origin_url: str | None = None,
        folder_id: str | None = None,
        archive_root_id: str | None = None,
        archive_rel_path: str | None = None,
        by_user: str | None = None,
    ) -> PhotoSet:
        """A new, empty set; the database refuses a second set over the same folder or archive."""
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            rows = list(
                await connection.execute_fetchall(
                    _INSERT,
                    (
                        new_id(),
                        name,
                        sort_key(name),
                        origin,
                        origin_url,
                        folder_id,
                        archive_root_id,
                        archive_rel_path,
                        self._now(),
                        # See `_INSERT`: the origin is the answer, so nothing else is asked.
                        "user" if origin == "manual" else "sift",
                        None if origin == "manual" else origin,
                        by_user if origin == "manual" else None,
                    ),
                )
            )
        return photo_set_from_row(rows[0])

    async def rename(self, photo_set_id: str, name: str, *, actor: Actor) -> PhotoSet | None:
        """Rename, recording the old name; None when there is no such grouping."""
        was_called = await self._name_of(photo_set_id)
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            rows = list(
                await connection.execute_fetchall(_RENAME, (name, sort_key(name), photo_set_id))
            )
            if rows and was_called != name:
                await record_event(
                    connection,
                    actor=actor,
                    verb="renamed",
                    subject=Subject(kind="photo_set", id=photo_set_id, name=name),
                    payload=json.dumps({"before": was_called}),
                )
        return photo_set_from_row(rows[0]) if rows else None

    async def set_notes(
        self, photo_set_id: str, notes: str | None, *, actor: Actor
    ) -> PhotoSet | None:
        """Write the note somebody typed on it. None when there is no such grouping."""
        named = await self._name_of(photo_set_id)
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            rows = list(await connection.execute_fetchall(_SET_NOTES, (notes, photo_set_id)))
            if rows:
                await record_event(
                    connection,
                    actor=actor,
                    verb="edited",
                    subject=Subject(kind="photo_set", id=photo_set_id, name=named),
                    payload=json.dumps({"field": "notes"}),
                )
        return photo_set_from_row(rows[0]) if rows else None

    async def _name_of(self, photo_set_id: str) -> str | None:
        """What this grouping is called, or None when there is no such row."""
        row = await self._db.fetch_one(_NAME, (photo_set_id,))
        return None if row is None else str(row["name"])

    async def chosen_cover(self, photo_set_id: str) -> ChosenCover:
        """What this set is drawn as: an uploaded picture, or a file and a moment of it."""
        row = await self._db.fetch_one(_CHOSEN_COVER, (photo_set_id,))
        if row is None:  # pragma: no cover (the route resolved the set a line ago)
            return ChosenCover()
        return chosen_from_row(row)

    async def set_cover(
        self,
        photo_set_id: str,
        asset_id: str | None,
        at_ms: int | None = None,
        upload_id: str | None = None,
        *,
        actor: Actor,
        frame: CoverFrame | None = None,
    ) -> PhotoSet | None:
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            # The cover as it was, so a new window on the same picture reads as a reframe.
            was = list(await connection.execute_fetchall(_CHOSEN_COVER, (photo_set_id,)))
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
                    (asset_id, at_ms, upload_id, change.frame, change.cleared_at, photo_set_id),
                )
            )
            if rows:
                # The still is the object, so a chosen cover shows on the file's own pane too.
                await record_event(
                    connection,
                    actor=actor,
                    verb="edited",
                    subject=Subject(kind="photo_set", id=photo_set_id, name=str(rows[0]["name"])),
                    object=change.object,
                    payload=change.payload,
                )
        return photo_set_from_row(rows[0]) if rows else None

    async def under_floor(self, floor: int) -> list[str]:
        """The sets Sift made under the floor, oldest first; never one a person assembled."""
        rows = await self._db.fetch_all(_UNDER_FLOOR, (floor,))
        return [str(row["id"]) for row in rows]

    async def delete(
        self, photo_set_id: str, *, actor: Actor, under_floor: int | None = None
    ) -> None:
        """Delete the set and the grants naming it; pictures untouched. `under_floor` says why."""
        # Read before the row goes: afterwards there is no name left anywhere to write down.
        was_called = await self._name_of(photo_set_id)
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            cursor = await connection.execute(_DELETE, (photo_set_id,))
            if cursor.rowcount:
                await record_event(
                    connection,
                    actor=actor,
                    verb="deleted",
                    subject=Subject(kind="photo_set", id=photo_set_id, name=was_called),
                    payload=(
                        None if under_floor is None else json.dumps({"under_floor": under_floor})
                    ),
                )
        await self._access.forget_object(ObjectType.PHOTO_SET, photo_set_id)

    async def add(self, photo_set_id: str, asset_ids: Sequence[str], *, actor: Actor) -> int:
        """Put pictures in, at the end, in order; returns how many rows were new."""
        added = 0
        landed: list[str] = []
        named = await self._name_of(photo_set_id)
        async with self._db.write() as connection:
            row = await (await connection.execute(_NEXT_POSITION, (photo_set_id,))).fetchone()
            position = int(row["next"]) if row else 0
            # One moment for the whole add.
            now = self._now()
            for asset_id in asset_ids:
                cursor = await connection.execute(
                    _ADD_ITEM, (photo_set_id, asset_id, position, now)
                )
                if cursor.rowcount:
                    added += 1
                    position += 1
                    landed.append(asset_id)
            # Only a row that landed is an act; this event is the only record of when.
            for asset_id in landed:
                await record_event(
                    connection,
                    actor=actor,
                    verb="linked",
                    subject=Subject(kind="asset", id=asset_id),
                    object=Object(kind="photo_set", id=photo_set_id, name=named),
                )
            if added:
                moved = await bump_stamps_for_object(connection, ObjectType.PHOTO_SET, photo_set_id)
                announce(moved, About.LIBRARY)
        return added

    async def remove(self, photo_set_id: str, asset_ids: Sequence[str], *, actor: Actor) -> int:
        """Take pictures out, recording each removal. Returns how many rows went."""
        removed = 0
        named = await self._name_of(photo_set_id)
        async with self._db.write() as connection:
            for asset_id in asset_ids:
                cursor = await connection.execute(_REMOVE_ITEM, (photo_set_id, asset_id))
                if not cursor.rowcount:
                    continue
                removed += cursor.rowcount
                await record_event(
                    connection,
                    actor=actor,
                    verb="unlinked",
                    subject=Subject(kind="asset", id=asset_id),
                    object=Object(kind="photo_set", id=photo_set_id, name=named),
                )
            if removed:
                moved = await bump_stamps_for_object(connection, ObjectType.PHOTO_SET, photo_set_id)
                announce(moved, About.LIBRARY)
        return removed

    async def set_favorite(
        self, viewer: Viewer, photo_set_id: str, *, favorite: bool
    ) -> tuple[bool, int | None]:
        now = self._now()
        async with telling(self._db, Audience.of_user(viewer.id), About.MINE) as connection:
            # Read before the upsert, which is the statement that destroys it.
            before = await opinion_before(
                connection,
                subject_kind="photo_set",
                subject_id=photo_set_id,
                user_id=viewer.id,
                kind=OpinionKind.FAVORITE,
            )
            rows = list(
                await connection.execute_fetchall(
                    _SET_FAVORITE, (photo_set_id, viewer.id, int(favorite), now)
                )
            )
            await record_opinion(
                connection,
                user_id=viewer.id,
                subject_kind="photo_set",
                subject_id=photo_set_id,
                kind=OpinionKind.FAVORITE,
                before=before,
                after=int(favorite),
                at=now,
            )
        return _opinion_of(rows[0])

    async def set_rating(
        self, viewer: Viewer, photo_set_id: str, *, rating: int | None
    ) -> tuple[bool, int | None]:
        now = self._now()
        async with telling(self._db, Audience.of_user(viewer.id), About.MINE) as connection:
            before = await opinion_before(
                connection,
                subject_kind="photo_set",
                subject_id=photo_set_id,
                user_id=viewer.id,
                kind=OpinionKind.RATING,
            )
            rows = list(
                await connection.execute_fetchall(
                    _SET_RATING, (photo_set_id, viewer.id, rating, now)
                )
            )
            await record_opinion(
                connection,
                user_id=viewer.id,
                subject_kind="photo_set",
                subject_id=photo_set_id,
                kind=OpinionKind.RATING,
                before=before,
                after=rating,
                at=now,
            )
        return _opinion_of(rows[0])

    async def set_vault(self, viewer: Viewer, photo_set_id: str, *, vault: bool) -> None:
        """Hide the set and its pictures from this user, or stop; the revocation stamp moves too."""
        now = self._now()
        async with self._db.write() as connection:
            before = await opinion_before(
                connection,
                subject_kind="photo_set",
                subject_id=photo_set_id,
                user_id=viewer.id,
                kind=OpinionKind.HIDE,
            )
            await connection.execute(
                _SET_VAULT, (photo_set_id, viewer.id, int(vault), now if vault else None, now)
            )
            # `hidden_at` is cleared on bringing back; this is what remembers it was ever hidden.
            await record_opinion(
                connection,
                user_id=viewer.id,
                subject_kind="photo_set",
                subject_id=photo_set_id,
                kind=OpinionKind.HIDE,
                before=before,
                after=int(vault),
                at=now,
            )
            moved = await bump_stamps_for_object(connection, ObjectType.PHOTO_SET, photo_set_id)
            announce(moved, About.LIBRARY)

    async def set_tag(self, photo_set_id: str, tag_id: str, *, on: bool) -> None:
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            if on:
                await connection.execute(_SET_TAG, (photo_set_id, tag_id, self._now()))
            else:
                await connection.execute(_UNSET_TAG, (photo_set_id, tag_id))

    async def tags_on(self, photo_set_id: str) -> list[Row]:
        """The tags on one, by name."""
        return await self._db.fetch_all(_TAGS_ON, (photo_set_id,))

    async def from_folder(
        self, folder_id: str, name: str, asset_ids: Sequence[str]
    ) -> tuple[PhotoSet, int]:
        """Make or fill in the set over one folder's pictures, handed in scoped and ordered."""
        existing = await self._db.fetch_one(_BY_FOLDER, (folder_id,))
        photo_set = (
            photo_set_from_row(existing)
            if existing
            else await self._tie_orphan("folder", asset_ids, _TIE_TO_FOLDER, (folder_id,))
            or await self._tie_orphan(VIA_STASH_LIBRARY, asset_ids, _TIE_TO_FOLDER, (folder_id,))
            or await self._made_once(
                lambda: self.create(name, origin="folder", folder_id=folder_id),
                _BY_FOLDER,
                (folder_id,),
            )
        )
        return await self._fill(photo_set, asset_ids, via=VIA_FOLDER)

    async def from_archive(
        self, root_id: str, rel_path: str, name: str, asset_ids: Sequence[str]
    ) -> tuple[PhotoSet, int]:
        """Make or fill in the set over one archive's pictures, keyed on library and path."""
        existing = await self._db.fetch_one(_BY_ARCHIVE, (root_id, rel_path))
        photo_set = (
            photo_set_from_row(existing)
            if existing
            else await self._tie_orphan("archive", asset_ids, _TIE_TO_ARCHIVE, (root_id, rel_path))
            or await self._tie_orphan(
                VIA_STASH_LIBRARY, asset_ids, _TIE_TO_ARCHIVE, (root_id, rel_path)
            )
            or await self._made_once(
                lambda: self.create(
                    name, origin="archive", archive_root_id=root_id, archive_rel_path=rel_path
                ),
                _BY_ARCHIVE,
                (root_id, rel_path),
            )
        )
        return await self._fill(photo_set, asset_ids, via=VIA_ARCHIVE)

    async def _made_once(
        self,
        create: Callable[[], Awaitable[PhotoSet]],
        found_by: str,
        key: tuple[str, ...],
    ) -> PhotoSet:
        """Create the set, or take the one a racing grouping made first."""
        try:
            return await create()
        except IntegrityError:
            existing = await self._db.fetch_one(found_by, key)
            if existing is None:
                raise
            return photo_set_from_row(existing)

    async def holding(self, asset_ids: Sequence[str]) -> str | None:
        """The set that already holds every one of these pictures, where they are most of it."""
        wanted = list(dict.fromkeys(asset_ids))
        if not wanted:
            return None
        found = await self._db.fetch_one(_HOLDING, (json.dumps(wanted), len(wanted)))
        return None if found is None else str(found["id"])

    async def _tie_orphan(
        self, origin: str, asset_ids: Sequence[str], tie: str, to: tuple[str, ...]
    ) -> PhotoSet | None:
        """The set this rule made before its folder or library went, tied to where it is now."""
        found = await self._db.fetch_one(_ORPHAN_HOLDING, (origin, json.dumps(list(asset_ids))))
        if found is None:
            return None
        shared, held = int(found["shared"]), int(found["held"])
        if shared * 2 <= len(asset_ids) or shared * 2 <= held:
            return None
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            rows = list(await connection.execute_fetchall(tie, (*to, str(found["id"]))))
        if not rows:  # pragma: no cover (another pass tied it between the read and the write)
            return None
        return photo_set_from_row(rows[0])

    async def _fill(
        self, photo_set: PhotoSet, asset_ids: Sequence[str], *, via: str
    ) -> tuple[PhotoSet, int]:
        """Put the pictures in and give a coverless set its first one, for both derived rules."""
        added = await self.add(photo_set.id, list(asset_ids), actor=Actor.sift(via))
        if added and photo_set.cover_asset_id is None and asset_ids:
            # Only when there is no cover: a chosen one is a decision and this is a default.
            updated = await self.set_cover(photo_set.id, asset_ids[0], actor=Actor.sift(via))
            if updated is not None:  # pragma: no cover (the set was written a moment ago)
                photo_set = updated
        return photo_set, added


#: Held as a part so nothing imports this slice to reach it.
SERVICE: Part[PhotoSetService] = Part("photo_sets")
