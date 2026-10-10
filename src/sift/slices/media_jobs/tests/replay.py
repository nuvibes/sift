# SPDX-License-Identifier: AGPL-3.0-or-later
"""Play a recorded import through the ledger and Activity's rows on a stand-in clock, and score
the time left each row said against when its work really ended."""

from __future__ import annotations

import gzip
import statistics
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path

# The `ran` event lands in the workbench's table.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.db import Database
from sift.kernel.jobs import ledger as ledger_module
from sift.kernel.jobs import register_handler, time_left
from sift.kernel.jobs.families import Family
from sift.kernel.jobs.holding import Holding
from sift.kernel.jobs.ledger import Ledger
from sift.kernel.jobs.queue_rows import FilesToRead
from sift.kernel.jobs.switchboard import Switchboard
from sift.slices.media_jobs import pooled
from sift.slices.media_jobs.activity_families import _families
from sift.slices.media_jobs.router import KindOfWork

DATA = Path(__file__).parent / "data"
FAMILIES = {
    "probe": Family.SCAN,
    "scan": Family.SCAN,
    "scan_count": Family.SCAN,
    "thumbnail": Family.GENERATE,
    "preview": Family.GENERATE,
    "sprite": Family.GENERATE,
    "fingerprint_file": Family.FINGERPRINT,
    "face_scan": Family.IDENTIFY,
    "watermark_read": Family.IDENTIFY,
    "semantic_describe": Family.SEMANTIC,
}
WALKS = ("scan", "scan_count")
STEP = 10.0
TICK = 3.0


@dataclass(slots=True)
class Job:
    type: str
    kind: str
    queued: float | None
    claimed: float | None
    ended: float | None
    ok: bool


@dataclass
class Recording:
    jobs: list[Job]
    marks: list[tuple[str, float, int]]
    #: Items still to do at the start that its jobs do not hold, by type and kind (a slice of a run).
    left: Counter[tuple[str, str]] = field(default_factory=Counter)

    @property
    def whole(self) -> bool:
        return not self.left


def load(name: str) -> Recording:
    jobs: list[Job] = []
    marks: list[tuple[str, float, int]] = []
    left: Counter[tuple[str, str]] = Counter()
    with gzip.open(DATA / name, "rt") as lines:
        for line in lines:
            part = line.rstrip("\n").split(",")
            if part[0] == "mark":
                marks.append((part[1], int(part[2]) / 1000, int(part[3] or 0)))
            elif part[0] == "left":
                left[(part[1], part[2])] = int(part[3])
            elif part[0] == "job":
                at = [None if not one else int(one) / 1000 for one in part[3:6]]
                jobs.append(Job(part[1], part[2], at[0], at[1], at[2], part[6] == "1"))
    return Recording(jobs, marks, left)


class Clock:
    now = 0.0
    #: Where the recording being played starts, after any played before it.
    offset = 0.0

    def time(self) -> float:
        return 1_800_000_000 + self.offset + self.now

    def monotonic(self) -> float:
        return 1_000_000 + self.offset + self.now


@dataclass
class _Pool:
    concurrency: int
    limits: dict[str, int] = field(default_factory=dict)
    holding: Holding = field(default_factory=Holding)


@dataclass(frozen=True, slots=True)
class Said:
    at: float
    family: str
    quick: int | None
    slow: int | None
    unknown: str | None
    waiting: int
    #: The time is the least the work takes, not a window.
    at_least: bool = False


async def _nothing(_context: object) -> None:
    return None


def _spans(
    record: Recording, on: tuple[str, ...], off: tuple[str, ...]
) -> list[tuple[float, float]]:
    spans, opened = [], None
    for name, at, _workers in record.marks:
        if name in on and opened is None:
            opened = at
        elif name in off and opened is not None:
            spans.append((opened, at))
            opened = None
    return spans + ([(opened, float("inf"))] if opened is not None else [])


async def replay(
    record: Recording,
    database: Database,
    *,
    workers: int,
    pool_bound: Callable[[float], bool] = lambda _at: True,
    after: Recording | None = None,
) -> list[Said]:
    """Every claim and end into the ledger, the pool's tick every 3 s, a page every 10 s; `after`
    is played first, unscored, as the run this machine did before."""
    for job_type, family in FAMILIES.items():
        register_handler(job_type, _nothing, name=job_type, family=family)
    clock = Clock()
    timed = (vars(ledger_module), vars(pooled))
    kept = [one["time"] for one in timed]
    for one in timed:
        one["time"] = clock
    try:
        await database.initialize_schema()
        book = Ledger(database, families_of=FAMILIES)
        await book.start()
        if after is not None:
            await _play(after, book, clock, workers, pool_bound, pages=False)
            await book.settle({}, settings={})
            clock.offset, clock.now = clock.offset + clock.now + 3600, 0.0
        return await _play(record, book, clock, workers, pool_bound)
    finally:
        for one, was in zip(timed, kept, strict=True):
            one["time"] = was


