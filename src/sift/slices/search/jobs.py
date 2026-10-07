# SPDX-License-Identifier: AGPL-3.0-or-later
"""Keeping the search index in step with the library.

The indexing itself is the kernel's (see `kernel/access/search_index.py`). It lives there because
assembling an asset's searchable text means reading `assets` and `asset_locations` unscoped, and
nothing outside the kernel may do that: a query written in a feature returns rows to anybody. The
index is also a table the permission resolver joins, so its DDL and its one write path sit
together, beside the resolver.

What belongs here is the job: WHEN the index is rebuilt, and how a change becomes work. That is a
scheduling decision, and it is this feature's, because this feature is the only thing that reads
the index.

Three shapes, one handler. With an `asset_id` in the payload it reindexes that one asset; with
`asset_ids`, those assets a chunk at a time (a rename's). Without either, it rebuilds the whole thing, which is always safe: the index is a cache, so a rebuild is never a repair
with state to get wrong.

## How the index gets filled, and why nothing runs on a timer

An index that is created empty and never filled looks fine to every test that builds its own, while
free text matches an empty table and finds nothing, so the running application must fill it.

A timer is the obvious way, and it is wrong here. A job queued every few seconds puts a line on
the jobs dashboard that never means anything, and the rule everywhere else here is that a pending
job means there is really something waiting to be done. An idle Sift
should look idle.

So work is queued only when there IS some, and two things ask:

  * **Boot.** Everything that changed while Sift was not running, including a database restored
    from a backup taken before there was an index at all. Queued only if the index is actually
    behind.
  * **A search.** Asking whether anything is unindexed is one indexed lookup that stops at the
    first row, so it is affordable on a request, and a search is exactly the moment staleness
    matters. Imports become findable the next time somebody looks for anything.

The numbers behind the shape: a full rebuild is about eight seconds at fifty thousand assets, and
finding the unindexed is an index lookup thanks to the row map in the kernel's schema (a scan
would be minutes at the same size). Without that table none of this would be cheap enough to ask
on a request.

An EDIT to something already indexed (a tag added, a person renamed) is not noticed by any of
this, because the asset already has a row. The features that write indexed text say so through
the reindex seam as they write (`reindex.py`), and the rebuild at boot covers whatever changed while
Sift was not running.
"""

from __future__ import annotations

import time
from typing import Any

from sift.kernel.access import (
    anything_unindexed,
    index_assets,
    index_new_assets,
    unindexed_count,
)
from sift.kernel.changes import About, announce, who_may_see_a_file
from sift.kernel.db import Database
from sift.kernel.jobs import JobContext, JobQueue, JobState, register_handler
from sift.kernel.log import get_logger
from sift.kernel.seams import SettingsSeam
from sift.kernel.version import app_version

log = get_logger(__name__)

#: The job type. One name, used at registration and by whatever enqueues the work.
FTS_REINDEX = "fts_reindex"

#: The sweep that keeps the record of searches from being permanent.
SEARCH_EVENTS_PRUNE = "search_events_prune"

#: How long a record of a search is kept, in days, and the setting that decides it.
#:
#: FOR EVER by default (zero), as sittings are: the questions the record is for are about years
#: ("more than last year", the first thing you ever looked for, a search you came back to over three
#: years), and any horizon loses the oldest year the day it passes. One short row per search is
#: something the table can afford for decades. Each User clears their own whenever they like (the
#: search box's Clear, and `Settings > Privacy > Your history`). A year's horizon would answer this
#: year and forget last year's, which is the comparison a yearly recap is for.
#:
#: It is a setting rather than a constant because a self-hoster who wants a month should not have
#: to edit Python for it.
KEEP_DAYS_KEY = "search.keep_records_days"
DEFAULT_KEEP_DAYS = 0

#: How often the sweep runs, once it is on. Daily, like the quarantine sweep beside it on the same
#: screen: the horizon is measured in days, so looking more often than once a day cannot find
#: anything the last look did not.
PRUNE_EVERY_SECONDS = 24 * 60 * 60

_SECONDS_PER_DAY = 24 * 60 * 60

#: Everything past the horizon.
#:
#: Here rather than beside the statements that WRITE the table, which is where it belongs by
#: subject: the service imports this module, so an import the other way would close the circle,
#: and the sweep is the only thing that ever reads it.
_FORGET_OLD_EVENTS = "DELETE FROM search_events WHERE at < ?"


async def forget_events_older_than(database: Database, before: int) -> int:
    """Drop every recorded search older than this moment. Returns how many went.

    Not scoped to a user, and it is the only write in this slice that is not. That is what a
    horizon IS: it applies to everybody's records equally and nobody can ask for a different one,
    because the reason for having one is that a permanent complete record of what everybody
    searched for is the query log this slice does not keep.
    """
    async with database.write() as connection:
        cursor = await connection.execute(_FORGET_OLD_EVENTS, (before,))
        return int(cursor.rowcount)


