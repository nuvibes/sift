# SPDX-License-Identifier: AGPL-3.0-or-later
"""Keeping the search index in step with the library, queued only when there is work."""

from __future__ import annotations

import time
from asyncio import sleep
from typing import Any

from sift.kernel.access import (
    anything_unindexed,
    index_assets,
    index_new_assets,
    sweep_orphans,
    unindexed_count,
)
from sift.kernel.changes import About, announce, who_may_see_a_file
from sift.kernel.db import Database
from sift.kernel.jobs import JobContext, JobQueue, JobState, register_handler
from sift.kernel.log import get_logger
from sift.kernel.seams import SettingsSeam
from sift.kernel.version import app_version

log = get_logger(__name__)

FTS_REINDEX = "fts_reindex"

SEARCH_EVENTS_PRUNE = "search_events_prune"

#: Days a search is kept; zero, the default, keeps it for ever.
KEEP_DAYS_KEY = "search.keep_records_days"
DEFAULT_KEEP_DAYS = 0

#: Daily: the horizon is in days, so a more frequent look finds nothing new.
PRUNE_EVERY_SECONDS = 24 * 60 * 60

_SECONDS_PER_DAY = 24 * 60 * 60

#: Here, not beside the writes: the service imports this module.
_FORGET_OLD_EVENTS = "DELETE FROM search_events WHERE at < ?"


async def forget_events_older_than(database: Database, before: int) -> int:
    """Drop every recorded search older than this moment, for every user; returns how many went."""
    async with database.write() as connection:
        cursor = await connection.execute(_FORGET_OLD_EVENTS, (before,))
        return int(cursor.rowcount)


def keep_days_from(value: object) -> int:
    """The retention rule as a whole number of days; zero is off."""
    try:
        return max(0, int(str(value)))
    except (TypeError, ValueError):
        return DEFAULT_KEEP_DAYS


#: A rename's files, a chunk per write transaction so no other write waits long.
FTS_REINDEX_FILES = "fts_reindex_files"
ASSET_IDS = "asset_ids"
IDS_PER_JOB = 2000
IDS_PER_WRITE = 100
PAUSE_BETWEEN_WRITES = 0.005


async def reindex_files(context: JobContext, *, database: Database) -> None:
    """Rewrite these assets' rows a chunk at a time, then tell whoever may be searching them."""
    asset_ids = context.payload.get(ASSET_IDS)
    if not isinstance(asset_ids, list) or not all(isinstance(one, str) for one in asset_ids):
        raise ValueError("asset_ids must be a list of asset ids")
    await context.set_note(_files(len(asset_ids), "For"))
    written = 0
    for at in range(0, len(asset_ids), IDS_PER_WRITE):
        await context.raise_if_canceled()
        chunk = asset_ids[at : at + IDS_PER_WRITE]
        written += await index_assets(database, asset_ids=chunk, sweep=False)
        await context.set_progress(min(1.0, (at + IDS_PER_WRITE) / len(asset_ids)))
        await sleep(PAUSE_BETWEEN_WRITES)
    await sweep_orphans(database)
    async with database.write() as connection:
        announce(await who_may_see_a_file(connection), About.LIBRARY)
    await context.set_note(_files(written, "Indexed"))
    log.info("search.reindexed", assets=written, whole_library=False)


def _files(count: int, verb: str) -> str:
    return f"{verb} {count:,} {'file' if count == 1 else 'files'}"


SCOPE = "scope"
CATCH_UP = "catch_up"


async def reindex(context: JobContext, *, database: Database) -> None:
    """The job, in its three shapes: one asset, a catch-up, or a full rebuild."""
    asset_id = context.payload.get("asset_id")
    if asset_id is not None and not isinstance(asset_id, str):
        raise ValueError("asset_id must be the id of an asset")

    await context.raise_if_canceled()

    if asset_id is not None:
        written = await index_assets(database, asset_id=asset_id)
        log.info("search.reindexed", assets=written, whole_library=False)
        return

    catching_up = context.payload.get(SCOPE) == CATCH_UP

    # Counted once, before any row is written, so the screen says why an unasked pass is running.
    waiting = await unindexed_count(database)
    await context.set_note(_what_it_is_doing(waiting, catching_up=catching_up))

    if catching_up:
        written = await index_new_assets(database)
    else:
        written = await index_assets(database, rebuild=True)
    await context.set_progress(1.0)
    await context.set_note(_files(written, "Indexed"))
    log.info("search.reindexed", assets=written, whole_library=not catching_up)


