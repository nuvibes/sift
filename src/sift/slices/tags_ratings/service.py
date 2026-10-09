# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tags, shared across the install, and the writes behind the per-person heart and stars."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace

from sift.kernel.access import (
    ENTITY_SORT_SEEN,
    NO_FILTER,
    NO_NARROWING,
    AssetFilter,
    EntityNarrowing,
    Made,
    ObjectType,
    Repository,
    TagPage,
    Viewer,
    bump_stamps_for_object,
    by_user,
)
from sift.kernel.access.repository.walls import shown
from sift.kernel.access.tag_tree import TAGS_ABOVE
from sift.kernel.audience import NOBODY, Audience
from sift.kernel.cache_stamp import bump_cache_stamp
from sift.kernel.changes import About, announce, telling, who_may_see_a_file
from sift.kernel.content.entity_state import opinion_before
from sift.kernel.content.user_state import OpinionKind, record_opinion
from sift.kernel.cover_frame import CoverFrame
from sift.kernel.covers import ChosenCover, chosen_from_row, cover_change
from sift.kernel.db import Connection, Database, Row
from sift.kernel.ids import new_id
from sift.kernel.ledger import ACTOR_USER, Actor, Object, record_event
from sift.kernel.log import get_logger
from sift.kernel.sorting import sort_key
from sift.kernel.sql_splice import splice
from sift.kernel.vocabulary import Subject
from sift.kernel.wiring import Part

log = get_logger(__name__)


def _and_the_actor(told: Audience, actor: Actor) -> Audience:
    """The audience of a membership write, plus whoever made it, who is otherwise never told."""
    if actor.kind == ACTOR_USER and actor.id:
        return told | Audience.of_user(actor.id)
    return told


class TagLoop(Exception):
    """The parent asked for is the tag itself, or a tag already filed somewhere under it."""


class DuplicateTag(Exception):
    """A tag by that name already exists, decided by the insert so two requests cannot race."""


@dataclass(frozen=True, slots=True)
class Opinion:
    """One viewer's heart and stars on a tag."""

    favorite: bool
    rating: int | None


def _opinion(row: Row) -> Opinion:
    return Opinion(
        favorite=bool(row["favorite"]),
        rating=None if row["rating"] is None else int(row["rating"]),
    )


@dataclass(frozen=True, slots=True)
class Tag:
    id: str
    name: str
    created_at: int
    #: Hidden by the user asking; only returned once their Hidden is open, so safe to draw.
    vault: bool = False
    #: A write reply replaces the card's row, so a mark missing here is taken off the card.
    keep_local: bool = False
    keep_from_swaps: bool = False


def tag_from_row(row: Row) -> Tag:
    return Tag(
        id=row["id"],
        name=row["name"],
        created_at=int(row["created_at"]),
        vault=bool(row["vault"]),
        keep_local=bool(row["keep_local"]),
        keep_from_swaps=bool(row["keep_from_swaps"]),
    )


def _unscoped_tag(row: Row) -> Tag:
    """A tag row straight from `tags`, for the caller that just wrote it and knows nobody hid it."""
    return Tag(
        id=row["id"],
        name=row["name"],
        created_at=int(row["created_at"]),
        keep_local=bool(row["keep_local"]),
        keep_from_swaps=bool(row["keep_from_swaps"]),
    )


# `DO NOTHING` rather than a lookup first: the insert is the check.
#: The maker is bound, never written in: an enrichment creates tags with no user.
_INSERT_TAG = """
INSERT INTO tags
  (id, name, name_sort, created_at, created_by_kind, created_by_via, created_by_user_id)
VALUES (?, ?, ?, ?, ?, ?, ?)
ON CONFLICT DO NOTHING
RETURNING *
"""

# `OR IGNORE` likewise; no row means the name is taken or the tag is gone.
#: The ordering key is written with the name, or the wall would sort by the old name.
_UPDATE_TAG = "UPDATE OR IGNORE tags SET name = ?, name_sort = ? WHERE id = ? RETURNING *"

# An upsert: nothing writes a state row until somebody hides the tag.
#: `RETURNING id`, so "no such tag" and "written" are told apart by whether a row came back.
# The moment is written with the file, always: one write, both columns, or neither.
#: The file a cover names and which moment of it; visibility is settled either side of this.
_CHOSEN_COVER = (
    "SELECT cover_asset_id, cover_at_ms, cover_upload_id, cover_frame FROM tags WHERE id = ?"
)

