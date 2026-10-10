# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Jobs dashboard's endpoints: admin-only, enforced here, since the queue is a picture of
somebody's library. Live because the shared live connection says when the queue moved."""

from __future__ import annotations

from collections.abc import Awaitable, Collection, Mapping, Sequence
from typing import Annotated, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import Field

from sift.kernel import attention, device_load, wiring
from sift.kernel.access import Repository, Viewer
from sift.kernel.attention import stepping_back, turbo_mode
from sift.kernel.content import ContentStore
from sift.kernel.content.library import LibraryStore, names_for_assets, names_for_roots
from sift.kernel.db import Database
from sift.kernel.jobs import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    Job,
    JobQueue,
    JobState,
    WorkAhead,
    WorkerPool,
    WorkKind,
    WorkSummary,
    by_itself_job_types,
    registered_families,
    registered_job_names,
    registered_product_carriers,
    unlisted_job_types,
    waits_for_password,
)
from sift.kernel.jobs.failure_words import in_one_line, in_plain_words, kind_of
from sift.kernel.jobs.families import BACKGROUND, LONG_PASSES, Family
from sift.kernel.jobs.ledger import Ledger
from sift.kernel.jobs.queue_enqueue import PRESS
from sift.kernel.jobs.queue_rows import FilesToRead
from sift.kernel.jobs.schedules import get_schedule
from sift.kernel.jobs.switchboard import one_reading
from sift.kernel.jobs.work_ahead import Ahead
from sift.kernel.log import get_logger, timing_hook
from sift.kernel.sampling import PREVIEW_SHAPE_SETTING, preview_shape
from sift.kernel.seams import SettingsSeam
from sift.kernel.wire import Wire
from sift.kernel.wiring import ACCESS, DATABASE, LEDGER, LIBRARY, part_or_none
from sift.slices.auth import csrf_protect, require_admin
from sift.slices.media_jobs.activity_families import (
    _AFTER_THE_READ,
    _UNFINISHED_STATES,
    FAMILY_TASKS,
    _families,
    _held_for_quiet_hours,
    _joined,
    _paced_by,
    _pool_bound,
)
from sift.slices.media_jobs.activity_families import (
    PAUSED_FOR_THE_BENCHMARK as PAUSED_FOR_THE_BENCHMARK,
)
from sift.slices.media_jobs.activity_families import _housekeeping as _housekeeping
from sift.slices.media_jobs.activity_families import _with_unread as _with_unread
from sift.slices.media_jobs.activity_wire import Chore as Chore
from sift.slices.media_jobs.activity_wire import FailureLine as FailureLine
from sift.slices.media_jobs.activity_wire import FamilyOfWork as FamilyOfWork
from sift.slices.media_jobs.activity_wire import JobsPage as JobsPage
from sift.slices.media_jobs.activity_wire import JobView as JobView
from sift.slices.media_jobs.activity_wire import KindOfWork as KindOfWork
from sift.slices.media_jobs.activity_wire import PartOfWork as PartOfWork
from sift.slices.media_jobs.activity_wire import StepsOfJob as StepsOfJob
from sift.slices.media_jobs.activity_wire import StepSummary as StepSummary
from sift.slices.media_jobs.activity_wire import Stopped as Stopped
from sift.slices.media_jobs.folds import fold_tops
from sift.slices.media_jobs.jobs import REBUILD_PREVIEWS, REBUILD_THUMBNAILS, preview_recipe
from sift.slices.media_jobs.presses import read_presses
from sift.slices.media_jobs.read_first import after_the_read_first
from sift.slices.media_jobs.router_controls import controls, held_views

log = get_logger(__name__)

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _database(request: Request) -> Database | None:
    """The database, or None in a test built without one: its rows then name no file."""
    return part_or_none(request, DATABASE)


def _access(request: Request) -> Repository | None:
    """Who may be shown what, or None in a test that builds routes without it. No file is then
    named at all: a name is only ever handed out once it is known the vault is not holding it."""
    return part_or_none(request, ACCESS)


class _Shown:
    """Which files the viewer may be shown by name: seen by them, and not held in their vault."""

    def __init__(self, access: Repository | None, viewer: Viewer) -> None:
        self._access = access
        self._viewer = viewer

    async def of(self, asset_ids: Sequence[str]) -> set[str]:
        if self._access is None or not asset_ids:
            return set()
        standing = await self._access.standing_of(self._viewer, asset_ids)
        return {asset_id for asset_id, concealed in standing.items() if not concealed}