def _what_it_is_doing(waiting: int, *, catching_up: bool) -> str:
    """The sentence on the row while the pass runs, naming the version that started it."""
    files = f"{waiting:,} {'file' if waiting == 1 else 'files'}"
    scope = "Indexing what was imported" if catching_up else "Rebuilding the search index"
    version = app_version()
    if version:
        return f"Catching up after the upgrade to {version} \u2014 {scope}, {files}"
    return f"{scope} \u2014 {files}"


async def _pending(queue: JobQueue) -> bool:
    """Whether a pass of any kind is already waiting to run."""
    queued = await queue.list(job_type=FTS_REINDEX, state=JobState.QUEUED, limit=MAX_PENDING)
    return bool(queued.jobs)


def _is_whole_library(payload: dict[str, Any]) -> bool:
    """Whether a queued job is the pass that re-reads every asset."""
    return payload.get("asset_id") is None and payload.get(SCOPE) != CATCH_UP


async def rebuild_pending(queue: JobQueue) -> bool:
    """Whether a whole-library pass is coming: a catch-up never sees an edit."""
    queued = await queue.list(job_type=FTS_REINDEX, state=JobState.QUEUED, limit=MAX_PENDING)
    return any(_is_whole_library(job.payload) for job in queued.jobs)


MAX_PENDING = 50


async def ensure_scheduled(*, queue: JobQueue, database: Database) -> None:
    """Index what changed while Sift was not running. Called at boot."""
    if await _pending(queue):
        return
    if await anything_unindexed(database):
        await queue.enqueue(FTS_REINDEX)


async def catch_up_if_behind(*, queue: JobQueue, database: Database) -> bool:
    """Queue a catch-up if anything is unindexed and none is coming. Called from a search."""
    if not await anything_unindexed(database):
        return False
    if await _pending(queue):
        return False
    await queue.enqueue(FTS_REINDEX, {SCOPE: CATCH_UP})
    return True


async def prune_search_events(
    context: JobContext,
    *,
    database: Database,
    preferences: SettingsSeam,
    queue: JobQueue,
) -> None:
    """Delete recorded searches past the horizon, read fresh on every run."""
    del queue
    gone = 0
    keep_days = keep_days_from(await preferences.get_app(KEEP_DAYS_KEY))
    if keep_days > 0:
        gone = await forget_events_older_than(
            database, int(time.time()) - keep_days * _SECONDS_PER_DAY
        )
        # The count only: nothing about a search reaches the log.
        log.info("search.events_pruned", removed=gone, keep_days=keep_days)
    await context.set_progress(1.0)
    await context.set_note(
        "Nothing in your search history was old enough to delete."
        if gone == 0
        else f"Deleted {gone:,} search{'' if gone == 1 else 'es'} from your search history."
    )


def register_handlers(
    *,
    database: Database,
    preferences: SettingsSeam | None = None,
    queue: JobQueue | None = None,
) -> None:
    """Claim the job types this slice owns. Called once, at boot."""

    async def handler(context: JobContext) -> None:
        await reindex(context, database=database)

    register_handler(FTS_REINDEX, handler, name="Recreating the search index")

    async def files(context: JobContext) -> None:
        await reindex_files(context, database=database)

    register_handler(FTS_REINDEX_FILES, files, name="Updating the search index")

    if preferences is None or queue is None:
        return

    async def prune(context: JobContext) -> None:
        await prune_search_events(context, database=database, preferences=preferences, queue=queue)

    register_handler(SEARCH_EVENTS_PRUNE, prune, name="Clearing old search records", unlisted=True)
