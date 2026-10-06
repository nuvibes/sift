# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Jobs dashboard's endpoints: admin-only, enforced here, since the queue is a picture of
somebody's library. Live because the shared live connection says when the queue moved."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import Field

from sift.kernel import attention, device_load, lanes, wiring
from sift.kernel.access import Repository, Viewer
from sift.kernel.attention import full_amount, stepping_back
from sift.kernel.content import ContentStore
from sift.kernel.content.identity_counts import UNREAD_KINDS
from sift.kernel.content.library import LibraryStore, names_for_assets, names_for_roots
from sift.kernel.db import Database
from sift.kernel.jobs import (
    CANCELABLE_STATES,
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    STEP_COUNT_CAP,
    Job,
    JobQueue,
    JobState,
    StepCounts,
    WorkAhead,
    WorkerPool,
    WorkKind,
    WorkSummary,
    by_itself_job_types,
    counted_as,
    folded_state,
    registered_families,
    registered_job_names,
    registered_product_carriers,
    unlisted_job_types,
    waits_for_password,
)
from sift.kernel.jobs.failure_words import in_plain_words, kind_of
from sift.kernel.jobs.families import (
    FAMILY_LABELS,
    HOUSEKEEPING,
    LONG_PASSES,
    PRODUCT_FAMILIES,
    PRODUCT_TYPES,
    Family,
    own_estimate,
)
from sift.kernel.jobs.families import Chore as HousekeepingChore
from sift.kernel.jobs.ledger import Estimate, Ledger
from sift.kernel.jobs.queue import LiveProducts, LiveWork
from sift.kernel.jobs.queue_rows import FilesToRead
from sift.kernel.jobs.schedules import get_schedule
from sift.kernel.jobs.switchboard import Readiness, Switch, Switchboard
from sift.kernel.jobs.work_ahead import Ahead
from sift.kernel.log import get_logger
from sift.kernel.sampling import PREVIEW_SHAPE_SETTING, preview_shape
from sift.kernel.seams import SettingsSeam
from sift.kernel.wire import Wire
from sift.kernel.wiring import ACCESS, DATABASE, LEDGER, LIBRARY, part_or_none
from sift.slices.auth import csrf_protect, require_admin
from sift.slices.media_jobs.activity_wire import Chore as Chore
from sift.slices.media_jobs.activity_wire import FamilyOfWork as FamilyOfWork
from sift.slices.media_jobs.activity_wire import JobsPage as JobsPage
from sift.slices.media_jobs.activity_wire import JobView as JobView
from sift.slices.media_jobs.activity_wire import KindOfWork as KindOfWork
from sift.slices.media_jobs.activity_wire import PartOfWork as PartOfWork
from sift.slices.media_jobs.activity_wire import StepsOfJob as StepsOfJob
from sift.slices.media_jobs.activity_wire import StepSummary as StepSummary
from sift.slices.media_jobs.jobs import (
    PROBE,
    REBUILD_PREVIEWS,
    REBUILD_THUMBNAILS,
    preview_recipe,
)
from sift.slices.media_jobs.pooled import WAITING_FOR_THE_SCAN as WAITING_FOR_THE_SCAN
from sift.slices.media_jobs.pooled import priced_together
from sift.slices.media_jobs.presses import Presses, read_presses
from sift.slices.media_jobs.read_first import after_the_read_first

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
        name=_named(job.type),
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
    )


#: The payload keys naming what a job is about, in order; with none it is whole-library work.
_ASSET_KEY = "asset_id"
_ROOT_KEY = "root_id"


