# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Loops wall and its lookups, scoped to the viewer asking."""

from __future__ import annotations

from sift.kernel.access.constraints import (
    NO_FILTER,
    AssetFilter,
)
from sift.kernel.access.repository.core import RepositoryCore
from sift.kernel.access.repository.entities import (
    ENTITY_SORT_SEEN,
    _entity_sort,
    loops_position,
    loops_query,
)
from sift.kernel.access.repository.views import (
    LoopPage,
    LoopView,
    _is_object_id,
    _like_anywhere,
    _loop_from_row,
)
from sift.kernel.access.viewer import Viewer
from sift.kernel.paging import MAX_PAGE_SIZE


class LoopReads(RepositoryCore):
    """The scoped reads of loops."""

    # --- loops -----------------------------------------------------------------------------

    async def list_loops(
        self,
        viewer: Viewer,
        *,
        limit: int = 20,
        offset: int = 0,
        sort: str = ENTITY_SORT_SEEN,
        asset_id: str | None = None,
        asset_filter: AssetFilter = NO_FILTER,
        tag: str | None = None,
        called: str | None = None,
    ) -> LoopPage:
        """One page of the loops this viewer may see.

        `asset_id` filters to the loops on one file, which is what the player asks for. An id that
        names no file matches nothing: the predicate's doing rather than a check in front of it,
        exactly as `visible_assets` handles its own filter ids.

        `tag` is how this wall is filtered to a tag, and it is the ONLY way: a Loop may carry a tag
        itself, or be cut from a video that carries one, and the statement answers both per Loop.
        The filter language describes files and cannot say the first, so the statement is told
        directly, and the tag must NOT also be in `asset_filter`. Put there, it filters the
        videos to the ones carrying it and the Loops tagged in their own right are lost: a narrower
        answer and never a wider one, which is the safe direction for a mistake. See `loops_query`.

        `asset_filter` is everything else: the related narrowing (a person, a site, a collection)
        and the viewer's own filter from the bar, one tree, every condition binding every Loop.

        `called` is the wall's search box: the Loops whose OWN name holds it, in any case. It is not
        a file filter, since the name belongs to the Loop and the files have their own.
        """
        if limit < 1:
            raise ValueError("a loop list needs at least one row")
        limit = min(limit, MAX_PAGE_SIZE)
        if offset < 0:
            raise ValueError("a page cannot start before the first row")

        where, bound = asset_filter.predicate()
        rows = await self._db.fetch_all(
            loops_query(where),
            {
                **bound,
                **self._loop_params(
                    viewer,
                    loop_id=None,
                    asset_id=asset_id,
                    limit=limit,
                    offset=offset,
                    sort=sort,
                    loop_tag=tag,
                    called=called,
                ),
            },
        )
        total = int(rows[0]["total_count"]) if rows else 0
        return LoopPage(items=[_loop_from_row(row, viewer) for row in rows], total=total)

    async def visible_loop(self, viewer: Viewer, loop_id: str) -> LoopView | None:
        """One loop, if this viewer may see the file it is cut from. None either way."""
        where, bound = NO_FILTER.predicate()
        rows = await self._db.fetch_all(
            loops_query(where),
            {
                **bound,
                **self._loop_params(
                    viewer, loop_id=loop_id, asset_id=None, limit=1, offset=0, sort=ENTITY_SORT_SEEN
                ),
            },
        )
        return _loop_from_row(rows[0], viewer) if rows else None

    def _loop_params(
        self,
        viewer: Viewer,
        *,
        loop_id: str | None,
        asset_id: str | None,
        limit: int,
        offset: int = 0,
        sort: str = ENTITY_SORT_SEEN,
        loop_tag: str | None = None,
        called: str | None = None,
    ) -> dict[str, object]:
        return {
            "viewer": viewer.id,
            "is_admin": 1 if viewer.is_admin else 0,
            "reveal": self._reveal_existence(viewer),
            "reveal_named": self._reveal_named(viewer),
            "loop_id": loop_id,
            "loop_asset_id": asset_id,
            # The tag being asked about, when a tag is what is being asked about. NULL on every
            # other wall, which is what makes the Loop-tag clauses in the statement no-ops there.
            "loop_tag": loop_tag,
            # The typed name, taken literally and matched anywhere; NULL where nothing is typed.
            "loop_called": _like_anywhere(called) if called else None,
            "entity_sort": _entity_sort(sort),
            "limit": limit,
            "offset": offset,
        }

    async def position_of_loop(
        self,
        viewer: Viewer,
        loop_id: str,
        *,
        sort: str = ENTITY_SORT_SEEN,
        asset_id: str | None = None,
        asset_filter: AssetFilter = NO_FILTER,
        tag: str | None = None,
        called: str | None = None,
    ) -> int | None:
        """How far into the Loops wall one Loop sits, counting from zero. None if it is not in it.

        The wall of Loops is drawn by the media grid rather than by a wall of cards, and the grid
        asks the server to resolve the row in the address: this is what resolves it. See
        `position_of_photo_set` for the shape.

        EVERY ARGUMENT `list_loops` TAKES, `tag` and `called` included and `tag` kept out of
        `asset_filter` exactly as it is there: a ranking taken any other way is a position in a list this wall does not
        draw.

        None for a Loop of a file this viewer may not be shown, and None for an id that was never
        minted: the same answer from the same rules.
        """
        # An id that is not an id matches nothing, and is never bound as NULL: the statement reads
        # a NULL here as "no filter" and would answer with the position of an arbitrary row.
        if not _is_object_id(loop_id):
            return None

        where, bound = asset_filter.predicate()
        rows = await self._db.fetch_all(
            loops_position(where),
            {
                **bound,
                **self._loop_params(
                    viewer,
                    # The wall is never filtered to one Loop, and this must not be either: binding
                    # an id here would rank a list of one and every answer would be position zero.
                    loop_id=None,
                    asset_id=asset_id,
                    # Not read by this statement; bound anyway, for the reason `_loop_params`
                    # exists.
                    limit=1,
                    sort=sort,
                    loop_tag=tag,
                    called=called,
                ),
                "position_of": loop_id,
            },
        )
        if not rows:
            return None
        # ROW_NUMBER counts from one and an offset counts from zero. Converted here, once.
        return int(rows[0]["position"]) - 1
