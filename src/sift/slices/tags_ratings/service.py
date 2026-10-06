# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tags, and the writes behind the heart and the stars.

Two things live here, and they are deliberately not the same shape.

A tag is shared. There is one `tags` table for the whole install, and a tag carries access grants:
a share on a tag reaches every asset under it. Editing a tag edits what everybody sees, and
deleting one throws away grants that were somebody's guarantee, so the tag surfaces are admin-only
and the delete has to clean up after itself.

The heart and the rating are the opposite: one row per person per asset. Those are not written
here at all. They go through the kernel's store, which three features share, because a second
implementation would be a second set of rules about what a rating is allowed to be.
"""

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
    """The audience of a membership write, plus whoever made it.

    `bump_stamps_for_object` names the users whose VIEW the write changed, and on an install
    where nothing is shared that is nobody, so the user who put a file in a collection would
    never be told, and their own History threads and other tabs would go on showing the file
    where it was. The writer always hears of its own write; a pass of Sift's has no user to tell.
    """
    if actor.kind == ACTOR_USER and actor.id:
        return told | Audience.of_user(actor.id)
    return told


async def _whoever_may_see(database: Database) -> Audience:
    """Every admin and every user given anything. Read before the write, so `telling` still says
    nothing when no row moved."""
    async with database.read() as connection:
        return await who_may_see_a_file(connection)


class TagLoop(Exception):
    """The parent asked for is the tag itself, or a tag already filed somewhere under it.

    Refused rather than stored: a loop would make the tag its own ancestor, and a wall drawing
    where a tag is filed could never finish drawing it.
    """


class DuplicateTag(Exception):
    """A tag by that name already exists.

    Decided by the write itself rather than by a check in front of it. `tags.name` is unique under
    a case-insensitive collation, and asking first would leave a gap between the question and the
    answer: two requests naming the same new tag at the same moment both find nothing and both
    go on to insert. The conflict clause closes the gap: the row either lands or it does not, and
    which one happened comes back from the same statement.
    """


@dataclass(frozen=True, slots=True)
class Opinion:
    """One viewer's heart and stars on a tag.

    Its own type rather than the people slice's `EntityState`, which is the identical pair: slices
    do not import one another's models, and the shape agreeing is not a reason to couple them.
    """

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
    #: Hidden by the user asking. A tag they hid does not come back from the scoped list at all
    #: while their Hidden is shut, so anything reading this has already entered the PIN, which is
    #: what makes it safe to draw.
    vault: bool = False
    #: Whether this tag may never be sent outside the machine. See `TagSuggestion` in the
    #: repository's views: a write reply is what a screen puts in place of the row it was holding,
    #: so a mark missing here is a mark a rename takes off the card.
    keep_local: bool = False
    #: Marked "Don't swap", for the same reason.
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
    """A tag row straight from `tags`, with no user attached.

    For the one caller that has just written the row and knows nobody has hidden it. Everything else
    reads through a statement that joins the asking user's state, because "is this hidden" has no
    answer without one.
    """
    return Tag(
        id=row["id"],
        name=row["name"],
        created_at=int(row["created_at"]),
        keep_local=bool(row["keep_local"]),
        keep_from_swaps=bool(row["keep_from_swaps"]),
    )


# `DO NOTHING` rather than a lookup first: the insert is the check. A name already taken comes back
# as no row, from the same statement that would have written one.
#: !! THE MAKER IS BOUND, never written into the statement: the Tags screen reaches it, and so
#: does an enrichment (`composition.LibraryNaming.tag_named` calls `create` with no user). A
#: statement that said a person made every tag would have every tag a stash-box answer invented
#: claim a person made it, with a NULL where the person should be. The pass is bound with it, and
#: the enricher says which it is.
_INSERT_TAG = """
INSERT INTO tags
  (id, name, name_sort, created_at, created_by_kind, created_by_via, created_by_user_id)
