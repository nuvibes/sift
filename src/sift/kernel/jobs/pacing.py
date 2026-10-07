# SPDX-License-Identifier: AGPL-3.0-or-later
"""What one item of a family's work costs, from a sample of `(seconds per item, items)` pairs."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from sift.kernel.db import Row
from sift.kernel.jobs.run_records import RunRecord

#: Under this many items a sample is not a pace.
FEWEST_ITEMS = 20

#: How many items one stretch of the sample holds. The range is quoted between the cheapest and
#: the dearest stretch's mean: what the rest will cost is a SUM of many items, which lands near
#: their mean, and the mean moves with what the files are: a stretch of photographs, a stretch of
#: long videos. Quartiles of single items are the wrong spread for a sum: a pass whose items are
#: mostly cheap and sometimes very dear costs more than its third quarter says, every time.
STRETCH_ITEMS = 10


@dataclass(frozen=True, slots=True)
class Pace:
    """What one item of a family's work costs on this machine: the mean, and how far it moves."""

    items: int
    """How many items the sample covers. Under `FEWEST_ITEMS` there is no pace at all."""
    quick: float
    """Seconds per item over the cheapest stretch of the sample (the cheapest run, from runs)."""
    middle: float
    """Seconds per item over the whole sample: the mean, weighted by items."""
    slow: float
    """Seconds per item over the dearest stretch of the sample (the dearest run, from runs)."""
    from_items: bool
    """True when every item was timed on its own, False when the sample is runs' own means. A
    sample of runs has a spread only where there is more than one run, so a single run answers
    with three equal figures and the screen draws one number rather than a range."""


@dataclass(frozen=True, slots=True)
class Estimate:
    """How long a family's remaining work will take, between two honest bounds."""

    quick_seconds: int
    slow_seconds: int
    items: int
    """The size of the sample it was priced from, so a screen can say how much it rests on."""
    at_once: int
    """The number of workers this family can occupy, which the estimate divides by. The same
    library on the same machine with this halved takes about twice as long, so it travels with
    the figure rather than being assumed by whoever reads it."""
    floor: bool = False
    """The least it takes: the benchmark's price, which counts only the models and the frames."""


@dataclass(frozen=True, slots=True)
class KindPrices:
    """What one file of each kind costs this family, and how many workers its runs kept busy."""

    paces: dict[str, Pace]
    """Worker seconds per item, by media kind (`video`, `image`, `gif`). A kind is absent when the
    runs looked at hold fewer than `FEWEST_ITEMS` of it."""
    busy: float | None
    """Worker seconds per wall second over the runs big enough to fill the pool: how many workers
    the work really kept going, which is fewer than the pool when one kind of work is capped or a
    file is read by one process. None where no run was that big."""


def priced(sample: Sequence[tuple[float, int]]) -> Pace | None:
    """A pace from `(seconds per item, items)` pairs, newest first, or None under `FEWEST_ITEMS`.

    The spread is the cheapest and the dearest stretch of `STRETCH_ITEMS`, so a run of three files
    is not one end of a range on its own, and never narrower than the mean: the oldest few items,
    too few for a stretch of their own, still move the mean, and a range that leaves out its own
    middle is not a range.
    """
    items = sum(weight for _cost, weight in sample)
    if items < FEWEST_ITEMS:
        return None
    stretches = _stretches(sample, STRETCH_ITEMS)
    middle = _mean(sample)
    return Pace(
        items=items,
        quick=min(*stretches, middle),
        middle=middle,
        slow=max(*stretches, middle),
        from_items=False,
    )


def _shares(kinds: Mapping[str, float] | None) -> dict[str, float]:
    """What is left by kind, as fractions of the whole. Empty when nothing says."""
    wanted = {kind: float(n) for kind, n in (kinds or {}).items() if n > 0}
    total = sum(wanted.values())
    return {kind: n / total for kind, n in wanted.items()} if total > 0 else {}


