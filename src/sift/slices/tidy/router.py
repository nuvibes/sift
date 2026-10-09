# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tidy's endpoints, admin-only; reading is separate from running, since all of it is permanent."""

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
    """What has built up, the disk counts from the last survey. Removes nothing, and must not."""
    found = await survey_all(_resources(request))
    return TidyView(leftovers=[_view(one) for one in found], surveying=await _surveying(queue))


@router.post("/tidy/survey", response_model=SurveyStarted, dependencies=[Depends(csrf_protect)])
async def start_survey(
    queue: Annotated[JobQueue, Depends(wiring.queue)],
    _admin: Annotated[Viewer, Depends(require_admin)],
) -> SurveyStarted:
    """Count what the costly tidyings would remove, in the background, one survey at a time."""
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
    """Run one tidying by name: each is permanent, so one press is never several decisions."""
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
    """Refresh statistics, merge the search segments, then fold the log back in; never VACUUM."""
    database = part_of(request, DATABASE)
    was = await asyncio.to_thread(_file_size, database)
    # Not VACUUM: it locks the file for its whole run, so Sift would appear frozen.
    # Never nested: `write()` is not reentrant, and the checkpoint is refused in a transaction.
    # The log goes LAST, since the rebuilt index lands in it as a whole copy.
    await database.refresh_statistics(reason="tidy", every_table=True, force=True)
    async with database.write() as connection:
        await _optimize_search(connection)
    await database.fold_the_log_back()
    now = await asyncio.to_thread(_file_size, database)
    log.info("tidy.optimized", was_bytes=was, now_bytes=now)
    return OptimizeResult(was_bytes=was, now_bytes=now, freed_bytes=max(0, was - now))


async def _optimize_search(connection: Connection) -> None:
    """Fold the full-text index into fewer segments; an install can boot before it exists."""
    try:
        await connection.execute("INSERT INTO assets_fts(assets_fts) VALUES ('optimize')")
    except Exception as exc:  # any refusal means there is no index to fold
        log.info("tidy.search_index_not_optimized", reason=str(exc))


def _file_size(database: Database) -> int:
    """How much disk the database takes, the write-ahead log included, since it is what grows."""
    total = 0
    for suffix in ("", "-wal", "-shm"):
        try:
            total += database.path.with_name(database.path.name + suffix).stat().st_size
        except OSError:
            continue
    return total
