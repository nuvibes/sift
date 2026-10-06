# SPDX-License-Identifier: AGPL-3.0-or-later
"""The walls of files: a page, its total, its facets, a position on it, and the files a list of
ids comes to for one viewer."""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Hashable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import PurePosixPath

from sift.kernel.access.constraints import (
    NO_FILTER,
    AllOf,
    AnyOf,
    AssetFilter,
    Node,
    Not,
    Where,
)
from sift.kernel.access.repository.asset_facets import (
    FACET_PRESENCE,
    PRESENCE_WORDS,
    STORED_COLUMNS,
    STORED_PRESENCE,
)
from sift.kernel.access.repository.assets import (
    _PRESENT_LOCATIONS_OF_ASSETS,
    DEFAULT_SORT,
    FACET_LABELS,
    FACETS,
    SEEKABLE_SORTS,
    assets_count_query,
    assets_query,
    concealed_value,
    drive_for,
    facet_query,
    newest_filed_query,
    position_query,
    seek_anchor,
    waiting_pile_page_query,
    waiting_pile_position_query,
)
from sift.kernel.access.repository.core import RepositoryCore
from sift.kernel.access.repository.views import (
    Actionable,
    AssetPage,
    AssetView,
    FacetCount,
    Memberships,
    _asset_view,
    _is_object_id,
)
from sift.kernel.access.viewer import Viewer
from sift.kernel.changes import current_mark
from sift.kernel.db import Row, in_clause, point_read
from sift.kernel.paging import MAX_PAGE_SIZE

# A user's stored counts and the highest row number handed out, one seek per page (`drive_for`).
_VIEWER_STATS = point_read(
    "access.viewer_stats",
    "SELECT s.permitted, s.concealed, s.permitted_bytes, s.concealed_bytes,"
    " (SELECT MAX(rowid) FROM assets) AS library"
    " FROM viewer_stats s WHERE s.user_id = ?",
)

# One thing's stored counts for one user, kept as the walls' are; no row is nothing permitted.
_ENTITY_COUNT = point_read(
    "access.entity_count",
    "SELECT permitted, concealed, permitted_bytes, concealed_bytes FROM viewer_entity_counts"
    " WHERE user_id = ? AND kind = ? AND object_id = ?",
)

# Whether a tag has a tag filed under it, which `tags:` then takes in.
_TAG_BRANCHES = point_read("access.tag_branches", "SELECT 1 FROM tags WHERE parent_id = ? LIMIT 1")

#: The one-value conditions a stored count answers exactly, and the kind it is stored under.
_STORED_KINDS = {
    "people": "person",
    "usernames": "username",
    "collections": "collection",
    "photo_sets": "photo_set",
    "songs": "song",
    "sites": "site",
    "tags": "tag",
    "media_type": "media",
}

# Whether this user hides any folder, which a shut vault's placeholders leave its name out for.
_HIDES_A_FOLDER = "SELECT 1 FROM folder_user_state WHERE user_id = ? AND hidden = 1 LIMIT 1"

#: How many questions' totals are carried to the pages that continue them.
_TOTALS_KEPT = 256

# Which of a list of files this user may act on: a row per permitted file, with the vault's answer.
# Absent means refused, or not there: the same answer on purpose.
_STANDING_OF = (
    "SELECT asset_id, concealed FROM viewer_assets WHERE user_id = ? AND asset_id IN (?*)"
)

#: How many of a list of files carry each person, Site, collection, photo set, song and tag. NOT
#: scoped: every caller resolves the files through `actionable_of` first, and every write these
#: counts are drawn for is an admin's, so a scope here could never fire. Not for a non-admin.
_TAG_MEMBERSHIPS = (
    "SELECT tag_id AS id, COUNT(*) AS carried FROM asset_tags"
    " WHERE asset_id IN (?*) GROUP BY tag_id"
)
_PERSON_MEMBERSHIPS = (
    "SELECT person_id AS id, COUNT(*) AS carried FROM asset_people"
    " WHERE asset_id IN (?*) GROUP BY person_id"
)
_COLLECTION_MEMBERSHIPS = (
    "SELECT collection_id AS id, COUNT(*) AS carried FROM collection_items"
    " WHERE asset_id IN (?*) GROUP BY collection_id"
)
_PHOTO_SET_MEMBERSHIPS = (
    "SELECT photo_set_id AS id, COUNT(*) AS carried FROM photo_set_items"
    " WHERE asset_id IN (?*) GROUP BY photo_set_id"
)
#: A file carries at most one song, so this is a count of the files on each song.
_SONG_MEMBERSHIPS = (
    "SELECT song_id AS id, COUNT(*) AS carried FROM song_files"
    " WHERE asset_id IN (?*) GROUP BY song_id"
)
#: A file is filed under a username, and a Site holds several, so `COUNT(DISTINCT ...)`.
_SITE_MEMBERSHIPS = (
    "SELECT ac.site_id AS id, COUNT(DISTINCT aa.asset_id) AS carried"
    " FROM asset_usernames aa JOIN usernames ac ON ac.id = aa.username_id"
    " WHERE aa.asset_id IN (?*) GROUP BY ac.site_id"
)

