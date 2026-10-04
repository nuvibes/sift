# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Jobs dashboard's endpoints.

Admin-only, all of them, enforced here on the server. The dashboard shows what Sift is doing with
the files in the library: their names, the errors it hit, what failed and why. That is a picture
of somebody's library, and it is not a guest's to see. Hiding the nav item is not this and is
never trusted to be: these routes refuse a guest called directly, with no interface involved.

These are ordinary reads and writes: the dashboard is live because the connection that carries
every kind of live update in this application tells it when the queue has moved, not because this
feature holds a socket of its own.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import Field

from sift.kernel import attention, wiring
from sift.kernel.access import Repository, Viewer
from sift.kernel.attention import full_amount, stepping_back
from sift.kernel.content import ContentStore
from sift.kernel.content.library import names_for_assets, names_for_roots
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
from sift.kernel.jobs.failure_words import in_plain_words
from sift.kernel.jobs.families import (
    FAMILY_LABELS,
    HOUSEKEEPING,
    LONG_PASSES,
    PRODUCT_FAMILIES,
    Family,
    own_estimate,
)
from sift.kernel.jobs.families import Chore as HousekeepingChore
from sift.kernel.jobs.ledger import Estimate, Ledger
from sift.kernel.jobs.queue import LiveProducts, LiveWork
from sift.kernel.jobs.schedules import get_schedule
from sift.kernel.jobs.switchboard import Readiness, Switch, Switchboard
from sift.kernel.jobs.work_ahead import Ahead
from sift.kernel.log import get_logger
from sift.kernel.sampling import PREVIEW_SHAPE_SETTING, preview_shape
from sift.kernel.seams import SettingsSeam
from sift.kernel.wire import Wire
from sift.kernel.wiring import ACCESS, DATABASE, LEDGER, part_or_none
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
    REBUILD_PREVIEWS,
    REBUILD_THUMBNAILS,
    preview_recipe,
)
from sift.slices.media_jobs.presses import Presses, read_presses

log = get_logger(__name__)

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _database(request: Request) -> Database | None:
    """The database, or None in a test that builds routes without one. A row then shows what the
    work is and not which file, which is a smaller answer rather than a broken page."""
    return part_or_none(request, DATABASE)


def _access(request: Request) -> Repository | None:
    """Who may be shown what, or None in a test that builds routes without it. No file is then
    named at all: a name is only ever handed out once it is known the vault is not holding it."""
    return part_or_none(request, ACCESS)


class _Shown:
    """Which files the viewer asking may be shown by name: seen by them, and not held in their vault.

    A job names the file it works on, and the vault conceals a file from its own admin too. Asked
    once per page for the files the page is about to name.
    """

    def __init__(self, access: Repository | None, viewer: Viewer) -> None:
        self._access = access
        self._viewer = viewer

    async def of(self, asset_ids: Sequence[str]) -> set[str]:
        if self._access is None or not asset_ids:
            return set()
        standing = await self._access.standing_of(self._viewer, asset_ids)
        return {asset_id for asset_id, concealed in standing.items() if not concealed}


def _ledger(request: Request) -> Ledger | None:
    """The ledger, or None in a test that builds routes without one: the families are still
    drawn, with no estimate beside them."""
    return part_or_none(request, LEDGER)


def _pool(request: Request) -> WorkerPool | None:
    """The worker pool, or None in a test that builds routes without one.

    What it is here for is the EFFECTIVE CONCURRENCY (how many workers a family can occupy at
    once), which the estimate divides by. Without it the estimate would price a library as though
    one worker were doing the whole of it.
    """
    return part_or_none(request, wiring.POOL)


#: What a row of a type this version has no handler for is called. The table keeps no name of
#: its own for a row, and the type is an id nobody should read, so the row says what it is: work
#: an older release did. The Type choice's one entry for all of them is the client's words.
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
        # Already scrubbed on the way in: the queue stores what the redactor let through. This is
        # the one screen where a raw path would otherwise be most likely to escape, because it is
        # the screen that exists to show what went wrong.
        error=job.error,
        # Scrubbed on the way in like the error beside it, so a note that named a folder cannot
        # reach this screen with the name still in it.
        note=job.note,
        run_after=job.run_after,
        position=position,
        created_at=job.created_at,
        updated_at=job.updated_at,
        steps=steps,
        waits_for_password=job.state is JobState.BLOCKED and waits_for_password(job.error),
    )


