# SPDX-License-Identifier: AGPL-3.0-or-later
"""Time left priced as work: each item at what its type and media kind cost, done by the pool."""

from __future__ import annotations

import math
import statistics
from collections import deque
from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass, field

#: Either side of the estimate: the range is the estimate over this to it times this.
RATIO = 1.6
#: A shown end moves only when the new figure is this far off it ...
MOVE_BY = 0.10
#: ... and has stayed off for this long, in seconds.
MOVE_AFTER = 60.0
#: A pass with work waiting, none running and nothing finished for this long has stopped.
STALLED_AFTER = 120.0
#: The live pace of a run is never read over less than this, in seconds.
PACE_AT_LEAST = 600.0
#: A price is quoted from this many items of a type and kind, over at most the newest `PRICED_OVER`.
FEWEST_PRICED = 10
PRICED_OVER = 200
#: How far outside its words a said range may be from the real finish and still be right.
TOLERANCE = 60.0
#: The price of a type over every kind, for work whose kind nobody counted.
ANY_KIND = ""

Key = tuple[str, str]

#: The words' steps (`frontend/src/lib/shell/when.ts`): up to how long, rounded out to how much.
_STEPS = ((1200.0, 300), (3600.0, 900), (21600.0, 3600), (172800.0, 10800), (math.inf, 86400))


def work(left: Mapping[Key, float], prices: Mapping[Key, float]) -> float | None:
    """Worker seconds of the items left, None where one has no price."""
    total = 0.0
    for (job_type, kind), n in left.items():
        price = prices.get((job_type, kind), prices.get((job_type, ANY_KIND)))
        if price is None:
            return None
        total += n * price
    return total


@dataclass(frozen=True, slots=True)
class Import:
    """What the read and the passes after it have left, and what the pool can do about it."""

    read: Mapping[Key, float]
    passes: Mapping[Key, float]
    prices: Mapping[Key, float]
    workers: int
    #: Jobs of each pass's type queued or running now.
    queued: Mapping[str, int] = field(default_factory=dict)
    #: Worker seconds of work per second the read has done, or None before it has a pace.
    read_pace: float | None = None
    #: False while the read waits on its share's places rather than on the pool's workers.
    pool_bound: bool = True
    read_stalled: bool = False


def seconds_left(job: Import) -> tuple[float | None, float | None]:
    """The read's seconds left and the passes', each None where it cannot be priced."""
    workers = max(1, job.workers)
    passes = work(job.passes, job.prices)
    if not job.read:
        return None, None if passes is None else passes / workers
    read = work(job.read, job.prices)
    own = None if read is None or not job.read_pace else read / job.read_pace
    if own is None or read is None:
        return own, None
    queued = sum(n * job.prices.get((t, ANY_KIND), 0.0) for t, n in job.queued.items()) / workers
    if passes is None:
        # A kind nothing has priced yet: no sooner than the read and what is queued after it.
        return own, own + queued
    every = (read + passes) / workers
    if job.pool_bound:
        own = max(own, every - queued)
    return own, every if job.read_stalled else max(every, own + queued)


#: What a rate is of: items finished, bytes read, or worker seconds of finished work.
ITEMS, BYTES, WORK = 0, 1, 2

#: A family's rate is read over its newest this many seconds of having work due ...
RATE_OVER = 600.0
#: ... and there is none before this much of it, or before this many items finished in it: a
#: batch's first minute is its pipeline filling, measured 40 times slower than its second.
RATE_AFTER = 120.0
RATE_FEWEST = 20
#: Nor while the newest minute goes this many times faster than any before it: still filling.
RISING_AT_MOST = 2.0
#: Minutes older than this are not evidence of the pace now, however little of them was busy.
RATE_MINUTES_HELD = 60
#: A minute is a point of the spread only with this much of it busy.
SPREAD_BUSY_AT_LEAST = 20.0
#: How far either side of the figure the window reaches: one plus the spread of the minutes' rates.
SPREAD_LEAST = 1.25
SPREAD_MOST = 3.0
#: A family idle this long has finished its batch: the next one's pace is its own, so a share's
#: import is not quoted the pace of a local one before it.
BATCH_GAP_SECONDS = 120.0
#: Ticks further apart than this count as this much busy time: the process was not running.
TICK_AT_MOST = 15.0
#: What a row with work due says while no rate of its own has been measured yet.
MEASURING = "Measuring."


@dataclass(frozen=True, slots=True)
class Rate:
    """What a family finished per second of the time it had work due (items, bytes or worker
    seconds), and the spread of its minutes."""

    per_second: float
    spread: float
    items: float
    seconds: float

    def window(self, left: float) -> tuple[float, float]:
        seconds = left / self.per_second
        return seconds / self.spread, seconds * self.spread