#: One statement writes every cover pointer, so an entity can only ever have one cover.
_SET_TAG_COVER = (
    "UPDATE tags SET cover_asset_id = ?, cover_at_ms = ?, cover_upload_id = ?, cover_frame = ?,"
    " cover_cleared_at = ?, cover_by_default = NULL"
    # `name` beside the id, so the event snapshots the tag's name without a second read.
    " WHERE id = ? RETURNING id, name"
)

# A tag's record: what it means and its other words; aliases are rows the word index joins.
_SET_TAG_RECORD = "UPDATE tags SET description = ?, category = ? WHERE id = ?"
_INSERT_TAG_ALIAS = (
    "INSERT OR IGNORE INTO tag_aliases (id, tag_id, alias, alias_sort, added_at)"
    " VALUES (?, ?, ?, ?, ?)"
)
_CLEAR_TAG_ALIASES = "DELETE FROM tag_aliases WHERE tag_id = ?"

#: When each alias arrived, read before the save rewrites the list, so none is re-dated.
_TAG_ALIAS_MOMENTS = "SELECT alias, added_at FROM tag_aliases WHERE tag_id = ?"
_TAG_ALIASES = (
    "SELECT alias FROM tag_aliases WHERE tag_id = ? ORDER BY COALESCE(alias_sort, alias), id"
)
_TAG_RECORD = "SELECT description, category FROM tags WHERE id = ?"

#: The tag this one is filed under, with its name, for the record's "Part of".
_TAG_PARENT = splice(
    """
SELECT parent.id AS id, parent.name AS name
  FROM tags child JOIN tags parent ON parent.id = child.parent_id
 WHERE child.id = :tag AND {{SHOWN}}
""",
    SHOWN=shown("tag", "parent"),
)
_SET_TAG_PARENT = "UPDATE tags SET parent_id = ? WHERE id = ?"
_TAG_ID_NAMED = "SELECT id FROM tags WHERE name = ?"

_SET_TAG_VAULT = """
INSERT INTO tag_user_state (tag_id, user_id, hidden, hidden_at, updated_at)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(tag_id, user_id) DO UPDATE SET
    hidden     = excluded.hidden,
    hidden_at  = excluded.hidden_at,
    updated_at = excluded.updated_at
"""

# Written out per statement, never assembled from a column name.
_SET_TAG_FAVORITE = """
INSERT INTO tag_user_state (tag_id, user_id, favorite, updated_at)
VALUES (?, ?, ?, ?)
ON CONFLICT(tag_id, user_id) DO UPDATE SET
    favorite   = excluded.favorite,
    updated_at = excluded.updated_at
RETURNING favorite, rating
"""

_SET_TAG_RATING = """
INSERT INTO tag_user_state (tag_id, user_id, rating, updated_at)
VALUES (?, ?, ?, ?)
ON CONFLICT(tag_id, user_id) DO UPDATE SET
    rating     = excluded.rating,
    updated_at = excluded.updated_at
RETURNING favorite, rating
"""

_DELETE_TAG = "DELETE FROM tags WHERE id = ? RETURNING id"

_TAG_NAME = "SELECT name FROM tags WHERE id = ?"

# This user's `vault` mark rides on every read; LEFT JOIN, since most tags have no row.
_TAG_BY_ID = """
SELECT t.*, COALESCE(h.hidden, 0) AS vault
  FROM tags t
  LEFT JOIN tag_user_state h ON h.tag_id = t.id AND h.user_id = ?
 WHERE t.id = ?
"""

# `OR IGNORE` keeps the first answer to who decided and when: a second drag changes nothing.
_ASSIGN = (
    "INSERT OR IGNORE INTO asset_tags (asset_id, tag_id, source, decided_at, box_id)"
    " VALUES (?, ?, ?, ?, ?)"
)

_UNASSIGN = "DELETE FROM asset_tags WHERE asset_id = ? AND tag_id = ?"

_TAGS_OF_ASSET = """
SELECT t.*, COALESCE(h.hidden, 0) AS vault
  FROM tags t
  JOIN asset_tags at ON at.tag_id = t.id
  LEFT JOIN tag_user_state h ON h.tag_id = t.id AND h.user_id = ?
 WHERE at.asset_id = ?
 ORDER BY COALESCE(t.name_sort, t.name) ASC, t.id ASC
"""

