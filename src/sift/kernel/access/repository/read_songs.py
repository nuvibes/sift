# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Songs wall and its lookups, scoped to the viewer asking."""

from __future__ import annotations

from sift.kernel.access.constraints import (
    NO_FILTER,
    NO_NARROWING,
    AssetFilter,
    EntityNarrowing,
)
from sift.kernel.access.repository.core import RepositoryCore
from sift.kernel.access.repository.entities import (
    ENTITY_SORT_SEEN,
    SONG_SORT_KEYS,
    songs_position,
    songs_query,
)
from sift.kernel.access.repository.views import (
    FacetCount,
    SongPage,
    SongView,
    _is_object_id,
    _like_anywhere,
    _like_prefix,
    _song_from_row,
)
from sift.kernel.access.repository.wall_songs import SONG_BY_ID
from sift.kernel.access.repository.walls import plain_order
from sift.kernel.access.viewer import Viewer
from sift.kernel.paging import MAX_PAGE_SIZE


class SongReads(RepositoryCore):
    """The scoped reads of songs."""

    async def song_facets(
        self,
        viewer: Viewer,
        facet: str,
        *,
        limit: int = 24,
        prefix: str = "",
        anywhere: bool = False,
        asset_filter: AssetFilter = NO_FILTER,
        narrowing: EntityNarrowing = NO_NARROWING,
    ) -> list[FacetCount]:
        """What the songs on this wall are made of, along one dimension. See `people_facets`."""
        return await self._entity_facets(
            "song",
            facet,
            asset_filter=asset_filter,
            narrowing=narrowing,
            params=self._song_params(
                viewer,
                song_id=None,
                prefix=prefix,
                anywhere=anywhere,
                limit=max(1, min(limit, MAX_PAGE_SIZE)),
                asset_filter=asset_filter,
            ),
        )

    # --- songs -----------------------------------------------------------------------------

    async def list_songs(
        self,
        viewer: Viewer,
        prefix: str = "",
        *,
        limit: int = 20,
        offset: int = 0,
        anywhere: bool = False,
        sort: str = ENTITY_SORT_SEEN,
        asset_filter: AssetFilter = NO_FILTER,
        narrowing: EntityNarrowing = NO_NARROWING,
        count_narrowed: bool = False,
    ) -> SongPage:
        """One page of the songs this viewer may know about: the Photo Sets wall's read, over the
        songs (`list_photo_sets` says what each argument means)."""
        if limit < 1:
            raise ValueError("a song list needs at least one row")
        limit = min(limit, MAX_PAGE_SIZE)
        if offset < 0:
            raise ValueError("a page cannot start before the first row")
        where, bound = asset_filter.predicate()
        narrowed, picked = narrowing.predicate()
        params = self._song_params(
            viewer,
            song_id=None,
            prefix=prefix,
            anywhere=anywhere,
            limit=limit,
            offset=offset,
            sort=sort,
            asset_filter=asset_filter,
            count_narrowed=count_narrowed,
        )
        rows = await self._db.fetch_all(
            songs_query(where, narrowed, plain=plain_order(params, "song_id")),
            {**bound, **picked, **params},
        )
        total = int(rows[0]["total_count"]) if rows else 0
        return SongPage(items=[_song_from_row(row) for row in rows], total=total)

    async def visible_song(self, viewer: Viewer, song_id: str) -> SongView | None:
        """One song, if this viewer may be shown it. None means "no such song" and "not for you"
        together, which is the only answer either should get (see `visible_collection`)."""
        if not _is_object_id(song_id):
            return None
        _where, bound = NO_FILTER.predicate()
        rows = await self._db.fetch_all(
            SONG_BY_ID,
            {
                **bound,
                **self._song_params(
                    viewer, song_id=song_id, limit=1, offset=0, sort=ENTITY_SORT_SEEN
                ),
            },
        )
        return _song_from_row(rows[0]) if rows else None

    def _song_params(
        self,
        viewer: Viewer,
        *,
        song_id: str | None,
        prefix: str = "",
        anywhere: bool = False,
        limit: int,
        offset: int = 0,
        sort: str = ENTITY_SORT_SEEN,
        asset_filter: AssetFilter = NO_FILTER,
        count_narrowed: bool = False,
    ) -> dict[str, object]:
        """What the songs statement binds. One place; see `_photo_set_params`."""
        return {
            "viewer": viewer.id,
            "is_admin": 1 if viewer.is_admin else 0,
            "list_empty": self._lists_empty_rows(viewer, asset_filter),
            "reveal": self._reveal_existence(viewer),
            "reveal_named": self._reveal_named(viewer),
            "by_id": 1 if song_id is not None else 0,
            "song_id": song_id,
            "prefix": prefix,
            "like": _like_anywhere(prefix) if anywhere else _like_prefix(prefix),
            # The shared orders, and the Music wall's own (`SONG_SORT_KEYS`).
            "entity_sort": sort if sort in SONG_SORT_KEYS else ENTITY_SORT_SEEN,
            "count_narrowed": 1 if count_narrowed else 0,
            "limit": limit,
            "offset": offset,
        }

    async def position_of_song(
        self,
        viewer: Viewer,
        song_id: str,
        prefix: str = "",
        *,
        anywhere: bool = False,
        sort: str = ENTITY_SORT_SEEN,
        asset_filter: AssetFilter = NO_FILTER,
        narrowing: EntityNarrowing = NO_NARROWING,
        count_narrowed: bool = False,
    ) -> int | None:
        """How far into the Music wall one song sits, counting from zero. None if it is not in it.
        Every argument `list_songs` takes, for the reason `position_of_photo_set` gives."""
        if not _is_object_id(song_id):
            return None
        where, bound = asset_filter.predicate()
        narrowed, picked = narrowing.predicate()
        rows = await self._db.fetch_all(
            songs_position(where, narrowed),
            {
                **bound,
                **picked,
                **self._song_params(
                    viewer,
                    # Never one song: see `position_of_photo_set`.
                    song_id=None,
                    prefix=prefix,
                    anywhere=anywhere,
                    limit=1,
                    sort=sort,
                    asset_filter=asset_filter,
                    count_narrowed=count_narrowed,
                ),
                "position_of": song_id,
            },
        )
        if not rows:
            return None
        return int(rows[0]["position"]) - 1
