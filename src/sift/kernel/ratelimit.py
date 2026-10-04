# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pacing requests to one host, and backing off hard when it says stop.

A token bucket per host, with a shared self-tuning backoff. What it is FOR is not being polite in
the abstract: the accounts and keys Sift uses belong to the person running it, and the failure mode
of asking too fast is that person's own account being restricted, which no retry recovers.

## Why this is in the kernel

It says "host" and not "download" in every name it has, because it knows nothing about
downloading. Two slices call it (the downloader, and the stash-box adapter, which must impose its
own floor because those services publish no rate-limit header of any kind) and two slices may not
import each other.

Each caller brings its own numbers. The intervals, bursts and backoff a site resolver needs are not
the ones a metadata service needs, and the configuration is the caller's business; what is here is
the mechanism.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable

from sift.kernel.numbers import as_float

#: Backoff used when a "too many requests" answer carries no readable `Retry-After`.
DEFAULT_BACKOFF_SEC = 5.0

# When a managed host complains its interval grows by this factor (capped), so requests stop
# re-tripping the limit; after a streak of clean requests it relaxes one step back toward base.
_INTERVAL_GROWTH = 1.5
_INTERVAL_DECAY = 0.8

Clock = Callable[[], float]
Sleep = Callable[[float], Awaitable[None]]


def parse_retry_after(value: str | None) -> float | None:
    """Read a `Retry-After` header. Only the plain seconds form is honoured (the HTTP-date form is
    rare for these JSON APIs) and anything else reads as absent.

    This value is written by somebody else's server, which is the whole reason it is parsed rather
    than character-checked. `isdigit()` is true for characters `float()` refuses (the superscript
    digits among them) so a host answering with one would raise here, letting a remote party
    choose when a download job crashed. A negative wait is dropped for the same reason a missing one
    is: it is not a delay.
    """
    if not value:
        return None
    seconds = as_float(value.strip())
    if seconds is None or seconds < 0:
        return None
    return seconds


class HostRateLimiter:
    """Per-host token-bucket pacing with a shared, self-tuning backoff on a "too many requests"."""

    def __init__(
        self,
        *,
        default_interval: float = 0.0,
        intervals: dict[str, float] | None = None,
        clock: Clock = time.monotonic,
        sleep: Sleep = asyncio.sleep,
        max_interval_factor: float = 3.0,
        decay_after: int = 5,
        bursts: dict[str, int] | None = None,
        default_burst: int = 1,
        jitter_frac: float = 0.0,
        rand: Callable[[], float] = random.random,
    ) -> None:
        self._default = default_interval
        self._intervals = dict(intervals or {})  # configured base refill interval per host
        self._clock = clock
        self._sleep = sleep
        self._max_factor = max_interval_factor
        self._decay_after = decay_after
        self._bursts = dict(bursts or {})  # token-bucket capacity per host (default 1)
        self._default_burst = default_burst
        self._jitter_frac = max(0.0, jitter_frac)
        self._rand = rand
        self._locks: dict[str, asyncio.Lock] = {}
        self._blocked_until: dict[str, float] = {}  # host -> time before which it is hard-blocked
        self._tokens: dict[str, float] = {}  # current tokens per host
        self._updated: dict[str, float] = {}  # last-refill time per host
        self._current: dict[str, float] = {}  # current adapted interval (>= base) per host
        self._clean: dict[str, int] = {}  # clean-acquire streak per host

    def _base_interval(self, host: str) -> float:
        return self._intervals.get(host, self._default)

    def _interval(self, host: str) -> float:
        return self._current.get(host, self._base_interval(host))

    def _burst(self, host: str) -> int:
        return self._bursts.get(host, self._default_burst)

    def _lock(self, host: str) -> asyncio.Lock:
        lock = self._locks.get(host)
        if lock is None:
            lock = self._locks[host] = asyncio.Lock()
        return lock

    def _refill(self, host: str, now: float) -> None:
        """Top up a host's bucket for the time since it was last touched, capped at its burst. A host
        with no spacing (interval 0) is always full."""
        cap = float(self._burst(host))
        refill = self._interval(host)
        if refill <= 0:
            self._tokens[host] = cap
            self._updated[host] = now
            return
        last = self._updated.get(host, now)
        cur = self._tokens.get(host, cap)
        self._tokens[host] = min(cap, cur + (now - last) / refill)
        self._updated[host] = now

    def pace(self, host: str, interval: float) -> None:
        """Set how far apart requests to one host must be, in seconds.

        For a caller whose pace is configuration rather than a constant: a stash-box carries its
        own requests-per-minute, so the number is not known when the limiter is built.

        Only ever WIDENS what a refusal has already widened. `_current` is where a `429` records the
        backed-off interval, and re-declaring the base must not undo that: a box that just asked
        Sift to slow down would otherwise be back at full speed on the next request that mentioned
        its pace.
        """
        self._intervals[host] = interval
        if self._current.get(host, 0.0) < interval:
            self._current.pop(host, None)

    async def acquire(self, host: str) -> None:
        """Wait until a token is free for `host` (and any backoff has cleared), then spend it. The
        host's lock is held across the wait so concurrent callers pace out instead of all firing at
        once; from a full bucket the first `burst` calls return at once, the rest pace out."""
        async with self._lock(host):
            now = self._clock()
            self._refill(host, now)
            refill = self._interval(host)
            tokens = self._tokens[host]
            block_wait = max(0.0, self._blocked_until.get(host, 0.0) - now)
            token_wait = 0.0 if (tokens >= 1.0 or refill <= 0) else (1.0 - tokens) * refill
            if token_wait > 0.0 and self._jitter_frac > 0.0:
                # Symmetric jitter, mean factor 1.0: an irregular beat, the same average spacing.
                # Only the pacing wait is jittered; the backoff below is honoured in full via `max`.
                token_wait *= 1.0 + self._jitter_frac * (2.0 * self._rand() - 1.0)
                token_wait = max(0.0, token_wait)
            wait = max(token_wait, block_wait)
            if wait > 0:
                await self._sleep(wait)
                self._refill(host, self._clock())
            self._tokens[host] -= 1.0
            self._note_clean(host)

    def _note_clean(self, host: str) -> None:
        """Count a clean acquire; after `decay_after` of them relax the host's adapted interval one
        step back toward its base."""
        streak = self._clean.get(host, 0) + 1
        if streak < self._decay_after:
            self._clean[host] = streak
            return
        self._clean[host] = 0
        base = self._base_interval(host)
        cur = self._current.get(host)
        if cur is not None and cur > base:
            self._current[host] = max(base, cur * _INTERVAL_DECAY)

    def note_retry_after(self, host: str, seconds: float) -> None:
        """A host asked us to back off. Hard-block it until the cooldown elapses so every task aimed
        at it waits together, and widen its steady interval a step so the next requests space out."""
        target = self._clock() + max(0.0, seconds)
        self._blocked_until[host] = max(self._blocked_until.get(host, 0.0), target)
        self._widen(host)

    def _widen(self, host: str) -> None:
        """Grow a managed host's steady interval one step (capped) and reset its clean streak. A host
        with no base spacing is left alone: the shared backoff already rides out its rare complaint."""
        self._clean[host] = 0
        base = self._base_interval(host)
        if base <= 0:
            return
        cur = self._current.get(host, base)
        self._current[host] = min(base * self._max_factor, max(cur, base) * _INTERVAL_GROWTH)

    def set_jitter(self, frac: float) -> None:
        """Boot-time override of the pacing jitter fraction (0 disables it)."""
        self._jitter_frac = max(0.0, frac)