#: How many pins a wall reads ahead of the page: bounded, so a runaway cannot cost a whole-set order.
MAX_PINS = 500

#: Besides the mark: what they may see, and the index's structure record every write moves.
_WORDS_STAND_ON = point_read(
    "access.words_stand_on",
    "SELECT s.permitted, s.concealed,"
    " (SELECT d.block FROM assets_fts_data d WHERE d.id = 10) AS written"
    " FROM viewer_stats s WHERE s.user_id = ?",
)

_WORD_LEAVES = frozenset({"text_match", "text_contains"})
_WORD_IDS = "SELECT a.id FROM assets a WHERE "
_WORD_LIST = "a.id IN (SELECT value FROM json_each(:word_ids))"


def _word_ids(where: str) -> str:
    return _WORD_IDS + where


@dataclass(frozen=True, slots=True)
class WordMatches:
    """One viewer's files holding some words, as a JSON array (`word_matches`)."""

    viewer: str
    words: tuple[str, tuple[str, ...]]
    ids: str
    count: int


def _is_word(node: Node) -> bool:
    return isinstance(node, Where) and node.key in _WORD_LEAVES


def _asks_words(node: Node) -> bool:
    if isinstance(node, Not):
        return _asks_words(node.part)
    if isinstance(node, AllOf | AnyOf):
        return any(_asks_words(part) for part in node.parts)
    return _is_word(node)


def _constrains(node: Node) -> bool:
    """Whether a tree narrows anything: ALL of nothing, however nested, does not."""
    if isinstance(node, AllOf):
        return any(_constrains(part) for part in node.parts)
    return True


def _terms(node: Node) -> tuple[Node, ...]:
    """The conditions that must all hold, nested ALLs opened."""
    if isinstance(node, AllOf):
        return tuple(term for part in node.parts for term in _terms(part))
    return (node,)


def _shown(row: Row | None, *, reveal: int, named: int, hidden_only: bool) -> tuple[int, int]:
    """`(files, bytes)` of a stored count by the page's own two vault rules; no row is none."""
    if row is None:
        return 0, 0
    permitted, concealed = int(row["permitted"]), int(row["concealed"])
    permitted_bytes, concealed_bytes = int(row["permitted_bytes"]), int(row["concealed_bytes"])
    if hidden_only:
        return (concealed if reveal else 0), (concealed_bytes if named else 0)
    return (
        permitted if reveal else permitted - concealed,
        permitted_bytes if named else permitted_bytes - concealed_bytes,
    )


def _conjuncts(asset_filter: AssetFilter) -> tuple[Node, ...]:
    root = asset_filter.where
    return root.parts if isinstance(root, AllOf) else (root,)


def words_of(asset_filter: AssetFilter) -> tuple[str, tuple[str, ...]] | None:
    """Words every file must hold, and their leaves; None for none, or any under an OR or NOT."""
    parts = _conjuncts(asset_filter)
    leaves = tuple(
        sorted({part.key for part in parts if isinstance(part, Where) and _is_word(part)})
    )
    rest = [part for part in parts if not _is_word(part)]
    if not leaves or asset_filter.text is None or any(_asks_words(part) for part in rest):
        return None
    return asset_filter.text, leaves


def _narrowed(
    viewer: Viewer, asset_filter: AssetFilter, words: WordMatches | None, also: Node | None = None
) -> tuple[str, str | None, dict[str, object]]:
    """The filter's text and binds, its words read from `words` only if that is this viewer's list
    for these words; and what a page's second read re-applies (None: all of it)."""
    listed = (
        words.ids
        if words is not None
        and not viewer.is_admin
        and words.viewer == viewer.id
        and words.words == words_of(asset_filter)
        else None
    )
    used = asset_filter
    if listed is not None:
        rest = tuple(part for part in _conjuncts(asset_filter) if not _is_word(part))
        used = replace(asset_filter, where=AllOf(rest))
    plain, bound = (used if also is None else used.also(also)).predicate()
    if listed is None:
        return plain, None, bound
    return f"({plain} AND {_WORD_LIST})", plain, bound | {"word_ids": listed}


@dataclass(frozen=True, slots=True)
class _Asked:
    """Which wall of files a page is of: its order, and what a read binds besides its window."""

    sort: str
    tag_id: str | None
    collection_id: str | None
    photo_set_id: str | None
    hidden_only: bool
    pinned_first: bool
    seed: int | None