VALUES (?, ?, ?, ?, ?, ?, ?)
ON CONFLICT DO NOTHING
RETURNING *
"""

# `OR IGNORE` for the same reason, with one difference: no row here means either that the name is
# taken or that there is no such tag, so the caller separates them by looking the tag up first.
#: The ordering key is written WITH the name, or the wall would sort a renamed tag by its old name.
_UPDATE_TAG = "UPDATE OR IGNORE tags SET name = ?, name_sort = ? WHERE id = ? RETURNING *"

# Hiding a tag, for one user. An upsert because the state lives in a row of its own and most of
# them do not exist: nothing writes one until somebody hides the tag.
#: Point a tag at the still it is drawn as, or clear it.
#:
#: `RETURNING id` rather than a rowcount, so "no such tag" and "written" are told apart by whether
#: a row came back: the same shape every other write in this service uses.
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
    "SELECT cover_asset_id, cover_at_ms, cover_upload_id, cover_frame FROM tags WHERE id = ?"
)

#: One statement writes EVERY cover pointer, which is what makes "an entity has one cover" a
#: property of the schema's use rather than a rule each writer remembers. A file cover, an uploaded
#: cover, and the moment of a file are three columns and one decision, so choosing any of them
#: clears the other two, and there is no arrangement in which a row claims both kinds at once.
_SET_TAG_COVER = (
    "UPDATE tags SET cover_asset_id = ?, cover_at_ms = ?, cover_upload_id = ?, cover_frame = ?,"
    # The clear mark, and the rule's default let go (`kernel/access/default_covers.py`).
    " cover_cleared_at = ?, cover_by_default = NULL"
    # `name` beside the id, so the event written in the same transaction snapshots what the tag
    # was called without a second read.
    " WHERE id = ? RETURNING id, name"
)

# --- A tag's record --------------------------------------------------------------------------
#
# What a tag MEANS, so two people use it the same way, plus the other words for the same thing.
#
# Aliases in their own table for the reason a person's and a site's are: an alias is SEARCHED, so it
# has to be a row the word index can join rather than a list inside a column. `INSERT OR IGNORE`
# because the form sends the whole list on every save.
_SET_TAG_RECORD = "UPDATE tags SET description = ?, category = ? WHERE id = ?"
_INSERT_TAG_ALIAS = (
    "INSERT OR IGNORE INTO tag_aliases (id, tag_id, alias, alias_sort, added_at)"
    " VALUES (?, ?, ?, ?, ?)"
)
_CLEAR_TAG_ALIASES = "DELETE FROM tag_aliases WHERE tag_id = ?"

#: What this tag's aliases arrived at, read before the save rewrites the list.
#:
#: The form sends the whole list every time and the save CLEARS and re-inserts it, so without this
#: every alias would be re-dated by any later edit: a word that has been there a year reading as
#: added today, which is the one thing a moment column may not do. Keyed on the FOLDED spelling,
#: because that is what the table's uniqueness is keyed on.
_TAG_ALIAS_MOMENTS = "SELECT alias, added_at FROM tag_aliases WHERE tag_id = ?"
_TAG_ALIASES = (
    "SELECT alias FROM tag_aliases WHERE tag_id = ? ORDER BY COALESCE(alias_sort, alias), id"
)
_TAG_RECORD = "SELECT description, category FROM tags WHERE id = ?"

#: The tag this one is filed under, with its name, for the record's "Part of". The same pair a
#: site's parent is read as: the value is the name, and the id rides beside it (`links_to`).
_TAG_PARENT = splice(
    """
SELECT parent.id AS id, parent.name AS name
  FROM tags child JOIN tags parent ON parent.id = child.parent_id
 WHERE child.id = :tag AND {{SHOWN}}
""",
    SHOWN=shown("tag", "parent"),
)
_SET_TAG_PARENT = "UPDATE tags SET parent_id = ? WHERE id = ?"
#: A tag by its name, which the column compares without case.
_TAG_ID_NAMED = "SELECT id FROM tags WHERE name = ?"

_SET_TAG_VAULT = """
INSERT INTO tag_user_state (tag_id, user_id, hidden, hidden_at, updated_at)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(tag_id, user_id) DO UPDATE SET
    hidden     = excluded.hidden,
    hidden_at  = excluded.hidden_at,
    updated_at = excluded.updated_at