#: Which payload key names the thing a job is about, in the order they are looked for. A job that
#: carries none of them is whole-library work and has no subject to show.
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
    """The name of the thing each job is about, keyed by job id.

    Two lookups for a whole page rather than one per row: a page is fifty jobs and this screen
    refreshes every second. The queries themselves are the kernel's: nothing outside it writes
    SQL against the tables that carry permissions, whatever the route's own authorization says.

    Absent from the result is not an error: a file deleted since its job was queued has no name to
    show, and the row still says what the work was.
    """
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
        presses=presses,
        live=live,
    )
    return families, held, sealed


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
    # Only for a job whose subject really resolved: an id whose file has since gone would be a link
    # to a page that is not there, which is worse than a plain name.
    assets = {
        job_id: asset_id for job_id, asset_id in _subject_assets(jobs).items() if job_id in subjects
    }
    folded = await _folded(queue, database, jobs, subjects, assets, shown) if fold else {}
    return subjects, assets, folded


async def _work_of(
    summary: WorkSummary, work_ahead: WorkAhead, upkeep: frozenset[str]
) -> tuple[dict[str, KindOfWork], Ahead]:
    """Each kind of work's run, from the queue's tally and what the library has left to do.

    Counted from the library (see `WorkAhead`, which counts behind the request and is told first
    when a run has ended, `observe`), so this waits for nothing on nearly every read. The
    remainders, their denominators and the mix by media kind come from one count.
    """
    finished = {
        kind: sum(n for state, n in by_state.items() if state not in _UNFINISHED_STATES)
        for kind, by_state in summary.states.items()
    }
    work_ahead.observe((kind for kind, one in summary.run.items() if one.outstanding > 0), finished)
    counted = await work_ahead.counted()
    ahead, wanted = counted.waiting, counted.wanted
    # Every kind either half knows about. A kind with a counter and no jobs yet still has a number
    # worth drawing (it is exactly the case this exists for), and a kind with jobs and no counter
    # still has its queue.
    work: dict[str, KindOfWork] = {}
    for kind in (set(summary.run) | set(ahead)) - upkeep:
        one = summary.run.get(kind, WorkKind())
        left = ahead.get(kind)
        if left is None:
            # Nothing can count this kind from the library: the folder walk is the real case,
            # since there is no record of a file nobody has seen. The queue is all there is.
            work[kind] = KindOfWork(
                done=one.done,
                outstanding=one.outstanding,
                failed=one.failed,
            )
            continue
        # DONE IS DERIVED FROM WHAT IS LEFT, not counted from the queue (see `run_of`): the queue's
        # window of finished jobs moves forward as the oldest finish.
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
) -> JobsPage:
    # THE BACKGROUND UPKEEP IS NOT LISTED (`unlisted_job_types`): neither rows nor tallies, though
    # a caller naming one by type still reads it, and a family's steps are never upkeep.
    upkeep = unlisted_job_types()
    # AND THE WORK THAT RUNS BY ITSELF AS FILES ARRIVE (`by_itself_job_types`) is left off where
    # it heads its own row, nobody pressed it and it is waiting, running or done. Its failures
    # stay, a step of a run stays folded in the run, and a caller naming its type reads every row.
    unnamed = job_type is None and parent_id is None
    quiet = sorted(by_itself_job_types()) if unnamed else []
    # The whole queue's shape, not the page's: the tallies above the table count everything. Read
    # before the page, because the page's "Older tasks" are the kinds of it no handler claims.
    summary = await queue.work_summary()
    claimed = registered_job_names()
    gone = sorted(kind for kind in summary.states if kind not in claimed and kind not in upkeep)
    # On a page of families a state is the state a family's row SHOWS (`folded`), never a row's
    # own: the tab's list and its number then name the same families.
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
    # WHAT IS STILL TO COME, so a bar has its denominator before the work has been queued: a pass
    # queues a page at a time, so the queue alone would make the total climb while somebody watches.
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
    # Only for the rows that could be in the line, so a page with nothing waiting on it costs
    # nothing at all, and the whole page is asked in one statement rather than one per row.
    places = await queue.positions_of([job.id for job in page.jobs if job.state is JobState.QUEUED])
    families, held, sealed = await _families_and_holds(queue, work, ledger, pool, listed, counted)
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
    """The numbers above the list, in the universe the list draws, with `all` their sum.

    ONE UNIVERSE PER LIST, and the list decides it. A folded page draws families, so its tallies
    are the families by the state each one's row shows (`families`, from the page's own
    statement): a family counts once under All and once under its state, and a step counts
    nowhere on its own. A page of rows draws rows, so its tallies are rows: of the kind asked for,
    of every kind no handler claims (`older`), or of every kind (`rows`, the quiet and the upkeep
    already taken off). Rows counted under a tab whose list draws families read Done and Failed
    together as more than All. Never narrowed by the state being looked at, so each tab keeps its
    number while another is chosen.
    """
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
    """Each top row's family, folded: its steps counted, its one state, its file named once.

    Three statements for the page whatever its size (the counts, the one file each family is
    about, and that file's name) rather than a walk of each family. See `JobQueue.step_counts`.
    """
    counts = await queue.step_counts([job.id for job in tops])
    # Named from the steps only where the top has no subject of its own and its family is small
    # enough to have been counted whole: a family past the cap is a pass over many files.
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


