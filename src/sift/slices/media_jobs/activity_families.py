# SPDX-License-Identifier: AGPL-3.0-or-later
"""The long passes' rows on Activity: each family's work, time left and why it waits."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import cast

from sift.kernel import lanes
from sift.kernel.content.identity_counts import UNREAD_KINDS
from sift.kernel.content.library import LibraryStore
from sift.kernel.jobs import (
    CANCELABLE_STATES,
    JobQueue,
    JobState,
    WorkerPool,
    WorkKind,
    WorkSummary,
    counted_as,
    registered_families,
    registered_product_carriers,
)
from sift.kernel.jobs.failure_words import in_plain_words
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
from sift.slices.media_jobs.activity_wire import Chore, FamilyOfWork, KindOfWork, PartOfWork
from sift.slices.media_jobs.jobs import PROBE
from sift.slices.media_jobs.pooled import priced_together
from sift.slices.media_jobs.presses import Presses
from sift.slices.media_jobs.router_controls import (
    PAUSED,
    parts_off,
    paused_parts,
    runs,
    switched_off,
)

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
#: The same, with what the share has given Sift over its last minutes of reading.
PACED_BY_SHARE_AT = "Reading is limited by the network share that holds {folders}: {mbps} MB/s."
#: A chore with work outstanding, none of it running, and some of it parked until somebody gives
#: the password (`WaitingForPassword`): the row's own rows say which key, and the unlock bar asks.
WAITING_FOR_UNLOCK = "Waiting for your password."


async def _held_for_quiet_hours(board: Switchboard, queue: JobQueue | None) -> Mapping[str, int]:
    """What quiet hours hold back now, by job type, read once for the page; nothing while they are on."""
    hold = await board.quiet_hold()
    if queue is None or hold.open:
        return {}
    return await queue.held_by_type(hold.types)


def _running_together(
    pool: WorkerPool | None,
    job_types: Sequence[str],
    work: Mapping[str, KindOfWork] | None = None,
) -> int:
    """How many workers this family can occupy: the caps of its types with work added (all of them
    with none), held under the pool's count; one with no pool."""
    if pool is None:
        return 1
    workers = pool.concurrency
    limits = pool.limits
    with_work = [
        one
        for one in job_types
        if work is not None
        and (found := work.get(one)) is not None
        and (found.outstanding > 0 or (found.waiting or 0) > 0)
    ]
    using = with_work or job_types
    return max(1, min(workers, sum(limits.get(job_type, workers) for job_type in using)))


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
        benchmark=benchmark,
        standing=standing or {},
        arriving=arriving or {},
    )
    answer: dict[str, FamilyOfWork] = {}
    alone: set[str] = set()
    standing_of: dict[str, int] = {}
    for family, types in grouped.items():
        answer[family.value], by_presses_alone, standing_of[family.value] = await _family(
            family, types, reads
        )
        if by_presses_alone:
            alone.add(family.value)
    answer = pictured_in_the_read(answer, alone)
    if ledger is not None and pool is not None:
        answer = await priced_together(
            answer,
            work,
            kinds or {},
            ledger,
            pool.concurrency,
            pool_bound,
            alone,
            standing_of,
            counting=bool(unread and unread.uncounted),
        )
    answer = not_before_the_read(answer, alone)
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
    benchmark: bool = False
    standing: Mapping[str, int] = field(default_factory=dict)
    arriving: Mapping[str, Mapping[str, float]] = field(default_factory=dict)


async def _own_work(
    family: Family, types: list[str], reads: _Reads
) -> tuple[list[str], list[str], tuple[float, float, int, int, list[PartOfWork], int]]:
    """The family's sub-tasks switched off, the kinds it is priced by, and its work counted
    without the ones off, whose lines are drawn and counted in nothing."""
    carried_left, carried_outstanding, carried_by = reads.carried
    off = await switched_off(reads.board, types, reads.work)
    counted_types = [one for one in types if one not in off]
    counted = _counted(
        counted_types,
        reads.work,
        reads.carriers,
        left=carried_left.get(family, 0.0),
        outstanding=carried_outstanding.get(family, 0),
    )
    counted[4].extend(parts_off(off, reads.work))
    # Its own kinds' items, plus a carrier's whose every live task is this family's.
    priced = [one for one in counted_types if one not in reads.carriers] + [
        one for one, whose in carried_by.items() if whose is family
    ]
    return off, priced, counted