def _library(request: Request) -> LibraryStore | None:
    """The library folders, or None in a test that builds routes without them: no share is named."""
    return part_or_none(request, LIBRARY)


def _ledger(request: Request) -> Ledger | None:
    """The ledger, or None in a test that builds routes without one: the families are still
    drawn, with no estimate beside them."""
    return part_or_none(request, LEDGER)


def _pool(request: Request) -> WorkerPool | None:
    """The worker pool, or None in a test without one: what a family can occupy, which the estimate
    divides by."""
    return part_or_none(request, wiring.POOL)


#: What a row of a type this version has no handler for is called: work an older release did.
OLDER_TASK = "Older task"


def _named(job_type: str) -> str:
    """What a row is called: its handler's declared name, or `OLDER_TASK` where none claims it."""
    return registered_job_names().get(job_type, OLDER_TASK)


def _view(
    job: Job,
    subject: str | None,
    subject_id: str | None = None,
    position: int | None = None,
    steps: StepSummary | None = None,
) -> JobView:
    return JobView(
        id=job.id,
        parent_id=job.parent_id,
        type=job.type,
        # A press over some files is named in the press's own words (`enqueue_many`'s title).
        name=str(job.payload.get("title") or _named(job.type)),
        subject=subject,
        subject_id=subject_id,
        state=job.state,
        progress=job.progress,
        attempts=job.attempts,
        max_attempts=job.max_attempts,
        # Already scrubbed on the way in: the queue stores what the redactor let through.
        error=job.error,
        # Scrubbed on the way in, like the error beside it.
        note=job.note,
        run_after=job.run_after,
        position=position,
        created_at=job.created_at,
        updated_at=job.updated_at,
        steps=steps,
        waits_for_password=job.state is JobState.BLOCKED and waits_for_password(job.error),
        reason=in_one_line(job.error) if job.state is JobState.FAILED and job.error else None,
    )


#: The payload keys naming what a job is about, in order; with none it is whole-library work.
_ASSET_KEY = "asset_id"
_ROOT_KEY = "root_id"


def _subject_assets(jobs: Sequence[Job]) -> dict[str, str]:
    """The file each job is about, keyed by job id. Only jobs about one file are in it."""
    return {
        job.id: (job.payload or {})[_ASSET_KEY]
        for job in jobs
        if isinstance((job.payload or {}).get(_ASSET_KEY), str) and job.type != PRESS
    }


async def _subjects(
    database: Database | None, jobs: Sequence[Job], shown: _Shown
) -> dict[str, str]:
    """The name of the thing each job is about, by job id, in two lookups for the page; a file gone
    since is left out."""
    if database is None:
        return {}

    assets: dict[str, list[str]] = {}
    roots: dict[str, list[str]] = {}
    for job in jobs:
        payload = job.payload or {}
        if isinstance(payload.get(_ASSET_KEY), str):
            assets.setdefault(payload[_ASSET_KEY], []).append(job.id)
        elif isinstance(payload.get(_ROOT_KEY), str):
            roots.setdefault(payload[_ROOT_KEY], []).append(job.id)

    visible = await shown.of(sorted(assets))
    assets = {asset_id: jobs_of for asset_id, jobs_of in assets.items() if asset_id in visible}
    named: dict[str, str] = {}
    for ids, lookup in ((assets, names_for_assets), (roots, names_for_roots)):
        if not ids:
            continue
        for identifier, name in (await lookup(database, list(ids))).items():
            for job_id in ids[identifier]:
                named[job_id] = name
    return named


async def _families_and_holds(
    queue: JobQueue,
    work: dict[str, KindOfWork],
    ledger: Ledger | None,
    pool: WorkerPool | None,
    listed: dict[str, dict[str, int]],
    counted: Ahead,
    library: LibraryStore | None = None,
    unread: FilesToRead | None = None,
) -> tuple[dict[str, FamilyOfWork], Mapping[str, int], dict[str, int]]:
    """The long passes' rows, with what quiet hours hold and what waits for the password."""
    held = await _held_for_quiet_hours(queue.switchboard, queue)
    sealed = await queue.waiting_for_password()
    presses, live = await read_presses(
        queue, {kind for kind, one in work.items() if one.waiting is not None}
    )
    families = await _families(
        work,
        ledger,
        queue.switchboard,
        pool,
        queue,
        listed,
        held=held,
        kinds=counted.by_kind,
        standing=counted.standing,
        arriving=counted.arriving,
        presses=presses,
        live=live,
        unread=unread,
        pace=await _paced_by(library),
        pool_bound=_pool_bound(),
        benchmark=await queue.held_by_exclusive(),
    )
    families = await after_the_read_first(families, library, _AFTER_THE_READ, _joined)
    roots = None if library is None else {root.id for root in await library.roots()}
    for key, (failed, why) in (await _failed_runs(queue, roots)).items():
        families[key] = families[key].model_copy(update={"failed": failed, "last_error": why})
    return families, held, sealed