#: What a pass that is not running says, in one sentence each. Written here rather than on the
#: screen because only the server can tell them apart: the night window is the pool's limits going
#: to nought, which a browser cannot see.
#:
#: "Paused." and "Another pass is using the disk." are deliberately not here: this route cannot
#: tell them apart. A family held by the night window and one held by a budget set to nothing are
#: both every type capped at zero, and whether the storage lane is in the way is a fact about
#: `kernel/lanes` that nothing publishes. Either would be a guess that reads as a measurement.
NOTHING_WAITING = "Nothing waiting"
WAITING_FOR_WINDOW = "Waiting for tonight's window."
#: The same sentence with the hour the window opens, when the family declared where to read it
#: (`Switchboard.declare_window`). Without the hour somebody cannot tell ten minutes from ten hours.
WAITING_FOR_WINDOW_AT = "Waiting for tonight's window, which opens at {opens}."
#: A family whose every unfinished job waits for quiet hours: its task is set to "In quiet hours" (or
#: it was pressed "Run during quiet hours") and the range is shut. No hour in it: the range is the install's
#: one setting, drawn with its hour at the top of Tasks, and an hour written here would be in the
#: server's words rather than the reader's clock.
WAITING_FOR_QUIET_HOURS = "Waiting for quiet hours."
#: A chore with work outstanding, none of it running, and some of it parked until somebody gives
#: the password (`WaitingForPassword`): the row's own rows say which key, and the unlock bar asks.
WAITING_FOR_UNLOCK = "Waiting for your password."


async def _held_for_quiet_hours(board: Switchboard, queue: JobQueue | None) -> Mapping[str, int]:
    """What quiet hours are holding back at this moment, by job type: nothing while they are on.

    One read for the page, handed to the passes and to the housekeeping alike, so two rows held the
    same way say the same thing.
    """
    hold = await board.quiet_hold()
    if queue is None or hold.open:
        return {}
    return await queue.held_by_type(hold.types)


def _at_once(pool: WorkerPool | None, job_types: Sequence[str]) -> int:
    """How many workers this family can occupy at once.

    The sum of its types' caps, held under the pool's own worker count, which is what "can
    occupy" means: three types capped at eight each cannot take more than the twelve workers there
    are. A type with no cap can take them all, so it answers with the whole pool.

    One when there is no pool to ask, which is a test rather than an install.
    """
    if pool is None:
        return 1
    workers = pool.concurrency
    limits = pool.limits
    return max(1, min(workers, sum(limits.get(job_type, workers) for job_type in job_types)))


