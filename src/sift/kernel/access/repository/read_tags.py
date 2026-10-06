# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Tags wall and its lookups, scoped to the viewer asking."""

from __future__ import annotations

from collections.abc import Sequence

from sift.kernel.access.constraints import (
    NO_FILTER,
    NO_NARROWING,
    AssetFilter,
    EntityNarrowing,
)
from sift.kernel.access.repository.core import RepositoryCore
from sift.kernel.access.repository.entities import (
    ENTITY_SORT_SEEN,
    TAG_BY_ID,
    TAGS_BY_ID,
    _entity_sort,
    tags_position,
    tags_query,
)
from sift.kernel.access.repository.views import (
    FacetCount,
    TagPage,
    TagSuggestion,
    _is_object_id,
    _like_anywhere,
    _like_prefix,
    _tag_from_row,
)
from sift.kernel.access.viewer import Viewer
from sift.kernel.paging import MAX_PAGE_SIZE


class TagReads(RepositoryCore):
    """The scoped reads of tags."""

    async def tag_facets(
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
        """What the tags on this wall are made of, along one dimension. See `people_facets`."""
        return await self._entity_facets(
            "tag",
            facet,
            asset_filter=asset_filter,
            narrowing=narrowing,
            params=self._tag_params(
                viewer,
                tag_id=None,
                prefix=prefix,
                anywhere=anywhere,
                limit=max(1, min(limit, MAX_PAGE_SIZE)),
                asset_filter=asset_filter,
            ),
        )

    async def suggest_tags(
        self, viewer: Viewer, prefix: str = "", *, limit: int = 20, anywhere: bool = False
    ) -> list[TagSuggestion]:
        """Tags this viewer may know about, most-used first.

        The one tag suggester, for the chip editor, the autocomplete and the search token. An empty
        prefix is all of them; counts resolve through the grid's rule. `anywhere` matches inside the
        name, for a box that filters a page; a dropdown finishing a word matches prefixes.
        """
        return (await self.list_tags(viewer, prefix, limit=limit, anywhere=anywhere)).items

    async def list_tags(
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
    ) -> TagPage:
        """One page of the same list, with the scoped total beside it.

        `count_narrowed` as `suggest_people` reads it. One statement for the page and its count,
        since a second would be a second copy of every concealment rule.
        """
        if limit < 1:
            raise ValueError("a suggestion list needs at least one row")
        limit = min(limit, MAX_PAGE_SIZE)
        if offset < 0:
            raise ValueError("a page cannot start before the first row")

        where, bound = asset_filter.predicate()
        # ...and the wall's OWN rows, filtered by what the thing is rather than by its files. A
        # second seam beside the file filter rather than part of it; see `_row_narrowed`.
        narrowed, picked = narrowing.predicate()
        rows = await self._db.fetch_all(
            tags_query(where, narrowed),
            bound
            | picked
            | self._tag_params(
                viewer,
                tag_id=None,
                prefix=prefix,
                anywhere=anywhere,
                limit=limit,
                offset=offset,
                sort=sort,
                asset_filter=asset_filter,
                count_narrowed=count_narrowed,
            ),
        )
        # Nothing on the page means nothing to read a total from: no tags, or past the end.
        total = int(rows[0]["total_count"]) if rows else 0
        return TagPage(items=[_tag_from_row(row) for row in rows], total=total)

    def _tag_params(
        self,
        viewer: Viewer,
        *,
        tag_id: str | None,
        prefix: str = "",
        anywhere: bool = False,
        limit: int,
        offset: int = 0,
        sort: str = ENTITY_SORT_SEEN,
        asset_filter: AssetFilter = NO_FILTER,
        count_narrowed: bool = False,
    ) -> dict[str, object]:
        """What the tag statement binds. One place, so the wall and the by-id read cannot differ;
        `count_narrowed` is off for a by-id read, a tag's own page."""
        return {
            "viewer": viewer.id,
            "is_admin": 1 if viewer.is_admin else 0,
            "list_empty": self._lists_empty_rows(viewer, asset_filter),
            "reveal": self._reveal_existence(viewer),
            "reveal_named": self._reveal_named(viewer),
            # A read naming its row by id keeps the name of a row the walls draw as a locked tile;
            # see `_LOCKED_TILE`. Worked out here, beside the id it is about, so no caller can
            # bind the id and forget the other.
            "by_id": 1 if tag_id is not None else 0,
            "tag_id": tag_id,
            "prefix": prefix,
            "like": _like_anywhere(prefix) if anywhere else _like_prefix(prefix),
            "entity_sort": _entity_sort(sort),
            # Which tally a card on this wall prints. See `list_tags`.
            "count_narrowed": 1 if count_narrowed else 0,
            "limit": limit,
            "offset": offset,
        }

    async def visible_tag(self, viewer: Viewer, tag_id: str) -> TagSuggestion | None:
        """One tag, if this viewer may be shown it. See `visible_collection`."""
        if not _is_object_id(tag_id):
            return None
        rows = await self._db.fetch_all(
            TAG_BY_ID, self._unfiltered() | self._tag_params(viewer, tag_id=tag_id, limit=1)
        )
        if not rows:
            self._log_denied(viewer, tag_id)
            return None
        return _tag_from_row(rows[0])

    async def visible_tags(
        self, viewer: Viewer, tag_ids: Sequence[str]
    ) -> dict[str, TagSuggestion]:
        """The tags among these ids that this viewer may be shown, keyed by id.

        The batched form of `visible_tag`, for the reason `visible_people` is one: a screen holding
        a list of ids asks once rather than once per id. Absent means "not allowed" and "not there"
        together. Read off `TAGS_BY_ID`, the wall's own statement with its stored counts (see it
        for what that saves).
        """
        return {
            one.id: one
            for one in await self._by_ids(
                TAGS_BY_ID,
                tag_ids,
                lambda chunk: self._tag_params(viewer, tag_id=None, limit=chunk),
                _tag_from_row,
            )
        }

    async def position_of_tag(
        self,
        viewer: Viewer,
        tag_id: str,
        prefix: str = "",
        *,
        anywhere: bool = False,
        sort: str = ENTITY_SORT_SEEN,
        asset_filter: AssetFilter = NO_FILTER,
        narrowing: EntityNarrowing = NO_NARROWING,
        count_narrowed: bool = False,
    ) -> int | None:
        """How far into the Tags wall one tag sits, counting from zero. None if it is not in it.

        What `position_of_photo_set` is for its wall, with every argument `list_tags` takes. None
        for a tag this viewer may not be shown and for an id never minted, alike.
        """
        # An id that is not an id matches nothing, and is never bound as NULL: the statement reads
        # a NULL here as "no filter" and would answer with the position of an arbitrary row.
        if not _is_object_id(tag_id):
            return None

        where, bound = asset_filter.predicate()
        narrowed, picked = narrowing.predicate()
        rows = await self._db.fetch_all(
            tags_position(where, narrowed),
            bound
            | picked
            | self._tag_params(
                viewer,
                # The wall is never filtered to one tag, and this must not be either: binding an id
                # here would rank a list of one and every answer would be position zero.
                tag_id=None,
                prefix=prefix,
                anywhere=anywhere,
                # Not read by this statement: it ranks the whole wall and picks one row out of the
                # ranking. Bound anyway, because `_tag_params` binds everything the tag statement
                # can take and a parameter left out here is a hard error at the driver.
                limit=1,
                sort=sort,
                asset_filter=asset_filter,
                count_narrowed=count_narrowed,
            )
            | {"position_of": tag_id},
        )
        if not rows:
            return None
        # ROW_NUMBER counts from one and an offset counts from zero. Converted here, once, rather
        # than at each caller.
        return int(rows[0]["position"]) - 1