def keep_days_from(value: object) -> int:
    """The retention rule as a whole number of days, however it is stored. Zero is OFF.

    Zero means the sweep does not run and nothing is deleted: exactly what zero means for the
    quarantine sweep, and one meaning for one number on one screen is worth more than a meaning
    chosen fresh here. Somebody who wants to keep nothing empties their history, which is a button
    on the search box and is immediate.
    """
    try:
        return max(0, int(str(value)))
    except (TypeError, ValueError):
        return DEFAULT_KEEP_DAYS


#: The files one queued job names, and how many of them one write transaction rewrites: a chunk
#: holds the write lock for a fraction of a second, so no other write waits on a rename's files.
#: Its own job type, so Activity names it for what it does rather than as a rebuild.
FTS_REINDEX_FILES = "fts_reindex_files"
ASSET_IDS = "asset_ids"
IDS_PER_JOB = 2000
IDS_PER_WRITE = 100


async def reindex_files(context: JobContext, *, database: Database) -> None:
    """Rewrite these assets' rows a chunk at a time, then tell whoever may be searching them."""
    asset_ids = context.payload.get(ASSET_IDS)
    if not isinstance(asset_ids, list) or not all(isinstance(one, str) for one in asset_ids):
        raise ValueError("asset_ids must be a list of asset ids")
    await context.set_note(_files(len(asset_ids), "For"))
    written = 0
    for at in range(0, len(asset_ids), IDS_PER_WRITE):
        await context.raise_if_canceled()
        written += await index_assets(database, asset_ids=asset_ids[at : at + IDS_PER_WRITE])
        await context.set_progress(min(1.0, (at + IDS_PER_WRITE) / len(asset_ids)))
    # A search on screen reads again now its words have moved; word only, each re-reads its own.
    async with database.write() as connection:
        announce(await who_may_see_a_file(connection), About.LIBRARY)
    await context.set_note(_files(written, "Indexed"))
    log.info("search.reindexed", assets=written, whole_library=False)


def _files(count: int, verb: str) -> str:
    return f"{verb} {count:,} {'file' if count == 1 else 'files'}"


#: Which kind of pass a queued job is. The payload carries it so one handler serves both and there
#: is one place that knows how a row is written.
SCOPE = "scope"
CATCH_UP = "catch_up"


async def reindex(context: JobContext, *, database: Database) -> None:
    """The job, in its three shapes.

    An `asset_id` reindexes that one asset and schedules nothing: it is a one-off, for a caller
    that knows what changed. A `catch_up` scope indexes whatever has no row yet and queues the next
    catch-up. Neither of those, and it is a full rebuild, which queues the next of both.

    Nothing about what was indexed is logged beyond a count. The text going into this index is
    filenames and the names of People, and a job log is not the place for either.
    """
    asset_id = context.payload.get("asset_id")
    if asset_id is not None and not isinstance(asset_id, str):
        raise ValueError("asset_id must be the id of an asset")

    await context.raise_if_canceled()

    if asset_id is not None:
        written = await index_assets(database, asset_id=asset_id)
        log.info("search.reindexed", assets=written, whole_library=False)
        return

    catching_up = context.payload.get(SCOPE) == CATCH_UP

    # WHAT IT IS DOING AND WHY, BEFORE IT STARTS, because nobody asked for this pass.
    #
    # It is queued at boot when the index is behind, which after an upgrade that changes the
    # index's shape means every file in the library. Without this a person looking at a busy
    # machine would see a row reading "Rebuilding search index" and have no way at all to tell
    # whether that meant a hundred files or a hundred thousand, or why it had started on its own.
    #
    # Counted ONCE, here, before any row is written (a denominator taken after the pass has
    # begun is not one), and said in the note the queue already carries onto the screen.
    waiting = await unindexed_count(database)
    await context.set_note(_what_it_is_doing(waiting, catching_up=catching_up))

    if catching_up:
        written = await index_new_assets(database)
    else:
        written = await index_assets(database, rebuild=True)
    await context.set_progress(1.0)
    # The count it really wrote, which is not always the count it expected: a rebuild writes every
    # asset rather than only the unindexed ones, and files can arrive while it runs.
    await context.set_note(_files(written, "Indexed"))
    log.info("search.reindexed", assets=written, whole_library=not catching_up)