def _held(pool: WorkerPool | None, job_types: Sequence[str]) -> bool:
    """Whether every one of these types is capped at nothing right now.

    Zero is how "only overnight" is expressed (the budget resolves the cap to nought outside its
    hours), so a family whose every type is at zero is held rather than idle, and a screen that
    said "no estimate" over it would be describing a pass that was working perfectly, at night.
    """
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
            # A carrier's work was attributed above, to the families of the products its
            # tasks name, rather than to the family its coordinator is registered under.
            continue
        here = kind.waiting if kind.waiting is not None else kind.left_units
        left += here
        counted_left += here
        outstanding += kind.outstanding
        # DONE AND TOTAL COME FROM THE LIBRARY, not from the run, and that is what makes the
        # bar defined at rest: `total` is the files that want this work whether they have it
        # or not, so a finished library is full rather than empty. A kind nothing can count a
        # total for contributes NOTHING here: a queued run's units are work in flight, and
        # would swell the denominator past the library the moment a Build was pressed.
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
    """One switch for the family, or none.

    A switch is declared per job type because "stop scanning" must not stop probing a file already
    taken in, so a family can hold types with different switches and types with none. Reported
    only where every switched type agrees: "off" over work that is half running would be worse
    than saying nothing.
    """
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
    kinds: Mapping[str, Mapping[str, int]] | None = None,
    presses: Mapping[Family, Presses] | None = None,
    live: Sequence[LiveWork] | None = None,
) -> dict[str, FamilyOfWork]:
    """The long passes, from the registry, each with its estimate and what it is allowed to do.

    `kinds` is what is waiting for each job type by media kind, where a counter can say
    (`WorkAhead.waiting_by_kind`). A family whose priced types have one is priced kind by kind.

    `presses` is the runs somebody pressed over some files (`presses.read_presses`). Given, a
    family whose live work is those presses alone is described by THEM: the amounts are done of
    total of the presses still going, per product, and the time left is their own live rows priced
    at the ledger's pace, never the library's owed files. Beside a pass over the library the row is
    the library's, plus the presses' work that makes something again, which no count of the
    library holds: one sum that is the sum. Not given (a caller with no queue), the row is the
    library's.

    A task's Run now over some library folders is told the same way and drawn by the same rule:
    alone, each line is what it has made of what its folders' files lacked when it was pressed,
    the count the task's dry run over them states and the run carries (`importing.start_runs`),
    and what is left of it is the rest. Beside a pass over the library its files are among the
    library's owed ones, and add nothing.

    `live` is the live rows the presses were read from (`read_presses`); given, the coordinators'
    work is laid at each product's family from it rather than from a second read of the queue.

    What is left for a family is what is left for its kinds, counted from the library where a kind
    can be and from the queue where it cannot: the same number the bar for each kind draws. From
    the queue it is files, not rows: a job's units times what it has not yet done, so a scan
    holding thousands of files weighs thousands.

    The switch and the readiness are the server's, declared where the work is registered (see
    `Switchboard`), so a family added on the server reaches the screen with both.
    """
    grouped: dict[Family, list[str]] = {family: [] for family in LONG_PASSES}
    for job_type, family in registered_families().items():
        if family in grouped:
            grouped[family].append(job_type)
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
    )
    answer: dict[str, FamilyOfWork] = {}
    alone: set[str] = set()
    for family, types in grouped.items():
        answer[family.value], by_presses_alone = await _family(family, types, reads)
        if by_presses_alone:
            alone.add(family.value)
    return not_before_the_read(pictured_in_the_read(answer, alone), alone)