def _page_size(limit: int, offset: int) -> int:
    if limit < 1:
        raise ValueError("a page needs at least one row")
    if offset < 0:
        raise ValueError("a page cannot start before the first row")
    return min(limit, MAX_PAGE_SIZE)


def _drive_of(stats: Row | None) -> str:
    """Which side a page walks first, from the viewer's stored counts (`drive_for`)."""
    permitted = 0 if stats is None else int(stats["permitted"])
    library = 0 if stats is None or stats["library"] is None else int(stats["library"])
    return drive_for(permitted, library)


class FileReads(RepositoryCore):
    """The scoped reads of many files at once."""

    async def visible_counts(self, viewer: Viewer) -> tuple[int, int]:
        """How many files this viewer may see and how many the vault holds back, in one probe."""
        row = await self._db.fetch_one(_VIEWER_STATS, (viewer.id,))
        if row is None:
            return (0, 0)
        return (int(row["permitted"]), int(row["concealed"]))

    async def words_stand_on(self, viewer: Viewer) -> tuple[int, int, bytes | None]:
        """`visible_counts` and the word index's structure record (None: no index to keep on)."""
        row = await self._db.fetch_one(_WORDS_STAND_ON, (viewer.id,))
        if row is None:
            return (0, 0, None)
        written = None if row["written"] is None else bytes(row["written"])
        return (int(row["permitted"]), int(row["concealed"]), written)

    async def word_matches(self, viewer: Viewer, asset_filter: AssetFilter) -> WordMatches | None:
        """The files among this viewer's own that hold the filter's words, for reads to take as
        `words`; None for an admin, whose words the index answers."""
        words = words_of(asset_filter)
        if viewer.is_admin or words is None:
            return None
        text, leaves = words
        where, bound = AssetFilter(where=AllOf(tuple(map(Where, leaves))), text=text).predicate()
        rows = await self._db.fetch_all(_word_ids(where), bound | {"viewer": viewer.id})
        ids = [str(row["id"]) for row in rows]
        return WordMatches(viewer.id, words, json.dumps(ids), len(ids))

    async def facet_counts(
        self,
        viewer: Viewer,
        facet: str,
        *,
        asset_filter: AssetFilter = NO_FILTER,
        hidden_only: bool = False,
        limit: int = 24,
        words: WordMatches | None = None,
    ) -> list[FacetCount]:
        """How many of the files this query reaches carry each value of one dimension, from the
        statement that decides what those files are, or for a question that narrows nothing from
        the stored counts kept by the same rules; a concealed value is absent, never a zero."""
        joins, value = FACETS[facet]
        label = FACET_LABELS.get(facet, "")
        # A concealed value never reaches the group; its files count under their other values.
        extra = concealed_value(facet)
        reveal = self._reveal_existence(viewer)
        asked = _Asked(DEFAULT_SORT, None, None, None, hidden_only, False, None)
        stored, whole, asset_filter = await self._stored_terms(viewer, asset_filter, asked)
        if 0 in (_shown(row, reveal=reveal, named=1, hidden_only=hidden_only)[0] for row in stored):
            return []
        where, _, bound = _narrowed(viewer, asset_filter, words)
        unnarrowed = not _constrains(asset_filter.where)
        stored_column = unnarrowed and facet in STORED_COLUMNS
        # Placeholders behind a shut vault: a hidden name is left out, its files still counted.
        locked = reveal == 1 and not self._reveal_named(viewer)
        if stored_column and facet == "in" and locked:
            stored_column = await self._db.fetch_one(_HIDES_A_FOLDER, (viewer.id,)) is None
        query = (
            STORED_COLUMNS[facet]
            if stored_column
            else facet_query(where, joins=joins, value=value, label=label, extra=extra)
        )
        params = self._asked_params(
            viewer,
            asked,
            reveal=reveal,
            limit=max(1, min(limit, MAX_PAGE_SIZE)),
            offset=0,
            pinned_first=False,
            bound=bound,
        )
        rows = await self._db.fetch_all(query, params)
        counted = [
            FacetCount(
                value=str(row["facet_value"]),
                count=int(row["files"]),
                # Asked of the table: a dimension with no label has no such column at all.
                label=str(row["facet_label"]) if label else None,
            )
            for row in rows
        ]
        return await self._facet_presence(
            viewer, facet, counted, params, asset_filter, asked, where, bound, stored, whole, locked
        )

    async def _facet_presence(
        self,
        viewer: Viewer,
        facet: str,
        counted: list[FacetCount],
        params: dict[str, object],
        asset_filter: AssetFilter,
        asked: _Asked,
        where: str,
        bound: Mapping[str, object],
        stored: list[Row | None],
        whole: bool,
        locked: bool,
    ) -> list[FacetCount]:
        """A presence facet's counts with its Has and No rows first; any other's as counted."""
        if facet not in FACET_PRESENCE:
            return counted
        reveal = self._reveal_existence(viewer)
        # "Has" from the link, "No" as the rest of the wall's total. Only a locked tile behind a shut
        # vault can carry nothing but hidden things, which the stored count cannot tell.
        link, kept = FACET_PRESENCE[facet]
        has = (
            await self._db.fetch_all(STORED_PRESENCE, params | {"presence": "has_" + facet})
            if not locked and not _constrains(asset_filter.where)
            else await self._db.fetch_all(
                facet_query(where, joins=link, value="'any'", extra=kept if locked else ""),
                params,
            )
        )
        stats = await self._db.fetch_one(_VIEWER_STATS, (viewer.id,))
        total, _ = await self._page_total(
            viewer,
            stats,
            asset_filter,
            asked,
            where,
            bound,
            reveal=reveal,
            limit=1,
            offset=0,
            stored=stored if whole else None,
        )
        some = int(has[0]["files"]) if has else 0
        heads = [FacetCount(value="any", count=some), FacetCount(value="none", count=total - some)]
        return [one for one in heads if one.count > 0] + [
            one for one in counted if one.value.lower() not in PRESENCE_WORDS
        ]

    async def visible_assets(
        self,
        viewer: Viewer,
        *,
        limit: int = 50,
        offset: int = 0,
        tag_id: str | None = None,
        collection_id: str | None = None,
        photo_set_id: str | None = None,
        hidden_only: bool = False,
        pinned_first: bool = False,
        asset_filter: AssetFilter = NO_FILTER,
        sort: str = DEFAULT_SORT,
        seed: int | None = None,
        after: str | None = None,
        words: WordMatches | None = None,
    ) -> AssetPage:
        """A page of what this viewer may see, and its total, both from one statement's filter.

        `seed` is WHICH shuffle; `after` continues after a row by one seek (`SEEKABLE_SORTS`);
        `pinned_first` is whether THIS wall floats the pin; `words`, from `word_matches`.
        """
        limit = _page_size(limit, offset)
        if collection_id is not None:
            # A collection's wall is its membership as the filter asks it, which a count is kept for.
            terms = (*_terms(asset_filter.where), Where("collections", (collection_id,)))
            asset_filter, collection_id = replace(asset_filter, where=AllOf(terms)), None
        asked = _Asked(sort, tag_id, collection_id, photo_set_id, hidden_only, pinned_first, seed)
        reveal = self._reveal_existence(viewer)
        stored, whole, asset_filter = await self._stored_terms(viewer, asset_filter, asked)
        # A term nothing is shown under empties the wall, whatever else is asked.
        if 0 in (_shown(row, reveal=reveal, named=1, hidden_only=hidden_only)[0] for row in stored):
            return AssetPage(items=[], total=0)
        where, outer, bound = _narrowed(viewer, asset_filter, words)
        count_where, count_bound = where, bound
        # A sequence or a pin in front of the sort costs the whole set: no index answers it.
        arranged = pinned_first or photo_set_id is not None
        seek = await self._continued(viewer, asked, after, reveal=reveal, bound=bound)
        if seek is None:
            return AssetPage(items=[], total=0)
        # The stored counts choose the side walked first and answer an unfiltered total.
        stats = await self._db.fetch_one(_VIEWER_STATS, (viewer.id,))
        drive = _drive_of(stats)
        pinned: list[Row] = []
        if pinned_first and photo_set_id is None:
            pinned, where, outer, seek = await self._pins(
                viewer, asset_filter, words, asked, drive=drive, reveal=reveal
            )
            arranged = False
        # A page begun among the pins takes the rest, then fills from the walk.
        pins_here = pinned[offset : offset + limit]
        rows = await self._walk(
            viewer,
            asked,
            where,
            seek,
            reveal=reveal,
            drive=drive,
            arranged=arranged,
            continued=after is not None,
            offset=max(0, offset - len(pinned)),
            limit=limit - len(pins_here),
            outer=outer,
            narrowed=_constrains(asset_filter.where),
        )
        items = [_asset_view(row, viewer) for row in (*pins_here, *rows)]
        total, total_bytes = await self._page_total(
            viewer,
            stats,
            asset_filter,
            asked,
            count_where,
            count_bound,
            reveal=reveal,
            limit=limit,
            offset=offset,
            stored=stored if whole else None,
            continued=after is not None,
        )
        return AssetPage(items=items, total=total, total_bytes=total_bytes)

    def _asked_params(
        self,
        viewer: Viewer,
        asked: _Asked,
        *,
        reveal: int,
        limit: int,
        offset: int,
        pinned_first: bool,
        bound: Mapping[str, object],
    ) -> dict[str, object]:
        """What a read of one wall of files binds, for the wall `asked` names."""
        named = {"reveal_named": self._reveal_named(viewer)}
        return named | self._params(
            viewer,
            asset_id=None,
            reveal=reveal,
            limit=limit,
            offset=offset,
            tag_id=asked.tag_id,
            collection_id=asked.collection_id,
            photo_set_id=asked.photo_set_id,
            hidden_only=asked.hidden_only,
            pinned_first=pinned_first,
            seed=asked.seed,
            bound=bound,
        )

    async def _continued(
        self,
        viewer: Viewer,
        asked: _Asked,
        after: str | None,
        *,
        reveal: int,
        bound: dict[str, object],
    ) -> dict[str, object] | None:
        """The binds that continue a wall from the row `after` (`bound` without one), or None where
        it names no row this viewer may see."""
        if after is None:
            return bound
        if asked.pinned_first or asked.photo_set_id is not None or asked.sort not in SEEKABLE_SORTS:
            raise ValueError("this wall cannot be continued from a row; page by offset")
        anchor = await self._db.fetch_one(seek_anchor(asked.sort), (after,))
        if anchor is None or await self._one(viewer, after, reveal=reveal) is None:
            return None
        return {**bound, "after_key": anchor["key"], "after_id": after}

    async def _pins(
        self,
        viewer: Viewer,
        asset_filter: AssetFilter,
        words: WordMatches | None,
        asked: _Asked,
        *,
        drive: str,
        reveal: int,
    ) -> tuple[list[Row], str, str | None, dict[str, object]]:
        """The pinned files of a wall that floats them, in the sort's order, and the filter the walk
        after them reads: the index with them excluded, so between them exactly what is counted."""
        pins_where, _, pins_bound = _narrowed(viewer, asset_filter, words, Where("pinned"))
        pinned = await self._db.fetch_all(
            assets_query(asked.sort, pins_where, arranged=False, counted=False, drive=drive),
            self._asked_params(
                viewer,
                asked,
                reveal=reveal,
                limit=MAX_PINS,
                offset=0,
                pinned_first=False,
                bound=pins_bound,
            ),
        )
        return pinned, *_narrowed(viewer, asset_filter, words, Not(Where("pinned")))

    async def _walk(
        self,
        viewer: Viewer,
        asked: _Asked,
        where: str,
        bound: Mapping[str, object],
        *,
        reveal: int,
        drive: str,
        arranged: bool,
        continued: bool,
        offset: int,
        limit: int,
        outer: str | None = None,
        narrowed: bool = False,
    ) -> list[Row]:
        """The rows of a page past its pins, walked in the sort's order; none for no room left."""
        if limit <= 0:
            return []
        params = self._asked_params(
            viewer,
            asked,
            reveal=reveal,
            limit=limit,
            offset=offset,
            pinned_first=asked.pinned_first and arranged,
            bound=bound,
        )
        walk = assets_query(
            asked.sort,
            where,
            arranged=arranged,
            counted=False,
            drive=drive,
            continued=continued,
            outer=outer,
            narrowed=narrowed,
        )
        return await self._db.fetch_all(walk, params)

    async def _page_total(
        self,
        viewer: Viewer,
        stats: Row | None,
        asset_filter: AssetFilter,
        asked: _Asked,
        where: str,
        bound: Mapping[str, object],
        *,
        reveal: int,
        limit: int,
        offset: int,
        stored: Sequence[Row | None] | None = None,
        continued: bool = False,
    ) -> tuple[int, int]:
        """`(files, bytes)` on the whole wall, pins included: stored for an unfiltered wall or one
        narrowed by one stored term (`stored`, every term of the question), else counted from the
        page's own seams, once a question. A vaulted file adds no bytes while the vault is shut."""
        named = self._reveal_named(viewer)
        narrowed = (
            _constrains(asset_filter.where)
            or asked.tag_id is not None
            or asked.collection_id is not None
        )
        if not narrowed or (stored is not None and len(stored) == 1):
            row = stats if not narrowed or stored is None else stored[0]
            return _shown(row, reveal=reveal, named=named, hidden_only=asked.hidden_only)
        params = self._asked_params(
            viewer,
            asked,
            reveal=reveal,
            limit=limit,
            offset=offset,
            pinned_first=asked.pinned_first,
            bound=bound,
        )

        async def count() -> tuple[int, int]:
            counted = await self._db.fetch_all(assets_count_query(where), params)
            return int(counted[0]["total_count"]), int(counted[0]["total_bytes"])

        question = (
            viewer.id,
            viewer.cache_stamp,
            reveal,
            named,
            asked.hidden_only,
            asked.tag_id,
            asked.collection_id,
            # A digest, as a guest's words bind every file they match.
            hashlib.blake2b(
                repr(
                    (where, sorted((name, repr(value)) for name, value in bound.items()))
                ).encode(),
                digest_size=16,
            ).digest(),
        )
        return await self._once(question, continued, count)

    _kept_totals: OrderedDict[tuple[Hashable, str], tuple[int, int]] | None = None

    async def _once(
        self,
        question: Hashable,
        continued: bool,
        count: Callable[[], Awaitable[tuple[int, int]]],
    ) -> tuple[int, int]:
        """A question's total, counted on its first page and carried to the pages continuing it
        while nothing has been announced since (the change bus's mark)."""
        mark = current_mark()
        if self._kept_totals is None:
            self._kept_totals = OrderedDict()
        kept = self._kept_totals
        held = kept.get((question, mark)) if continued and mark is not None else None
        if held is not None:
            return held
        total = await count()
        if mark is not None:
            kept[(question, mark)] = total
            while len(kept) > _TOTALS_KEPT:
                kept.popitem(last=False)
        return total

    async def _stored_terms(
        self, viewer: Viewer, asset_filter: AssetFilter, asked: _Asked
    ) -> tuple[list[Row | None], bool, AssetFilter]:
        """The stored count of each term of this question that one count answers, whether those
        terms are the whole question, and the filter with them first, fewest files first: the
        planner reads the first one's members and tests the rest."""
        wanted: list[tuple[str, str]] = []
        if asked.tag_id is not None:
            wanted.append(("tag", asked.tag_id))
        if asked.collection_id is not None:
            wanted.append(("collection", asked.collection_id))
        rows = [
            await self._db.fetch_one(_ENTITY_COUNT, (viewer.id, kind, object_id))
            for kind, object_id in wanted
        ]
        counted: list[tuple[int, Node]] = []
        rest: list[Node] = []
        for term in _terms(asset_filter.where):
            kind = await self._stored_kind(viewer, term)
            if kind is None or not isinstance(term, Where):
                rest.append(term)
                continue
            row = await self._db.fetch_one(_ENTITY_COUNT, (viewer.id, kind, str(term.values[0])))
            rows.append(row)
            counted.append((0 if row is None else int(row["permitted"]), term))
        if counted and len(counted) + len(rest) > 1:
            first = [term for _files, term in sorted(counted, key=lambda one: one[0])]
            asset_filter = replace(asset_filter, where=AllOf((*first, *rest)))
        return rows, not rest, asset_filter

    async def _stored_kind(self, viewer: Viewer, term: Node) -> str | None:
        """The kind a stored count of this one condition is kept under, or None for none."""
        if not isinstance(term, Where) or len(term.values) != 1:
            return None
        kind = _STORED_KINDS.get(term.key)
        # A tag takes in its branch. Asked for an admin only, so what is filed under a tag a guest
        # is shown nothing of never changes how long their read takes.
        if kind == "tag" and (
            not viewer.is_admin
            or await self._db.fetch_one(_TAG_BRANCHES, (term.values[0],)) is not None
        ):
            return None
        return kind

    async def count_visible(self, viewer: Viewer, asset_filter: AssetFilter) -> int:
        """How many files this filter finds for this viewer: the Files wall's own total, no page,
        for a number that opens that wall, read as `visible_assets` reads it."""
        asked = _Asked(DEFAULT_SORT, None, None, None, False, False, None)
        stored, whole, asset_filter = await self._stored_terms(viewer, asset_filter, asked)
        where, bound = asset_filter.predicate()
        total, _ = await self._page_total(
            viewer,
            await self._db.fetch_one(_VIEWER_STATS, (viewer.id,)),
            asset_filter,
            asked,
            where,
            bound,
            reveal=self._reveal_existence(viewer),
            limit=1,
            offset=0,
            stored=stored if whole else None,
        )
        return total

    async def position_of(
        self,
        viewer: Viewer,
        asset_id: str,
        *,
        tag_id: str | None = None,
        collection_id: str | None = None,
        photo_set_id: str | None = None,
        hidden_only: bool = False,
        pinned_first: bool = False,
        asset_filter: AssetFilter = NO_FILTER,
        sort: str = DEFAULT_SORT,
        seed: int | None = None,
    ) -> int | None:
        """How far into these results one file sits, from zero, with `visible_assets`' arguments.
        None alike for a file not there, not visible or not in them: a link cannot probe."""
        # A non-id matches nothing, never bound as NULL, which would read as "no filter".
        if not _is_object_id(asset_id):
            return None

        where, bound = asset_filter.predicate()
        rows = await self._db.fetch_all(
            position_query(sort, where),
            self._params(
                viewer,
                asset_id=None,
                reveal=self._reveal_existence(viewer),
                # Bound because the statement names them; the window ranks the whole set.
                limit=1,
                offset=0,
                tag_id=tag_id,
                collection_id=collection_id,
                photo_set_id=photo_set_id,
                hidden_only=hidden_only,
                # The page's own ordering, pins and shuffle included, or the anchor lands wrong.
                pinned_first=pinned_first,
                seed=seed,
                bound=bound,
            )
            | {"position_of": asset_id, "reveal_named": self._reveal_named(viewer)},
        )
        if not rows:
            return None
        # ROW_NUMBER counts from one and an offset from zero: converted here, once.
        return int(rows[0]["position"]) - 1

    async def visible_of(self, viewer: Viewer, asset_ids: Sequence[str]) -> set[str]:
        """Which of these files this viewer may see: a membership test bounded by the caller's list,
        not a page, which the page cap would cut short. Asked a page at a time (`assets_of`)."""
        return set(await self.assets_of(viewer, asset_ids))

    async def assets_of(self, viewer: Viewer, asset_ids: Sequence[str]) -> dict[str, AssetView]:
        """The rows for these ids that this viewer may see, keyed by id, a page at a time. Absent
        means not allowed or not there, and a caller must not tell those apart."""
        wanted = sorted(set(asset_ids))
        found: dict[str, AssetView] = {}
        for start in range(0, len(wanted), MAX_PAGE_SIZE):
            chunk = wanted[start : start + MAX_PAGE_SIZE]
            page = await self.visible_assets(
                viewer,
                limit=len(chunk),
                asset_filter=AssetFilter(where=Where("assets", tuple(chunk))),
            )
            found.update((item.asset.id, item) for item in page.items)
        return found

    async def newest_under_usernames(
        self, viewer: Viewer, username_ids: Sequence[str]
    ) -> dict[str, str]:
        """The newest file this viewer may see under each of these usernames, through the wall's own
        statement, keyed by username. Absent means nothing to see or no such username, alike."""
        wanted = [one for one in dict.fromkeys(username_ids) if _is_object_id(one)]
        if not wanted:
            return {}
        where, bound = AssetFilter(where=Where("usernames", tuple(wanted))).predicate()
        rows = await self._db.fetch_all(
            newest_filed_query(where),
            self._params(
                viewer,
                asset_id=None,
                reveal=self._reveal_existence(viewer),
                limit=len(wanted),
                offset=0,
                bound=bound,
            )
            | {"newest_of": json.dumps(wanted)},
        )
        return {str(row["username_id"]): str(row["asset_id"]) for row in rows}

    async def standing_of(self, viewer: Viewer, asset_ids: Sequence[str]) -> dict[str, bool]:
        """Which of these files this viewer may be shown, each with whether their vault conceals
        it; absent means not allowed or not there, alike, and a concealed file is present only where
        the grid would show it. `actionable_of`'s probe without the reasons or the denial log, for a
        screen deciding what to draw; `assets_of` when a name or a size is wanted."""
        revealed = self._reveal_existence(viewer) == 1
        standing = await self._standing(viewer, asset_ids)
        return {one: hidden for one, hidden in standing.items() if revealed or not hidden}

    async def _standing(self, viewer: Viewer, asset_ids: Sequence[str]) -> dict[str, bool]:
        """Each of these files this viewer is permitted, with whether their vault conceals it."""
        wanted = [asset_id for asset_id in dict.fromkeys(asset_ids) if _is_object_id(asset_id)]
        standing: dict[str, bool] = {}
        for start in range(0, len(wanted), MAX_PAGE_SIZE):
            chunk = wanted[start : start + MAX_PAGE_SIZE]
            sql, values = in_clause(_STANDING_OF, chunk)
            rows = await self._db.fetch_all(sql, [viewer.id, *values])
            standing.update((str(row["asset_id"]), bool(row["concealed"])) for row in rows)
        return standing

    async def actionable_of(self, viewer: Viewer, asset_ids: Sequence[str]) -> Actionable:
        """Which of these a viewer may WRITE to, and why not for the rest: the batch form of
        `open_asset`, so a write over a selection has the whole answer before it writes. A viewer
        may act on exactly what they may open; an id only THEIR open vault shows is concealed by
        their own lock, anything else absent is refused. A refusal is logged, a concealment is not.
        """
        standing = await self._standing(viewer, asset_ids)
        allowed: list[str] = []
        concealed: list[str] = []
        refused: list[str] = []
        # The caller's order, de-duplicated: two mentions of one id are one item.
        for asset_id in dict.fromkeys(asset_ids):
            hidden = standing.get(asset_id)
            if hidden is None:
                self._log_denied(viewer, asset_id)
                refused.append(asset_id)
            elif hidden and not viewer.show_hidden:
                concealed.append(asset_id)
            else:
                allowed.append(asset_id)
        return Actionable(
            allowed=tuple(allowed), concealed=tuple(concealed), refused=tuple(refused)
        )

    async def memberships_of(self, asset_ids: Sequence[str]) -> Memberships:
        """How many of these files carry each person, Site, collection, photo set, song and tag:
        what a picker needs to draw a tick or a half tick.

        **Hand it only ids `actionable_of` allowed.** It takes no viewer, so nobody can read a scope
        into it (the statements say why there is none). Paged: a selection runs to thousands."""
        wanted = [asset_id for asset_id in dict.fromkeys(asset_ids) if _is_object_id(asset_id)]
        tallies: list[dict[str, int]] = [{}, {}, {}, {}, {}, {}]
        statements = (
            _PERSON_MEMBERSHIPS,
            _SITE_MEMBERSHIPS,
            _COLLECTION_MEMBERSHIPS,
            _PHOTO_SET_MEMBERSHIPS,
            _TAG_MEMBERSHIPS,
            _SONG_MEMBERSHIPS,
        )
        for start in range(0, len(wanted), MAX_PAGE_SIZE):
            chunk = wanted[start : start + MAX_PAGE_SIZE]
            for tally, statement in zip(tallies, statements, strict=True):
                sql, values = in_clause(statement, chunk)
                for row in await self._db.fetch_all(sql, values):
                    # Added rather than assigned: a selection wider than one page is several
                    # statements, and each answers about its own page only.
                    tally[str(row["id"])] = tally.get(str(row["id"]), 0) + int(row["carried"])
        people, sites, collections, photo_sets, tags, songs = tallies
        return Memberships(
            people=people,
            sites=sites,
            collections=collections,
            photo_sets=photo_sets,
            tags=tags,
            songs=songs,
        )

    async def waiting_piles(
        self,
        viewer: Viewer,
        status: str,
        *,
        limit: int,
        offset: int,
        floor: int = 1,
        ceiling: int = 0,
    ) -> tuple[list[tuple[str, int]], int]:
        """One page of the groups waiting, each with how many faces this viewer may see, and the
        total, from one statement so the two agree. `floor` and `ceiling` bound a group by the faces
        this viewer may see; one and no ceiling is every group."""
        _where, bound = NO_FILTER.predicate()
        rows = await self._db.fetch_all(
            waiting_pile_page_query(),
            self._params(
                viewer,
                asset_id=None,
                reveal=self._reveal_existence(viewer),
                limit=max(1, min(limit, MAX_PAGE_SIZE)),
                offset=max(0, offset),
                bound={
                    **bound,
                    "pile_status": status,
                    "pile_floor": max(1, floor),
                    "pile_ceiling": max(0, ceiling),
                },
            ),
        )
        page = [(str(row["pile_id"]), int(row["visible"])) for row in rows]
        return page, int(rows[0]["total_count"]) if rows else 0

    async def waiting_pile_position(
        self,
        viewer: Viewer,
        status: str,
        pile_id: str,
        *,
        floor: int = 1,
        ceiling: int = 0,
    ) -> int | None:
        """How far down that wall a group sits, counting from zero, or None: for a group not there,
        one this viewer may see nothing of, and one outside `waiting_piles`' own `floor` and
        `ceiling`, alike."""
        if not _is_object_id(pile_id):
            return None
        _where, bound = NO_FILTER.predicate()
        rows = await self._db.fetch_all(
            waiting_pile_position_query(),
            self._params(
                viewer,
                asset_id=None,
                reveal=self._reveal_existence(viewer),
                limit=1,
                offset=0,
                bound={
                    **bound,
                    "pile_status": status,
                    "pile_id": pile_id,
                    "pile_floor": max(1, floor),
                    "pile_ceiling": max(0, ceiling),
                },
            ),
        )
        return int(rows[0]["position"]) - 1 if rows else None

    async def names_on_disk(self, viewer: Viewer, asset_ids: Sequence[str]) -> dict[str, str]:
        """What each of these files is called now (a renamed file keeps its `original_filename`),
        keyed by asset id: the first present copy's relative path, as `locate` serves, not the
        resolved one, which can be a content address. Absent means not allowed, not there or no
        copy present, alike."""
        allowed = await self.assets_of(viewer, asset_ids)
        if not allowed:
            return {}
        names: dict[str, str] = {}
        wanted = sorted(allowed)
        for start in range(0, len(wanted), MAX_PAGE_SIZE):
            chunk = wanted[start : start + MAX_PAGE_SIZE]
            rows = await self._db.fetch_all(_PRESENT_LOCATIONS_OF_ASSETS, (json.dumps(chunk),))
            for row in rows:
                names.setdefault(str(row["asset_id"]), PurePosixPath(str(row["rel_path"])).name)
        return names