def _what_it_is_doing(waiting: int, *, catching_up: bool) -> str:
    """The sentence on the row while the pass runs.

    The version is named because that is the answer to "why is this happening": this pass starts by
    itself at the first boot after an upgrade whose migration emptied the index, and a number
    somebody can compare with the one on the About screen is what makes an unasked-for pass read as
    an upgrade finishing rather than as something going wrong. An install running from a checkout
    has no version at all, and says the rest of the sentence without inventing one.
    """
    files = f"{waiting:,} {'file' if waiting == 1 else 'files'}"
    scope = "Indexing what has arrived" if catching_up else "Rebuilding the search index"
    version = app_version()
    if version:
        return f"Catching up after the upgrade to {version} \u2014 {scope}, {files}"
    return f"{scope} \u2014 {files}"


async def _pending(queue: JobQueue) -> bool:
    """Whether a pass of ANY kind is already waiting to run.

    Checked before queueing anything, so a busy Sift ends up with one pending reindex rather than
    one per search. The bound is there so a queue somebody has filled by hand cannot make this
    read the whole table.

    Right for deciding whether to add ANOTHER catch-up, and wrong for deciding whether an EDIT is
    covered: the two questions differ, which is what `rebuild_pending` exists for. See there.
    """
    queued = await queue.list(job_type=FTS_REINDEX, state=JobState.QUEUED, limit=MAX_PENDING)
    return bool(queued.jobs)


def _is_whole_library(payload: dict[str, Any]) -> bool:
    """Whether a queued job is the pass that re-reads EVERY asset.

    One job type carries three different jobs, and only this one sees an edit. A single-asset pass
    rewrites the asset it names and no other. A catch-up writes rows for assets that have none,
    and is deliberately blind to edits: an asset whose tag changed already has a row, so it is
    never revisited.
    """
    return payload.get("asset_id") is None and payload.get(SCOPE) != CATCH_UP


async def rebuild_pending(queue: JobQueue) -> bool:
    """Whether a WHOLE-LIBRARY pass is already coming: the only kind that carries an edit.

    This is separate from `_pending` because conflating them would lose an edit, invisibly. A
    catch-up is queued by **every search** that finds the index behind, so on any busy library
    there is usually one waiting. Standing down for it would mean a rename queued nothing, the
    catch-up then wrote only the newly-arrived assets, and the renamed tag stayed unfindable until
    the next boot: a stale index, caused by a guard meant to prevent duplicate work.
    """
    queued = await queue.list(job_type=FTS_REINDEX, state=JobState.QUEUED, limit=MAX_PENDING)
    return any(_is_whole_library(job.payload) for job in queued.jobs)


#: How many queued passes to look at when deciding whether one is already coming. At most one is
#: ever queued by this module, so the bound is a guard rather than a real limit.
MAX_PENDING = 50


async def ensure_scheduled(*, queue: JobQueue, database: Database) -> None:
    """Index what changed while Sift was not running. Called at boot.

    Queued only if the index is actually behind, so starting an already-indexed library adds
    nothing to the dashboard. A rebuild rather than a catch-up, because what happened while Sift
    was down includes edits, and only a rebuild sees those.
    """
    if await _pending(queue):
        return
    if await anything_unindexed(database):
        await queue.enqueue(FTS_REINDEX)


async def catch_up_if_behind(*, queue: JobQueue, database: Database) -> bool:
    """Queue a catch-up if anything is unindexed and none is already coming. Called from a search.

    A search is the moment staleness matters, and the question is one indexed lookup that stops at
    the first row, so asking it here is what lets the refresh be quiet the rest of the time.
    Returns whether one was queued, which is what the test asserts on.
    """
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
    """Delete recorded searches past the horizon.

    The rule is read fresh on every run rather than held, so shortening it takes effect on the very
    next sweep instead of the one after a restart: the same shape the quarantine sweep has, and
    for the same reason.

    Its NEXT run is placed by the one scheduler (`kernel.jobs.clock`) when this run settles, as
    every timed task's is (see the quarantine sweep for why it does not queue itself).
    """
    del queue
    gone = 0
    keep_days = keep_days_from(await preferences.get_app(KEEP_DAYS_KEY))
    if keep_days > 0:
        gone = await forget_events_older_than(
            database, int(time.time()) - keep_days * _SECONDS_PER_DAY
        )
        # The COUNT and never a word of what was in them. Nothing about a search reaches the log
        # (see this module's slice docstring, which is the rule this line is the obvious place to
        # break).
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
    """Claim the job types this slice owns. Called once, at boot.

    `preferences` and `queue` are optional so that a caller with neither (a test, or a build
    wiring only the index) still gets the reindex handler. The sweep needs both: one to read the
    horizon, one to queue its own next run.
    """

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

    # Upkeep nobody watches: its row on Tasks and its line in History say what it did, and
    # Activity's list of what is happening now leaves it off.
    register_handler(SEARCH_EVENTS_PRUNE, prune, name="Clearing old search records", unlisted=True)