@dataclass(frozen=True, slots=True)
class _Reads:
    """What every family's row is built from, read once for the page."""

    work: Mapping[str, KindOfWork]
    ledger: Ledger | None
    board: Switchboard
    pool: WorkerPool | None
    states: Mapping[str, Mapping[str, int]] | None
    kinds: Mapping[str, Mapping[str, int]] | None
    presses: Mapping[Family, Presses] | None
    switches: Mapping[str, Switch]
    readiness: Mapping[Family, Readiness]
    carried: tuple[dict[Family, float], dict[Family, int], dict[str, Family]]
    carriers: frozenset[str]
    held_rows: Mapping[str, int]


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
    # The family prices itself from its own kinds' finished items, plus a carrier's when that
    # carrier's every live task is this family's: a pressed Smart Search run is priced by its
    # own tasks and is not Identify's sample, though its task type is Identify's.
    priced = [one for one in types if one not in reads.carriers] + [
        one for one, whose in carried_by.items() if whose is family
    ]
    press = None if reads.presses is None else reads.presses.get(family)
    if press is not None:
        press.held = sum(int(reads.held_rows.get(one, 0)) for one in types)
        # A coordinator's live tasks for products the library counts are among the files those
        # counts already hold, so they are not added again.
        library_left = counted_left if parts else left
        left, done, total, parts = _with_presses(press, library_left, done, total, parts)
        if press.alone:
            by_presses_alone = True
            # The mix of what is left is the presses' own files: a carrier's live tasks.
            priced_mix = [one for one in priced if one in reads.carriers]
        else:
            priced_mix = priced
    else:
        priced_mix = priced
    estimate = await _estimate(reads, family, priced, priced_mix, left=left, at_once=at_once)
    on = await _switched_on(reads.board, types, reads.switches)
    state = reads.readiness.get(family)
    ready = True if state is None else state.ready
    # A coordinator's cap is the press's own entitlement, not the family's window; the window
    # holds the family's own types, so those are what "held" asks about.
    own = [one for one in types if one not in reads.carriers]
    held_now = _held(reads.pool, own)
    quiet = _held_for_quiet(
        own,
        outstanding=outstanding,
        carried=carried_outstanding.get(family, 0),
        states=reads.states,
        held_rows=reads.held_rows,
    )
    row = FamilyOfWork(
        label=FAMILY_LABELS[family],
        types=sorted(types),
        on=on,
        ready=ready,
        problem=None if state is None else state.problem,
        quick_seconds=None if estimate is None else estimate.quick_seconds,
        slow_seconds=None if estimate is None else estimate.slow_seconds,
        sample=0 if estimate is None else estimate.items,
        at_once=at_once,
        outstanding=outstanding,
        waiting=round(left),
        done=min(done, total),
        total=total,
        parts=parts,
        reason=_reason(
            left=left,
            outstanding=outstanding,
            on=on,
            ready=ready,
            held=held_now,
            # Asked only of a held family: the hour is the answer to "held until when".
            opens=await reads.board.window_opens(family) if held_now else None,
            quiet=quiet,
        ),
        task=FAMILY_TASKS.get(family),
    )
    return row, by_presses_alone


async def _estimate(
    reads: _Reads,
    family: Family,
    priced: list[str],
    priced_mix: list[str],
    *,
    left: float,
    at_once: int,
) -> Estimate | None:
    """The ledger's estimate of a family's time left, priced kind by kind where a counter can say."""
    mix: dict[str, float] = {}
    for job_type in priced_mix:
        for media_kind, n in (reads.kinds or {}).get(job_type, {}).items():
            mix[media_kind] = mix.get(media_kind, 0.0) + n
    if reads.ledger is None:
        return None
    return await reads.ledger.estimate(
        family, priced, left=left, at_once=at_once, kinds=mix or None
    )


def _with_presses(
    press: Presses, library_left: float, done: int, total: int, parts: list[PartOfWork]
) -> tuple[float, int, int, list[PartOfWork]]:
    """A family's left, done, total and lines with the runs over some files.

    Alone, those runs ARE the row: each line is done of total of the presses still going, plus
    what the runs over some folders have made of what their folders' files lacked, and what is
    left is the presses' live rows and the files still lacking in those folders. Beside the
    library's work the library's lines stand, each with the pressed work that makes something
    again added to it, and so does what is left: the sum of two runs, one line per product. A run
    over some folders adds nothing there, its files being among the library's owed ones. No such
    run going, the library's figures as they are.
    """
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
    """Generate's work arriving from the read, counted as Generate running.

    Every read hands out its file's thumbnail, and its hover clip and strip where Generate runs as
    files arrive, so while files are still being read Generate has work on the way and none of it
    queued yet: at the tail of a first import the row would read "Not started" between the last
    picture of one file and the read of the next. The read's outstanding work is Generate's too.
    """
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


#: The passes whose count of what is left takes in files not read yet (`Product.coming`). Their
#: work on such a file cannot start before the read, so neither can their finish.
_AFTER_THE_READ = (Family.GENERATE, Family.FINGERPRINT)


