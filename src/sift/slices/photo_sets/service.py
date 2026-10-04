# SPDX-License-Identifier: AGPL-3.0-or-later
"""Photo-set writes, and the reads those writes check themselves against.

Three rules run through it, and each is the collections slice's rule for the same reason it is that
slice's rule: the two differ in what a set IS, not in how a write is made safe.

**Nothing here decides who may do anything.** Every write is unscoped and the route resolves the set
through the access layer before it calls, which is what stops a row being written against an id the
user may not be shown or one that was never minted.

**Order is data.** A shoot is a sequence, so `position` is written on every add and rewritten on
every rearrange, and it is what a set's contents come back in. A set of pictures shown shuffled is
showing something else.

**Membership may be derived.** This is the whole difference from a collection: `from_folder` builds
a set out of the pictures already sitting in one folder, and `origin` records that it did. A second
pass over that folder finds the set it made rather than making another beside it.
"""

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
    #: The archive it was derived from, when it was: which library, and where the file sits inside
    #: it. The pair is what makes a rescan find the set it made last time rather than make another.
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


#: WHO MADE IT, read off `origin` rather than asked for separately, because `origin` already
#: answers it: three of its four words are passes of Sift's own and the fourth is somebody pressing
#: New. A second argument saying the same thing is a second answer free to disagree with the first.
#: WHICH PASS is read off the same column and for the same reason: `folder`, `download` and
#: `archive` are pass words already, so `created_by_via` is `origin` itself wherever `origin` is
#: not 'manual', and NULL where it is. That is a column repeated rather than a fact invented: the
#: five tables are read together by one wire, and a screen that had to know a Photo Set keeps its
#: provenance somewhere else is a screen with a special case in it.
_INSERT = """
INSERT INTO photo_sets
  (id, name, name_sort, cover_asset_id, origin, origin_url, folder_id,
   archive_root_id, archive_rel_path, created_at, created_by_kind, created_by_via,
   created_by_user_id)
VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)
RETURNING *
"""

# Names are not unique, for the reason a collection's are not: two shoots really can be called
# "beach", and the id is what tells them apart.
_RENAME = "UPDATE photo_sets SET name = ?, name_sort = ? WHERE id = ? RETURNING *"
_SET_NOTES = "UPDATE photo_sets SET notes = ? WHERE id = ? RETURNING *"
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
    "SELECT cover_asset_id, cover_at_ms, cover_upload_id, cover_frame FROM photo_sets WHERE id = ?"
)

#: One statement writes EVERY cover pointer, which is what makes "an entity has one cover" a
#: property of the schema's use rather than a rule each writer remembers. A file cover, an uploaded
#: cover, and the moment of a file are three columns and one decision, so choosing any of them
#: clears the other two, and there is no arrangement in which a row claims both kinds at once.
_SET_COVER = (
    "UPDATE photo_sets SET cover_asset_id = ?, cover_at_ms = ?, cover_upload_id = ?, cover_frame = ?,"
    # The clear mark, and the rule's default let go (`kernel/access/default_covers.py`).
    " cover_cleared_at = ?, cover_by_default = NULL WHERE id = ? RETURNING *"
)
_DELETE = "DELETE FROM photo_sets WHERE id = ?"
# Sift's own sets under a floor. The count is correlated rather than joined and grouped, because
# what is asked is "which sets", and a set with no pictures at all (one whose files have since
# gone) has no row to group and is exactly one that should answer.
_UNDER_FLOOR = (
    "SELECT ps.id AS id FROM photo_sets ps WHERE ps.origin != 'manual'"
    " AND (SELECT COUNT(*) FROM photo_set_items psi WHERE psi.photo_set_id = ps.id) < ?"
    " ORDER BY ps.id"
)

#: What one grouping is CALLED, for the snapshot an event carries. Asked before a write rather
#: than as part of one, which is why it is its own one-column read.
_NAME = "SELECT name FROM photo_sets WHERE id = ?"
_ONE = "SELECT * FROM photo_sets WHERE id = ?"

#: Where the next picture goes. `MAX + 1` rather than a count, because a removal leaves a gap and a
#: count would then hand out a position something already holds.
_NEXT_POSITION = (
    "SELECT COALESCE(MAX(position), -1) + 1 AS next FROM photo_set_items WHERE photo_set_id = ?"
)

#: `added_at` is NAMED, and the value is bound rather than defaulted: the column is nullable, so a
#: writer that left it out would write a row saying this picture went in at no moment at all,
#: which reads exactly like a row from before the column existed.
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

#: The set already derived from one folder, if there is one. What makes a second pass idempotent.
_BY_FOLDER = "SELECT * FROM photo_sets WHERE folder_id = ? LIMIT 1"

