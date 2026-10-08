# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pause, resume and cancel for a pass, a sub-task or the whole queue, and what a pass's row says
of its sub-tasks: which are off, which are paused, and what its Run now presses."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import ConfigDict, Field

from sift.kernel import wiring
from sift.kernel.access import Viewer
from sift.kernel.jobs import (
    FamilyFailure,
    Job,
    JobQueue,
    JobState,
    StepCounts,
    WorkerPool,
    counted_as,
    registered_families,
)
from sift.kernel.jobs.families import Family, products_of
from sift.kernel.jobs.ledger import Ledger
from sift.kernel.jobs.switchboard import Switchboard
from sift.kernel.wire import Wire
from sift.kernel.wiring import part_or_none
from sift.slices.auth import csrf_protect, require_admin
from sift.slices.media_jobs.activity_wire import JobView, KindOfWork, PartOfWork, RunPress, Stopped

controls = APIRouter()

#: What a pass that somebody paused, or every pass while the whole queue is, says.
PAUSED = "Paused."

#: What Run now on each pass presses, by the sub-task it runs: a task, and its part that is this
#: pass's (None for all of it). A test holds every id to the task registry and every part to its task.
FAMILY_RUNS: dict[Family, tuple[tuple[str | None, str, str | None], ...]] = {
    Family.SCAN: ((None, "scan", None),),
    Family.GENERATE: ((None, "generate", None),),
    Family.FINGERPRINT: (
        ("fingerprint_file", "generate", "fingerprints"),
        ("audio_fingerprint", "music", None),
    ),
    Family.IDENTIFY: ((None, "identify", None),),
    Family.SEMANTIC: ((None, "smart-search", None),),
}


def runs(family: Family, off: Collection[str]) -> list[RunPress]:
    """Run now's presses for a pass, less a sub-task switched off."""
    return [
        RunPress(task=task, parts=None if part is None else [part])
        for job_type, task, part in FAMILY_RUNS.get(family, ())
        if job_type is None or job_type not in off
    ]


async def switched_off(
    board: Switchboard, types: Sequence[str], work: Mapping[str, KindOfWork]
) -> list[str]:
    """The family's sub-tasks switched off with none of their work queued or running."""
    off: list[str] = []
    for job_type in sorted(types):
        kind = work.get(job_type)
        if kind is not None and kind.outstanding > 0:
            continue
        if await board.shown_off(job_type):
            off.append(job_type)
    return off


def parts_off(off: Sequence[str], work: Mapping[str, KindOfWork]) -> list[PartOfWork]:
    """The lines of the sub-tasks switched off: drawn as off, counted in nothing."""
    lines: list[PartOfWork] = []
    for job_type in off:
        kind = work.get(job_type)
        if kind is not None and kind.total is not None:
            done = min(kind.done, kind.total)
            caption = counted_as(job_type)
            lines.append(
                PartOfWork(type=job_type, caption=caption, done=done, total=kind.total, on=False)
            )
    return lines


def paused_parts(
    pool: WorkerPool | None, types: Collection[str], parts: list[PartOfWork]
) -> tuple[bool, list[PartOfWork]]:
    """Whether the pass is paused, and its lines with each paused sub-task said so."""
    held = frozenset() if pool is None else pool.holding.held
    paused = pool is not None and (pool.holding.held_all or set(types) <= held)
    return paused, [
        one.model_copy(update={"paused": True}) if one.type in held else one for one in parts
    ]


def held_views(views: list[JobView], jobs: Sequence[Job], pool: WorkerPool | None) -> list[JobView]:
    """A waiting row of paused work says Paused: it is not in the line while the pause holds."""
    if pool is None or not (pool.holding.held_all or pool.holding.held or pool.holding.products):
        return views
    made = {job.id: (job.payload or {}).get("products") for job in jobs}
    shown: list[JobView] = []
    for one in views:
        products = made.get(one.id)
        held = (
            pool.holding.held_all
            or one.type in pool.holding.held
            or (
                isinstance(products, list)
                and bool(products)
                and set(products) <= pool.holding.products
            )
        )
        if held and one.steps is not None and one.steps.state is JobState.QUEUED:
            steps = one.steps.model_copy(update={"state": JobState.PAUSED})
            one = one.model_copy(update={"steps": steps})
        elif held and one.steps is None and one.state is JobState.QUEUED:
            one = one.model_copy(update={"state": JobState.PAUSED, "position": None})
        shown.append(one)
    return shown


async def failures_of(
    queue: JobQueue, tops: Sequence[Job], counts: Mapping[str, StepCounts]
) -> dict[str, FamilyFailure]:
    """The newest failure of each top that failed or holds a failed step."""
    failed = [
        job.id
        for job in tops
        if job.state is JobState.FAILED
        or (job.id in counts and counts[job.id].by_state.get("failed"))
    ]
    return await queue.family_failures(failed)


class WhichWork(Wire):
    """A family, one of its sub-tasks by job type, or with neither the whole queue."""

    model_config = ConfigDict(extra="forbid")

    family: Family | None = Field(default=None, description="A pass on Activity, by its key.")
    type: str | None = Field(
        default=None, max_length=64, description="One sub-task of a pass, by its job type."
    )


class Held(Wire):
    """What is paused after a press: the whole queue, and the job types paused one by one."""

    paused: bool
    types: list[str]


def _types_of(which: WhichWork) -> tuple[list[str], list[str]] | None:
    """The job types and products a press names, or None for the whole queue; a 404 for a name
    nobody runs. A pass's products reach its work run as another pass's per-file steps."""
    if which.type is not None:
        if which.type not in registered_families():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "There's no task by that name.")
        return [which.type], products_of(job_type=which.type)
    if which.family is not None:
        types = sorted(t for t, f in registered_families().items() if f is which.family)
        return types, products_of(which.family)
    return None


def _running_pool(request: Request) -> WorkerPool:
    pool: WorkerPool | None = part_or_none(request, wiring.POOL)
    if pool is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Tasks aren't running on this device.")
    return pool


@controls.post("/pause", dependencies=[Depends(csrf_protect)])
async def pause_work(
    which: WhichWork,
    pool: Annotated[WorkerPool, Depends(_running_pool)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> Held:
    """Pause a pass, a sub-task or the whole queue: nothing new of it starts, and what is running
    finishes the step in its hand. Held until Resume, or until Sift starts again."""
    named = _types_of(which)
    pool.holding.hold(*(named or (None,)))
    return Held(paused=pool.holding.held_all, types=sorted(pool.holding.held))


@controls.post("/resume", dependencies=[Depends(csrf_protect)])
async def resume_work(
    which: WhichWork,
    pool: Annotated[WorkerPool, Depends(_running_pool)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> Held:
    """Start again what a pause held, for the same pass, sub-task or whole queue."""
    named = _types_of(which)
    pool.holding.release(*(named or (None,)))
    return Held(paused=pool.holding.held_all, types=sorted(pool.holding.held))


@controls.post("/cancel-work", dependencies=[Depends(csrf_protect)])
async def cancel_work(
    which: WhichWork,
    queue: Annotated[JobQueue, Depends(wiring.queue)],
    ledger: Annotated[Ledger, Depends(wiring.ledger)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> Stopped:
    """Cancel every unfinished task of a pass or one of its sub-tasks; the whole queue is
    `cancel-all`, so a press naming neither is refused."""
    named = _types_of(which)
    if named is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Name a task or one of its parts.")
    ledger.stopped_by_hand()
    return Stopped(stopped=await queue.cancel_types(*named))