def _run_types() -> dict[Family, list[str]]:
    """A pass's run types: its own less each file's work and the carriers, and its task's."""
    left_out = by_itself_job_types() | registered_product_carriers()
    runs: dict[Family, list[str]] = {family: [] for family in LONG_PASSES}
    for job_type, family in registered_families().items():
        if family in runs and job_type not in left_out:
            runs[family].append(job_type)
    for family, task_id in FAMILY_TASKS.items():
        task = get_schedule(task_id)
        if task is not None and task.job_type is not None:
            runs[family].append(task.job_type)
    return runs


def _over(job: Job) -> tuple[str, object]:
    return job.type, (job.payload or {}).get(_ROOT_KEY)


async def _failed_runs(
    queue: JobQueue, roots: Collection[str] | None = None
) -> dict[str, tuple[int, str]]:
    """Each pass's failed runs a person can still act on, with the newest one's reason in plain
    words: none over a folder gone from `roots`, none a later walk of its folder made good, and no
    restart a later scan of any kind made good."""
    of = {job_type: family for family, types in _run_types().items() for job_type in types}
    failed = (await queue.list(state=JobState.FAILED, among=sorted(of), limit=MAX_PAGE_SIZE)).jobs
    if not failed:
        return {}
    done = (await queue.list(state=JobState.DONE, among=sorted({one.type for one in failed}))).jobs
    ran = {_over(one): one.updated_at for one in reversed(done)}
    walked = {_over(one): one.updated_at for one in reversed(done) if "paths" not in one.payload}
    answer: dict[str, tuple[int, str]] = {}
    for one in failed:
        root = one.payload.get(_ROOT_KEY)
        restarted = getattr(kind_of(one.error or ""), "name", None) == "restarted"
        if (roots is not None and root is not None and root not in roots) or (
            (ran if restarted else walked).get(_over(one), 0) > one.updated_at
        ):
            continue
        family = of[one.type].value
        count, why = answer.get(family, (0, in_plain_words(one.error or "")))
        answer[family] = (count + 1, why)
    return answer


async def _about_the_rows(
    queue: JobQueue,
    database: Database | None,
    jobs: Sequence[Job],
    shown: _Shown,
    *,
    fold: bool,
) -> tuple[dict[str, str], dict[str, str], dict[str, StepSummary]]:
    """What the page's rows are about: the names, the files to link to, and each family's fold."""
    subjects = await _subjects(database, jobs, shown)
    # Only for a subject that resolved: a link to a file since gone is worse than a plain name.
    assets = {
        job_id: asset_id for job_id, asset_id in _subject_assets(jobs).items() if job_id in subjects
    }
    folded = (
        await fold_tops(queue, database, jobs, subjects, assets, shown.of, _named) if fold else {}
    )
    return subjects, assets, folded


async def _work_of(
    summary: WorkSummary, work_ahead: WorkAhead, upkeep: frozenset[str]
) -> tuple[dict[str, KindOfWork], Ahead]:
    """Each kind of work's run, from the queue's tally and one count of what the library has left."""
    finished = {
        kind: sum(n for state, n in by_state.items() if state not in _UNFINISHED_STATES)
        for kind, by_state in summary.states.items()
    }
    work_ahead.observe((kind for kind, one in summary.run.items() if one.outstanding > 0), finished)
    counted = await work_ahead.counted()
    ahead, wanted = counted.waiting, counted.wanted
    # Every kind either half knows about: a counter with no jobs yet, or jobs with no counter.
    work: dict[str, KindOfWork] = {}
    for kind in (set(summary.run) | set(ahead)) - upkeep:
        one = summary.run.get(kind, WorkKind())
        left = ahead.get(kind)
        if left is None:
            # Nothing in the library counts a walk's files: the queue is all there is.
            work[kind] = KindOfWork(
                done=one.done,
                outstanding=one.outstanding,
                failed=one.failed,
            )
            continue
        # Done is derived from what is left (`run_of`): the queue's window of finished jobs moves.
        run = work_ahead.run_of(
            kind,
            left=left,
            done_already=one.done,
            busy=one.outstanding > 0,
            wanted=wanted.get(kind),
        )
        work[kind] = KindOfWork(
            done=run.done,
            outstanding=one.outstanding,
            failed=one.failed,
            waiting=run.left,
            total=run.total if kind in wanted else None,
        )
    return work, counted