def not_before_the_read(
    answer: dict[str, FamilyOfWork], alone: Collection[str] = ()
) -> dict[str, FamilyOfWork]:
    """A pass waiting on files that are still being read is not done before the read is.

    Its own price is the pace of its own items (a thumbnail is a fifth of a second), and it has
    no way to see that the files it is counting reach it only as fast as they are read. On a first
    import Generate would say "under a minute" for most of the run and finish minutes later,
    because every file it was waiting for is behind the read, and the read carries the
    fingerprints. So while the read has work, a
    pass after it says at least what the read says, and where the read cannot say, neither can it.
    """
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
            quick = max(after.quick_seconds, reading.quick_seconds)
            slow = max(after.slow_seconds, reading.slow_seconds)
        answer[family.value] = after.model_copy(
            update={"quick_seconds": quick, "slow_seconds": slow}
        )
    return answer


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
    """The work that is not a pass over the library, with the same four columns.

    Its estimate is priced the same way a pass's is (the middle half of what one of these has
    been costing, divided by the workers it can occupy), so the two groups on the screen are one
    piece of arithmetic and cannot come to disagree. What differs is the last column: there is no
    library count that says how far through a duplicate sweep is, so the row says how its last run
    went instead of drawing a bar over a denominator nothing can supply.
    """
    # Whose runs the last column reads: the task's own job type for a chore that is a task, its own
    # type for one that is not. Enrichment's chore is the files' questions (`stash_box_scan`) and
    # its task is the sweep (`stash_box_sweep`), and the last run shown must be the one Tasks shows.
    ran_as = {chore.job_type: _run_type(chore) for chore in HOUSEKEEPING}
    # One statement for every chore rather than one each, and THE SAME FUNCTION the Tasks row reads
    # (`JobQueue.last_finished_runs`), so the two say one run to the second. The job row is the
    # only thing that records WHICH chore ran, since they all share the `other` family in the work
    # ledger.
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
        # Held for quiet hours by the pass's own rule (`_families`): work outstanding, none of it
        # running, and every row of it held while the range is shut.
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


#: WHICH TASK EACH PASS IS, on the Tasks screen, by the task's id. Activity's bar links to that
#: task's row ("Run in Tasks"), where it is run and where when it runs is chosen, rather than
#: being one more door that starts the same work.
#:
#: Named rather than worked out from the registry, because a family and a task are not the same cut:
#: Identify is faces AND watermarks, and Fingerprint is Generate's stash-box fingerprints AND the
#: music task's, so neither is one task and both are left out (their bars open Tasks at the top,
#: which is still the one right place). A test holds every id here to the task registry.
#: The states a job is still going to be worked on in, by their stored spelling: the queue's
#: summary keys its tallies by that spelling.
_UNFINISHED_STATES = frozenset(state.value for state in CANCELABLE_STATES)


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
    """What the product-carrying task types have in flight, laid at each PRODUCT's family.

    A Build's task is typed by its coordinator, so without this a run filtered to Smart Search
    would draw as Identify's work and Smart Search would read "Waiting" while it ran. Each live
    task counts
    once under every family its products belong to (a task making faces and meaning is both
    families' work), and a coordinator's remaining units are shared out over its live rows the
    same way. A product the map does not know stays with its coordinator.

    `live` is the page's one grouped read of the live rows (`JobQueue.live_by_press`), whose lines
    hold the same type, products and count; given, the queue is not asked again.
    """
    left: dict[Family, float] = {}
    outstanding: dict[Family, int] = {}
    # A carrier whose every live task makes products of ONE family: that family may price itself
    # from the carrier's finished items. A run making faces and meaning at once prices nobody,
    # because a task's cost is not one family's.
    single: dict[str, Family] = {}
    if queue is None:
        return left, outstanding, single
    # Counted by the queue, grouped by the products each row names: a run's thousands of tasks
    # name a handful of lists, and reading every payload here would parse each one on the event
    # loop on every read of this screen.
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
) -> str | None:
    """Why this pass is not running, in one sentence, or None while it is.

    In the order somebody would ask it. Switched off first, because a person who turned a pass off
    is owed that answer rather than a sentence about model files. Then whether it can run here at
    all, which the family's own words answer and this does not repeat. Then the night window, then
    nothing left to do, and a pass with work outstanding and none of the above is simply running,
    which has no reason and says none.
    """
    if not on or not ready:
        # Both already have their own words on the family: `on` is drawn as "Switched off" with
        # the way to change it, and `problem` is the feature's own sentence. A third copy here
        # would be a second place for the same answer to be worded differently.
        return None
    if held:
        return WAITING_FOR_WINDOW if opens is None else WAITING_FOR_WINDOW_AT.format(opens=opens)
    if quiet:
        return WAITING_FOR_QUIET_HOURS
    if left <= 0 and outstanding == 0:
        return NOTHING_WAITING
    # Work to do and nothing claimed is NOT a reason: it is a pass nobody has started, and the
    # estimate beside it is exactly what somebody deciding whether to press Run now wants to read.
    return None


