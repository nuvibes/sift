# SPDX-License-Identifier: AGPL-3.0-or-later
"""What every read of the repository stands on: who is asking, what a statement binds, and
the one question every per-file route asks.

The reads are split by the thing read, one module each, and `store.Repository` puts them back
together as the one object that knows who is asking.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence

from sift.kernel.access.constraints import (
    NO_FILTER,
    AssetFilter,
    EntityNarrowing,
)
from sift.kernel.access.repository.assets import (
    point_query,
    shuffle_of,
)
from sift.kernel.access.repository.entities import (
    entity_facet_query,
)
from sift.kernel.access.repository.folders import (
    _VISIBLE_FOLDERS,
)
from sift.kernel.access.repository.grants import (
    _AN_ADMIN,
    _USER_BY_ID,
)
from sift.kernel.access.repository.views import (
    AccessError,
    FacetCount,
    Folder,
    _folder_from_row,
    _is_object_id,
)
from sift.kernel.access.viewer import Concealment, ObjectType, Role, Viewer
from sift.kernel.content import (
    ContentStore,
)
from sift.kernel.db import Database, Row, point_read
from sift.kernel.log import get_logger
from sift.kernel.paging import MAX_PAGE_SIZE

log = get_logger(__name__)

_POINT_WHERE, _POINT_BOUND = NO_FILTER.predicate()
_ONE_ASSET = point_read("access.one_asset", point_query(_POINT_WHERE))


class RepositoryCore:
    """The database, the content store and the clock every read is made with."""

    def __init__(
        self,
        database: Database,
        content: ContentStore,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._db = database
        # Held so a permitted file becomes a path HERE, behind the scope check: the content store
        # takes no viewer, so nothing else outside the kernel's wiring may hold it.
        self._content = content
        self._clock = clock

    def _now(self) -> int:
        return int(self._clock())

    async def load_viewer(
        self,
        user_id: str,
        *,
        show_hidden: bool = False,
        concealment: Concealment = Concealment.FULLY_GONE,
    ) -> Viewer | None:
        """Build the viewer for this request, from the database, every time. None for no such user
        or a disabled one, so disabling takes effect on the next request."""
        row = await self._db.fetch_one(_USER_BY_ID, (user_id,))
        if row is None:
            return None
        return Viewer(
            id=row["id"],
            role=Role(row["role"]),
            show_hidden=show_hidden,
            concealment=concealment,
            cache_stamp=int(row["cache_stamp"]),
        )

    async def an_admin(self) -> str | None:
        """The id of one enabled admin, or None: the user a pass that runs as Sift reads the library
        as, since every read is scoped. With no enabled admin the pass stops."""
        row = await self._db.fetch_one(_AN_ADMIN, ())
        return None if row is None else str(row["id"])

    async def _entity_facets(
        self,
        subject: str,
        facet: str,
        *,
        asset_filter: AssetFilter,
        narrowing: EntityNarrowing,
        params: dict[str, object],
    ) -> list[FacetCount]:
        """One dimension of one wall of things, counted from the statement that decides what the
        wall holds (`entity_facet_query`). The one reader of an entity facet's rows."""
        where, bound = asset_filter.predicate()
        narrowed, picked = narrowing.predicate()
        rows = await self._db.fetch_all(
            entity_facet_query(subject, facet, where, narrowed),
            bound | picked | params,
        )
        return [
            FacetCount(
                value=str(row["facet_value"]),
                count=int(row["facet_count"]),
                # A readable value selects a NULL label, so the row is asked, one shape for all.
                label=None if row["facet_label"] is None else str(row["facet_label"]),
            )
            for row in rows
        ]

    async def _by_ids[T](
        self,
        statement: str,
        ids: Sequence[str],
        params: Callable[[int], dict[str, object]],
        build: Callable[[Row], T],
    ) -> list[T]:
        """Every row a by-id statement answers for these ids, a page of ids at a time: bounded by
        the caller's list. The ids bind as one JSON array under `:ids`."""
        wanted = sorted(set(ids))
        found: list[T] = []
        for start in range(0, len(wanted), MAX_PAGE_SIZE):
            chunk = wanted[start : start + MAX_PAGE_SIZE]
            # By id, so a row the walls draw as a locked tile keeps its name here (`_LOCKED_TILE`).
            bound = self._unfiltered() | params(len(chunk)) | {"ids": json.dumps(chunk), "by_id": 1}
            found.extend(build(row) for row in await self._db.fetch_all(statement, bound))
        return found

    def reveals_named_rows(self, viewer: Viewer | None) -> bool:
        """Whether this viewer may be shown the row of a concealed person, collection, tag or Site:
        one answer for all, since such a row is nothing but a NAME, so only an open vault brings one
        back. The list and the by-id read ask the same. `None`, no viewer, is closed."""
        return viewer is not None and viewer.show_hidden

    def _reveal_named(self, viewer: Viewer | None) -> int:
        """The same answer, as the integer the queries bind: stricter than `_reveal_existence`, as
        these rows have no content but the name."""
        return 1 if self.reveals_named_rows(viewer) else 0

    @staticmethod
    def _lists_empty_rows(viewer: Viewer, asset_filter: AssetFilter) -> int:
        """Whether a row with nothing visible under it belongs on this wall: on an admin's
        unfiltered wall, where things are edited, and nowhere else."""
        return 1 if viewer.is_admin and asset_filter is NO_FILTER else 0

    @staticmethod
    def _unfiltered() -> dict[str, object]:
        """What a statement binds when nothing is filtering it, asked of the empty filter: which
        parameters a filter binds is the filter's business."""
        return NO_FILTER.predicate()[1]

    @staticmethod
    def _check_object(object_type: ObjectType, object_id: str | None) -> None:
        """A grant that can never match anything is refused where it is made: the global grant names
        no object and every other kind needs one, or a restrict would silently be no promise."""
        if object_type is ObjectType.GLOBAL:
            if object_id is not None:
                raise AccessError("a global grant names no object")
        elif object_id is None:
            raise AccessError(f"a {object_type.value} grant needs an object to name")

    @staticmethod
    def _reveal_existence(viewer: Viewer) -> int:
        """Whether concealed rows come back at all: with the vault open, or as locked placeholders."""
        revealed = viewer.show_hidden or viewer.concealment is Concealment.PLACEHOLDER
        return 1 if revealed else 0

    def _params(
        self,
        viewer: Viewer,
        *,
        asset_id: str | None,
        reveal: int,
        limit: int,
        offset: int,
        tag_id: str | None = None,
        collection_id: str | None = None,
        photo_set_id: str | None = None,
        hidden_only: bool = False,
        pinned_first: bool = False,
        seed: int | None = None,
        bound: Mapping[str, object],
    ) -> dict[str, object]:
        offset_of_shuffle, stride = shuffle_of(seed)
        # The filter's own parameters are merged in, never listed twice: a condition never bound
        # silently stops applying.
        return {
            "viewer": viewer.id,
            "is_admin": 1 if viewer.is_admin else 0,
            "asset_id": asset_id,
            "reveal": reveal,
            "limit": limit,
            "offset": offset,
            "tag_id": tag_id,
            "collection_id": collection_id,
            "photo_set_id": photo_set_id,
            "hidden_only": 1 if hidden_only else 0,
            "pinned_first": 1 if pinned_first else 0,
            # WHICH shuffle, bound on every statement, since the tail is chosen after this is built.
            "shuffle_offset": offset_of_shuffle,
            "shuffle_stride": stride,
            **bound,
        }

    async def _one(self, viewer: Viewer, asset_id: str, *, reveal: int) -> Row | None:
        """One asset, if this viewer may see it. The single question every per-asset route asks.

        The point form of the grid's statement (`_ONE_ASSET`), built once at import: the same text
        cut at checked seams, so it resolves visibility identically, but it seeks one file where the
        set form resolves the whole library. Every picture on a page asks it.
        """
        # An id that is not an id matches nothing, and crucially is never bound as NULL, which
        # the query reads as "no filter" and would answer with the first visible row.
        if not _is_object_id(asset_id):
            return None
        rows = await self._db.fetch_all(
            _ONE_ASSET,
            self._params(
                viewer, asset_id=asset_id, reveal=reveal, limit=1, offset=0, bound=_POINT_BOUND
            ),
        )
        return rows[0] if rows else None

    async def _folders(
        self,
        viewer: Viewer,
        *,
        folder_id: str | None = None,
        folder_ids: Sequence[str] | None = None,
        parent_id: str | None = None,
        root_id: str | None = None,
        top_level: bool = False,
    ) -> list[Folder]:
        rows = await self._db.fetch_all(
            _VISIBLE_FOLDERS,
            {
                "viewer": viewer.id,
                "is_admin": 1 if viewer.is_admin else 0,
                "folder_id": folder_id,
                "folder_ids": None if folder_ids is None else json.dumps(list(folder_ids)),
                "parent_id": parent_id,
                "root_id": root_id,
                "top_level": 1 if top_level else 0,
                "reveal": self._reveal_existence(viewer),
            },
        )
        return [_folder_from_row(row) for row in rows]

    def _log_denied(self, viewer: Viewer, object_id: str) -> None:
        """A denial is worth a line, and the line carries no filename: who asked for which id is
        enough to spot somebody walking the library, and a log gets pasted into bug reports."""
        log.info("access.denied", viewer_id=viewer.id, object_id=object_id)
