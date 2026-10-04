# SPDX-License-Identifier: AGPL-3.0-or-later
"""Two endpoints: what could be tidied, and tidy one of them.

Admin-only, like the rest of Maintenance, and for the same reason: neither route names an asset the
caller chose, both describe the library as a whole, and the honest answer to a guest asking about
the library as a whole is no. Hiding the section in the client is a courtesy, never the control.

Reading is separate from running, and that separation is the feature. Everything here is
irreversible; a survey that removed what it counted would make looking dangerous.
"""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from sift.kernel import wiring
from sift.kernel.access import Viewer
from sift.kernel.db import Connection, Database
from sift.kernel.jobs import JobQueue
from sift.kernel.log import get_logger
from sift.kernel.tidy import Leftovers, Resources, build_all, remember_survey, survey_all
from sift.kernel.wiring import DATABASE, SETTINGS, SETTINGS_HUB, part_of
from sift.slices.auth import csrf_protect, require_admin
from sift.slices.tidy.jobs import TIDY_SURVEY
from sift.slices.tidy.models import (
    LeftoversView,
    OptimizeResult,
    SurveyStarted,
    TidyResult,
    TidyView,
)

log = get_logger(__name__)

router = APIRouter(tags=["tidy"])


def _resources(request: Request) -> Resources:
    database = part_of(request, DATABASE)
    settings = part_of(request, SETTINGS)
    return Resources(
        database=database, settings=settings, preferences=part_of(request, SETTINGS_HUB)
    )


def _view(leftovers: Leftovers) -> LeftoversView:
    return LeftoversView(
        name=leftovers.name,
        title=leftovers.title,
        detail=leftovers.detail,
        noun=leftovers.noun,
        nouns=leftovers.nouns,
        count=leftovers.count,
        frees_bytes=leftovers.frees_bytes,
        surveyed_at=leftovers.surveyed_at,
    )


async def _surveying(queue: JobQueue) -> bool:
    return await queue.is_live(TIDY_SURVEY, {})


@router.get("/tidy", response_model=TidyView)
async def survey(
    request: Request,
    queue: Annotated[JobQueue, Depends(wiring.queue)],
    _admin: Annotated[Viewer, Depends(require_admin)],
) -> TidyView:
    """What has built up. Removes nothing, and must not.

    Reads no directory either: the counts that read the disk are the last survey's, with when it
    was taken, and `surveying` says whether a fresh one is on its way.
    """
    found = await survey_all(_resources(request))
    return TidyView(leftovers=[_view(one) for one in found], surveying=await _surveying(queue))


@router.post("/tidy/survey", response_model=SurveyStarted, dependencies=[Depends(csrf_protect)])
async def start_survey(
    queue: Annotated[JobQueue, Depends(wiring.queue)],
    _admin: Annotated[Viewer, Depends(require_admin)],
) -> SurveyStarted:
    """Count what the costly tidyings would remove, in the background. Removes nothing.

    Its own route rather than a side of the survey read, because reading the cache directory of
    a large library is seconds of the disk, and a screen that paid that on every open would take
    that long to draw. One at a time: a second press while one is going is answered rather than
    doubled.
    """
    if await _surveying(queue):
        return SurveyStarted(queued=False)
    await queue.enqueue(TIDY_SURVEY, {}, dedupe=True)
    return SurveyStarted(queued=True)


@router.post("/tidy/{name}", response_model=TidyResult)
async def run(
    name: str,
    request: Request,
    queue: Annotated[JobQueue, Depends(wiring.queue)],
    _admin: Annotated[Viewer, Depends(require_admin)],
    _csrf: Annotated[None, Depends(csrf_protect)],
) -> TidyResult:
    """Run one tidying, by name. Nothing else runs with it.

    One at a time on purpose. Each of these is permanent and each has a different consequence, so
    "tidy everything" would be a single press standing in for several different decisions.

    A costly tidying is surveyed again after it runs and the answer kept, so the count the screen
    reads back is what is left rather than what was counted before the run.
    """
    resources = _resources(request)
    chosen = next((tidying for tidying in build_all(resources) if tidying.name == name), None)
    if chosen is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "There's nothing of that name to tidy.")
    removed = await chosen.run()
    if chosen.costly:
        await remember_survey(resources, chosen)
    found = await survey_all(resources)
    return TidyResult(
        removed=removed,
        leftovers=[_view(one) for one in found],
        surveying=await _surveying(queue),
    )


