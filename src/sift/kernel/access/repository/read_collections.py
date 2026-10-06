# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Collections wall and its lookups, scoped to the viewer asking."""

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
    collections_position,
    collections_query,
)
from sift.kernel.access.repository.views import (
    CollectionPage,
    CollectionView,
    FacetCount,
    _collection_from_row,
    _is_object_id,
    _like_anywhere,
    _like_prefix,
)
from sift.kernel.access.repository.wall_collections import COLLECTION_BY_ID
from sift.kernel.access.viewer import Viewer
from sift.kernel.paging import MAX_PAGE_SIZE


class CollectionReads(RepositoryCore):
    """The scoped reads of collections."""

    async def collection_facets(
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
        """What the collections on this wall are made of, along one dimension. See
        `people_facets`."""
        return await self._entity_facets(
            "collection",
            facet,
            asset_filter=asset_filter,
            narrowing=narrowing,
            params=self._collection_params(
                viewer,
                collection_id=None,
                prefix=prefix,
                anywhere=anywhere,
                limit=max(1, min(limit, MAX_PAGE_SIZE)),
                asset_filter=asset_filter,
            ),
        )

    def _collection_params(
        self,
        viewer: Viewer,
        *,
        collection_id: str | None,
        prefix: str = "",
        anywhere: bool = False,
        limit: int,
        offset: int = 0,
        sort: str = ENTITY_SORT_SEEN,
        asset_filter: AssetFilter = NO_FILTER,
    ) -> dict[str, object]:
        return {
            "viewer": viewer.id,
            "is_admin": 1 if viewer.is_admin else 0,
            "list_empty": self._lists_empty_rows(viewer, asset_filter),
            "reveal": self._reveal_existence(viewer),
            "reveal_named": self._reveal_named(viewer),
            # A read naming its row by id keeps the name of a row the walls draw as a locked tile;
            # see `_LOCKED_TILE`. Worked out here, beside the id it is about, so no caller can
            # bind the id and forget the other.
            "by_id": 1 if collection_id is not None else 0,
            "collection_id": collection_id,
            "prefix": prefix,
            "like": _like_anywhere(prefix) if anywhere else _like_prefix(prefix),
            "entity_sort": _entity_sort(sort),
            "limit": limit,
            "offset": offset,
        }

    async def visible_collections(
        self, viewer: Viewer, *, limit: int = MAX_PAGE_SIZE
    ) -> list[CollectionView]:
        """Collections this viewer may know about, by name, each with a scoped count.

        The one collections lister. The list screen, the drag target and anything that offers a
        collection to add to all read it, for the reason the tag and people suggesters give: two
        of them drifting apart is a collection appearing in one place and not the other with
        nothing to say which is right.
        """
        return (await self.list_collections(viewer, limit=limit)).items

    async def list_collections(
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
    ) -> CollectionPage:
        """One page of the same list, with the scoped total beside it. See `list_tags`."""
        if limit < 1:
            raise ValueError("a collection list needs at least one row")
        limit = min(limit, MAX_PAGE_SIZE)
        if offset < 0:
            raise ValueError("a page cannot start before the first row")

        where, bound = asset_filter.predicate()
        # ...and the wall's OWN rows, filtered by what the thing is rather than by its files. A
        # second seam beside the file filter rather than part of it; see `_row_narrowed`.
        narrowed, picked = narrowing.predicate()
        rows = await self._db.fetch_all(
            collections_query(where, narrowed),
            bound
            | picked
            | self._collection_params(
                viewer,
                collection_id=None,
                prefix=prefix,
                anywhere=anywhere,
                limit=limit,
                offset=offset,
                sort=sort,
                asset_filter=asset_filter,
            ),
        )
        total = int(rows[0]["total_count"]) if rows else 0
        return CollectionPage(items=[_collection_from_row(row) for row in rows], total=total)

    async def visible_collection(self, viewer: Viewer, collection_id: str) -> CollectionView | None:
        """One collection, if this viewer may be shown it.

        None means "no such collection" and "not for you" together, which is the only answer
        either should get. Built from the query the list is built from rather than reimplemented:
        a by-id route deciding this for itself is a second opinion about what exists, and that is
        how a list comes to refuse something a detail route hands over.
        """
        if not _is_object_id(collection_id):
            return None
        rows = await self._db.fetch_all(
            COLLECTION_BY_ID,
            self._unfiltered()
            | self._collection_params(viewer, collection_id=collection_id, limit=1),
        )
        if not rows:
            self._log_denied(viewer, collection_id)
            return None
        return _collection_from_row(rows[0])

    async def position_of_collection(
        self,
        viewer: Viewer,
        collection_id: str,
        prefix: str = "",
        *,
        anywhere: bool = False,
        sort: str = ENTITY_SORT_SEEN,
        asset_filter: AssetFilter = NO_FILTER,
        narrowing: EntityNarrowing = NO_NARROWING,
    ) -> int | None:
        """How far into the Collections wall one collection sits, counting from zero.

        None if it is not in it. Every argument `list_collections` takes, for the reason
        `position_of_photo_set` gives: a position only means anything in the list it was taken from.
        """
        # An id that is not an id matches nothing, and is never bound as NULL: the statement reads
        # a NULL here as "no filter" and would answer with the position of an arbitrary row.
        if not _is_object_id(collection_id):
            return None

        where, bound = asset_filter.predicate()
        narrowed, picked = narrowing.predicate()
        rows = await self._db.fetch_all(
            collections_position(where, narrowed),
            bound
            | picked
            | self._collection_params(
                viewer,
                # The wall is never filtered to one collection, and this must not be either.
                collection_id=None,
                prefix=prefix,
                anywhere=anywhere,
                # Not read by this statement; bound anyway. See `position_of_tag`.
                limit=1,
                sort=sort,
                asset_filter=asset_filter,
            )
            | {"position_of": collection_id},
        )
        if not rows:
            return None
        # ROW_NUMBER counts from one and an offset counts from zero. Converted here, once.
        return int(rows[0]["position"]) - 1