@router.get("")
async def list_jobs(
    queue: Annotated[JobQueue, Depends(wiring.queue)],
    database: Annotated[Database | None, Depends(_database)],
    work_ahead: Annotated[WorkAhead, Depends(wiring.work_ahead)],
    ledger: Annotated[Ledger | None, Depends(_ledger)],
    pool: Annotated[WorkerPool | None, Depends(_pool)],
    access: Annotated[Repository | None, Depends(_access)],
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

    The bounds are DECLARED rather than checked in the body, which is the same enforcement and a
    truthful schema. Written out by hand they would be invisible to anything reading what this
    route accepts, and a client generated from the schema would have nothing to respect.

    ONE ROUTE, TWO ANSWERS, chosen by the caller and never guessed: without `fold` a page is rows
    (every step its own row, the file name on each); with it a page is families. A folded page
    filtered by state reads the state the family's row shows (`folded_state`: a family with a
    failed step in it IS a failed family, so folding hides no failure), which puts every family
    under exactly one state. The choice between a failed family and a family with a failed step
    is `folded_state`'s, made once for the row, and a state's tab that listed steps instead would
    count a universe its All does not (`_tallies`).
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
    """The steps folded under one top row, a page at a time, asked for when somebody opens it.

    Every job the top started, however deep, in the order they were handed out: a download's
    probing, then the seven steps it started. Each is a whole row, named, so a step reads on its
    own.

    404 for a job that is not there AND for one that heads no family (it has a parent): its steps
    are its top's, asked of the top. One answer for both, like `retry`, since neither has steps.
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
    """Put a failed job back in the queue, with its attempts reset.

    404 for a job that is not there and for one that cannot be retried alike: a job that is
    already running is not a thing to say "no" to twice.
    """
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
    """Put everything that failed back in the queue.

    Failures come in batches (a fix to how a kind of file is read, a drive that was unplugged and
    is back), and retrying them one row at a time is not something anybody does. Cancelled work is
    left alone: somebody stopped it on purpose.

    Nothing to retry is a success with a zero, not a 404. The button was pressed and the queue now
    holds no failures, which is what was asked for.
    """
    return Retried(retried=await queue.retry_failed())


@router.post("/retry-canceled", dependencies=[Depends(csrf_protect)])
async def retry_canceled_jobs(
    queue: Annotated[JobQueue, Depends(wiring.queue)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> Retried:
    """Start everything that was stopped, again.

    The other half of `cancel-all`: stopping an import that got away is one press, and without
    this the only route back to the same work would be to scan the folders again, which re-walks
    every file to rediscover the ones it already knew about. What was stopped is still in the
    table with its payload; it can simply be offered again.

    Failures are left where they are, and that is not tidiness. Stopped and failed are two
    different situations wearing the same "unfinished" label: one is a decision somebody made and
    is taking back, the other is work that broke and will likely break again more expensively.
    `retry-failed` beside this is the button for the second, and it says so.

    Nothing to start is a success with a zero, the same as retrying nothing.
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
    """Throw away everything that failed.

    The companion to retrying, and the answer to failures that cannot succeed however often they
    are offered again: a graphics card that was not installed at the time, a drive that has gone,
    a run made under settings nobody uses now. Without it the only way to shift them would be to
    retry every one and watch it fail a second time.

    Cancelled and finished work is left alone: one is somebody's decision and the other ages out by
    itself. Nothing to clear is a success with a zero, the same as retrying nothing.
    """
    return Cleared(cleared=await queue.clear_failed())