# --- settling the database down ------------------------------------------------------------


@router.post("/tidy/database/optimize", response_model=OptimizeResult)
async def optimize_database(
    request: Request,
    _admin: Annotated[Viewer, Depends(require_admin)],
    _csrf: Annotated[None, Depends(csrf_protect)],
) -> OptimizeResult:
    """Settle the database down: fold the write-ahead log back in and re-plan the indexes.

    Its own route rather than another tidying, because a tidying REMOVES something and this removes
    nothing. Every row survives, every query answers the same, and the only thing that changes is
    how much disk the file takes and how well SQLite chooses between its indexes. Offering it beside
    things that delete data, under a heading about leftovers, would be inviting somebody to read it
    as one of them.

    Three steps, in this order and for three different reasons:

    * The table statistics are refreshed, which is what makes the query planner pick the index it
      should. That also happens at boot, at the end of every whole-library pass and daily
      (`Database.refresh_statistics`), but this is the one somebody presses when a screen has gone
      slow, so it runs whatever the timers have done.
    * The full-text index is rebuilt into fewer, larger segments. Search reads every segment, so an
      index written a row at a time over months is read many times over on every query. The new
      segment is written before the old ones are let go, so the file can end a little larger, with
      the old pages kept inside it for later writes.
    * The write-ahead log is folded back into the database and cut to nothing. LAST, because the
      rebuilt index arrives in the log as a copy of the whole index, and left there it reads as
      the file having grown by that much. A reader that never closes is what stops SQLite doing
      this on its own.

    Deliberately NOT `VACUUM`. It rewrites the whole file, needs as much free disk again as the
    database takes, and holds an exclusive lock for as long as it runs, which on a self-hosted box
    with a large library is a Sift that appears to have frozen. What it buys over this is the space
    inside the file being handed back to the filesystem, and that space is reused by the next writes
    regardless. Backup already takes a `VACUUM INTO` copy for anybody who wants a compacted file.
    """
    database = part_of(request, DATABASE)
    was = await asyncio.to_thread(_file_size, database)
    # Three writes one after another, never nested: `write()` is not reentrant. The checkpoint
    # cannot share the fold's guard either, as it is refused inside an open transaction.
    await database.refresh_statistics(reason="tidy", every_table=True, force=True)
    async with database.write() as connection:
        await _optimize_search(connection)
    await database.fold_the_log_back()
    now = await asyncio.to_thread(_file_size, database)
    log.info("tidy.optimized", was_bytes=was, now_bytes=now)
    return OptimizeResult(was_bytes=was, now_bytes=now, freed_bytes=max(0, was - now))


async def _optimize_search(connection: Connection) -> None:
    """Fold the full-text index into fewer segments, if there is one to fold.

    Guarded rather than assumed: the index is a component like any other and an install can boot
    before it exists. A missing table here would fail the whole optimize, which removes nothing,
    so the failure would be pure loss.
    """
    try:
        await connection.execute("INSERT INTO assets_fts(assets_fts) VALUES ('optimize')")
    except Exception as exc:  # any refusal means there is no index to fold
        log.info("tidy.search_index_not_optimized", reason=str(exc))


def _file_size(database: Database) -> int:
    """How much disk the database takes, log and all.

    All three files, because the write-ahead log is usually the part that grew and quoting the main
    file alone would report a run that reclaimed a gigabyte as having freed nothing.

    Called off the loop. Three stats are microseconds and a thread hop costs more than they do, so
    this is not an optimisation: it is that the whole run either side of it takes seconds, which
    makes the hop free in proportion, and a rule with no exception in it is worth more here than
    two hundred microseconds.
    """
    total = 0
    for suffix in ("", "-wal", "-shm"):
        try:
            total += database.path.with_name(database.path.name + suffix).stat().st_size
        except OSError:
            continue
    return total