#: Which files carry one tag, for the rename and the delete to reindex; unscoped like the index.
_ASSETS_WITH_TAG = "SELECT asset_id FROM asset_tags WHERE tag_id = ?"


class TagService:
    """Tag writes, and the reads that are not permission-scoped."""

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

    async def list_tags(
        self,
        viewer: Viewer,
        prefix: str = "",
        *,
        limit: int = 50,
        offset: int = 0,
        anywhere: bool = False,
        sort: str = ENTITY_SORT_SEEN,
        asset_filter: AssetFilter = NO_FILTER,
        narrowing: EntityNarrowing = NO_NARROWING,
        count_narrowed: bool = False,
    ) -> TagPage:
        """One page of the tag list, scoped, with the total beside it."""
        return await self._access.list_tags(
            viewer,
            prefix,
            limit=limit,
            offset=offset,
            anywhere=anywhere,
            sort=sort,
            asset_filter=asset_filter,
            narrowing=narrowing,
            count_narrowed=count_narrowed,
        )

    async def create(self, name: str, *, made: Made) -> Tag:
        """A new tag, at the top of the tree; raises `DuplicateTag` when the name is taken."""
        async with telling(self._db, who_may_see_a_file, About.LIBRARY) as connection:
            rows = list(
                await connection.execute_fetchall(
                    _INSERT_TAG,
                    (
                        new_id(),
                        name,
                        sort_key(name),
                        self._now(),
                        made.kind,
                        made.via,
                        made.user_id,
                    ),
                )
            )
        if not rows:
            raise DuplicateTag(name)
        return _unscoped_tag(rows[0])

    async def update(self, viewer: Viewer, tag_id: str, name: str) -> Tag | None:
        """Rename. None when there is no such tag, `DuplicateTag` when it is taken."""
        was = await self.get(viewer, tag_id)
        async with telling(self._db, who_may_see_a_file, About.LIBRARY) as connection:
            rows = list(
                await connection.execute_fetchall(_UPDATE_TAG, (name, sort_key(name), tag_id))
            )
            # A line only for a rename: a save that renamed nothing did nothing.
            if rows and was is not None and was.name != name:
                await record_event(
                    connection,
                    actor=Actor.user(viewer.id),
                    verb="renamed",
                    subject=Subject(kind="tag", id=tag_id, name=name),
                    payload=json.dumps({"before": was.name}),
                )
        if rows:
            # Re-read: a rename says nothing about whether this user has the tag hidden.
            return await self.get(viewer, tag_id)
        # Nothing changed: still there means the new name belongs to something else.
        if await self.get(viewer, tag_id) is None:
            return None
        raise DuplicateTag(name)

    async def record_of(self, tag_id: str, viewer: Viewer | None = None) -> dict[str, object]:
        """A tag's record fields, by field key; an empty field is left out, not sent as null."""
        row = await self._db.fetch_one(_TAG_RECORD, (tag_id,))
        if row is None:
            return {}
        record: dict[str, object] = {}
        if row["description"]:
            record["description"] = str(row["description"])
        if row["category"]:
            record["category"] = str(row["category"])
        aliases = [str(one["alias"]) for one in await self._db.fetch_all(_TAG_ALIASES, (tag_id,))]
        if aliases:
            record["aliases"] = aliases
        admin = viewer is None or viewer.is_admin
        parent = await self._db.fetch_one(
            _TAG_PARENT,
            {
                "tag": tag_id,
                "is_admin": int(admin),
                "viewer": None if viewer is None else viewer.id,
            },
        )
        if parent is not None:
            record["parent"] = str(parent["name"])
            record["parent_id"] = str(parent["id"])
        return record

    async def set_record(
        self,
        tag_id: str,
        *,
        description: str | None,
        category: str | None,
        aliases: Sequence[str],
        parent: str | None,
        actor: Actor,
    ) -> None:
        """Write what a tag means, its category, aliases and parent, all replaced whole."""
        name = await self._name_of(tag_id)
        wanted = (parent or "").strip()
        parent_id = await self._parent_named(tag_id, wanted, actor=actor) if wanted else None
        held = await self._alias_moments(tag_id)
        async with telling(self._db, who_may_see_a_file, About.LIBRARY) as connection:
            await self._write_record(
                connection, tag_id, description, category, aliases, held=held, now=self._now()
            )
            await connection.execute(_SET_TAG_PARENT, (parent_id, tag_id))
            # Replaced whole, so the payload says what the tag ended up with.
            await record_event(
                connection,
                actor=actor,
                verb="edited",
                subject=Subject(kind="tag", id=tag_id, name=name),
                payload=json.dumps(
                    {
                        "aliases": [one for one in aliases if one.strip()],
                        "category": category,
                        "parent": wanted or None,
                    }
                ),
            )

    async def _parent_named(self, tag_id: str, wanted: str, *, actor: Actor) -> str:
        """The tag a typed parent names, made when it names none. Raises `TagLoop`."""
        found = await self._db.fetch_one(_TAG_ID_NAMED, (wanted,))
        if found is None:
            try:
                made = await self.create(
                    wanted, made=by_user(actor.id if actor.kind == ACTOR_USER else None)
                )
            except DuplicateTag:
                found = await self._db.fetch_one(_TAG_ID_NAMED, (wanted,))
                if found is None:  # pragma: no cover (deleted in the instant between)
                    raise
            else:
                return made.id
        parent_id = str(found["id"])
        if parent_id == tag_id or await self.is_under(parent_id, tag_id):
            raise TagLoop(wanted)
        return parent_id

    async def refuse_a_loop(self, tag_id: str, wanted: str) -> None:
        """Raise `TagLoop` when filing this tag under `wanted` would make a loop. Writes nothing."""
        found = await self._db.fetch_one(_TAG_ID_NAMED, (wanted.strip(),))
        if found is None:
            return
        parent_id = str(found["id"])
        if parent_id == tag_id or await self.is_under(parent_id, tag_id):
            raise TagLoop(wanted)

    async def is_under(self, tag_id: str, ancestor_id: str) -> bool:
        """Whether `ancestor_id` is somewhere above `tag_id` in the tree."""
        above = await self._db.fetch_all(TAGS_ABOVE, (tag_id,))
        return any(str(row["id"]) == ancestor_id for row in above)

    async def merge_enriched_record(
        self,
        tag_id: str,
        *,
        description: str | None,
        category: str | None,
        aliases: Sequence[str],
    ) -> None:
        """What `set_record` writes, for a stash-box's answer, with no event of its own."""
        held = await self._alias_moments(tag_id)
        async with telling(self._db, who_may_see_a_file, About.LIBRARY) as connection:
            await self._write_record(
                connection, tag_id, description, category, aliases, held=held, now=self._now()
            )

    async def _alias_moments(self, tag_id: str) -> dict[str, object]:
        """When each alias this tag holds arrived, by its folded spelling. See `_write_record`."""
        return {
            str(row["alias"]).casefold(): row["added_at"]
            for row in await self._db.fetch_all(_TAG_ALIAS_MOMENTS, (tag_id,))
        }

    @staticmethod
    async def _write_record(
        connection: Connection,
        tag_id: str,
        description: str | None,
        category: str | None,
        aliases: Sequence[str],
        *,
        held: Mapping[str, object],
        now: int,
    ) -> None:
        """The record's rows, replaced whole. The one set of statements both writers above go through."""
        await connection.execute(
            _SET_TAG_RECORD,
            ((description or "").strip() or None, (category or "").strip() or None, tag_id),
        )
        await connection.execute(_CLEAR_TAG_ALIASES, (tag_id,))
        for alias in dict.fromkeys(one.strip() for one in aliases):
            if alias:
                await connection.execute(
                    _INSERT_TAG_ALIAS,
                    (
                        new_id(),
                        tag_id,
                        alias,
                        sort_key(alias),
                        held.get(alias.casefold(), now) or now,
                    ),
                )

    async def chosen_cover(self, tag_id: str) -> ChosenCover:
        """What this tag is drawn as: an uploaded picture, or a file and a moment of it."""
        row = await self._db.fetch_one(_CHOSEN_COVER, (tag_id,))
        if row is None:  # pragma: no cover (the route resolved the tag a line ago)
            return ChosenCover()
        return chosen_from_row(row)

    async def set_cover(
        self,
        tag_id: str,
        asset_id: str | None,
        at_ms: int | None = None,
        upload_id: str | None = None,
        *,
        actor: Actor,
        frame: CoverFrame | None = None,
        box: Object | None = None,
    ) -> bool:
        """Point a tag at the still it is drawn as; the route checks visibility. False if no tag."""
        async with telling(self._db, who_may_see_a_file, About.LIBRARY) as connection:
            # The cover as it was, so a new window on the same picture reads as a reframe.
            was = list(await connection.execute_fetchall(_CHOSEN_COVER, (tag_id,)))
            change = cover_change(
                chosen_from_row(was[0]) if was else ChosenCover(),
                asset_id=asset_id,
                at_ms=at_ms,
                upload_id=upload_id,
                frame=frame,
                box=box,
            )
            rows = list(
                await connection.execute_fetchall(
                    _SET_TAG_COVER,
                    (asset_id, at_ms, upload_id, change.frame, change.cleared_at, tag_id),
                )
            )
            if rows:
                # The still is the object, so a chosen cover shows on the file's own pane too.
                await record_event(
                    connection,
                    actor=actor,
                    verb="edited",
                    subject=Subject(kind="tag", id=tag_id, name=str(rows[0]["name"])),
                    object=change.object,
                    # Which act it was where no file is the cover, and the box it came from.
                    payload=change.payload,
                )
        return bool(rows)

    async def set_vault(self, viewer: Viewer, tag_id: str, *, vault: bool) -> Tag | None:
        """Hide a tag, or bring it back, for this user only. None when there is no such tag."""
        existing = await self.get(viewer, tag_id)
        if existing is None:
            return None
        now = self._now()
        async with self._db.write() as connection:
            before = await opinion_before(
                connection,
                subject_kind="tag",
                subject_id=tag_id,
                user_id=viewer.id,
                kind=OpinionKind.HIDE,
            )
            await connection.execute(
                _SET_TAG_VAULT, (tag_id, viewer.id, int(vault), now if vault else None, now)
            )
            # `hidden_at` is cleared on bringing back; this is what remembers it was ever hidden.
            await record_opinion(
                connection,
                user_id=viewer.id,
                subject_kind="tag",
                subject_id=tag_id,
                kind=OpinionKind.HIDE,
                before=before,
                after=int(vault),
                at=now,
            )
            # In the same transaction, so the pictures stop being readable from the browser's store.
            announce(await bump_cache_stamp(connection, viewer.id), About.LIBRARY)
        return replace(existing, vault=vault)

    async def set_favorite(self, viewer: Viewer, tag_id: str, *, favorite: bool) -> Opinion | None:
        """Heart a tag, or take the heart off, for this viewer. None when there is no such tag."""
        if not await self.shown(viewer, tag_id):
            return None
        now = self._now()
        async with telling(self._db, Audience.of_user(viewer.id), About.MINE) as connection:
            # Read before the upsert, which is the statement that destroys it.
            before = await opinion_before(
                connection,
                subject_kind="tag",
                subject_id=tag_id,
                user_id=viewer.id,
                kind=OpinionKind.FAVORITE,
            )
            rows = list(
                await connection.execute_fetchall(
                    _SET_TAG_FAVORITE, (tag_id, viewer.id, int(favorite), now)
                )
            )
            await record_opinion(
                connection,
                user_id=viewer.id,
                subject_kind="tag",
                subject_id=tag_id,
                kind=OpinionKind.FAVORITE,
                before=before,
                after=int(favorite),
                at=now,
            )
        return _opinion(rows[0])

    async def set_rating(
        self, viewer: Viewer, tag_id: str, *, rating: int | None
    ) -> Opinion | None:
        """Set the stars on a tag, or clear them with None. This viewer's, not the tag's."""
        if not await self.shown(viewer, tag_id):
            return None
        now = self._now()
        async with telling(self._db, Audience.of_user(viewer.id), About.MINE) as connection:
            before = await opinion_before(
                connection,
                subject_kind="tag",
                subject_id=tag_id,
                user_id=viewer.id,
                kind=OpinionKind.RATING,
            )
            rows = list(
                await connection.execute_fetchall(_SET_TAG_RATING, (tag_id, viewer.id, rating, now))
            )
            await record_opinion(
                connection,
                user_id=viewer.id,
                subject_kind="tag",
                subject_id=tag_id,
                kind=OpinionKind.RATING,
                before=before,
                after=rating,
                at=now,
            )
        return _opinion(rows[0])

    async def delete(self, tag_id: str, *, actor: Actor) -> bool:
        """Delete a tag, forgetting every grant that named it first, as nothing cascades them."""
        await self._access.forget_object(ObjectType.TAG, tag_id)
        # Read before the row goes; afterwards there is no name left to write down anywhere.
        was_called = await self._name_of(tag_id)
        async with self._db.write() as connection:
            rows = list(await connection.execute_fetchall(_DELETE_TAG, (tag_id,)))
            if rows:
                await record_event(
                    connection,
                    actor=actor,
                    verb="deleted",
                    subject=Subject(kind="tag", id=tag_id, name=was_called),
                )
                announce(await who_may_see_a_file(connection), About.LIBRARY)
        deleted = bool(rows)
        if deleted:
            # No name in the log: a tag list says a great deal about what a library is for.
            log.info("tags.deleted", tag_id=tag_id)
        return deleted

    async def _name_of(self, tag_id: str) -> str | None:
        """What one tag is called, unscoped, for the snapshot an event carries."""
        row = await self._db.fetch_one(_TAG_NAME, (tag_id,))
        return None if row is None else str(row["name"])

    async def _names_of(self, tag_ids: Sequence[str]) -> dict[str, str]:
        """What these tags are called, read before the write that may take them off."""
        found: dict[str, str] = {}
        for tag_id in dict.fromkeys(tag_ids):
            name = await self._name_of(tag_id)
            if name is not None:
                found[tag_id] = name
        return found

    async def shown(self, viewer: Viewer, tag_id: str) -> bool:
        """Whether this viewer may be shown the tag: the Tags wall's own answer, by id."""
        return await self._access.visible_tag(viewer, tag_id) is not None

    async def get(self, viewer: Viewer, tag_id: str) -> Tag | None:
        """One tag as this viewer may know it, or None: the same answer for unseen and unknown."""
        if not await self.shown(viewer, tag_id):
            return None
        row = await self._db.fetch_one(_TAG_BY_ID, (viewer.id, tag_id))
        return tag_from_row(row) if row else None

    async def tags_of(self, viewer: Viewer, asset_id: str) -> list[Tag]:
        """Which tags one asset carries; the caller resolved the asset through the access layer."""
        rows = await self._db.fetch_all(_TAGS_OF_ASSET, (viewer.id, asset_id))
        return [tag_from_row(row) for row in rows]

    async def assets_with(self, tag_id: str) -> list[str]:
        """Every file carrying one tag, read after a rename or before a delete."""
        rows = await self._db.fetch_all(_ASSETS_WITH_TAG, (tag_id,))
        return [str(row["asset_id"]) for row in rows]

    async def assign(
        self,
        asset_ids: Sequence[str],
        tag_ids: Sequence[str],
        *,
        add: bool,
        source: str | None = None,
        box_id: str | None = None,
        actor: Actor,
    ) -> int:
        """Attach or detach tags and touch no file on disk; `source` None means a person did it."""
        statement = _ASSIGN if add else _UNASSIGN
        # One stamp for the whole press: it happened at one moment.
        decided_at = self._now()
        written = 0
        moved: set[str] = set()
        #: Only the pairs that really changed: a drag onto a tag already there is not an act.
        pairs: list[tuple[str, str]] = []
        named = await self._names_of(tag_ids)
        async with self._db.write() as connection:
            for asset_id in asset_ids:
                for tag_id in tag_ids:
                    bound = (
                        (asset_id, tag_id, source, decided_at, box_id)
                        if add
                        else (asset_id, tag_id)
                    )
                    cursor = await connection.execute(statement, bound)
                    if cursor.rowcount > 0:
                        written += cursor.rowcount
                        moved.add(tag_id)
                        pairs.append((asset_id, tag_id))
            # One event per file and tag that moved, so a removal is not erased with its row.
            for asset_id, tag_id in pairs:
                await record_event(
                    connection,
                    actor=actor,
                    verb="linked" if add else "unlinked",
                    subject=Subject(kind="asset", id=asset_id),
                    object=Object(kind="tag", id=tag_id, name=named.get(tag_id)),
                )
            # A tag carries shares and hides, so a change rings visibility for tags that moved.
            told = NOBODY
            for tag_id in sorted(moved):
                told |= await bump_stamps_for_object(connection, ObjectType.TAG, tag_id)
            announce(_and_the_actor(told, actor), About.LIBRARY)
        return written


SERVICE: Part[TagService] = Part("tags")
