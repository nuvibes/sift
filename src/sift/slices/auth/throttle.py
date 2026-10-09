# SPDX-License-Identifier: AGPL-3.0-or-later
"""Rate-limiting where a secret is guessed: a lockout for the PIN, a tarpit for login.

Keyed by the submitted string, real user or not, so neither reveals which names exist."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class _Attempts:
    count: int = 0
    locked_until: float = 0.0


class Throttle:
    """Consecutive-failure lockout, by key; a success clears it."""

    def __init__(
        self,
        *,
        max_failures: int,
        lockout_seconds: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_failures = max_failures
        self._lockout_seconds = lockout_seconds
        self._clock = clock
        self._by_key: dict[str, _Attempts] = {}

    def locked(self, key: str) -> bool:
        """Whether this key is locked out; checked before a secret is looked at."""
        record = self._by_key.get(key)
        if record is None:
            return False
        return bool(record.locked_until) and self._clock() < record.locked_until

    def record_failure(self, key: str) -> None:
        """Count a failed attempt, locking the key once it has failed too many times running."""
        record = self._by_key.setdefault(key, _Attempts())
        # A lapsed lockout is a clean slate, not a hair-trigger.
        if record.locked_until and self._clock() >= record.locked_until:
            record.count = 0
            record.locked_until = 0.0
        record.count += 1
        if record.count >= self._max_failures:
            record.locked_until = self._clock() + self._lockout_seconds

    def record_success(self, key: str) -> None:
        """Clear the count."""
        self._by_key.pop(key, None)


@dataclass
class _Run:
    count: int = 0
    last_at: float = 0.0


class Tarpit:
    """Escalating delay on failures, for login, where a lockout would shut out the admin."""

    def __init__(
        self,
        *,
        grace: int,
        base_delay_seconds: float,
        max_delay_seconds: float,
        forget_after_seconds: float,
        max_keys: int = 4096,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._grace = grace
        self._base = base_delay_seconds
        self._max = max_delay_seconds
        self._forget_after = forget_after_seconds
        self._max_keys = max_keys
        self._clock = clock
        self._by_key: dict[str, _Run] = {}

    def _forgotten(self, run: _Run) -> bool:
        return bool(run.last_at) and self._clock() - run.last_at >= self._forget_after

    def _reap(self) -> None:
        """Keep the map bounded, dropping forgotten runs first, then the least recently seen."""
        if len(self._by_key) <= self._max_keys:
            return
        for key in [k for k, run in self._by_key.items() if self._forgotten(run)]:
            del self._by_key[key]
        while len(self._by_key) > self._max_keys:
            oldest = min(self._by_key, key=lambda k: self._by_key[k].last_at)
            del self._by_key[oldest]

    def delay(self, key: str) -> float:
        """How long the next attempt on this key must wait; a read that changes nothing."""
        run = self._by_key.get(key)
        if run is None or self._forgotten(run):
            return 0.0
        return self._delay_for(run.count)

    def reserve(self, key: str) -> float:
        """Count this attempt as it starts and return its wait, in one step so a burst escalates."""
        run = self._by_key.setdefault(key, _Run())
        if self._forgotten(run):
            run.count = 0
        run.count += 1
        run.last_at = self._clock()
        self._reap()
        return self._delay_for(run.count)

    def _delay_for(self, count: int) -> float:
        over = count - self._grace
        if over <= 0:
            return 0.0
        return min(self._base * 2.0 ** (over - 1), self._max)

    def record_failure(self, key: str) -> None:
        """Count a wrong attempt; a forgotten run starts over from zero."""
        run = self._by_key.setdefault(key, _Run())
        if self._forgotten(run):
            run.count = 0
        run.count += 1
        run.last_at = self._clock()
        self._reap()

    def record_success(self, key: str) -> None:
        """A correct answer ends the run."""
        self._by_key.pop(key, None)


class InFlight:
    """One attempt per key at a time, so a flood is bounded by the delay, not the hashing."""

    def __init__(self) -> None:
        self._keys: set[str] = set()

    def claim(self, key: str) -> bool:
        """Take the key for one attempt, or say it is already taken."""
        if key in self._keys:
            return False
        self._keys.add(key)
        return True

    def release(self, key: str) -> None:
        """Give the key back once the attempt has been judged, however that went."""
        self._keys.discard(key)