"""

# The heart and the stars on a tag, for one user. Upserts for the same reason the vault write
# above is one: the state lives in a row of its own and most of them do not exist until somebody
# says something. Written out per statement rather than assembled from a column name: building SQL
# from parts is how a column name reaches a query as data, and there are only two of them.
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

#: What one tag is CALLED and nothing else, for the snapshot an event carries.
_TAG_NAME = "SELECT name FROM tags WHERE id = ?"

# `vault` rides along on every read of a tag because it is what a chip draws its mark from, and it
# is THIS user's, joined on the viewer. No matching row means not hidden, which is the ordinary
# case and why it is a LEFT JOIN and a COALESCE rather than a row written when a tag is made.
_TAG_BY_ID = """
SELECT t.*, COALESCE(h.hidden, 0) AS vault
  FROM tags t
  LEFT JOIN tag_user_state h ON h.tag_id = t.id AND h.user_id = ?
 WHERE t.id = ?
"""

# `OR IGNORE` because assigning a tag something already carries is not an error. Drag the same
# clip onto the same chip twice and the second one is a no-op, which is what somebody doing it
# means by it.
# `decided_at` travels with the source, because "who decided this" and "when" are two halves of one
# question and the row could only answer the first. `OR IGNORE` keeps the FIRST answer to both: a
# second drag does not restamp the tag with today.
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

#: The same join read the other way round: which files carry one tag.
#:
#: Asked by the rename and the delete, so they can hand the word index the files it has to rewrite.
#: A rebuild of the WHOLE index instead would cost many seconds of the write lock on a large
#: library, paid identically for a tag on ten thousand files and for a tag on none, and a tag on
#: none is the ordinary case when somebody is tidying up a vocabulary.
#:
#: Unscoped, like `_TAGS_OF_ASSET` beside it and for a stronger reason: the index holds one row per
#: file with no user in it, so an index written from what one admin may see would be wrong for
#: everybody else.
_ASSETS_WITH_TAG = "SELECT asset_id FROM asset_tags WHERE tag_id = ?"


class TagService:
    """Tag writes, and the reads that are not permission-scoped.

    Anything that has to be scoped by viewer is the access layer's: `suggest_tags` for the list
    and `visible_assets` for a tag's contents. This owns the writes and the seam in front of the
    per-person store.
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
        """One page of the tag list, scoped, with the total beside it.

        One lister, in the access layer, shared with search: the dropdowns take the first page of
        it and throw the count away.

        `count_narrowed` is passed straight through rather than decided here: only the caller knows
        whether a press on one of these cards carries the wall it was pressed from. See the access
        layer's `list_tags`.
        """
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
        """A new tag, at the top of the tree. `set_record` files it under another.

        Raises `DuplicateTag` when the name is taken. Taken is case-insensitive, so "Beach" and
        "beach" are one tag and the second attempt is refused rather than quietly making a
        near-duplicate nobody can tell apart in a list.

        It starts visible to everybody, which needs no lookup to know: hiding is per user and
        this tag did not exist a statement ago, so nobody has hidden it.

        `made` is who is making it, and there is no default: two callers reach this statement and
        they are not the same act: the Tags screen, where somebody typed the name, and a
        stash-box answer naming a tag this library has never held. ONE argument for the maker,
        built by the route: a second one for the user would be two arguments for one fact, free
        to disagree.
        """
        async with telling(self._db, await _whoever_may_see(self._db), About.LIBRARY) as connection:
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
        async with telling(self._db, await _whoever_may_see(self._db), About.LIBRARY) as connection:
            rows = list(
                await connection.execute_fetchall(_UPDATE_TAG, (name, sort_key(name), tag_id))
            )
            # A line only for a RENAME, the one thing this statement can change now a tag has no
            # colour; an "edited" line for a save that renamed nothing would say something happened
            # when nothing did. The record half writes its own line (`set_record`). An "Edited the
            # color" line an older build wrote still reads: the field word comes from the key it
            # recorded, not from any column (`sentences.edited_fields`).
            if rows and was is not None and was.name != name:
                await record_event(
                    connection,
                    actor=Actor.user(viewer.id),
                    verb="renamed",
                    subject=Subject(kind="tag", id=tag_id, name=name),
                    payload=json.dumps({"before": was.name}),
                )
        if rows:
            # Re-read rather than built from what was written: a rename says nothing about whether
            # this user has the tag hidden, and that is what the chip draws its mark from.
            return await self.get(viewer, tag_id)
        # The write changed nothing, which is two different answers. Looking the tag up tells them
        # apart: still there means the new name belongs to something else.
        if await self.get(viewer, tag_id) is None:
            return None
        raise DuplicateTag(name)

    async def record_of(self, tag_id: str, viewer: Viewer | None = None) -> dict[str, object]:
        """A tag's record fields, by field key. Empty for a tag nobody has described.

        A field with nothing in it is left OUT rather than carried as null: absent and null read
        the same to everything that draws a record, and leaving it out keeps a library of four
        hundred plain tags from sending three nulls each.
        """
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
        """Write what a tag means, its category, its other names and its parent: all replaced whole.

        The aliases are cleared and rewritten rather than diffed, for the reason a site's are: the
        form sends the entire list every time, so a diff would be working out what changed from two
        lists that are already the before and the after.

        An empty box is nothing, not an empty string. A tag whose description is `''` and one whose
        description was never written are the same tag, and storing two values for it would make
        every reader decide which of them means "blank".

        The parent is typed as a NAME, as a site's is: found among the tags, and made when it names
        none, because a word somebody filed this under that did nothing would be the field looking
        as though it had worked. Raises `TagLoop` for the tag itself or one already under it.
        """
        name = await self._name_of(tag_id)
        wanted = (parent or "").strip()
        parent_id = await self._parent_named(tag_id, wanted, actor=actor) if wanted else None
        held = await self._alias_moments(tag_id)
        async with telling(self._db, await _whoever_may_see(self._db), About.LIBRARY) as connection:
            await self._write_record(
                connection, tag_id, description, category, aliases, held=held, now=self._now()
            )
            await connection.execute(_SET_TAG_PARENT, (parent_id, tag_id))
            # Every field here is replaced whole and the aliases are cleared first, so what a save
            # took away leaves nothing behind at all. The payload says what the tag ended up with,
            # which is the only true statement a writer that replaces can make.
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
        """The tag a typed parent names, made when it names none. Raises `TagLoop`.

        A parent that is this tag, or that is already filed under it, is refused: the walk up from
        the parent must never arrive back here.
        """
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
        """Raise `TagLoop` when filing this tag under `wanted` would make a loop. Writes nothing.

        A name that names no tag yet cannot make one: it would be made fresh, with nothing under it.
        """
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
        """What `set_record` writes, for a stash-box's answer, with NO event of its own.

        The same rows and the same replace-whole rule; the difference is only the ledger. A person
        hand-typing a tag's record is an `edited` act and `set_record` says so. An ask that filled
        it is recorded ONCE, as the `enriched` event `StashBoxService.record_enrichment` writes
        beside the run (naming the box and what landed), and an `edited` line from here as well
        would draw the one press twice ("Edited the aliases" beside "FansDB filled in its
        aliases"). The person writer's `merge_person_record` writes no event for the same reason.
        """
        held = await self._alias_moments(tag_id)
        async with telling(self._db, await _whoever_may_see(self._db), About.LIBRARY) as connection:
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
                        # An alias that survives the save keeps the moment it arrived at.
                        held.get(alias.casefold(), now) or now,
                    ),
                )

    async def chosen_cover(self, tag_id: str) -> ChosenCover:
        """What this tag is drawn as: an uploaded picture, or a file and a moment of it.

        One shape rather than a widening tuple. See `ChosenCover`, and `serve_cover` for which of
        the two wins.
        """
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
        """Point a tag at the still it is drawn as. False when there is no such tag.

        `box` is the stash-box an uploaded picture came from, where one did; the event names it.

        The asset is NOT checked for visibility here, and that is the caller's job rather than an
        omission: the route knows the viewer and refuses an id they may not see. The read side
        refuses to hand back a cover the asker may not open regardless, so a cover set to something
        concealed comes out as no cover rather than as a leak. The same division People and
        Sites use.
        """
        async with telling(self._db, await _whoever_may_see(self._db), About.LIBRARY) as connection:
            # The cover as it WAS, in the write's own transaction: the same picture with a new
            # window is a reframe and says so. See `kernel/covers.py cover_change`.
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
                # The still is the OBJECT and the tag the subject, so a picture chosen as a tag's
                # cover says so on the FILE's own pane too. See `history_events`, which reads an
                # event from both sides. None where the cover was cleared, which is an act with
                # nothing on the other end of it.
                await record_event(
                    connection,
                    actor=actor,
                    verb="edited",
                    subject=Subject(kind="tag", id=tag_id, name=str(rows[0]["name"])),
                    object=change.object,
                    # Which act it was where no file is the cover (a picture sent in, or the
                    # cover taken away) and the box a fetched one came from, as the person's and
                    # the site's writer say it. See `covers.cover_payload`.
                    payload=change.payload,
                )
        return bool(rows)

    async def set_vault(self, viewer: Viewer, tag_id: str, *, vault: bool) -> Tag | None:
        """Hide a tag, or bring it back, for this user. None when there is no such tag.

        Hiding a tag conceals every file carrying it and takes the tag itself off the wall (a name
        with a zero beside it says what is being kept back and what it is filed under), all on the
        screens of the user who did it. Nobody else is affected; to keep something from another
        user, restrict it. Whether the caller is allowed to do either is settled before this is
        reached; see the route.

        The tag is looked up first rather than relying on the write to fail, because the write
        cannot fail: the state row is this user's own and would happily be created against an id
        that names nothing.
        """
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
            # `hidden_at` says only when the CURRENT concealment began: bringing a tag back clears
            # it, and with it the fact that it was ever hidden. This is what remembers.
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
            # In the same transaction as the hide, which is what stops the pictures of everything
            # carrying this tag staying readable out of the browser's own store afterwards.
            announce(await bump_cache_stamp(connection, viewer.id), About.LIBRARY)
        return replace(existing, vault=vault)

    async def set_favorite(self, viewer: Viewer, tag_id: str, *, favorite: bool) -> Opinion | None:
        """Heart a tag, or take the heart off. This viewer's, not the tag's.

        None when there is no such tag. Looked up first rather than relying on the write to fail,
        because the write cannot fail: the state row is this user's own and would happily be
        created against an id that names nothing, and a row keyed on an id that was never minted
        is one nothing will ever clean up, since the cascade it hangs off has nothing to cascade
        from. The same reasoning as `set_vault` above, and the same shape.

        A tag this viewer may not be shown is no such tag here, so an id answers the same whether
        or not it names something, and no row is written against it.
        """
        if not await self.shown(viewer, tag_id):
            return None
        now = self._now()
        async with telling(self._db, Audience.of_user(viewer.id), About.MINE) as connection:
            # Read before the upsert, which is the statement that destroys it. See
            # `kernel.content.entity_state` for why the upsert's own RETURNING cannot answer this.
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
        """Set the stars on a tag, or clear them with None. This viewer's, not the tag's.

        Zero is not a rating and is refused by the model rather than quietly read as "unrated":
        the same rule an asset's rating follows, so one number means one thing everywhere.
        """
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
        """Delete a tag, and forget every grant that named it.

        The grant half is the reason this is not one statement. `acl_grants.object_id` carries no
        foreign key (it names a different table depending on the type beside it), so nothing
        cascades, and a tag deleted on its own leaves its grants behind to apply to whatever ends
        up with that id. A stale share hands somebody a file; a stale restrict is a promise that
        stopped being kept. Neither announces itself.

        Forgotten first, so a failure between the two leaves grants naming a tag that still exists
        rather than grants naming nothing.
        """
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
                # Its grants went first, before the row: this is the commit a re-read sees it gone.
                announce(await who_may_see_a_file(connection), About.LIBRARY)
        deleted = bool(rows)
        if deleted:
            # No name in the log. A tag list says a great deal about what a library is for, which
            # is exactly what somebody reading a log file has no need to learn.
            log.info("tags.deleted", tag_id=tag_id)
        return deleted

    async def _name_of(self, tag_id: str) -> str | None:
        """What one tag is called, unscoped, for the snapshot an event carries.

        Unscoped for the reason `_ASSETS_WITH_TAG` is: what a row is NAMED is not a permission
        question, the caller has already settled what it may touch, and a pass has no viewer to
        scope with.
        """
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
        """One tag as this viewer may know it, or None: the same answer for unseen and unknown.

        Scoped through the access layer first, as every other entity read is. Read unscoped, a
        route that answers from this (hiding a tag, adding tags to files) would tell a guest which
        ids name a real tag it may not see, by answering those differently from an id that names
        nothing: an oracle over what exists. The row itself is still read here because it carries
        this viewer's own `vault` mark, which the access layer's tag does not.
        """
        if not await self.shown(viewer, tag_id):
            return None
        row = await self._db.fetch_one(_TAG_BY_ID, (viewer.id, tag_id))
        return tag_from_row(row) if row else None

    async def tags_of(self, viewer: Viewer, asset_id: str) -> list[Tag]:
        """Which tags one asset carries.

        The viewer is not what decides whether a row comes back: the caller has already resolved
        the asset through the access layer, and this is a read of the join table rather than of the
        asset. It is here because the mark on each chip is the asking user's own. Calling this
        with an id that was not resolved through the access layer is the bug it cannot defend
        against itself.
        """
        rows = await self._db.fetch_all(_TAGS_OF_ASSET, (viewer.id, asset_id))
        return [tag_from_row(row) for row in rows]

    async def assets_with(self, tag_id: str) -> list[str]:
        """Every file carrying one tag. The tag's name and its other names are indexed on each.

        Read AFTER a rename, where the assignments do not move, and BEFORE a delete, where the
        cascade takes them: each caller says which it is doing.
        """
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
        """Attach or detach tags, and touch no file on disk.

        This is the whole promise of organising logically: a clip dragged onto a tag chip moves
        nothing, is copied nowhere, and its path is the same afterwards. The only table written is
        the join. There is a test that asserts the bytes and the location row are untouched, and
        it is the most load-bearing one in the slice.

        The caller resolves every asset through the access layer first. Nothing here can tell a
        visible asset from a hidden one, which is why it does not try to.

        `source` records HOW a tag got onto a file, and the default is the important half of it:
        None means a person did this, which is what every call from a screen means. Anything that
        applies tags on its own names itself, so the two can be told apart afterwards: filtered,
        counted, and taken back off without touching the ones somebody chose. Ignored when
        detaching, which has no provenance to record. `box_id` is the stash-box whose answer decided
        it, where `source` is a box's.
        """
        statement = _ASSIGN if add else _UNASSIGN
        # One stamp for the whole press rather than one per row: a selection of forty tagged in one
        # act happened at one moment, and a clock read inside the loop spreads them over a second
        # or two, which a file's history then draws as separate things that happened.
        decided_at = self._now()
        written = 0
        moved: set[str] = set()
        #: The pairs that really changed, which is what the record is written from: a drag onto a
        #: chip a file already carries writes nothing and is not an act.
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
            # ONE event per file and tag that moved. The removal is the half that vanishes without
            # this: `_UNASSIGN` deletes the row, so taking a tag off a file erases the fact that it
            # was ever on it: the `decided_at` stamp goes with it.
            for asset_id, tag_id in pairs:
                await record_event(
                    connection,
                    actor=actor,
                    verb="linked" if add else "unlinked",
                    subject=Subject(kind="asset", id=asset_id),
                    object=Object(kind="tag", id=tag_id, name=named.get(tag_id)),
                )
            # A tag is what a share or a hide is attached to, so changing what carries it changes
            # what somebody may see without anything of theirs being written. In the same
            # transaction, and only for the tags that actually moved: dragging a clip onto a chip
            # it already has writes nothing and should cost nobody a re-fetch.
            told = NOBODY
            for tag_id in sorted(moved):
                told |= await bump_stamps_for_object(connection, ObjectType.TAG, tag_id)
            announce(_and_the_actor(told, actor), About.LIBRARY)
        return written


#: Tags and ratings.
SERVICE: Part[TagService] = Part("tags")
