# SPDX-License-Identifier: AGPL-3.0-or-later
"""The walls of files: a page, its total, its facets, a position on it, and the files a list of
ids comes to for one viewer.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath

from sift.kernel.access.constraints import (
    NO_FILTER,
    AssetFilter,
    Not,
    Where,
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
from sift.kernel.db import Row, in_clause, point_read
from sift.kernel.paging import MAX_PAGE_SIZE

# The stored counts for one user, and an upper bound on the library beside them (the highest row
# number handed out, one seek rather than a pass), read on every page. See `drive_for`.
_VIEWER_STATS = point_read(
    "access.viewer_stats",
    "SELECT s.permitted, s.concealed, s.permitted_bytes, s.concealed_bytes,"
    " (SELECT MAX(rowid) FROM assets) AS library"
    " FROM viewer_stats s WHERE s.user_id = ?",
)

# Which of a list of files this user may act on, in one statement: a row for every file they
# are permitted to see, with the vault's answer. Absent means refused, or not there: the same
# answer on purpose.
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

    async def facet_counts(
        self,
        viewer: Viewer,
        facet: str,
        *,
        asset_filter: AssetFilter = NO_FILTER,
        hidden_only: bool = False,
        limit: int = 24,
    ) -> list[FacetCount]:
        """How many of the files this query reaches carry each value of one dimension.

        From the statement that decides what those files ARE, so the number beside a facet cannot
        disagree with the screen: a count is a disclosure. A value whose own row this viewer may not
        be told about is absent, never a zero. An ID value is named in `label` by the same read.
        """
        joins, value = FACETS[facet]
        label = FACET_LABELS.get(facet, "")
        # A concealed value never reaches the group; its files still count under their other values.
        extra = concealed_value(facet)
        where, bound = asset_filter.predicate()
        query = facet_query(where, joins=joins, value=value, label=label, extra=extra)
        rows = await self._db.fetch_all(
            query,
            self._params(
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
            )
            | {"reveal_named": self._reveal_named(viewer)},
        )
        return [
            FacetCount(
                value=str(row["facet_value"]),
                count=int(row["files"]),
                # Asked of the table: a dimension with no label has no such column at all.
                label=str(row["facet_label"]) if label else None,
            )
            for row in rows
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
    ) -> AssetPage:
        """A page of what this viewer may see, and how many there are in total.

        `seed` is WHICH shuffle, from the address so a visit pages one shuffle. `after` continues
        after the last row of the page before by one seek (`SEEKABLE_SORTS`, nothing arranged).
        `tag_id` and `collection_id` filter; None is no filter. `pinned_first` is whether THIS wall
        floats the pin. `hidden_only` is the Hidden screen. A vaulted file is absent unless the
        vault is open or placeholders were asked for, and `asset_filter` is applied by this same
        statement, so the total counts exactly the rows the page is cut from.
        """
        if limit < 1:
            raise ValueError("a page needs at least one row")
        if offset < 0:
            raise ValueError("a page cannot start before the first row")
        limit = min(limit, MAX_PAGE_SIZE)
        asked = _Asked(sort, tag_id, collection_id, photo_set_id, hidden_only, pinned_first, seed)
        reveal = self._reveal_existence(viewer)
        where, bound = asset_filter.predicate()
        # A sequence or a pin in front of the sort, left in when nothing is named, costs the whole
        # set: an expression cannot be answered from an index.
        sequence = collection_id is not None or photo_set_id is not None
        arranged = pinned_first or sequence
        if after is not None:
            if arranged or sort not in SEEKABLE_SORTS:
                raise ValueError("this wall cannot be continued from a row; page by offset")
            continued = await self._continued(viewer, sort, after, reveal=reveal, bound=bound)
            if continued is None:
                return AssetPage(items=[], total=0)
            bound = continued
        # The stored counts decide which side the page walks first, and answer an unfiltered total.
        stats = await self._db.fetch_one(_VIEWER_STATS, (viewer.id,))
        drive = _drive_of(stats)
        pinned: list[Row] = []
        count_where, count_bound = where, bound
        if pinned_first and not sequence:
            pinned = await self._pins(viewer, asset_filter, asked, drive=drive, reveal=reveal)
            where, bound = asset_filter.also(Not(Where("pinned"))).predicate()
            arranged = False
        # A page that begins among the pins takes the rest of them and fills up from the walk.
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
        return self._params(
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
        self, viewer: Viewer, asset_filter: AssetFilter, asked: _Asked, *, drive: str, reveal: int
    ) -> list[Row]:
        """The pinned files of a wall that floats them, read on their own in the sort's order.

        Ordering the whole set by the pin defeats the index, so the pins are read as a predicate and
        the rest of the page walks the index with them excluded: the same statement with one
        conjunct each way, so between them exactly the files the total counts. A collection or a
        photo set is a sequence somebody arranged, and a pin does not float a row out of one.
        """
        pins_where, pins_bound = asset_filter.also(Where("pinned")).predicate()
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
            asked.sort, where, arranged=arranged, counted=False, drive=drive, continued=continued
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
        """How many files the whole wall holds, pins included, and their size: `(files, bytes)`.

        A wall that filtered nothing is counted already, per user, by the triggers that keep the
        verdict. One that did is counted by a statement assembled from the same seams as the page,
        so the two cannot come to describe different files. A file the vault holds back adds no
        bytes while it is shut, even where placeholder mode counts its locked tile.
        """
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
        params = {**params, "reveal_named": named}
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
        """How far into this screen's results one file sits, counting from zero. None if it is not
        in them.

        An address carries the file somebody was looking at, and this turns it back into a place.
        Every argument matches `visible_assets`, since a position only means anything in the list
        it was taken from. None for a file this viewer may not see, one not there, and one not in
        these results alike, so a deep link cannot ask whether something exists.
        """
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
            | {"position_of": asset_id},
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