# And the same question for an archive. The pair, because a path is only unique within one library:
# two of them can each hold a `galleries/100200.zip`, and a lookup on the path alone would hand back
# the wrong library's shoot and then fold this one's pictures into it.
_BY_ARCHIVE = """
SELECT * FROM photo_sets
 WHERE archive_root_id = ? AND archive_rel_path = ?
 LIMIT 1
"""

#: A set one of the two rules made whose folder or library has gone, holding the most of these
#: pictures, with how many it holds in all. What a library taken out and added back finds: every
#: folder row under it is new (removing a library forgets its folders, and the set's link with
#: them), while the pictures are the same files, so the pictures are what still name the set.
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

#: The set holding every one of these pictures, where they are most of it: the same shoot, made
#: into a set here by another rule. The fullest first, then the oldest.
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

#: Tie an orphaned set to the folder or archive it was found for. Guarded on still being an orphan,
#: so a second pass racing this one cannot move a set that has just been tied.
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
    """Photo-set writes. Takes the access layer as well as the database because deleting a set has
    to forget the grants that named it, and because a hide has to move the revocation stamp."""

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
        """A new, empty set. It starts with no cover, because a cover is one of the pictures.

        The two derived kinds pass what they were derived FROM, and the database refuses a second
        set over either. See the unique indexes. So a caller that forgets to look first gets an
        error rather than a duplicate somebody finds on a wall weeks later.
        """
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
        """Rename. None when there is no such grouping.

        The name it had goes into the payload, because the column is overwritten in place: a line
        saying it was renamed and unable to say what from is half a record.
        """
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
        """What this set is drawn as: an uploaded picture, or a file and a moment of it.

        One shape rather than a widening tuple. See `ChosenCover`, and `serve_cover` for which of
        the two wins.
        """
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
            # The cover as it WAS, in the write's own transaction: the same picture with a new
            # window is a reframe and says so. See `kernel/covers.py cover_change`.
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
                # The still is the OBJECT and the set the subject, so a picture chosen as a
                # cover says so on the FILE's own pane too: `history_events` reads an event from
                # both sides. None where the cover was cleared: an act with nothing on the other
                # end of it.
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
        """The sets Sift made that hold fewer pictures than the floor, oldest first.

        Sift's own and never a person's: `origin` says how a set was made, and one somebody
        assembled by hand is theirs whatever its size. Read for `jobs.dissolve_under_floor`, which
        is what takes a raised `MIN_PICTURES` to the sets made before it moved.
        """
        rows = await self._db.fetch_all(_UNDER_FLOOR, (floor,))
        return [str(row["id"]) for row in rows]

    async def delete(
        self, photo_set_id: str, *, actor: Actor, under_floor: int | None = None
    ) -> None:
        """Delete the set. The pictures are untouched: a set is a grouping, not a place.

        The grants that named it are forgotten too: that is what stops a stale row outliving the
        id and attaching itself to whatever reuses it.

        `under_floor` is WHY, where the set went because it held fewer pictures than that: the
        floor pass says so, and the event carries the number (`{"under_floor": n}`), so the line
        can say "fewer than 10 pictures" rather than only that a set was deleted.
        """
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
        """Put pictures in, at the end, in the order given. Returns how many rows were new.

        Idempotent by the primary key, so re-adding something already in the set is a no-op rather
        than an error, which is what a folder pass running twice needs.
        """
        added = 0
        landed: list[str] = []
        named = await self._name_of(photo_set_id)
        async with self._db.write() as connection:
            row = await (await connection.execute(_NEXT_POSITION, (photo_set_id,))).fetchone()
            position = int(row["next"]) if row else 0
            # One moment for the whole add. A folder pass puts hundreds of pictures in at once and
            # they went in together; reading the clock per picture would spread one act over
            # seconds and say they were each decided on separately.
            now = self._now()
            for asset_id in asset_ids:
                cursor = await connection.execute(
                    _ADD_ITEM, (photo_set_id, asset_id, position, now)
                )
                if cursor.rowcount:
                    added += 1
                    position += 1
                    landed.append(asset_id)
            # Membership carries no timestamp column at all, so this event is the whole of what
            # says when a picture was put here, and it is written only for a row that landed,
            # because re-adding something already in the grouping changed nothing.
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
        """Take pictures out. Returns how many rows went.

        The removal is the half that vanishes without a record: the membership row is deleted, so
        a picture taken out of a grouping stops ever having been in it.
        """
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
            # Read before the upsert, which is the statement that destroys it. See
            # `kernel.content.entity_state` for why the upsert's own RETURNING cannot answer this.
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
        """Hide the set from this user, or stop hiding it.

        Hiding a set conceals its PICTURES as well as its row (the resolver carries the arm),
        so the revocation stamp moves in the same transaction. Without that a picture already in a
        browser cache would go on being served from it after being hidden.
        """
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
            # `hidden_at` says only when the CURRENT concealment began: bringing a set back clears
            # it, and with it the fact that it was ever hidden. This is what remembers.
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
        """The tags on one, by name.

        Rows rather than a type, for the reason the collection reader beside it gives: the
        tag's shape belongs to the tags slice, and importing it here would couple the two.
        """
        return await self._db.fetch_all(_TAGS_ON, (photo_set_id,))

    async def from_folder(
        self, folder_id: str, name: str, asset_ids: Sequence[str]
    ) -> tuple[PhotoSet, int]:
        """Make a set out of the pictures already in one folder, or fill in the one that exists.

        Idempotent on the folder, which is the whole point: a folder swept twice finds the set it
        made the first time and adds only what has arrived since. Two sets over one folder would be
        two answers to "what was this shoot".

        The pictures are handed IN, already ordered, rather than looked up here, which is not a
        convenience. Every read of the library goes through the access layer, and a hand-written
        query against the asset tables returns rows to whoever asked, so a set derived from a
        folder holding something in the vault would sweep it in. The derive rule gathers them,
        stills only, in name order, from a folder's own files: a background pass, which is the one
        reader the access layer does not sit in front of.
        """
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
        """Make a set out of the pictures in one archive, or fill in the one that exists.

        The archive counterpart of `from_folder`, and it is here rather than left to the caller for
        the reason that one is: creating unconditionally would make a new set out of one shoot on
        every scan, because nothing could recognise the set the last scan had made.

        Keyed on the library AND the path, never the path alone. Two libraries can each hold a
        `galleries/100200.zip`, and a key that cannot tell them apart would fold one shoot's
        pictures into the other's set.

        The pictures are handed IN, already ordered, exactly as `from_folder` takes them, and for
        the same reason: a query written here would read the asset tables without a permission
        check in it.
        """
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
        """Create the set, or take the one another grouping made between the look and the write.

        Two groupings can meet one folder or archive at once (a scan and a Stash import); the
        unique index refuses the second set, and the loser answers with the winner's.
        """
        try:
            return await create()
        except IntegrityError:
            existing = await self._db.fetch_one(found_by, key)
            if existing is None:
                raise
            return photo_set_from_row(existing)

    async def holding(self, asset_ids: Sequence[str]) -> str | None:
        """The set that already holds every one of these pictures, where they are most of it.

        For a grouping read from somewhere else (a Stash gallery) whose pictures an archive or a
        folder here has already made into a set: that set is the answer, and a second made beside
        it would be the same shoot twice. Ids only, so nothing of a picture is read here.
        """
        wanted = list(dict.fromkeys(asset_ids))
        if not wanted:
            return None
        found = await self._db.fetch_one(_HOLDING, (json.dumps(wanted), len(wanted)))
        return None if found is None else str(found["id"])

    async def _tie_orphan(
        self, origin: str, asset_ids: Sequence[str], tie: str, to: tuple[str, ...]
    ) -> PhotoSet | None:
        """The set this rule made before its folder or library went, tied to where it is now.

        A library taken out and added back gives every folder a new row, and the set's link went
        with the old one; made afresh, every derived set would double, with the old one still
        holding its name, cover and tags beside a new blank one. So a set with no link that holds
        MOST of these pictures, and whose pictures are mostly these, is the same shoot come back
        and takes the new link. Most both ways, so a folder holding a few pictures copied out of an
        old shoot does not take that shoot over. A folder moved or renamed on disk is found the
        same way. A set whose folder never returns stays what it is: a grouping of files.

        Asked for a Stash library's sets too: an import can land before the folder or archive is
        grouped (the switch for it off at the time), and that gallery is the same shoot.
        """
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
        """Put the pictures in and give the set a cover if it has none. What both derived rules do
        once they have found or made their set, written once, so the two cannot drift into
        answering differently about which picture becomes the cover.

        `via` is which of the two rules is filling it, and the record says so: a set a folder made
        and one an archive made are two tasks, and "Sift" alone said neither."""
        added = await self.add(photo_set.id, list(asset_ids), actor=Actor.sift(via))
        if added and photo_set.cover_asset_id is None and asset_ids:
            # The first picture becomes the cover, so a derived set is never a blank card. Only when
            # there is not one already: a cover somebody chose is a decision and this is a default.
            updated = await self.set_cover(photo_set.id, asset_ids[0], actor=Actor.sift(via))
            if updated is not None:  # pragma: no cover (the set was written a moment ago)
                photo_set = updated
        return photo_set, added


#: The one photo-set service. Held as a part so nothing imports this slice to reach it.
SERVICE: Part[PhotoSetService] = Part("photo_sets")
