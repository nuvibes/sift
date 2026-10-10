# SPDX-License-Identifier: AGPL-3.0-or-later
"""The read's and the passes' time left priced together over the pool at the read's measured pace."""

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


def _by_itself(
    family: Family, left: Mapping[Key, float], prices: Mapping[Key, float], ledger: Ledger
) -> float | None:
    """A pass with no read ahead of it: its work at the worker seconds a second it has measured
    itself getting, not the pool's count of workers, which it may share."""
    worked = time_left.work(left, prices)
    rate = ledger.rate(family, time_left.WORK)
    return None if worked is None or rate is None else worked / rate.per_second


def _stopped(row: FamilyOfWork, ledger: Ledger, family: Family) -> bool:
    gone = ledger.stopped_for(family)
    return (
        row.outstanding > 0
        and row.running == 0
        and row.reason is None
        and gone is not None
        and gone >= time_left.STALLED_AFTER
    )


async def priced_together(
    answer: dict[str, FamilyOfWork],
    work: Mapping[str, KindOfWork],
    kinds: Mapping[str, Mapping[str, float]],
    ledger: Ledger,
    workers: int,
    pool_bound: bool,
    alone: Collection[str] = (),
    standing: Mapping[str, int] | None = None,
) -> dict[str, FamilyOfWork]:
    """The read and the passes as one import over the pool at the read's pace measured across its
    runs, else "Measuring."; `alone` rows keep their own, `standing` files wait for their task."""
    families = [one for one in LONG_PASSES if one.value in answer]
    rows = {one: answer[one.value] for one in families}
    stopped = {one for one, row in rows.items() if _stopped(row, ledger, one)}
    prices = ledger.prices()
    reading = Family.SCAN in rows and rows[Family.SCAN].waiting > 0
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
    pace = ledger.rate(Family.SCAN, time_left.WORK)
    job = Import(
        read=_left([PROBE], work, kinds) if reading else {},
        passes=passes,
        prices=prices,
        workers=workers,
        queued=queued,
        read_pace=None if pace is None else pace.per_second,
        pool_bound=pool_bound,
        read_stalled=Family.SCAN in stopped,
    )
    read_seconds, after_seconds = time_left.seconds_left(job)
    now = time.monotonic()
    for family, row in rows.items():
        seconds = (read_seconds if family is Family.SCAN else after_seconds) if reading else None
        if not reading and family is not Family.SCAN:
            seconds = _by_itself(family, own.get(family, {}), prices, ledger)
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
            continue
        quick, slow = _STEADY.show(family, now, *time_left.band(seconds))
        answer[family.value] = row.model_copy(
            update={"quick_seconds": int(quick), "slow_seconds": int(slow), "at_least": False}
        )
        await ledger.said(
            family, int(quick), int(slow), time_left.window_of(quick, slow), row.waiting
        )
    return answer