def _subject_assets(jobs: Sequence[Job]) -> dict[str, str]:
    """The file each job is about, keyed by job id. Only jobs about one file are in it."""
    return {
        job.id: (job.payload or {})[_ASSET_KEY]
        for job in jobs
        if isinstance((job.payload or {}).get(_ASSET_KEY), str)
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
        scan_held=await queue.held_for_family_by_type(),
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
    folded = await _folded(queue, database, jobs, subjects, assets, shown) if fold else {}
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
    # Background upkeep is not listed (`unlisted_job_types`), unless a caller names its type.
    upkeep = unlisted_job_types()
    # Nor the work that runs by itself as files arrive, where it heads its own row; its failures stay.
    unnamed = job_type is None and parent_id is None
    quiet = sorted(by_itself_job_types()) if unnamed else []
    # The whole queue's shape, not the page's: the tallies above the table count everything. Read
    # before the page, because the page's "Older tasks" are the kinds of it no handler claims.
    summary = await queue.work_summary()
    claimed = registered_job_names()
    gone = sorted(kind for kind in summary.states if kind not in claimed and kind not in upkeep)
    # On a page of families a state is the one a family's row shows (`folded`), never a row's own.
    page = await queue.list(
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
    subjects, assets, folded = await _about_the_rows(queue, database, page.jobs, shown, fold=fold)
    # What is still to come, so a bar's total does not climb as a pass queues a page at a time.
    work, counted = await _work_of(summary, work_ahead, upkeep)
    listed = {kind: by_state for kind, by_state in summary.states.items() if kind not in upkeep}
    counts = await _row_counts(queue, listed, quiet)
    tallies = _tallies(
        families=page.by_state if fold else None,
        rows=counts,
        by_type=summary.states,
        job_type=job_type,
        older=gone if older else None,
        parent_id=parent_id,
    )
    # Only the rows that could be in the line, asked in one statement for the whole page.
    places = await queue.positions_of([job.id for job in page.jobs if job.state is JobState.QUEUED])
    unread = await work_ahead.unread_now(counted)
    families, held, sealed = await _families_and_holds(
        queue, work, ledger, pool, listed, counted, library, unread
    )
    return JobsPage(
        jobs=_views(page.jobs, subjects, assets, places, folded),
        total=page.total,
        counts=counts,
        tallies=tallies,
        by_type=listed,
        names={kind: claimed[kind] for kind in listed if kind in claimed},
        older=[kind for kind in gone if kind in listed],
        work=work,
        families=families,
        housekeeping=await _housekeeping(queue, summary, ledger, pool, held, sealed),
        stepping_back=stepping_back(),
        full_amount=full_amount(),
        step_back_share=attention.ATTENTION.share,
        step_back_for=attention.ATTENTION.cause,
        step_back_over=device_load.READER.over if attention.ATTENTION.cause == "others" else [],
        password_wanted=sum(sealed.values()),
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


async def _folded(
    queue: JobQueue,
    database: Database | None,
    tops: Sequence[Job],
    subjects: Mapping[str, str],
    assets: Mapping[str, str],
    shown: _Shown,
) -> dict[str, StepSummary]:
    """Each top row's family folded (steps counted, one state, its file named once), in three
    statements for the page."""
    counts = await queue.step_counts([job.id for job in tops])
    # Named from the steps only where the top has no subject and its family was counted whole.
    unnamed = [
        job.id
        for job in tops
        if job.id not in subjects and not counts.get(job.id, _NO_STEPS).at_least
    ]
    files = await queue.family_files(unnamed)
    # `names_for_assets` answers an empty list with nothing and asks no question.
    wanted = sorted(await shown.of(sorted(set(files.values()))))
    names = await names_for_assets(database, wanted) if database is not None else {}
    folded: dict[str, StepSummary] = {}
    for job in tops:
        counted = counts.get(job.id, _NO_STEPS)
        subject, subject_id = subjects.get(job.id), assets.get(job.id)
        if subject is None and files.get(job.id) in names:
            subject_id = files[job.id]
            subject = names[subject_id]
        folded[job.id] = StepSummary(
            count=counted.steps,
            by_state=counted.by_state,
            at_least=counted.at_least,
            cap=STEP_COUNT_CAP,
            state=folded_state(job.state, counted.by_state),
            subject=subject,
            subject_id=subject_id,
        )
    return folded


#: A top that started nothing. The same answer `step_counts` gives one, for a top it was not asked.
_NO_STEPS = StepCounts(by_state={}, at_least=False)


#: What a pass that is not running says, in one sentence each: only the server can tell them apart.
NOTHING_WAITING = "Nothing waiting"
WAITING_FOR_WINDOW = "Waiting for tonight's window."
#: With the hour the window opens, where the family declared it (`Switchboard.declare_window`).
WAITING_FOR_WINDOW_AT = "Waiting for tonight's window, which opens at {opens}."
#: A family whose every unfinished job waits for quiet hours; their hour is drawn at the top of Tasks.
WAITING_FOR_QUIET_HOURS = "Waiting for quiet hours."
PAUSED_FOR_THE_BENCHMARK = "Paused while Sift benchmarks this device."
AFTER_THE_BENCHMARK = "Waits until the benchmark's done."
_HOLD: dict[str, object] = {"time_unknown": AFTER_THE_BENCHMARK, "for_task": None, "pace": None}
#: What the Scan row and every pass after it say in place of a time while a folder is uncounted.
NOT_KNOWN_UNTIL_COUNTED = "Not known until every folder is counted."
#: What sets the pace of the read, by the library folders on the share.
PACED_BY_SHARE = "Reading is limited by the network share that holds {folders}."
#: A chore with work outstanding, none of it running, and some of it parked until somebody gives
#: the password (`WaitingForPassword`): the row's own rows say which key, and the unlock bar asks.
WAITING_FOR_UNLOCK = "Waiting for your password."


async def _held_for_quiet_hours(board: Switchboard, queue: JobQueue | None) -> Mapping[str, int]:
    """What quiet hours hold back now, by job type, read once for the page; nothing while they are on."""
    hold = await board.quiet_hold()
    if queue is None or hold.open:
        return {}
    return await queue.held_by_type(hold.types)


def _at_once(pool: WorkerPool | None, job_types: Sequence[str]) -> int:
    """How many workers this family can occupy: its types' caps added, held under the pool's count;
    one with no pool."""
    if pool is None:
        return 1
    workers = pool.concurrency
    limits = pool.limits
    return max(1, min(workers, sum(limits.get(job_type, workers) for job_type in job_types)))


def _held(pool: WorkerPool | None, job_types: Sequence[str]) -> bool:
    """Whether every one of these types is capped at nothing now: held for the night, not idle."""
    if pool is None or not job_types:
        return False
    limits = pool.limits
    return all(limits.get(job_type, 1) == 0 for job_type in job_types)


def _counted(
    types: Sequence[str],
    work: Mapping[str, KindOfWork],
    carriers: Collection[str],
    *,
    left: float,
    outstanding: int,
) -> tuple[float, float, int, int, list[PartOfWork], int]:
    """A family's own kinds added to what its carriers brought: `(left, counted_left, done, total,
    parts, outstanding)`, where `counted_left` is the library's share of `left`."""
    counted_left = 0.0
    done = 0
    total = 0
    parts: list[PartOfWork] = []
    for job_type in types:
        kind = work.get(job_type)
        if kind is None or job_type in carriers:
            # A carrier's work went above, to the families of the products its tasks name.
            continue
        here = kind.waiting if kind.waiting is not None else kind.left_units
        left += here
        counted_left += here
        outstanding += kind.outstanding
        # From the library: a kind with no total adds nothing, so a queued run cannot swell it.
        if kind.total is not None:
            done += kind.done
            total += kind.total
            parts.append(
                PartOfWork(
                    type=job_type,
                    caption=counted_as(job_type),
                    done=min(kind.done, kind.total),
                    total=kind.total,
                )
            )
    return left, counted_left, done, total, parts, outstanding


async def _switched_on(
    board: Switchboard, types: Sequence[str], switches: Mapping[str, Switch]
) -> bool:
    """One switch for the family, reported only where every switched type agrees; on otherwise."""
    switched = [job_type for job_type in sorted(types) if job_type in switches]
    distinct = {switches[job_type].key for job_type in switched}
    return True if len(distinct) != 1 else await board.refusal(switched[0]) is None


def _held_for_quiet(
    own: Sequence[str],
    *,
    outstanding: int,
    carried: int,
    states: Mapping[str, Mapping[str, int]] | None,
    held_rows: Mapping[str, int],
) -> bool:
    """Held for quiet hours: work outstanding, none of it running, none of it carried by a
    coordinator (whose rows follow the press that made them), and every job of it held."""
    return (
        outstanding > 0
        and carried == 0
        and not any((states or {}).get(one, {}).get(JobState.RUNNING.value, 0) for one in own)
        and sum(int(held_rows.get(one, 0)) for one in own) >= outstanding
    )


async def _families(
    work: Mapping[str, KindOfWork],
    ledger: Ledger | None,
    board: Switchboard,
    pool: WorkerPool | None = None,
    queue: JobQueue | None = None,
    states: Mapping[str, Mapping[str, int]] | None = None,
    held: Mapping[str, int] | None = None,
    kinds: Mapping[str, Mapping[str, float]] | None = None,
    standing: Mapping[str, int] | None = None,
    arriving: Mapping[str, Mapping[str, float]] | None = None,
    presses: Mapping[Family, Presses] | None = None,
    live: Sequence[LiveWork] | None = None,
    unread: FilesToRead | None = None,
    scan_held: Mapping[str, int] | None = None,
    pace: str | None = None,
    benchmark: bool = False,
    pool_bound: bool = True,
) -> dict[str, FamilyOfWork]:
    """The long passes, each with its estimate, its reason and what it may do. `presses` alone
    describe a family whose live work is theirs; `unread` is what the walks have still to read."""
    grouped: dict[Family, list[str]] = {family: [] for family in LONG_PASSES}
    for job_type, family in registered_families().items():
        if family in grouped:
            grouped[family].append(job_type)
    if unread is not None:
        arriving = _with_unread(work, arriving or {}, unread)[1]
        work, kinds = _with_unread(work, kinds or {}, unread)
    reads = _Reads(
        work=work,
        ledger=ledger,
        board=board,
        pool=pool,
        states=states,
        kinds=kinds,
        presses=presses,
        switches=board.switches(),
        readiness=await board.readiness(),
        carried=await _carried(work, queue, live),
        carriers=registered_product_carriers(),
        # What quiet hours are holding back, read once for every family: the pool's caps alone
        # would say "Running" over a family whose every job is held at the claim.
        held_rows=held if held is not None else await _held_for_quiet_hours(board, queue),
        scan_held=scan_held or {},
        benchmark=benchmark,
        standing=standing or {},
        arriving=arriving or {},
    )
    answer: dict[str, FamilyOfWork] = {}
    alone: set[str] = set()
    for family, types in grouped.items():
        answer[family.value], by_presses_alone = await _family(family, types, reads)
        if by_presses_alone:
            alone.add(family.value)
    answer = not_before_the_read(pictured_in_the_read(answer, alone), alone)
    if ledger is not None and pool is not None and not (unread and unread.uncounted):
        answer = await priced_together(
            answer, work, kinds or {}, ledger, pool.concurrency, pool_bound, alone
        )
    answer = not_known_yet(answer, uncounted=0 if unread is None else unread.uncounted, pace=pace)
    return {
        key: one.model_copy(update=_HOLD) if one.reason == PAUSED_FOR_THE_BENCHMARK else one
        for key, one in answer.items()
    }


@dataclass(frozen=True, slots=True)
class _Reads:
    """What every family's row is built from, read once for the page."""

    work: Mapping[str, KindOfWork]
    ledger: Ledger | None
    board: Switchboard
    pool: WorkerPool | None
    states: Mapping[str, Mapping[str, int]] | None
    kinds: Mapping[str, Mapping[str, float]] | None
    presses: Mapping[Family, Presses] | None
    switches: Mapping[str, Switch]
    readiness: Mapping[Family, Readiness]
    carried: tuple[dict[Family, float], dict[Family, int], dict[str, Family]]
    carriers: frozenset[str]
    held_rows: Mapping[str, int]
    scan_held: Mapping[str, int]
    benchmark: bool = False
    standing: Mapping[str, int] = field(default_factory=dict)
    arriving: Mapping[str, Mapping[str, float]] = field(default_factory=dict)


async def _family(family: Family, types: list[str], reads: _Reads) -> tuple[FamilyOfWork, bool]:
    """One long pass's row, and whether the presses alone describe it."""
    carried_left, carried_outstanding, carried_by = reads.carried
    by_presses_alone = False
    left, counted_left, done, total, parts, outstanding = _counted(
        types,
        reads.work,
        reads.carriers,
        left=carried_left.get(family, 0.0),
        outstanding=carried_outstanding.get(family, 0),
    )
    at_once = _at_once(reads.pool, types)
    # Its own kinds' items, plus a carrier's whose every live task is this family's.
    priced = [one for one in types if one not in reads.carriers] + [
        one for one, whose in carried_by.items() if whose is family
    ]
    press = None if reads.presses is None else reads.presses.get(family)
    priced_mix = priced
    if press is not None:
        press.held = sum(int(reads.held_rows.get(one, 0)) for one in types)
        # A coordinator's live tasks are among the files the library's counts already hold.
        library_left = counted_left if parts else left
        left, done, total, parts = _with_presses(press, library_left, done, total, parts)
        if press.alone:
            by_presses_alone = True
            # The mix of what is left is the presses' own files: a carrier's live tasks.
            priced_mix = [one for one in priced if one in reads.carriers]
    alone = outstanding > 0 and not carried_outstanding.get(family) and not (press and press.going)
    estimate, standing = await _estimate(reads, family, priced, priced_mix, left, at_once, alone)
    on = await _switched_on(reads.board, types, reads.switches)
    state = reads.readiness.get(family)
    ready = True if state is None else state.ready
    # A coordinator's cap is its press's, not the family's window: "held" asks of its own types.
    own = [one for one in types if one not in reads.carriers]
    held_now = _held(reads.pool, own)
    running = sum((reads.states or {}).get(one, {}).get(JobState.RUNNING.value, 0) for one in types)
    held_by = partial(
        _held_for_quiet,
        own,
        outstanding=outstanding,
        carried=carried_outstanding.get(family, 0),
        states=reads.states,
    )
    row = FamilyOfWork(
        label=FAMILY_LABELS[family],
        types=sorted(types),
        on=on,
        ready=ready,
        problem=None if state is None else state.problem,
        quick_seconds=None if estimate is None else estimate.quick_seconds,
        slow_seconds=None if estimate is None else estimate.slow_seconds,
        at_least=estimate is not None and estimate.floor,
        sample=0 if estimate is None else estimate.items,
        at_once=at_once,
        outstanding=outstanding,
        waiting=round(left),
        done=min(done, total),
        total=total,
        parts=parts,
        time_unknown=_for_task(standing, more=False) if standing >= left else None,
        for_task=_for_task(standing, more=True) if 0 < standing < left else None,
        reason=_reason(
            left=left,
            outstanding=outstanding,
            on=on,
            ready=ready,
            held=held_now,
            # Asked only of a held family: the hour is the answer to "held until when".
            opens=await reads.board.window_opens(family) if held_now else None,
            quiet=held_by(held_rows=reads.held_rows),
            by_scan=held_by(held_rows=reads.scan_held),
            by_benchmark=reads.benchmark and outstanding > 0 and not running,
        ),
        task=FAMILY_TASKS.get(family),
        running=running,
    )
    return row, by_presses_alone


def _for_task(files: int, *, more: bool) -> str | None:
    """What waits for its task's own run, said apart from the time left, or None for nothing."""
    if files <= 0:
        return None
    said = f"{files:,} more" if more else f"{files:,}"
    return f"{said} waits for its task." if files == 1 else f"{said} wait for their task."


async def _estimate(
    reads: _Reads,
    family: Family,
    priced: list[str],
    priced_mix: list[str],
    left: float,
    at_once: int,
    alone: bool,
) -> tuple[Estimate | None, int]:
    """The ledger's estimate of a family's time left, priced kind by kind where a counter can say,
    and the files waiting for their task: while only arriving files run, those are not coming."""
    standing = round(min(left, sum(reads.standing.get(one, 0) for one in priced))) if alone else 0
    kinds = reads.arriving if standing else (reads.kinds or {})
    mix: dict[str, float] = {}
    for job_type in priced_mix:
        for media_kind, n in kinds.get(job_type, {}).items():
            mix[media_kind] = mix.get(media_kind, 0.0) + n
    if reads.ledger is None:
        return None, standing
    found = await reads.ledger.estimate(
        family, priced, left=left - standing, at_once=at_once, kinds=mix or None
    )
    return found, standing


def _with_presses(
    press: Presses, library_left: float, done: int, total: int, parts: list[PartOfWork]
) -> tuple[float, int, int, list[PartOfWork]]:
    """A family's left, done, total and lines with the runs over some files: alone, those runs are
    the row; beside the library's work, what they make again is added to it."""
    if not press.going:
        return library_left, done, total, parts
    if press.alone:
        tallies = {job_type: list(counted) for job_type, counted in press.parts.items()}
        for job_type in {*press.folders_done, *press.folders_total}:
            made = press.folders_done.get(job_type, 0)
            tally = tallies.setdefault(job_type, [0, 0])
            tally[0] += made
            tally[1] += max(made, press.folders_total.get(job_type, 0))
        order = {part.type: index for index, part in enumerate(parts)}
        lines = [
            PartOfWork(type=job_type, caption=counted_as(job_type), done=min(got, of), total=of)
            for job_type, (got, of) in sorted(
                tallies.items(), key=lambda one: (order.get(one[0], len(order)), one[0])
            )
            if of > 0
        ]
        return (
            float(press.live + press.folders_left),
            sum(line.done for line in lines),
            sum(line.total for line in lines),
            lines,
        )
    lines = []
    for part in parts:
        got, of = press.again_parts.get(part.type, [0, 0])
        lines.append(part.model_copy(update={"done": part.done + got, "total": part.total + of}))
        done, total = done + got, total + of
    return library_left + press.again_live, done, total, lines


def pictured_in_the_read(
    answer: dict[str, FamilyOfWork], alone: Collection[str] = ()
) -> dict[str, FamilyOfWork]:
    """Generate's work arriving from the read counted as Generate running, so the tail of a first
    import does not read "Not started" between one file's pictures and the next file's read."""
    generate = answer.get(Family.GENERATE.value)
    read = answer.get(Family.SCAN.value)
    if generate is None or read is None or read.outstanding <= 0 or generate.waiting <= 0:
        return answer
    # A read somebody pressed for files already in the library hands Generate nothing, and a
    # Generate row that is a press over some files is not waiting for the library's read.
    if {Family.SCAN.value, Family.GENERATE.value} & set(alone):
        return answer
    answer[Family.GENERATE.value] = generate.model_copy(
        update={"outstanding": generate.outstanding + read.outstanding}
    )
    return answer


#: The passes made from the files the read takes in: none can finish before the read does.
_AFTER_THE_READ = (Family.GENERATE, Family.FINGERPRINT, Family.IDENTIFY, Family.SEMANTIC)


def _with_unread(
    work: Mapping[str, KindOfWork],
    kinds: Mapping[str, Mapping[str, float]],
    unread: FilesToRead,
) -> tuple[dict[str, KindOfWork], dict[str, dict[str, float]]]:
    """The read (key "") and each product after it with the walks' files not yet taken in added to
    what it has left, to its total and to its mix, by the library's rule for an unread file
    (`UNREAD_KINDS`). A file taken in leaves the walk's count as the library's starts counting it."""
    added = dict(work)
    mixed = {job_type: dict(mix) for job_type, mix in kinds.items()}
    for key, job_type in (("", PROBE), *PRODUCT_TYPES.items()):
        kind = work.get(job_type)
        # A product switched off wants nothing and has no total; a new library's read has 0.
        if kind is None or kind.waiting is None or not (kind.total or job_type == PROBE):
            continue
        wanted = UNREAD_KINDS.get(key)
        coming = {one: n for one, n in unread.by_kind.items() if wanted is None or one in wanted}
        files = round(sum(coming.values()))
        if files <= 0 or (key and PRODUCT_FAMILIES[key] not in _AFTER_THE_READ):
            continue
        added[job_type] = kind.model_copy(
            update={"waiting": kind.waiting + files, "total": (kind.total or 0) + files}
        )
        mix = mixed.setdefault(job_type, {})
        for one, n in coming.items():
            mix[one] = mix.get(one, 0.0) + n
    return added, mixed


def not_before_the_read(
    answer: dict[str, FamilyOfWork], alone: Collection[str] = ()
) -> dict[str, FamilyOfWork]:
    """A pass after the read is not done before the read is: at least the read's time, and the
    read's time AND its own while the read holds its work back. Where the read cannot say, neither
    can it."""
    reading = answer.get(Family.SCAN.value)
    if reading is None or reading.waiting <= 0 or Family.SCAN.value in alone:
        return answer
    for family in _AFTER_THE_READ:
        after = answer.get(family.value)
        if after is None or after.waiting <= 0 or family.value in alone:
            continue
        quick: int | None = None
        slow: int | None = None
        if not (
            after.quick_seconds is None
            or after.slow_seconds is None
            or reading.quick_seconds is None
            or reading.slow_seconds is None
        ):
            if after.reason == WAITING_FOR_THE_SCAN:
                quick = after.quick_seconds + reading.quick_seconds
                slow = after.slow_seconds + reading.slow_seconds
            else:
                quick = max(after.quick_seconds, reading.quick_seconds)
                slow = max(after.slow_seconds, reading.slow_seconds)
        answer[family.value] = after.model_copy(
            update={"quick_seconds": quick, "slow_seconds": slow}
        )
    return answer


def not_known_yet(
    answer: dict[str, FamilyOfWork], *, uncounted: int, pace: str | None = None
) -> dict[str, FamilyOfWork]:
    """No time on the Scan row or any pass after it while a folder waits to be counted, and the
    running read's pace where a share sets it."""
    scan = answer.get(Family.SCAN.value)
    if scan is not None and pace is not None and scan.outstanding > 0:
        answer[Family.SCAN.value] = scan.model_copy(update={"pace": pace})
    if uncounted <= 0:
        return answer
    for family in (Family.SCAN, *_AFTER_THE_READ):
        row = answer.get(family.value)
        if row is not None:
            answer[family.value] = row.model_copy(
                update={
                    "quick_seconds": None,
                    "slow_seconds": None,
                    "time_unknown": NOT_KNOWN_UNTIL_COUNTED,
                }
            )
    return answer


#: How long a share's readers are watched for, and the share of it they must have spent waiting,
#: between them, for the share to be named as what sets the pace.
PACE_WINDOW_SECONDS = 60.0
PACE_WAITED_SHARE = 0.5


class ShareWaits:
    """Each network share's seconds waited, as the page has seen them over the last minute."""

    def __init__(self, waits: tuple[str, ...] = ("urgent_wait_seconds", "ordinary_wait_seconds")):
        self._waits = waits
        self._seen: deque[tuple[float, dict[str, float]]] = deque()

    def busiest(self, readings: Mapping[str, Mapping[str, object]], now: float) -> str | None:
        """The share whose readers waited most of the last minute, by its key, or None."""
        totals = {
            key: sum(cast(float, one[wait]) for wait in self._waits)
            for key, one in readings.items()
            if one.get("remote")
        }
        seen = self._seen
        seen.append((now, totals))
        while len(seen) > 1 and now - seen[1][0] >= PACE_WINDOW_SECONDS:
            seen.popleft()
        then, before = seen[0]
        span = now - then
        # A page not read for a while has no last minute to speak of: watch again from here.
        if span > 2 * PACE_WINDOW_SECONDS:
            seen.clear()
            seen.append((now, totals))
        if not PACE_WINDOW_SECONDS <= span <= 2 * PACE_WINDOW_SECONDS:
            return None
        waited = {key: total - before.get(key, 0.0) for key, total in totals.items()}
        key = max(waited, key=waited.__getitem__, default=None)
        return key if key is not None and waited[key] >= PACE_WAITED_SHARE * span else None


_SHARE_WAITS = ShareWaits()
#: The files' reads alone: waiting on a share's places, the read is that share's, not the pool's.
_READ_WAITS = ShareWaits(("urgent_wait_seconds",))


def _pool_bound() -> bool:
    installed = lanes.installed()
    return installed is None or _READ_WAITS.busiest(installed.readings(), time.monotonic()) is None


def _joined(names: Sequence[str]) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


async def _paced_by(library: LibraryStore | None) -> str | None:
    """The sentence naming the share that sets the read's pace, by its library folders, or None."""
    installed = lanes.installed()
    if installed is None:
        return None
    busiest = _SHARE_WAITS.busiest(installed.readings(), time.monotonic())
    if busiest is None or library is None:
        return None
    names = sorted(
        root.name
        for root in await library.roots()
        if lanes.storage_for(Path(root.abs_path) / "walk").key == busiest
    )
    return PACED_BY_SHARE.format(folders=_joined(names)) if names else None


def _run_type(chore: HousekeepingChore) -> str:
    """The job type a chore's runs are: its task's own, where it is a task, else its own."""
    task = get_schedule(chore.task) if chore.task is not None else None
    return task.job_type if task is not None and task.job_type is not None else chore.job_type


async def _housekeeping(
    queue: JobQueue,
    summary: WorkSummary,
    ledger: Ledger | None,
    pool: WorkerPool | None,
    held: Mapping[str, int] | None = None,
    sealed: Mapping[str, int] | None = None,
) -> list[Chore]:
    """The work that is not a pass over the library, priced as a pass is, with its last run in place
    of a bar."""
    # The last run shown is the one Tasks shows: the task's own type where the chore is a task.
    ran_as = {chore.job_type: _run_type(chore) for chore in HOUSEKEEPING}
    # One statement for every chore, through the function the Tasks row reads, so the two agree.
    last = await queue.last_finished_runs(sorted(set(ran_as.values())))
    rows: list[Chore] = []
    for chore in HOUSEKEEPING:
        one = summary.run.get(chore.job_type, WorkKind())
        states = summary.states.get(chore.job_type, {})
        estimate = None
        if chore.priced and ledger is not None and one.outstanding > 0:
            estimate = await ledger.estimate(
                Family.OTHER,
                [chore.job_type],
                left=one.outstanding,
                at_once=_at_once(pool, [chore.job_type]),
            )
        # Work whose length is a person's says where it stands itself, never from the runs before.
        own = None if chore.priced or one.outstanding == 0 else own_estimate(chore.job_type)
        said = None if own is None else own.seconds
        quick = said if estimate is None else estimate.quick_seconds
        slow = said if estimate is None else estimate.slow_seconds
        run = last.get(ran_as[chore.job_type])
        # Held for quiet hours by the pass's rule (`_families`): outstanding, none running, all held.
        quiet = (
            one.outstanding > 0
            and not states.get(JobState.RUNNING.value, 0)
            and int((held or {}).get(chore.job_type, 0)) >= one.outstanding
        )
        rows.append(
            Chore(
                job_type=chore.job_type,
                label=chore.label,
                running=states.get(JobState.RUNNING.value, 0),
                outstanding=one.outstanding,
                failed=one.failed,
                quick_seconds=quick,
                slow_seconds=slow,
                last_started_at=None if run is None else run.started_at,
                last_seconds=None if run is None else run.seconds,
                last_state=None if run is None else run.state.value,
                last_error=None if run is None or run.error is None else in_plain_words(run.error),
                last_job=None if run is None else run.id,
                task=chore.task,
                reason=WAITING_FOR_QUIET_HOURS
                if quiet
                else WAITING_FOR_UNLOCK
                if one.outstanding > 0
                and not states.get(JobState.RUNNING.value, 0)
                and (sealed or {}).get(chore.job_type, 0) > 0
                else chore.waiting
                if own is not None and own.waiting
                else None,
            )
        )
    return rows


#: The states a job is still to be worked on in, as the queue's summary spells them.
_UNFINISHED_STATES = frozenset(state.value for state in CANCELABLE_STATES)


#: Which task each pass is on Tasks; a test holds every id here to the task registry.
FAMILY_TASKS: dict[Family, str] = {
    Family.SCAN: "scan",
    Family.GENERATE: "generate",
    Family.IDENTIFY: "identify",
    Family.SEMANTIC: "smart-search",
}


async def _carried(
    work: Mapping[str, KindOfWork],
    queue: JobQueue | None,
    live: Sequence[LiveWork] | None = None,
) -> tuple[dict[Family, float], dict[Family, int], dict[str, Family]]:
    """What the product-carrying task types have in flight, laid at each product's family, from `live`
    where given."""
    left: dict[Family, float] = {}
    outstanding: dict[Family, int] = {}
    # A carrier whose every live task is ONE family's: that family may price from its items.
    single: dict[str, Family] = {}
    if queue is None:
        return left, outstanding, single
    # Grouped by the queue, so no payload is parsed on the event loop for every read of this screen.
    carriers = registered_product_carriers()
    by_type: dict[str, list[LiveProducts | LiveWork]] = {}
    lines: Sequence[LiveProducts | LiveWork] = (
        [line for line in live if line.type in carriers]
        if live is not None
        else await queue.live_products(sorted(carriers))
    )
    for line in lines:
        by_type.setdefault(line.type, []).append(line)
    for job_type, lines in sorted(by_type.items()):
        kind = work.get(job_type)
        rows = sum(line.count for line in lines)
        units_each = kind.left_units / rows if kind is not None and kind.left_units else 1.0
        seen: set[Family] = set()
        for line in lines:
            families = {PRODUCT_FAMILIES[key] for key in line.products if key in PRODUCT_FAMILIES}
            seen |= families
            for family in families:
                left[family] = left.get(family, 0.0) + units_each * line.count
                outstanding[family] = outstanding.get(family, 0) + line.count
        if len(seen) == 1:
            single[job_type] = next(iter(seen))
    return left, outstanding, single


def _reason(
    *,
    left: float,
    outstanding: int,
    on: bool,
    ready: bool,
    held: bool,
    opens: str | None = None,
    quiet: bool = False,
    by_scan: bool = False,
    by_benchmark: bool = False,
) -> str | None:
    """Why this pass is not running, in one sentence, or None while it is."""
    if not on or not ready:
        # Both already have their own words on the family; a third copy could only disagree.
        return None
    if by_benchmark:
        return PAUSED_FOR_THE_BENCHMARK
    if held:
        return WAITING_FOR_WINDOW if opens is None else WAITING_FOR_WINDOW_AT.format(opens=opens)
    if quiet:
        return WAITING_FOR_QUIET_HOURS
    if by_scan:
        return WAITING_FOR_THE_SCAN
    if left <= 0 and outstanding == 0:
        return NOTHING_WAITING
    # Work nobody has started is not a reason: its estimate is what somebody pressing Run now reads.
    return None


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

    The bounds are declared rather than checked in the body, so the schema says them. A folded page
    filtered by state reads the state the family's row shows (`folded_state`: a family with a
    failed step is a failed family), so every family is under exactly one state (`_tallies`).
    """
    if fold and parent_id is not None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "fold pages families and cannot be filtered by parent_id",
        )
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


class Stopped(Wire):
    """How many jobs were stopped."""

    stopped: int


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


class FullAmountAsked(Wire):
    """Whether to use the full amount of this device although it is in use."""

    on: bool = Field(
        description="True runs every task although somebody is at the keyboard; false steps back "
        "again while they are."
    )


class StepBack(Wire):
    """What background work is doing about somebody using the computer, after a press."""

    stepping_back: bool = Field(description="As on the jobs page.")
    full_amount: bool = Field(description="As on the jobs page.")
    pressed: bool = Field(
        description="Whether the full amount is pressed for, whether or not anybody is at the "
        "keyboard now: held until Sift stops or the next press."
    )


@router.post("/full-amount", dependencies=[Depends(csrf_protect)])
async def press_full_amount(
    body: FullAmountAsked,
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> StepBack:
    """Use the full amount of this device although it is in use, or step back again.

    Held in memory, not stored; the pool reaches it at its next reconfigure, a few seconds later.
    """
    reading = attention.ATTENTION
    reading.press(full=body.on)
    return StepBack(
        stepping_back=reading.holding, full_amount=reading.full_amount, pressed=reading.pressed
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