async def _play(
    record: Recording,
    book: Ledger,
    clock: Clock,
    workers: int,
    pool_bound: Callable[[float], bool],
    *,
    pages: bool = True,
) -> list[Said]:
    events = sorted(
        [(job.claimed, 0, job) for job in record.jobs if job.claimed is not None]
        + [(job.ended, 1, job) for job in record.jobs if job.ended is not None],
        key=lambda one: (one[0], one[1]),
    )
    end = max(at for at, _kind, _job in events)
    eco = _spans(record, ("eco_on",), ("eco_off", "full"))
    eco_workers = {at: n for name, at, n in record.marks if name == "eco_on"}
    bench = _spans(record, ("bench_on",), ("bench_off",))
    holds = [at for name, at, _n in record.marks if name == "hold"]
    said: list[Said] = []
    tick, page, index = TICK, STEP, 0
    while page <= end:
        upto = min(tick, page)
        while index < len(events) and events[index][0] <= upto:
            at, kind, job = events[index]
            clock.now = at
            if kind == 0:
                book.started(job.type)
            else:
                took = (at - (job.claimed or at)) * 1000
                units = 0 if job.type in WALKS else 1
                book.finished(
                    job.type, duration_ms=took, ok=job.ok, media_type=job.kind, units=units
                )
            index += 1
        clock.now = upto
        state = _State(record, upto, holds)
        if upto == tick:
            stepped = any(a <= upto < b for a, b in eco)
            await book.settle(state.unfinished(), settings={}, stepped_back=stepped)
            tick += TICK
        if upto == page:
            now_workers = workers
            for a, b in eco:
                if a <= upto < b:
                    now_workers = eco_workers.get(a, workers)
            if pages:
                bench_on = any(a <= upto < b for a, b in bench)
                said += await state.page(book, now_workers, pool_bound(upto), bench_on)
            page += STEP
    return said


class _State:
    """The queue and the library's counts at one moment of the recording."""

    def __init__(self, record: Recording, at: float, holds: list[float]) -> None:
        self.at = at
        self.left: Counter[tuple[str, str]] = Counter(record.left)
        self.active: Counter[str] = Counter()
        self.running: Counter[str] = Counter()
        self.waiting: Counter[str] = Counter()
        self.done: Counter[str] = Counter()
        walking = counting = False
        for job in record.jobs:
            ended = job.ended is not None and job.ended <= at
            claimed = job.claimed is not None and job.claimed <= at
            if job.type in WALKS:
                if claimed and not ended:
                    walking = walking or job.type == "scan"
                if (job.queued is None or job.queued <= at or claimed) and not ended:
                    counting = counting or job.type == "scan_count"
                continue
            if ended:
                self.done[job.type] += 1
                if not record.whole:
                    self.left[(job.type, job.kind)] -= 1
            elif record.whole:
                self.left[(job.type, job.kind)] += 1
            if not ended and (claimed or job.queued is None or job.queued <= at):
                self.active[job.type] += 1
                self.running[job.type] += claimed
                self.waiting[job.type] += not claimed
        self.uncounted = counting
        self.held = walking and any(one <= at for one in holds)

    def unfinished(self) -> dict[str, int]:
        if not self.held:
            return dict(self.active)
        return {t: n - (self.waiting[t] if t != "probe" else 0) for t, n in self.active.items()}

    async def page(self, book: Ledger, workers: int, bound: bool, bench: bool) -> list[Said]:
        kinds: dict[str, dict[str, float]] = {}
        for (job_type, kind), n in self.left.items():
            if n > 0:
                kinds.setdefault(job_type, {})[kind] = float(n)
        work = {
            job_type: KindOfWork(
                done=self.done[job_type],
                outstanding=self.active[job_type],
                failed=0,
                waiting=round(sum(mix.values())),
                total=round(sum(mix.values())) + self.done[job_type],
            )
            for job_type, mix in kinds.items()
        }
        answer = await _families(
            work,
            book,
            Switchboard(),
            _Pool(workers),  # type: ignore[arg-type]
            None,
            {t: {"running": n} for t, n in self.running.items()},
            held={},
            kinds=kinds,
            unread=FilesToRead(uncounted=int(self.uncounted)),
            benchmark=bench,
            pool_bound=bound,
        )
        return [
            Said(
                self.at,
                key,
                row.quick_seconds,
                row.slow_seconds,
                row.time_unknown,
                row.waiting,
                row.at_least,
            )
            for key, row in answer.items()
            if row.waiting > 0
        ]