async def _row_counts(
    queue: JobQueue, listed: dict[str, dict[str, int]], quiet: list[str]
) -> dict[str, int]:
    """Each state's number of rows, as the list draws them."""
    counts: dict[str, int] = {}
    for states in listed.values():
        # Not `state`: the page's own filter, which these tallies must not depend on.
        for named, how_many in states.items():
            counts[named] = counts.get(named, 0) + how_many
    # Less the quiet rows the list leaves out, so each state's number counts the rows its list
    # draws. Only on the unnarrowed view: a chosen kind reads its own tally (`by_type`).
    for named, how_many in (await queue.quiet_by_state(quiet)).items():
        counts[named] = counts.get(named, 0) - how_many
    return counts


def _views(
    jobs: Sequence[Job],
    subjects: Mapping[str, str],
    assets: Mapping[str, str],
    places: Mapping[str, int],
    folded: Mapping[str, StepSummary],
) -> list[JobView]:
    """The page's rows as the screen draws them."""
    return [
        _view(job, subjects.get(job.id), assets.get(job.id), places.get(job.id), folded.get(job.id))
        for job in jobs
    ]


async def _timed(name: str, read: Awaitable[_T]) -> _T:
    """One part of the Tasks page's read, timed: a slow one is said at Normal detail."""
    with timing_hook(f"jobs.page.{name}", level="debug", slow_ms=PAGE_PART_SLOW_MS):
        return await read


#: A part of the page's read slower than this is said at Normal detail.
PAGE_PART_SLOW_MS = 250.0
_T = TypeVar("_T")


async def _page(
    queue: JobQueue,
    work_ahead: WorkAhead,
    database: Database | None,
    *,
    ledger: Ledger | None = None,
    pool: WorkerPool | None = None,
    state: JobState | None,
    job_type: str | None,
    parent_id: str | None,
    limit: int,
    offset: int,
    fold: bool = False,
    older: bool = False,
    shown: _Shown,
    library: LibraryStore | None = None,
) -> JobsPage:
    # Upkeep is never listed unless named; work Sift started by itself only where it failed.
    upkeep = unlisted_job_types()
    unnamed = job_type is None and parent_id is None
    quiet = sorted((by_itself_job_types() | BACKGROUND) - upkeep) if unnamed else []
    # The whole queue's shape, read first: "Older tasks" are its kinds no handler claims (a
    # press's head is no kind of work).
    summary = await _timed("summary", queue.work_summary())
    claimed = registered_job_names()
    gone = sorted(kind for kind in summary.states if kind not in {*claimed, *upkeep, PRESS})
    # On a page of families a state is the one a family's row shows (`folded`), never a row's own.
    listing = queue.list(
        state=None if fold else state,
        folded=state if fold else None,
        job_type=job_type,
        parent_id=parent_id,
        tops_only=fold,
        leaving_out=sorted(upkeep) if unnamed else (),
        quiet=quiet,
        among=gone if older else None,
        limit=limit,
        offset=offset,
    )
    page = await _timed("list", listing)
    about = _about_the_rows(queue, database, page.jobs, shown, fold=fold)
    subjects, assets, folded = await _timed("rows", about)
    # What is still to come, so a bar's total does not climb as a pass queues a page at a time.
    work, counted = await _timed("work", _work_of(summary, work_ahead, upkeep))
    listed = {kind: by_state for kind, by_state in summary.states.items() if kind not in upkeep}
    counts = await _timed("counts", _row_counts(queue, listed, quiet))
    tallies = _tallies(
        families=page.by_state if fold else None,
        rows=counts,
        by_type=summary.states,
        job_type=job_type,
        older=gone if older else None,
        parent_id=parent_id,
    )
    in_line = [job.id for job in page.jobs if job.state is JobState.QUEUED]
    places = await _timed("places", queue.positions_of(in_line))
    unread = await _timed("unread", work_ahead.unread_now(counted))
    families, held, sealed = await _timed(
        "families", _families_and_holds(queue, work, ledger, pool, listed, counted, library, unread)
    )
    chores = await _timed("housekeeping", _housekeeping(queue, summary, ledger, pool, held, sealed))
    return JobsPage(
        jobs=held_views(_views(page.jobs, subjects, assets, places, folded), page.jobs, pool),
        total=page.total,
        counts=counts,
        tallies=tallies,
        by_type=listed,
        names={kind: claimed[kind] for kind in listed if kind in claimed},
        older=[kind for kind in gone if kind in listed],
        work=work,
        families=families,
        housekeeping=chores,
        stepping_back=stepping_back(),
        turbo_mode=turbo_mode(),
        step_back_share=attention.ATTENTION.share,
        step_back_for=attention.ATTENTION.cause,
        step_back_over=device_load.READER.over if attention.ATTENTION.cause == "others" else [],
        password_wanted=sum(sealed.values()),
        paused=pool is not None and pool.holding.held_all,
    )


