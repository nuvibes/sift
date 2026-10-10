# SPDX-License-Identifier: AGPL-3.0-or-later
"""Time left on the read and each pass after it: each its own work at its own measured pace, a pass
taking its share of the read's workers once the read ends, and none sooner than the read."""

from __future__ import annotations

import time
from collections.abc import Collection, Iterable, Mapping

from sift.kernel.jobs import time_left
from sift.kernel.jobs.families import LONG_PASSES, Family
from sift.kernel.jobs.ledger import Ledger
from sift.kernel.jobs.time_left import Import, Key
from sift.slices.media_jobs.activity_wire import FamilyOfWork, KindOfWork
from sift.slices.media_jobs.job_types import PROBE

#: What a row says in place of a time while it has stopped.
STALLED = "Waiting for other work."

_STEADY = time_left.Steady()


def _left(
    types: Iterable[str], work: Mapping[str, KindOfWork], kinds: Mapping[str, Mapping[str, float]]
) -> dict[Key, float]:
    """Each type's items left, shared out by the media kinds its count gave."""
    left: dict[Key, float] = {}
    for job_type in types:
        one = work.get(job_type)
        if one is None or not one.waiting:
            continue
        mix = {kind: n for kind, n in kinds.get(job_type, {}).items() if n > 0}
        total = sum(mix.values())
        for kind, n in mix.items() if total else ((time_left.ANY_KIND, 1.0),):
            left[(job_type, kind)] = one.waiting * n / (total or 1.0)
    return left


def _pace(ledger: Ledger, family: Family, worked: float | None) -> float | None:
    """The worker seconds a second a family has measured itself getting, not the pool's count of
    workers, which it shares; read over a span that grows with the time its work takes."""
    rate = ledger.rate(family, time_left.WORK)
    if rate is None or worked is None:
        return None if rate is None else rate.per_second
    longer = ledger.rate(family, time_left.WORK, time_left.rate_over(worked / rate.per_second))
    return (longer or rate).per_second


def _stopped(row: FamilyOfWork, ledger: Ledger, family: Family) -> bool:
    gone = ledger.stopped_for(family)
    return (
        row.outstanding > 0
        and row.running == 0
        and row.reason is None
        and gone is not None
        and gone >= time_left.STALLED_AFTER
    )


def _own_work(
    rows: Mapping[Family, FamilyOfWork],
    work: Mapping[str, KindOfWork],
    kinds: Mapping[str, Mapping[str, float]],
    standing: Mapping[str, int] | None,
) -> tuple[dict[Family, dict[Key, float]], dict[Key, float], dict[str, int]]:
    """Each pass's own items left less its task's files, all of them together, and their queues."""
    passes: dict[Key, float] = {}
    queued: dict[str, int] = {}
    own: dict[Family, dict[Key, float]] = {}
    for family, row in rows.items():
        if family is Family.SCAN:
            continue
        # A sub-task switched off never runs, so its waiting files have no price to wait for.
        off = {part.type for part in row.parts if not part.on}
        types = [one for one in row.types if one in work and one not in off]
        left = _left(types, work, kinds)
        whole = sum(left.values())
        share = max(0.0, whole - (standing or {}).get(family.value, 0)) / whole if whole else 0.0
        own[family] = {key: n * share for key, n in left.items()}
        for key, n in own[family].items():
            passes[key] = passes.get(key, 0.0) + n
        queued.update({one: work[one].outstanding for one in types})
    return own, passes, queued


def _seconds(
    family: Family,
    ledger: Ledger,
    job: Import,
    read_seconds: float | None,
    priced: Mapping[Family, tuple[float, bool]],
    counting: bool,
) -> tuple[float | None, bool]:
    """A row's seconds left and whether they are the least it takes."""
    seconds, at_least = read_seconds, counting
    if family is not Family.SCAN:
        every = sum(worked for worked, _whole in priced.values())
        worked, whole = priced.get(family, (0.0, True))
        pace = _pace(ledger, family, worked)
        seconds = None if pace is None or not worked else worked / pace
        if read_seconds is not None and job.read_pace and worked:
            # The read's workers go to the passes when it ends, each its share by its work.
            freed = job.read_pace * worked / every
            seconds = time_left.after_the_read(worked, pace or 0.0, read_seconds, freed)
        at_least = counting or not whole
    return seconds, at_least


async def _said(
    answer: dict[str, FamilyOfWork],
    family: Family,
    row: FamilyOfWork,
    ledger: Ledger,
    seconds: float | None,
    at_least: bool,
    now: float,
) -> None:
    """The row's time, steadied, and the window kept for its run's score; a floor is not kept."""
    if seconds is None:
        _STEADY.forget(family)
        answer[family.value] = row.model_copy(
            update={
                "quick_seconds": None,
                "slow_seconds": None,
                "at_least": False,
                "time_unknown": time_left.MEASURING,
            }
        )
        return
    quick, slow = _STEADY.show(family, now, *time_left.band(seconds))
    if at_least:
        answer[family.value] = row.model_copy(
            update={"quick_seconds": int(quick), "slow_seconds": int(quick), "at_least": True}
        )
        return
    answer[family.value] = row.model_copy(
        update={"quick_seconds": int(quick), "slow_seconds": int(slow), "at_least": False}
    )
    await ledger.said(family, int(quick), int(slow), time_left.window_of(quick, slow), row.waiting)


async def priced_together(
    answer: dict[str, FamilyOfWork],
    work: Mapping[str, KindOfWork],
    kinds: Mapping[str, Mapping[str, float]],
    ledger: Ledger,
    workers: int,
    pool_bound: bool,
    alone: Collection[str] = (),
    standing: Mapping[str, int] | None = None,
    counting: bool = False,
) -> dict[str, FamilyOfWork]:
    """Each row at its own pace measured across its runs, else "Measuring."; `alone` rows keep their
    own, `standing` files wait for their task. While a walk is `counting`, each time is the least
    the files counted so far take."""
    families = [one for one in LONG_PASSES if one.value in answer]
    rows = {one: answer[one.value] for one in families}
    stopped = {one for one, row in rows.items() if _stopped(row, ledger, one)}
    prices = ledger.prices()
    reading = Family.SCAN in rows and rows[Family.SCAN].waiting > 0
    own, passes, queued = _own_work(rows, work, kinds, standing)
    read = _left([PROBE], work, kinds) if reading else {}
    job = Import(
        read=read,
        passes=passes,
        prices=prices,
        workers=workers,
        queued=queued,
        read_pace=_pace(ledger, Family.SCAN, time_left.work(read, prices)),
        pool_bound=pool_bound,
    )
    read_seconds = time_left.read_seconds(job) if reading and Family.SCAN not in stopped else None
    # A kind nothing has priced yet is left out: what the rest takes is the least the pass can.
    priced = {family: time_left.priced_part(mine, prices) for family, mine in own.items()}
    now = time.monotonic()
    for family, row in rows.items():
        seconds, at_least = _seconds(family, ledger, job, read_seconds, priced, counting)
        if family in stopped:
            _STEADY.forget(family)
            answer[family.value] = row.model_copy(
                update={"quick_seconds": None, "slow_seconds": None, "time_unknown": STALLED}
            )
            await ledger.said(family, None, None, None, row.waiting)
            continue
        unqueued = min(row.waiting, row.outstanding) <= 0
        if unqueued or family.value in alone or row.time_unknown or row.reason:
            _STEADY.forget(family)
            continue
        await _said(answer, family, row, ledger, seconds, at_least, now)
    return answer
