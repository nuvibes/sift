# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Photo Sets wall and its lookups, scoped to the viewer asking."""

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
    _entity_sort,
    photo_sets_position,
    photo_sets_query,
)
from sift.kernel.access.repository.views import (
    FacetCount,
    PhotoSetPage,
    PhotoSetView,
    _is_object_id,
    _like_anywhere,
    _like_prefix,
    _photo_set_from_row,
)
from sift.kernel.access.viewer import Viewer
from sift.kernel.paging import MAX_PAGE_SIZE


class PhotoSetReads(RepositoryCore):
    """The scoped reads of photo sets."""

    async def photo_set_facets(
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
        """What the photo sets on this wall are made of, along one dimension. See
        `people_facets`."""
        return await self._entity_facets(
            "photo_set",
            facet,
            asset_filter=asset_filter,
            narrowing=narrowing,
            params=self._photo_set_params(
                viewer,
                photo_set_id=None,
                prefix=prefix,
                anywhere=anywhere,
                limit=max(1, min(limit, MAX_PAGE_SIZE)),
                asset_filter=asset_filter,
            ),
        )

    # --- photo sets ------------------------------------------------------------------------

    async def list_photo_sets(
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
    ) -> PhotoSetPage:
        """One page of the photo sets this viewer may know about. See `list_tags`.

        `asset_filter` is what makes this answer "the sets a person turns up in" as readily as it
        answers "every set": one parameter rather than a query per pair. See the filter seam in
        `entities.py` for why it can only ever filter.

        `count_narrowed` says which of the two tallies the number on a row is. Off, it is every
        item under that row this viewer may see, which is what the plain wall and the row's own
        page mean by it. On, it is how many of them are on THIS wall, which is what a card whose
        press carries the page it was pressed from has to say, or the number and the page it opens
        describe different sets with nothing on screen saying so. The People wall's rule, the same
        here, on the tags wall and on the sites wall; see `suggest_people`.
        """
        if limit < 1:
            raise ValueError("a photo set list needs at least one row")
        limit = min(limit, MAX_PAGE_SIZE)
        if offset < 0:
            raise ValueError("a page cannot start before the first row")

        where, bound = asset_filter.predicate()
        # ...and the wall's OWN rows, filtered by what the thing is rather than by its files. A
        # second seam beside the file filter rather than part of it; see `_row_narrowed`.
        narrowed, picked = narrowing.predicate()
        rows = await self._db.fetch_all(
            photo_sets_query(where, narrowed),
            {
                **bound,
                **picked,
                **self._photo_set_params(
                    viewer,
                    photo_set_id=None,
                    prefix=prefix,
                    anywhere=anywhere,
                    limit=limit,
                    offset=offset,
                    sort=sort,
                    asset_filter=asset_filter,
                    count_narrowed=count_narrowed,
                ),
            },
        )
        total = int(rows[0]["total_count"]) if rows else 0
        return PhotoSetPage(items=[_photo_set_from_row(row) for row in rows], total=total)

    async def visible_photo_set(self, viewer: Viewer, photo_set_id: str) -> PhotoSetView | None:
        """One photo set, if this viewer may be shown it. None means "no such set" and "not for
        you" together, which is the only answer either should get (see `visible_collection`)."""
        where, bound = NO_FILTER.predicate()
        rows = await self._db.fetch_all(
            photo_sets_query(where),
            {
                **bound,
                **self._photo_set_params(
                    viewer, photo_set_id=photo_set_id, limit=1, offset=0, sort=ENTITY_SORT_SEEN
                ),
            },
        )
        return _photo_set_from_row(rows[0]) if rows else None

    def _photo_set_params(
        self,
        viewer: Viewer,
        *,
        photo_set_id: str | None,
        prefix: str = "",
        anywhere: bool = False,
        limit: int,
        offset: int = 0,
        sort: str = ENTITY_SORT_SEEN,
        asset_filter: AssetFilter = NO_FILTER,
        count_narrowed: bool = False,
    ) -> dict[str, object]:
        """What the photo-set statement binds. One place; see `_tag_params`.

        `count_narrowed` is off for the by-id read, for the reason `_tag_params` gives.
        """
        return {
            "viewer": viewer.id,
            "is_admin": 1 if viewer.is_admin else 0,
            "list_empty": self._lists_empty_rows(viewer, asset_filter),
            "reveal": self._reveal_existence(viewer),
            "reveal_named": self._reveal_named(viewer),
            # A read naming its row by id keeps the name of a row the walls draw as a locked tile;
            # see `_LOCKED_TILE`. Worked out here, beside the id it is about, so no caller can
            # bind the id and forget the other.
            "by_id": 1 if photo_set_id is not None else 0,
            "photo_set_id": photo_set_id,
            "prefix": prefix,
            "like": _like_anywhere(prefix) if anywhere else _like_prefix(prefix),
            "entity_sort": _entity_sort(sort),
            # Which tally a card on this wall prints. See `list_photo_sets`.
            "count_narrowed": 1 if count_narrowed else 0,
            "limit": limit,
            "offset": offset,
        }

    async def position_of_photo_set(
        self,
        viewer: Viewer,
        photo_set_id: str,
        prefix: str = "",
        *,
        anywhere: bool = False,
        sort: str = ENTITY_SORT_SEEN,
        asset_filter: AssetFilter = NO_FILTER,
        narrowing: EntityNarrowing = NO_NARROWING,
        count_narrowed: bool = False,
    ) -> int | None:
        """How far into the Photo Sets wall one set sits, counting from zero. None if it is not in it.

        The same thing `position_of_person` is for the People wall: the wall pages by whole rows, so
        how many cards a page holds depends on the size of the screen and a page NUMBER means
        nothing durable. The address carries the set somebody was looking at instead, and this is
        what turns that back into a place to start from.

        EVERY ARGUMENT `list_photo_sets` TAKES, because the answer only means anything against the
        same question. Set forty of the whole library is a different set from set forty of one
        person's, and `count_narrowed` is in the list for a reason that is easy to miss: the wall's
        size orders read whichever tally the card prints, so a position read with the other one
        would rank by a number the page is not ordered by.

        None for a set this viewer may not be shown, and None for an id that was never minted: the
        same answer from the same rules, which is what stops a deep link being a way to ask whether
        a set exists.
        """
        # An id that is not an id matches nothing, and is never bound as NULL: the statement reads
        # a NULL here as "no filter" and would answer with the position of an arbitrary row.
        if not _is_object_id(photo_set_id):
            return None

        where, bound = asset_filter.predicate()
        narrowed, picked = narrowing.predicate()
        rows = await self._db.fetch_all(
            photo_sets_position(where, narrowed),
            {
                **bound,
                **picked,
                **self._photo_set_params(
                    viewer,
                    # The wall is never filtered to one set, and this must not be either: binding an
                    # id here would rank a list of one and every answer would be position zero.
                    photo_set_id=None,
                    prefix=prefix,
                    anywhere=anywhere,
                    # Not read by this statement: it ranks the whole wall and picks one row out of
                    # the ranking. Bound anyway, because `_photo_set_params` binds everything the
                    # photo-set statement can take and that is the whole point of there being one
                    # place; a parameter added there and left out here is a hard error at the
                    # driver.
                    limit=1,
                    sort=sort,
                    asset_filter=asset_filter,
                    count_narrowed=count_narrowed,
                ),
                "position_of": photo_set_id,
            },
        )
        if not rows:
            return None
        # ROW_NUMBER counts from one and an offset counts from zero. Converted here, once, rather
        # than at each caller.
        return int(rows[0]["position"]) - 1