def _tallies(
    *,
    families: dict[str, int] | None,
    rows: dict[str, int],
    by_type: dict[str, dict[str, int]],
    job_type: str | None,
    older: list[str] | None,
    parent_id: str | None,
) -> dict[str, int]:
    """The numbers above the list, in the universe the list draws (families or rows), `all` their sum,
    never narrowed by the state looked at."""
    if parent_id is not None:
        return {}
    if families is not None:
        tally = dict(families)
    elif job_type is not None:
        tally = dict(by_type.get(job_type, {}))
    elif older is not None:
        tally = {}
        for kind in older:
            for named, how_many in by_type.get(kind, {}).items():
                tally[named] = tally.get(named, 0) + how_many
    else:
        tally = dict(rows)
    shown = {named: how_many for named, how_many in tally.items() if how_many > 0}
    return {**shown, "all": sum(shown.values())}


@router.get("")
async def list_jobs(
    queue: Annotated[JobQueue, Depends(wiring.queue)],
    database: Annotated[Database | None, Depends(_database)],
    work_ahead: Annotated[WorkAhead, Depends(wiring.work_ahead)],
    ledger: Annotated[Ledger | None, Depends(_ledger)],
    pool: Annotated[WorkerPool | None, Depends(_pool)],
    access: Annotated[Repository | None, Depends(_access)],
    library: Annotated[LibraryStore | None, Depends(_library)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    state: JobState | None = None,
    type: str | None = None,
    parent_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
    fold: Annotated[
        bool,
        Query(
            description="Page by FAMILY: only the rows that head one (a download, a scan, anything "
            "with no parent), each carrying `steps`, its family folded. `total` is then families. "
            "Beside `state`, the families whose folded row SHOWS that state (failed if anything in "
            "the family failed): each family is in exactly one, so the tabs add up to All. "
            "Refused beside `parent_id`, whose rows are one family's steps."
        ),
    ] = False,
    older: Annotated[
        bool,
        Query(
            description="Only the rows of the types this version of Sift has no handler for "
            '(`older` on the page): the Type choice\'s one entry for them, "Older tasks".'
        ),
    ] = False,
) -> JobsPage:
    """A page of the queue, newest first: of every row, or with `fold`, of every family.

    The bounds are declared, so the schema says them. A folded page filtered by state reads the
    state its family's row shows (`folded_state`), so every family is under one state (`_tallies`).
    """
    if fold and parent_id is not None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "fold pages families and cannot be filtered by parent_id",
        )
    async with one_reading():
        return await _page(
            queue,
            work_ahead,
            database,
            ledger=ledger,
            pool=pool,
            state=state,
            job_type=type,
            parent_id=parent_id,
            limit=limit,
            offset=offset,
            fold=fold,
            older=older,
            shown=_Shown(access, viewer),
            library=library,
        )


