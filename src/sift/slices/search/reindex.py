# SPDX-License-Identifier: AGPL-3.0-or-later
"""Telling the index that something it already holds has changed: an edit is found by nothing."""

from __future__ import annotations

from collections.abc import Sequence

from sift.kernel.access import index_assets
from sift.kernel.db import Database
from sift.kernel.jobs import WAITED_ON_PRIORITY, JobQueue
from sift.kernel.jobs.quiet_hours import AT_NOW
from sift.kernel.log import get_logger
from sift.slices.search.jobs import (
    ASSET_IDS,
    FTS_REINDEX,
    FTS_REINDEX_FILES,
    IDS_PER_JOB,
    rebuild_pending,
)

logger = get_logger(__name__)


class Reindexer:
    """The reindex seam, one per application, at `app.state.reindexer`."""

    def __init__(self, *, database: Database, queue: JobQueue) -> None:
        self._database = database
        self._queue = queue

    async def touched(self, asset_id: str) -> None:
        """One asset's text changed: reindex it now; a failure is logged, never raised."""
        try:
            await index_assets(self._database, asset_id=asset_id)
        except Exception:
            logger.exception("could not refresh the search index for an asset")

    async def touched_many(self, asset_ids: Sequence[str]) -> None:
        """A set of assets whose text changed, in one pass rather than one each."""
        if not asset_ids:
            return
        try:
            await index_assets(self._database, asset_ids=list(asset_ids))
        except Exception:
            logger.exception("could not refresh the search index for a group of assets")

    async def queue_many(self, asset_ids: Sequence[str]) -> None:
        """A set of assets whose text changed, reindexed by a job a chunk at a time."""
        ids = list(asset_ids)
        if not ids:
            return
        try:
            await self._queue.enqueue_many(
                FTS_REINDEX_FILES,
                [{ASSET_IDS: ids[at : at + IDS_PER_JOB]} for at in range(0, len(ids), IDS_PER_JOB)],
                priority=WAITED_ON_PRIORITY,
                at=AT_NOW,
            )
        except Exception:
            logger.exception("could not queue a search index refresh for a group of assets")

    async def renamed(self) -> None:
        """A name many assets carry changed and they cannot be named: queue the whole rebuild."""
        try:
            if await rebuild_pending(self._queue):
                return
            await self._queue.enqueue(FTS_REINDEX)
        except Exception:
            logger.exception("could not queue a search index rebuild")
