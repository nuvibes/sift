# SPDX-License-Identifier: AGPL-3.0-or-later
"""The read's and the passes' time left priced together over the pool (`time_left`), held steady."""

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
#: A pass whose every unfinished job a big read keeps back until its reads end.
WAITING_FOR_THE_SCAN = "Waiting for the scan to finish."

_STEADY = time_left.Steady()
#: The read's last estimate, which sets how far back its pace is read.
_READ_SECONDS: dict[Family, float] = {}


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
    alone: Collection[str],
) -> dict[str, FamilyOfWork]:
    """Every row from the read on priced as one import, a stopped row said so, each kept."""
    families = [one for one in LONG_PASSES if one.value in answer]
    rows = {one: answer[one.value] for one in families}
    stopped = {one for one, row in rows.items() if _stopped(row, ledger, one)}
    prices = ledger.prices()
    passes = [row for one, row in rows.items() if one is not Family.SCAN]
    reading = Family.SCAN in rows and rows[Family.SCAN].waiting > 0
    life = ledger.life(Family.SCAN) or 0.0
    within = max(time_left.PACE_AT_LEAST, min(life, _READ_SECONDS.get(Family.SCAN, 0.0)))
    # A sub-task switched off never runs, so its waiting files have no price to wait for.
    off = {part.type for row in passes for part in row.parts if not part.on}
    pass_types = [one for row in passes for one in row.types if one in work and one not in off]
    job = Import(
        read=_left([PROBE], work, kinds) if reading else {},
        passes=_left(pass_types, work, kinds),
        prices=prices,
        workers=workers,
        queued={one: work[one].outstanding for one in pass_types},
        read_pace=ledger.realized(Family.SCAN, prices, within),
        pool_bound=pool_bound,
        held=any(row.reason == WAITING_FOR_THE_SCAN for row in passes),
        read_stalled=Family.SCAN in stopped,
    )
    read_seconds, after_seconds = time_left.seconds_left(job)
    if read_seconds is not None:
        _READ_SECONDS[Family.SCAN] = read_seconds
    now = time.monotonic()
    for family, row in rows.items():
        seconds = read_seconds if family is Family.SCAN else after_seconds
        if family in stopped:
            _STEADY.forget(family)
            answer[family.value] = row.model_copy(
                update={"quick_seconds": None, "slow_seconds": None, "time_unknown": STALLED}
            )
            await ledger.said(family, None, None, None, row.waiting)
            continue
        usable = row.time_unknown is None and row.for_task is None and family.value not in alone
        held = row.reason not in (None, WAITING_FOR_THE_SCAN)
        if seconds is None or row.waiting <= 0 or not usable or held:
            _STEADY.forget(family)
            continue
        quick, slow = _STEADY.show(family, now, *time_left.band(seconds))
        answer[family.value] = row.model_copy(
            update={"quick_seconds": int(quick), "slow_seconds": int(slow), "at_least": False}
        )
        await ledger.said(
            family, int(quick), int(slow), time_left.window_of(quick, slow), row.waiting
        )
    return answer