@router.get("/{job_id}/steps")
async def list_steps(
    job_id: str,
    queue: Annotated[JobQueue, Depends(wiring.queue)],
    database: Annotated[Database | None, Depends(_database)],
    access: Annotated[Repository | None, Depends(_access)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> StepsOfJob:
    """The steps folded under one top row, however deep, in the order they were handed out.

    404 for a job that is not there and for one that heads no family: neither has steps.
    """
    top = await queue.get(job_id)
    if top is None or top.parent_id is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such task heads a family")
    page = await queue.steps(job_id, limit=limit, offset=offset)
    subjects = await _subjects(database, page.jobs, _Shown(access, viewer))
    assets = {
        one: asset_id for one, asset_id in _subject_assets(page.jobs).items() if one in subjects
    }
    places = await queue.positions_of([job.id for job in page.jobs if job.state is JobState.QUEUED])
    return StepsOfJob(
        jobs=[
            _view(job, subjects.get(job.id), assets.get(job.id), places.get(job.id))
            for job in page.jobs
        ],
        total=page.total,
        at_least=page.at_least,
    )


@router.post(
    "/{job_id}/retry", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(csrf_protect)]
)
async def retry_job(
    job_id: str,
    queue: Annotated[JobQueue, Depends(wiring.queue)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> None:
    """Put a failed job back in the queue, its attempts reset; 404 for one that is not there or
    cannot be retried."""
    if not await queue.retry(job_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no job to retry")


class Retried(Wire):
    """How many jobs went back in the queue."""

    retried: int


@router.post("/retry-failed", dependencies=[Depends(csrf_protect)])
async def retry_failed_jobs(
    queue: Annotated[JobQueue, Depends(wiring.queue)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> Retried:
    """Put everything that failed back in the queue; canceled work is left alone.

    Nothing to retry is a success with a zero, not a 404.
    """
    return Retried(retried=await queue.retry_failed())


@router.post("/retry-canceled", dependencies=[Depends(csrf_protect)])
async def retry_canceled_jobs(
    queue: Annotated[JobQueue, Depends(wiring.queue)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> Retried:
    """Start everything that was stopped, again: the other half of `cancel-all`.

    Failures are left alone; `retry-failed` is for them. Nothing to start is a success with a zero.
    """
    return Retried(retried=await queue.retry_canceled())


class Cleared(Wire):
    """How many failures were thrown away."""

    cleared: int


@router.post("/clear-failed", dependencies=[Depends(csrf_protect)])
async def clear_failed_jobs(
    queue: Annotated[JobQueue, Depends(wiring.queue)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> Cleared:
    """Throw away everything that failed, for the failures no retry can fix.

    Canceled and finished work is left alone. Nothing to clear is a success with a zero.
    """
    return Cleared(cleared=await queue.clear_failed())


@router.post("/clear-canceled", dependencies=[Depends(csrf_protect)])
async def clear_canceled_jobs(
    queue: Annotated[JobQueue, Depends(wiring.queue)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> Cleared:
    """Throw away everything that was stopped, the pile a stopped import leaves behind.

    Finished work is left alone. Nothing to clear is a success with a zero.
    """
    return Cleared(cleared=await queue.clear_canceled())


@router.post("/cancel-all", dependencies=[Depends(csrf_protect)])
async def cancel_everything(
    queue: Annotated[JobQueue, Depends(wiring.queue)],
    ledger: Annotated[Ledger, Depends(wiring.ledger)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> Stopped:
    """Stop every job that has not finished, running work included: what runs keeps queueing
    more (a scan hands out a read per file).

    Nothing is deleted, and the next scan of a folder picks up what was never read. Nothing to stop
    is a success with a zero.
    """
    # Told first, so the runs this press ends are recorded as stopped by hand.
    ledger.stopped_by_hand()
    return Stopped(stopped=await queue.cancel_everything())


class TurboModeAsked(Wire):
    """Whether to turn turbo mode on: every task runs although this device is in use."""

    on: bool = Field(
        description="True runs every task although somebody is at the keyboard; false goes back "
        "to eco mode while they are."
    )


class StepBack(Wire):
    """What background work is doing about somebody using the computer, after a press."""

    stepping_back: bool = Field(description="As on the jobs page.")
    turbo_mode: bool = Field(description="As on the jobs page.")
    pressed: bool = Field(
        description="Whether turbo mode is pressed for, whether or not anybody is at the "
        "keyboard now: held until Sift stops or the next press."
    )


@router.post("/turbo-mode", dependencies=[Depends(csrf_protect)])
async def press_turbo_mode(
    body: TurboModeAsked,
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> StepBack:
    """Turn turbo mode on although this device is in use, or go back to eco mode.

    Held in memory, not stored; the press wakes the pool, which takes it within a moment.
    """
    reading = attention.ATTENTION
    reading.press(full=body.on)
    return StepBack(
        stepping_back=reading.holding, turbo_mode=reading.turbo_mode, pressed=reading.pressed
    )


class Rebuilding(Wire):
    """How many files a rebuild is about, and how many it queued."""

    total: int = Field(description="Files a rebuild would touch, whether or not one was started.")
    queued: int = Field(description="Jobs put in the queue by this request. Zero for a survey.")


@router.get("/rebuild-thumbnails", response_model=Rebuilding)
async def count_rebuildable(
    content: Annotated[ContentStore, Depends(wiring.content)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> Rebuilding:
    """How many files a rebuild would touch. Queues nothing, and must not.

    Separate from the run below for the same reason the tidy survey is separate from the tidying:
    this is minutes of the machine on a large library, and a control whose size is only visible
    after it has started is one nobody can use carefully.
    """
    return Rebuilding(total=await content.thumbnailable_count(), queued=0)


async def _current_recipe(hub: SettingsSeam) -> dict[str, int]:
    """What a hover clip built now would be built from: read per request, as it may change."""
    return preview_recipe(preview_shape(str(await hub.get_app(PREVIEW_SHAPE_SETTING))))


@router.get("/rebuild-previews", response_model=Rebuilding)
async def count_rebuildable_previews(
    content: Annotated[ContentStore, Depends(wiring.content)],
    hub: Annotated[SettingsSeam, Depends(wiring.settings_hub)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> Rebuilding:
    """How many hover clips are not the shape that is set. Queues nothing, and must not.

    Zero is the ordinary answer and is worth having: it is how the screen can say the library is
    already up to date rather than offering a button whose effect would be nothing.
    """
    total = await content.previews_of_another_recipe_count(await _current_recipe(hub))
    return Rebuilding(total=total, queued=0)


@router.post("/rebuild-previews", response_model=Rebuilding, dependencies=[Depends(csrf_protect)])
async def rebuild_previews(
    content: Annotated[ContentStore, Depends(wiring.content)],
    queue: Annotated[JobQueue, Depends(wiring.queue)],
    hub: Annotated[SettingsSeam, Depends(wiring.settings_hub)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> Rebuilding:
    """Bring every hover clip in the library up to the shape that is set.

    One sweep job that queues an encode per file, asked for once an import stops arriving.
    """
    total = await content.previews_of_another_recipe_count(await _current_recipe(hub))
    if total == 0:
        return Rebuilding(total=0, queued=0)
    await queue.enqueue_when_settled(REBUILD_PREVIEWS, requested_by=viewer.id)
    log.info("previews.rebuild_requested", total=total)
    return Rebuilding(total=total, queued=1)


@router.post("/rebuild-thumbnails", response_model=Rebuilding, dependencies=[Depends(csrf_protect)])
async def rebuild_thumbnails(
    content: Annotated[ContentStore, Depends(wiring.content)],
    queue: Annotated[JobQueue, Depends(wiring.queue)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> Rebuilding:
    """Make every picture again, for the whole library: a scan never revisits a made one.

    One sweep job that hands out the per-file rows, asked for once an import stops arriving.
    """
    total = await content.thumbnailable_count()
    if total == 0:
        return Rebuilding(total=0, queued=0)
    await queue.enqueue_when_settled(REBUILD_THUMBNAILS, requested_by=viewer.id)
    log.info("thumbnails.rebuild_requested", total=total)
    return Rebuilding(total=total, queued=1)


@router.post(
    "/{job_id}/cancel", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(csrf_protect)]
)
async def cancel_job(
    job_id: str,
    queue: Annotated[JobQueue, Depends(wiring.queue)],
    ledger: Annotated[Ledger | None, Depends(_ledger)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> None:
    """Stop a job, and everything it started.

    Cancelling a parent cancels its children: one file's work is one thing on the screen, so it is
    one thing to call off. A job already running stops at its next checkpoint: the fence on its
    claim means nothing it writes afterwards can land regardless.
    """
    # Told by the queue before the stop commits, so the runs this ends read as stopped by hand.
    canceled = await queue.cancel(
        job_id, on_canceled=None if ledger is None else ledger.stopped_by_hand
    )
    if not canceled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no job to cancel")


# What runs on a clock is `/api/tasks` (`slices/tasks/router.py`), not here.


router.include_router(controls)
