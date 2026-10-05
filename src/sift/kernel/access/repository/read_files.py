# SPDX-License-Identifier: AGPL-3.0-or-later
"""The walls of files: a page, its total, its facets, a position on it, and the files a list of
ids comes to for one viewer.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
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
from sift.kernel.access.repository.asset_facets import FACET_PRESENCE, PRESENCE_WORDS
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
from sift.kernel.db import Row, in_clause, point_read
from sift.kernel.paging import MAX_PAGE_SIZE

# The stored counts for one user and the highest row number handed out (one seek, not a pass),
# read on every page. See `drive_for`.
_VIEWER_STATS = point_read(
    "access.viewer_stats",
    "SELECT s.permitted, s.concealed, s.permitted_bytes, s.concealed_bytes,"
    " (SELECT MAX(rowid) FROM assets) AS library"
    " FROM viewer_stats s WHERE s.user_id = ?",
)

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
        """How many files this viewer may see, and how many of those the vault holds back: one
        probe, and any change to what they may see moves one of them."""
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
        statement that decides what those files are; a concealed value is absent, never a zero."""
        joins, value = FACETS[facet]
        label = FACET_LABELS.get(facet, "")
        # A concealed value never reaches the group; its files count under their other values.
        extra = concealed_value(facet)
        where, _, bound = _narrowed(viewer, asset_filter, words)
        query = facet_query(where, joins=joins, value=value, label=label, extra=extra)
        params = self._params(
            viewer,
            asset_id=None,
            reveal=self._reveal_existence(viewer),
            limit=max(1, min(limit, MAX_PAGE_SIZE)),
            offset=0,
            tag_id=None,
            collection_id=None,
            photo_set_id=None,
            hidden_only=hidden_only,
            bound=bound,
        ) | {"reveal_named": self._reveal_named(viewer)}
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
        if facet not in FACET_PRESENCE:
            return counted
        # "Has" from the link, "No" as the rest of the wall's total. Only a locked tile behind a shut
        # vault can carry nothing but hidden things.
        link, kept = FACET_PRESENCE[facet]
        locked = self._reveal_existence(viewer) and not self._reveal_named(viewer)
        has = await self._db.fetch_all(
            facet_query(where, joins=link, value="'any'", extra=kept if locked else ""), params
        )
        stats = await self._db.fetch_one(_VIEWER_STATS, (viewer.id,))
        asked = _Asked(DEFAULT_SORT, None, None, None, hidden_only, False, None)
        total, _ = await self._page_total(
            viewer,
            stats,
            asset_filter,
            asked,
            where,
            bound,
            reveal=self._reveal_existence(viewer),
            limit=1,
            offset=0,
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
        asked = _Asked(sort, tag_id, collection_id, photo_set_id, hidden_only, pinned_first, seed)
        reveal = self._reveal_existence(viewer)
        where, outer, bound = _narrowed(viewer, asset_filter, words)
        # A sequence or a pin in front of the sort costs the whole set: no index answers it.
        sequence = photo_set_id is not None
        arranged = pinned_first or sequence
        if after is not None:
            if arranged or sort not in SEEKABLE_SORTS:
                raise ValueError("this wall cannot be continued from a row; page by offset")
            continued = await self._continued(viewer, sort, after, reveal=reveal, bound=bound)
            if continued is None:
                return AssetPage(items=[], total=0)
            bound = continued
        # The stored counts choose the side walked first and answer an unfiltered total.
        stats = await self._db.fetch_one(_VIEWER_STATS, (viewer.id,))
        drive = _drive_of(stats)
        pinned: list[Row] = []
        count_where, count_bound = where, bound
        if pinned_first and not sequence:
            pinned = await self._pins(
                viewer, asset_filter, words, asked, drive=drive, reveal=reveal
            )
            where, outer, bound = _narrowed(viewer, asset_filter, words, Not(Where("pinned")))
            arranged = False
        # A page begun among the pins takes the rest, then fills from the walk.
        pins_here = pinned[offset : offset + limit]
        rows = await self._walk(
            viewer,
            asked,
            where,
            bound,
            reveal=reveal,
            drive=drive,
            arranged=arranged,
            continued=after is not None,
            offset=max(0, offset - len(pinned)),
            limit=limit - len(pins_here),
            outer=outer,
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
        self, viewer: Viewer, sort: str, after: str, *, reveal: int, bound: Mapping[str, object]
    ) -> dict[str, object] | None:
        """The binds that continue a wall from the row `after`, or None where it names no row this
        viewer may see."""
        anchor = await self._db.fetch_one(seek_anchor(sort), (after,))
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
    ) -> list[Row]:
        """The pinned files of a wall that floats them, in the sort's order: the page walks the
        index with them excluded, so between them exactly the files the total counts."""
        pins_where, _, pins_bound = _narrowed(viewer, asset_filter, words, Where("pinned"))
        return await self._db.fetch_all(
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
    ) -> tuple[int, int]:
        """`(files, bytes)` on the whole wall, pins included: stored for an unfiltered wall, else
        counted from the page's own seams. A vaulted file adds no bytes while the vault is shut."""
        named = self._reveal_named(viewer)
        narrowed = (
            asset_filter != NO_FILTER
            or asked.tag_id is not None
            or asked.collection_id is not None
            or asked.photo_set_id is not None
        )
        if not narrowed:
            permitted = 0 if stats is None else int(stats["permitted"])
            concealed = 0 if stats is None else int(stats["concealed"])
            permitted_bytes = 0 if stats is None else int(stats["permitted_bytes"])
            concealed_bytes = 0 if stats is None else int(stats["concealed_bytes"])
            if asked.hidden_only:
                total = concealed if reveal else 0
                total_bytes = concealed_bytes if named else 0
            else:
                total = permitted if reveal else permitted - concealed
                total_bytes = permitted_bytes if named else permitted_bytes - concealed_bytes
            return total, total_bytes
        params = self._asked_params(
            viewer,
            asked,
            reveal=reveal,
            limit=limit,
            offset=offset,
            pinned_first=asked.pinned_first,
            bound=bound,
        )
        counted = await self._db.fetch_all(assets_count_query(where), params)
        return int(counted[0]["total_count"]), int(counted[0]["total_bytes"])

    async def count_visible(self, viewer: Viewer, asset_filter: AssetFilter) -> int:
        """How many files this filter finds for this viewer: the Files wall's own total, no page,
        for a number that opens that wall. The same count statement `visible_assets` uses."""
        where, bound = asset_filter.predicate()
        params = self._params(
            viewer,
            asset_id=None,
            reveal=self._reveal_existence(viewer),
            limit=1,
            offset=0,
            bound=bound,
        ) | {"reveal_named": self._reveal_named(viewer)}
        counted = await self._db.fetch_all(assets_count_query(where), params)
        return int(counted[0]["total_count"])

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
        """Which of these files this viewer may see. A membership test, not a page: the caller's own
        list bounds it, where `visible_assets(limit=len(ids))` would be cut at the page cap and
        report everything past it as refused. Asked a page at a time through `assets_of`.
        """
        return set(await self.assets_of(viewer, asset_ids))

    async def assets_of(self, viewer: Viewer, asset_ids: Sequence[str]) -> dict[str, AssetView]:
        """The rows for these ids that this viewer may see, keyed by id. Absent means not allowed
        or not there, and a caller must not tell those apart. Chunked a page at a time, so the
        caller's own list bounds it."""
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
        """The newest file this viewer may see under each of these usernames, keyed by username:
        the picture beside a username, read through the wall's own statement so it is never a file
        this viewer may not see. Absent means nothing to see or no such username, alike."""
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
        it. Absent means not allowed or not there, alike.

        `actionable_of`'s probe without the reasons or the denial log, for a screen deciding what to
        draw. A concealed file is present only where the grid would show it. `assets_of` is the
        call when a name or a size is wanted.
        """
        wanted = [asset_id for asset_id in dict.fromkeys(asset_ids) if _is_object_id(asset_id)]
        revealed = self._reveal_existence(viewer) == 1
        standing: dict[str, bool] = {}
        for start in range(0, len(wanted), MAX_PAGE_SIZE):
            chunk = wanted[start : start + MAX_PAGE_SIZE]
            sql, values = in_clause(_STANDING_OF, chunk)
            rows = await self._db.fetch_all(sql, [viewer.id, *values])
            standing.update(
                (str(row["asset_id"]), bool(row["concealed"]))
                for row in rows
                if revealed or not row["concealed"]
            )
        return standing

    async def actionable_of(self, viewer: Viewer, asset_ids: Sequence[str]) -> Actionable:
        """Which of these a viewer may WRITE to, and why not for the rest: the batch form of
        `open_asset`, so a write over a selection has the whole answer before it writes.

        A viewer may act on exactly what they may open (a placeholder is not something to tag). An
        id that appears only with THIS viewer's vault open is concealed by their own lock; anything
        still absent is refused. One statement per page of ids. A refusal is logged, a concealment
        is not, as `is_concealed` does.
        """
        wanted = [asset_id for asset_id in dict.fromkeys(asset_ids) if _is_object_id(asset_id)]
        standing: dict[str, bool] = {}
        for start in range(0, len(wanted), MAX_PAGE_SIZE):
            chunk = wanted[start : start + MAX_PAGE_SIZE]
            sql, values = in_clause(_STANDING_OF, chunk)
            rows = await self._db.fetch_all(sql, [viewer.id, *values])
            standing.update((str(row["asset_id"]), bool(row["concealed"])) for row in rows)
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

        **Hand it only ids `actionable_of` allowed.** It takes no viewer, so reading it cannot leave
        anybody thinking a scope was applied (the statements say why there is none). Paged, since
        SQLite binds one value per placeholder and a selection runs to thousands.
        """
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
        total, scoped and paged by one statement so the two describe the same groups. `floor` and
        `ceiling` bound a group by the faces this viewer may see; one and no ceiling is every group.
        """
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
        """What each of these files is called now, keyed by asset id, for the ones this viewer may
        have and that are somewhere Sift can see: the imported `original_filename` never changes,
        and a renamed file would go on wearing it.

        The location's relative path, not the resolved one, which can end in a content-addressed
        name; the first present copy, as `locate` serves. Absent means not allowed, not there or no
        copy present, alike.
        """
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
