# SPDX-License-Identifier: AGPL-3.0-or-later
"""The two background jobs loops own: the stills sweep and the whole-file mark."""

from __future__ import annotations

from sift.kernel.jobs import JobContext, register_handler
from sift.kernel.log import get_logger
from sift.slices.loops.service import LoopService

log = get_logger(__name__)

LOOP_STILLS = "loop_stills"

#: Mark a just-produced file, whole, as a loop: queued because the clip's id is not known yet.
LOOP_WHOLE = "loop_whole"

#: A ceiling so one boot never queues thousands of jobs; what is left waits for the next sweep.
SWEEP_LIMIT = 500


async def backfill_stills(context: JobContext, *, service: LoopService) -> None:
    """Ask for a still for every marked moment that has none."""
    pending = await service.marks_without_still(SWEEP_LIMIT)
    for row in pending:
        await service.wants_still(str(row["asset_id"]), int(row["start_ms"]))
    log.info("loops.stills.swept", asked=len(pending), capped=len(pending) == SWEEP_LIMIT)


async def mark_whole(context: JobContext, *, service: LoopService) -> None:
    """Put a loop over the whole of a just-produced file, its length handed in by the cut."""
    asset_id = context.require_str("asset_id", "this job needs an asset_id")
    running = int(context.payload.get("duration_ms") or 0)
    if running <= 0:
        log.info("loops.whole.skipped", asset_id=asset_id)
        return
    made = await service.create(
        asset_id=asset_id,
        start_ms=0,
        end_ms=running,
        name=None,
        created_by=None,
        duration_ms=running,
    )
    # After the new row, never before: the mark's name and tags move onto it.
    retired = await _supersede_the_mark(context, service=service, running=running, into=made.id)
    log.info("loops.whole.marked", asset_id=asset_id, duration_ms=running, retired=retired)


async def _supersede_the_mark(
    context: JobContext, *, service: LoopService, running: int, into: str
) -> int:
    """Forget the mark this file was cut from, where the payload names one."""
    cut_from = str(context.payload.get("cut_from_asset_id") or "")
    if not cut_from:
        return 0
    started = int(context.payload.get("cut_from_start_ms") or 0)
    return await service.supersede(cut_from, started, started + running, into=into)


def register_handlers(*, service: LoopService) -> None:
    """Claim the sweep and the whole-file mark. Called once, at boot."""
    register_handler(
        LOOP_STILLS,
        lambda context: backfill_stills(context, service=service),
        name="Generating stills for saved Loops",
    )
    register_handler(
        LOOP_WHOLE,
        lambda context: mark_whole(context, service=service),
        name="Saving a clip as a loop",
    )
