# SPDX-License-Identifier: AGPL-3.0-or-later
"""Play a recorded day of a library's work through the ledger and Activity's rows: every job's
start and end as the log had them, each family's counts as the screen read them once a minute,
and the process's restarts. Nothing finished in it, so each time said is scored against the
finish the real work after it points to."""

from __future__ import annotations

import bisect
import gzip
import statistics
from collections.abc import Iterator
from dataclasses import dataclass, field
from itertools import pairwise

from sift.kernel.db import Database
from sift.kernel.jobs import ledger as ledger_module
from sift.kernel.jobs import register_handler, time_left
from sift.kernel.jobs.queue_rows import FilesToRead
from sift.kernel.jobs.switchboard import Switchboard
from sift.slices.media_jobs import pooled
from sift.slices.media_jobs.activity_families import _families
from sift.slices.media_jobs.router import KindOfWork
from sift.slices.media_jobs.tests.replay import DATA, FAMILIES, TICK, WALKS, Clock, _nothing, _Pool

#: Each family's types, the busiest first: where its queued jobs are counted.
TYPES = {
    family: [one for one, whose in FAMILIES.items() if whose.value == family and one not in WALKS]
    for family in ("scan", "generate", "fingerprint", "identify", "semantic")
}
HOUR = 3600.0
#: A job: when it was claimed and ended, its type, its seconds, and whether it was done.
Job = tuple[float, float, str, float, bool]


@dataclass(frozen=True, slots=True)
class Row:
    """One family as the screen read it: its queue, what is left, and what the row said."""

    outstanding: int
    waiting: int
    running: int
    counting: bool
    paced: bool
    quick: int | None
    slow: int | None


@dataclass
class Day:
    starts: list[float]
    #: By end.
    jobs: list[Job]
    rows: dict[float, dict[str, Row]] = field(default_factory=dict)
    #: At each read, each type's files left and done, and its total.
    kinds: dict[float, dict[str, tuple[int, int, int]]] = field(default_factory=dict)

    def ends(self, job_type: str) -> list[float]:
        return [end for _at, end, one, _took, ok in self.jobs if one == job_type and ok]

    def left(self, at: float, family: str) -> dict[str, int]:
        """Each of the family's types' files left, its count grown in proportion to the family's
        own (which held the walk's files not yet read)."""
        row, kinds = self.rows[at].get(family), self.kinds.get(at, {})
        mine = {one: kinds[one][0] for one in TYPES[family] if one in kinds}
        if row is None:
            return {}
        counted = sum(mine.values())
        if not counted:
            return {TYPES[family][0]: row.waiting}
        return {one: round(row.waiting * n / counted) for one, n in mine.items()}


def _int(text: str) -> int | None:
    return None if text in ("", "None") else int(float(text))


def load(name: str) -> Day:
    day = Day([], [])
    end = 0.0
    with gzip.open(DATA / name, "rt") as lines:
        for line in lines:
            part = line.rstrip("\n").split(",")
            if part[0] == "start":
                day.starts.append(float(part[1]))
            elif part[0] == "job":
                end += float(part[2])
                took = int(part[3]) / 10
                day.jobs.append((end - took, end, part[1], took, part[4] == "1"))
            elif part[0] == "family":
                at = float(part[1])
                day.rows.setdefault(at, {})[part[2]] = Row(
                    int(part[3]),
                    int(part[4]),
                    int(part[5]),
                    part[6] == "1",
                    part[7] == "1",
                    _int(part[8]),
                    _int(part[9]),
                )
            elif part[0] == "kind":
                day.kinds.setdefault(float(part[1]), {})[part[2]] = (
                    int(part[3]),
                    int(part[4]),
                    int(part[5] or 0),
                )
    return day


@dataclass(frozen=True, slots=True)
class Said:
    at: float
    family: str
    quick: int | None
    slow: int | None
    at_least: bool
    unknown: str | None
    waiting: int


def _work(day: Day, at: float) -> tuple[dict[str, KindOfWork], dict[str, dict[str, int]]]:
    """Each type's work as the page counted it, its files left grown in proportion to the
    family's own count (which held the walk's files not yet read); the family's queue on its
    busiest type."""
    rows, kinds = day.rows[at], day.kinds.get(at, {})
    work: dict[str, KindOfWork] = {}
    states: dict[str, dict[str, int]] = {}
    for family, row in rows.items():
        left = day.left(at, family)
        busiest = max(left, key=left.__getitem__)
        for one, waiting in left.items():
            _left, done, total = kinds.get(one, (0, 0, 0))
            work[one] = KindOfWork(
                done=done,
                outstanding=row.outstanding if one == busiest else 0,
                failed=0,
                waiting=waiting,
                total=max(total, done + waiting),
            )
        states[busiest] = {"running": row.running}
    return work, states


def _events(day: Day) -> Iterator[tuple[float, str, Job | None]]:
    """Restarts, claims, ends and reads, in order; a tick every `TICK` seconds."""
    events: list[tuple[float, int, str, Job | None]] = []
    events += [(at, 0, "start", None) for at in day.starts[1:]]
    events += [(job[0], 1, "claim", job) for job in day.jobs]
    events += [(job[1], 2, "end", job) for job in day.jobs]
    events += [(at, 3, "read", None) for at in day.rows]
    events.sort(key=lambda one: (one[0], one[1]))
    tick = 0.0
    for at, _order, what, job in events:
        while tick + TICK <= at:
            tick += TICK
            yield tick, "tick", None
        yield at, what, job