@router.post("/clear-canceled", dependencies=[Depends(csrf_protect)])
async def clear_canceled_jobs(
    queue: Annotated[JobQueue, Depends(wiring.queue)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> Cleared:
    """Throw away everything that was stopped.

    The pile `clear-failed` does not touch, and the one that actually accumulates: a stop is one
    press that cancels the whole queue, so a library-sized import leaves a library-sized heap of
    stopped rows behind, often beside a handful of failures.

    The rows are only ever swept a week after they were stopped, which is the right pace for
    housekeeping and no answer to somebody looking at a screen made of them today.

    Finished work is left alone. It is the record of what the library actually has, and `retry-
    canceled` beside this is the other thing to do with a stopped job. This is for when the answer
    is that the work is not wanted at all.
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
    """Stop every job that has not finished.

    For the queue that got away: a folder that turned out to hold far more than anybody meant to
    point at, a setting switched on that queued work for the whole library, an import worth hours
    that is no longer wanted. Fifty thousand rows is not something anybody cancels one at a time,
    and without this the only way to stop it is to close the application.

    Running work is stopped along with waiting work, and it has to be. What is in the queue was put
    there by something that is still running (a scan hands out a probing job per file, and each
    of those hands out a thumbnail, a preview and a sprite), so calling off only the waiting rows
    would leave the producer walking and the queue would refill behind the press.

    Nothing is deleted. The rows stay and can be read afterwards, and a file that was taken into
    the library but never looked at is picked up by the next scan of its folder. Nothing to stop is
    a success with a zero, the same as retrying nothing.
    """
    # Told before the queue is, so the runs that end by this press are recorded as stopped by
    # hand rather than as finished.
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

    The press behind the leaf on the sidebar. An admin's, as the queue is: the pool is the whole
    installation's. Answered with the state as asked for; the pool reaches it at its next
    reconfigure, a few seconds later, finishing the task in each retiring worker's hand first.

    Held in memory and not stored: see `kernel.attention` for why a press is a moment and the
    setting is the standing choice. Pressing what is already on is a success that changes nothing.
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
    """What a hover clip built right now would be built from.

    Read per request rather than bound at boot, because the answer has to be the one in force at
    the moment somebody presses the button, not the one that was in force when the server started.
    """
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

    One sweep job rather than one job per file from here, which is the opposite of what the
    thumbnail rebuild beside it does and is deliberate: the work of finding which clips are out of
    date is a query that belongs with the job, and doing it inside a request would hold the
    connection open while a large library is read. The sweep queues one encode per file once it has
    the list, so the dashboard still shows the real work rather than one opaque row.

    `enqueue_when_settled` rather than `enqueue`, so a rebuild asked for in the middle of an import
    waits for the import to stop arriving instead of competing with it for the encoders.
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
    """Make every picture again, for the whole library.

    The answer to a change nothing else can see. A thumbnail is made once, when a file arrives, and
    nothing ever revisits it, so a library that was imported before a sizing was fixed, or on a
    machine whose ffmpeg was producing something wrong, keeps those pictures for ever. A rescan does
    not help: a scan skips any file whose path, size and mtime are unchanged, which is exactly what
    makes a rescan quick.

    One sweep job, not a walk in the request: on a hundred-thousand-file library that would be a
    hundred thousand enqueues on the one write connection, with every job waiting behind them to
    record its progress, a browser holding the request open, and nothing stoppable until it ended.

    The sweep hands out the same per-file rows, paced and cancellable, from a worker instead of
    from a request: the shape `rebuild-previews` next door uses.
    `enqueue_when_settled` for the reason it gives: a rebuild asked for in the middle of an import
    waits for the import to stop arriving rather than competing with it for the encoders.
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
    # The runs this ends are recorded as stopped by hand, the way `cancel_everything` records
    # them, told by the queue before the stop is committed, for the reason it gives.
    canceled = await queue.cancel(
        job_id, on_canceled=None if ledger is None else ledger.stopped_by_hand
    )
    if not canceled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no job to cancel")


# --- what runs on a clock ----------------------------------------------------------------------
#
# Nothing here: the list of every task with its When, and the one door for running one, are
# `/api/tasks` (`slices/tasks/router.py`). History keeps every task's "ran" line for longer than a
# job row's week, and the Tasks row keeps the last run beside the choice.