class Throughput:
    """One family's finished items and bytes by minute, with the seconds it had work due, across
    its runs: the rate is the work's, not one run's, so a pass that drains and refills keeps it."""

    def __init__(self) -> None:
        # [minute, busy seconds, items, bytes, worker seconds]
        self._minutes: deque[list[float]] = deque(maxlen=RATE_MINUTES_HELD)
        self._worked_at: float | None = None

    def _now(self, now: float) -> list[float]:
        minute = now // 60
        while self._minutes and self._minutes[0][0] <= minute - RATE_MINUTES_HELD:
            self._minutes.popleft()
        if not self._minutes or self._minutes[-1][0] != minute:
            self._minutes.append([minute, 0.0, 0.0, 0.0, 0.0])
        return self._minutes[-1]

    def busy(self, now: float, seconds: float) -> None:
        if self._worked_at is not None and now - self._worked_at > BATCH_GAP_SECONDS:
            self._minutes.clear()
        self._worked_at = now
        self._now(now)[1] += max(0.0, min(seconds, TICK_AT_MOST))

    def done(self, now: float, items: float, nbytes: float = 0.0, worked: float = 0.0) -> None:
        minute = self._now(now)
        minute[2] += items
        minute[3] += nbytes
        minute[4] += worked

    def rate(self, now: float, of: int = ITEMS) -> Rate | None:
        """The rate over the newest busy minutes, or None before there is one to quote."""
        self._now(now)
        busy = items = amount = 0.0
        rates: list[float] = []
        for minute in reversed(self._minutes):
            if busy >= RATE_OVER:
                break
            seconds = minute[1]
            busy += seconds
            items += minute[2]
            amount += minute[2 + of]
            if seconds >= SPREAD_BUSY_AT_LEAST:
                rates.append(minute[2 + of] / seconds)
        if busy < RATE_AFTER or items < RATE_FEWEST or amount <= 0:
            return None
        mean = amount / busy
        if len(rates) >= 2 and rates[0] > RISING_AT_MOST * max(rates[1:]):
            return None
        spread = 1.0 + (statistics.pstdev(rates) / mean if len(rates) >= 2 else 1.0)
        return Rate(mean, min(SPREAD_MOST, max(SPREAD_LEAST, spread)), items, busy)

    def clear(self) -> None:
        self._minutes.clear()
        self._worked_at = None


def band(seconds: float) -> tuple[float, float]:
    return seconds / RATIO, seconds * RATIO


def each_item(
    prices: Mapping[Key, float], job_types: Sequence[str], shares: Mapping[str, float]
) -> float | None:
    """Worker seconds one item of these types costs by the live sample, over the kinds left."""

    def of(kind: str) -> float | None:
        found = [
            price
            for one in job_types
            if (price := prices.get((one, kind), prices.get((one, ANY_KIND)))) is not None
        ]
        return sum(found) / len(found) if found else None

    total = 0.0
    for kind, share in (shares or {ANY_KIND: 1.0}).items():
        price = of(kind)
        if price is None:
            return None
        total += share * price
    return total


def measured(work_seconds: float, rate: float | None, workers: int) -> tuple[int, int]:
    """The window of this much work at the run's measured rate, else spread over the workers."""
    quick, slow = band(work_seconds / (rate or max(1, workers)))
    return int(quick), int(slow)


def window_of(quick: float, slow: float) -> tuple[int, int]:
    """The window the words stand for, rounded out as the screen rounds them."""
    if slow < 60:
        return 0, 60
    if slow <= 300:
        return 0, 300
    step = next(step for up_to, step in _STEPS if slow <= up_to)
    return int(quick // step * step), int(math.ceil(slow / step) * step)


class Steady:
    """A shown range counts down with the clock, and moves only when the figure has stayed off."""

    def __init__(self) -> None:
        self._shown: dict[Hashable, tuple[float, float, float]] = {}
        self._off_since: dict[Hashable, float] = {}

    def show(self, key: Hashable, now: float, quick: float, slow: float) -> tuple[float, float]:
        shown = self._shown.get(key)
        if shown is not None:
            at, was_quick, was_slow = shown
            quick_now = max(0.0, was_quick - (now - at))
            slow_now = max(0.0, was_slow - (now - at))
            off = abs(quick - quick_now) > MOVE_BY * max(quick_now, 1.0) or abs(
                slow - slow_now
            ) > MOVE_BY * max(slow_now, 1.0)
            if not off:
                self._off_since.pop(key, None)
                return quick_now, slow_now
            if now - self._off_since.setdefault(key, now) < MOVE_AFTER and slow_now > 0:
                return quick_now, slow_now
        self._shown[key] = (now, quick, slow)
        self._off_since.pop(key, None)
        return quick, slow

    def forget(self, key: Hashable) -> None:
        self._shown.pop(key, None)
        self._off_since.pop(key, None)


@dataclass(frozen=True, slots=True)
class Said:
    """One minute of what a row said: the window its words stood for, or that it had stopped."""

    at: int
    low: int | None
    high: int | None
    left: int
    stalled: bool = False


def score(said: Sequence[Said], ended: int) -> dict[str, object] | None:
    """How often the words held the real finish, in all and by third of the row's life."""
    if not said:
        return None
    first = said[0].at
    life = max(1, ended - first)
    thirds = [[0, 0], [0, 0], [0, 0]]
    stalled = 0
    for one in said:
        if one.stalled or one.low is None or one.high is None:
            stalled += one.stalled
            continue
        right = one.low - TOLERANCE <= ended - one.at <= one.high + TOLERANCE
        third = thirds[min(2, 3 * (one.at - first) // life)]
        third[0] += 1
        third[1] += right
    timed = sum(n for n, _right in thirds)
    return {
        "minutes": timed,
        "right": round(100 * sum(right for _n, right in thirds) / timed) if timed else None,
        "thirds": [round(100 * right / n) if n else None for n, right in thirds],
        "stalled": stalled,
    }