async def play(day: Day, database: Database) -> list[Said]:
    """The day through the ledger and `_families`, a fresh ledger and steadying at each restart."""
    for job_type, family in FAMILIES.items():
        register_handler(job_type, _nothing, name=job_type, family=family)
    clock = Clock()
    timed = (vars(ledger_module), vars(pooled))
    kept = [one["time"] for one in timed]
    steady = pooled._STEADY
    for one in timed:
        one["time"] = clock
    said: list[Said] = []
    try:
        await database.initialize_schema()
        book = await _fresh(database)
        latest: float | None = None
        for at, what, job in _events(day):
            clock.now = at
            if what == "start":
                book = await _fresh(database)
            elif what == "claim" and job is not None:
                book.started(job[2])
            elif what == "end" and job is not None:
                _claimed, _end, job_type, took, ok = job
                units = 0 if job_type in WALKS else 1
                book.finished(
                    job_type, duration_ms=took * 1000, ok=ok, media_type="image", units=units
                )
            elif what == "tick" and latest is not None:
                rows = day.rows[latest]
                unfinished = {TYPES[f][0]: row.outstanding for f, row in rows.items()}
                await book.settle(unfinished, settings={})
            elif what == "read":
                latest = at
                said += await _page(day, at, book)
    finally:
        for one, was in zip(timed, kept, strict=True):
            one["time"] = was
        pooled._STEADY = steady
    return said


async def _fresh(database: Database) -> ledger_module.Ledger:
    """What a start of the process holds: the database's runs, and nothing in memory."""
    pooled._STEADY = time_left.Steady()
    book = ledger_module.Ledger(database, families_of=FAMILIES)
    await book.start()
    return book


async def _page(day: Day, at: float, book: ledger_module.Ledger) -> list[Said]:
    rows = day.rows[at]
    work, states = _work(day, at)
    counting = any(row.counting for row in rows.values())
    answer = await _families(
        work,
        book,
        Switchboard(),
        _Pool(23),  # type: ignore[arg-type]
        None,
        states,
        held={},
        kinds={},
        unread=FilesToRead(uncounted=int(counting)),
        pool_bound=not any(row.paced for row in rows.values()),
    )
    return [
        Said(
            at,
            key,
            row.quick_seconds,
            row.slow_seconds,
            row.at_least,
            row.time_unknown,
            row.waiting,
        )
        for key, row in answer.items()
        if key in TYPES and row.waiting > 0
    ]


def recorded(day: Day) -> list[Said]:
    """What the screen itself said at each read."""
    return [
        Said(at, family, row.quick, row.slow, False, None, row.waiting)
        for at, rows in sorted(day.rows.items())
        for family, row in rows.items()
    ]


def _projected(day: Day, ends: dict[str, list[float]], said: Said, over: float) -> float | None:
    """When the work points to finishing from `said.at`: each type's files left at the pace the
    next `over` seconds really went, the family done with its last type, a pass after the read no
    sooner than the read; None where a type with files left finished none."""

    def at_pace(family: str) -> float | None:
        finish = 0.0
        for job_type, left in day.left(said.at, family).items():
            mine = ends[job_type]
            done = bisect.bisect_right(mine, said.at + over) - bisect.bisect_right(mine, said.at)
            if left > 0 and done == 0:
                return None
            finish = max(finish, over * left / done if left else 0.0)
        return finish

    own = at_pace(said.family)
    read = day.rows[said.at].get("scan")
    if own is None or said.family == "scan" or read is None or read.waiting <= 0:
        return own
    first = at_pace("scan")
    return None if first is None else max(own, first)


def scored(day: Day, said: list[Said], over: float | None) -> dict[str, dict[str, float | None]]:
    """Per family: reads with a time, and of those how often the finish the next `over` seconds
    point to (None: to the end of the day) fell inside the window (a floor: above its time), its
    width (slow end over quick end), and how far its quick end's finish moved an hour."""
    ends = {one: day.ends(one) for types in TYPES.values() for one in types}
    last = max(end for _at, end, _type, _took, _ok in day.jobs)
    out: dict[str, dict[str, float | None]] = {}
    for family in TYPES:
        mine = [one for one in said if one.family == family and one.waiting > 0]
        timed = [one for one in mine if one.quick is not None]
        held = n = 0
        widths: list[float] = []
        for one in timed:
            span = last - one.at if over is None else over
            if span < HOUR or one.at + span > last:
                continue
            truth = _projected(day, ends, one, span)
            if truth is None:
                continue
            assert one.quick is not None
            n += 1
            if one.at_least or one.slow is None:
                held += one.quick - time_left.TOLERANCE <= truth
                continue
            held += one.quick - time_left.TOLERANCE <= truth <= one.slow + time_left.TOLERANCE
            widths.append(one.slow / max(1, one.quick))
        moves = [
            abs((b.at + (b.quick or 0)) - (a.at + (a.quick or 0)))
            for a, b in pairwise(timed)
            if b.at - a.at <= 90
        ]
        hours = (timed[-1].at - timed[0].at) / HOUR if len(timed) > 1 else 0
        out[family] = {
            "said": 100 * len(timed) / len(mine) if mine else None,
            "held": 100 * held / n if n else None,
            "width": statistics.median(widths) if widths else None,
            "moved": sum(moves) / HOUR / hours if hours else None,
        }
    return out