def ends(record: Recording) -> dict[str, float]:
    """When each row's last job ended."""
    out: dict[str, float] = {}
    for job in record.jobs:
        family = FAMILIES[job.type].value
        if job.type not in WALKS and job.ended is not None:
            out[family] = max(out.get(family, 0.0), job.ended)
    return out


def scored(record: Recording, said: list[Said]) -> dict[str, dict[str, float | None]]:
    """Per row: minutes with a time, words holding the real end (within a minute) overall and by
    third, the numbers holding it, minutes stopped, word changes an hour, and the ends' move a minute."""
    finish = ends(record)
    counted = max(
        (job.ended or 0.0 for job in record.jobs if job.type == "scan_count"), default=0.0
    )
    out: dict[str, dict[str, float | None]] = {}
    for family, end in finish.items():
        mine = [one for one in said if one.family == family and one.at >= counted and one.at < end]
        if not mine:
            continue
        thirds: list[list[int]] = [[0, 0], [0, 0], [0, 0]]
        timed = numbers = stalled = 0
        windows: list[tuple[int, int] | None] = []
        moves: list[float] = []
        before = {one.at: one for one in mine}
        for one in mine:
            if one.unknown == pooled.STALLED:
                stalled += 1
            if one.quick is None or one.slow is None:
                windows.append(None)
                continue
            truth = end - one.at
            low, high = time_left.window_of(one.quick, one.slow)
            windows.append((low, high))
            third = thirds[min(2, int(3 * (one.at - counted) / (end - counted)))]
            # A floor holds while the real finish is no sooner than it.
            if one.at_least:
                high = int(truth + time_left.TOLERANCE)
            right = low - time_left.TOLERANCE <= truth <= high + time_left.TOLERANCE
            third[0] += 1
            third[1] += right
            timed += 1
            numbers += one.quick <= truth <= (truth if one.at_least else one.slow)
            then = before.get(one.at - 60)
            if then is not None and then.slow:
                moves.append(abs(one.slow - (then.slow - 60)) / then.slow)
        changes = sum(1 for a, b in pairwise(windows) if a != b)
        hours = len(mine) * STEP / 3600
        out[family] = {
            "said": 100 * timed / len(mine),
            "words": 100 * sum(t[1] for t in thirds) / timed if timed else None,
            "thirds": [100 * t[1] / t[0] if t[0] else None for t in thirds],  # type: ignore[dict-item]
            "numbers": 100 * numbers / timed if timed else None,
            "stalled": stalled * STEP / 60,
            "changes_an_hour": changes / hours,
            "move": 100 * statistics.median(moves) if moves else None,
        }
    return out


def horizon(record: Recording, said: list[Said], family: str, items: int) -> float | None:
    """For a slice of a run: how often the next `items` items took a time inside the said range,
    scaled from what was left."""
    types = {t for t, f in FAMILIES.items() if f.value == family and t not in WALKS}
    ended = sorted(j.ended for j in record.jobs if j.type in types and j.ended is not None)
    hit = n = 0
    for one in said:
        if one.family != family or one.quick is None or one.slow is None or not one.waiting:
            continue
        done = sum(1 for at in ended if at <= one.at)
        if done + items > len(ended):
            continue
        took = ended[done + items - 1] - one.at
        n += 1
        hit += one.quick * items / one.waiting <= took <= one.slow * items / one.waiting
    return 100 * hit / n if n else None


def steadiness(said: list[Said], family: str) -> dict[str, float | None]:
    """Minutes with a time, word changes an hour, and the slow end's move a minute beyond the clock."""
    mine = [one for one in said if one.family == family]
    windows = [
        None if one.quick is None or one.slow is None else time_left.window_of(one.quick, one.slow)
        for one in mine
    ]
    before = {one.at: one for one in mine}
    moves = [
        abs(one.slow - (then.slow - 60)) / then.slow
        for one in mine
        if one.slow and (then := before.get(one.at - 60)) is not None and then.slow
    ]
    return {
        "said": 100 * sum(one is not None for one in windows) / len(mine) if mine else None,
        "changes_an_hour": sum(1 for a, b in pairwise(windows) if a != b)
        / (len(mine) * STEP / 3600)
        if mine
        else None,
        "move": 100 * statistics.median(moves) if moves else None,
    }