async def _family(
    family: Family, types: list[str], reads: _Reads
) -> tuple[FamilyOfWork, bool, int]:
    """One long pass's row, whether the presses alone describe it, and its files waiting for their
    task's own run."""
    _left, carried_outstanding, _by = reads.carried
    by_presses_alone = False
    off, priced, (left, counted_left, done, total, parts, outstanding) = await _own_work(
        family, types, reads
    )
    at_once = _running_together(reads.pool, types, reads.work)
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
    paused, parts = paused_parts(reads.pool, types, parts)
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
        paused=paused,
        reason=PAUSED
        if paused
        else _reason(
            left=left,
            outstanding=outstanding,
            on=on,
            ready=ready,
            held=held_now,
            # Asked only of a held family: the hour is the answer to "held until when".
            opens=await reads.board.window_opens(family) if held_now else None,
            quiet=held_by(held_rows=reads.held_rows),
            by_benchmark=reads.benchmark and outstanding > 0 and not running,
        ),
        task=FAMILY_TASKS.get(family),
        runs=runs(family, off),
        running=running,
    )
    return row, by_presses_alone, standing


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
    """A pass after the read is not done before the read is: at least the read's time. Where the
    read cannot say, neither can it, and it says why the read cannot."""
    reading = answer.get(Family.SCAN.value)
    if reading is None or reading.waiting <= 0 or Family.SCAN.value in alone:
        return answer
    for family in _AFTER_THE_READ:
        after = answer.get(family.value)
        if after is None or after.waiting <= 0 or family.value in alone:
            continue
        if after.quick_seconds is None or after.slow_seconds is None:
            continue
        if reading.quick_seconds is None or reading.slow_seconds is None:
            update: dict[str, object] = {
                "quick_seconds": None,
                "slow_seconds": None,
                "time_unknown": reading.time_unknown,
            }
        else:
            update = {
                "quick_seconds": max(after.quick_seconds, reading.quick_seconds),
                "slow_seconds": max(after.slow_seconds, reading.slow_seconds),
            }
        answer[family.value] = after.model_copy(update=update)
    return answer


def not_known_yet(
    answer: dict[str, FamilyOfWork], *, uncounted: int, pace: str | None = None
) -> dict[str, FamilyOfWork]:
    """No time on the Scan row or any pass after it while a folder waits to be counted, but the
    least the files counted so far take where that is priced, and the running read's pace where a
    share sets it."""
    scan = answer.get(Family.SCAN.value)
    if scan is not None and pace is not None and scan.outstanding > 0:
        answer[Family.SCAN.value] = scan.model_copy(update={"pace": pace})
    if uncounted <= 0:
        return answer
    for family in (Family.SCAN, *_AFTER_THE_READ):
        row = answer.get(family.value)
        if row is not None and not (row.at_least and row.quick_seconds):
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
    readings = installed.readings()
    busiest = _SHARE_WAITS.busiest(readings, time.monotonic())
    if busiest is None or library is None:
        return None
    names = sorted(
        root.name
        for root in await library.roots()
        if lanes.storage_for(Path(root.abs_path) / "walk").key == busiest
    )
    if not names:
        return None
    given = readings.get(busiest, {}).get("achieved_mb_per_second")
    if isinstance(given, int | float):
        return PACED_BY_SHARE_AT.format(folders=_joined(names), mbps=f"{given:g}")
    return PACED_BY_SHARE.format(folders=_joined(names))


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
                at_once=_running_together(pool, [chore.job_type]),
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
    if left <= 0 and outstanding == 0:
        return NOTHING_WAITING
    # Work nobody has started is not a reason: its estimate is what somebody pressing Run now reads.
    return None
