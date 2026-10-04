# SPDX-License-Identifier: AGPL-3.0-or-later
"""Telling the index that something it already holds has changed.

Arrivals are found on their own (an anti-join finds a file with no row), but an edited row is
looked for by nothing, so a renamed tag would not be found by its new name until a rebuild. The
features that write indexed text say so here, and the existing reindex does the rest.

`touched` and `touched_many` reindex named assets inside the request, so the next search is right:
a tag, person, username or site knows its files. `renamed()` queues the whole-library rebuild for
the one case that cannot name them, a merge, whose moved files are decided inside its own
transaction. Its guard stands down only for a queued WHOLE-LIBRARY pass: a queued catch-up is blind
to edits, so standing down for one would lose the edit.
"""

from __future__ import annotations

from collections.abc import Sequence

from sift.kernel.access import index_assets
from sift.kernel.db import Database
from sift.kernel.jobs import JobQueue
from sift.kernel.log import get_logger
from sift.slices.search.jobs import FTS_REINDEX, rebuild_pending

logger = get_logger(__name__)


class Reindexer:
    """The reindex seam. One per application, reached at `app.state.reindexer`.

    Held by the slices that change indexed text rather than imported: they depend on the shape and
    know nothing of the index behind it.
    """

    def __init__(self, *, database: Database, queue: JobQueue) -> None:
        self._database = database
        self._queue = queue

    async def touched(self, asset_id: str) -> None:
        """One asset's text changed. Reindex it now.

        Failure is logged and swallowed: the write it describes has committed, and a 500 would only
        invite repeating it. A briefly stale search is the smaller failure, and the next rebuild
        corrects it.
        """
        try:
            await index_assets(self._database, asset_id=asset_id)
        except Exception:
            logger.exception("could not refresh the search index for an asset")

    async def touched_many(self, asset_ids: Sequence[str]) -> None:
        """A set of assets whose text changed. One pass, not one pass each.

        One transaction and one orphan sweep for the whole set, where a loop over `touched` would
        hold the write lock once per asset. Swallows and logs as `touched` does.
        """
        if not asset_ids:
            return
        try:
            await index_assets(self._database, asset_ids=list(asset_ids))
        except Exception:
            logger.exception("could not refresh the search index for a group of assets")

    async def renamed(self) -> None:
        """A name that many assets carry has changed. Queue the rebuild that catches all of them.

        Only for a write that cannot say which files it changed; one that can calls `touched_many`.
        """
        try:
            if await rebuild_pending(self._queue):
                return
            await self._queue.enqueue(FTS_REINDEX)
        except Exception:
            logger.exception("could not queue a search index rebuild")