def _quantile(sample: Sequence[tuple[float, int]], at: float) -> float:
    """One quantile of `(cost, weight)` pairs, the weights counting items.

    Weighted rather than plain, because one row is not one item: a scan's walk carries thousands
    of files at one cost, and counting it as a single observation would let a handful of walks
    outvote every file they found. The pairs need not be sorted; nearest-rank, so the answer is
    always a cost that was actually measured rather than an average of two that were not.
    """
    ordered = sorted(sample)
    total = sum(weight for _cost, weight in ordered)
    wanted = at * total
    seen = 0
    # Every cost but the dearest is asked whether the rank falls on it; the dearest is where the
    # rank lands when none of them holds it, which for `at` up to 1 is always so by then.
    for cost, weight in ordered[:-1]:
        seen += weight
        if seen >= wanted:
            return cost
    return ordered[-1][0]


def _mean(sample: Sequence[tuple[float, int]]) -> float:
    """The mean cost of `(cost, weight)` pairs, the weights counting items."""
    total = sum(weight for _cost, weight in sample)
    return sum(cost * weight for cost, weight in sample) / total if total else 0.0


def _stretches(sample: Sequence[tuple[float, int]], size: int) -> list[float]:
    """The mean cost of each run of `size` items, in the order the sample is in (newest first).

    A last run shorter than `size` is the oldest end of the sample and is left out of the spread;
    a sample too small for one whole run is one stretch.
    """
    means: list[float] = []
    held: list[tuple[float, int]] = []
    count = 0
    for cost, weight in sample:
        held.append((cost, weight))
        count += weight
        if count >= size:
            means.append(_mean(held))
            held, count = [], 0
    return means or [_mean(sample)]


def _price_row(
    family: str, profile: str, kind: str, pace: Pace | None, busy: list[list[int]] | None = None
) -> tuple[object, ...]:
    figures = (None,) * 4 if pace is None else (pace.items, pace.quick, pace.middle, pace.slow)
    made = int(pace is not None and pace.from_items)
    return (family, profile, kind, *figures, made, json.dumps(busy or []))


def _pace_of_runs(runs: Iterable[RunRecord], since: int) -> Pace | None:
    """The pace of a family's newest unstopped runs, each its own mean, since the settings moved."""
    sample = [
        (run.worker_ms / 1000 / run.files_total, run.files_total)
        for run in runs
        if run.started_at >= since and run.files_total > 0 and run.worker_ms > 0
    ]
    if sum(weight for _cost, weight in sample) < FEWEST_ITEMS:
        return None
    return Pace(
        items=sum(weight for _cost, weight in sample),
        quick=min(cost for cost, _weight in sample),
        middle=_mean(sample),
        slow=max(cost for cost, _weight in sample),
        from_items=False,
    )


def _kept_pace(row: Row | None) -> Pace | None:
    if row is None or row["items"] is None:
        return None
    return Pace(
        int(row["items"]),
        float(row["quick"]),
        float(row["middle"]),
        float(row["slow"]),
        from_items=bool(row["from_items"]),
    )


def _kept_kind_prices(kept: Mapping[str, Row], at_once: int) -> KindPrices:
    """`Ledger.kind_prices` from kept rows: how busy the runs kept the pool is reckoned here,
    because it turns on how many workers there are now."""
    paces = {kind: row for kind, row in kept.items() if kind not in ("", "*")}
    busy_ms = wall_ms = 0
    for files, worker_ms, seconds in json.loads(str(kept[""]["runs"])) if "" in kept else []:
        if seconds and files >= 2 * max(1, at_once):
            busy_ms += worker_ms
            wall_ms += seconds * 1000
    return KindPrices(
        paces={kind: found for kind, row in paces.items() if (found := _kept_pace(row))},
        busy=busy_ms / wall_ms if wall_ms > 0 else None,
    )
