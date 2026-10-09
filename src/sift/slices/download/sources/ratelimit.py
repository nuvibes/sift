# SPDX-License-Identifier: AGPL-3.0-or-later
"""How fast Sift asks each site for a link, and what it does when one says stop.

The limiter is the kernel's; the per-host numbers here are facts about those sites."""

from __future__ import annotations

import asyncio
import time
from urllib.parse import urlsplit

from sift.kernel.ratelimit import (
    DEFAULT_BACKOFF_SEC,
    Clock,
    HostRateLimiter,
    Sleep,
    parse_retry_after,
)
from sift.slices.download.sources.tuning import RunPolicy

# Free middleman APIs with soft caps; instasave's bucket refills in about 4s.
_DEFAULT_INTERVALS: dict[str, float] = {
    "www.tikwm.com": 1.0,
    "tikwm.com": 1.0,
    "instasave.website": 4.5,
    "api.instasave.website": 4.5,
}

# instasave's bucket holds about 3, so a short paste resolves immediately.
_DEFAULT_BURSTS: dict[str, int] = {
    "instasave.website": 3,
    "api.instasave.website": 3,
}

_DEFAULT_JITTER_FRAC = 0.3

LIMITER = HostRateLimiter(
    default_interval=0.0,
    intervals=_DEFAULT_INTERVALS,
    bursts=_DEFAULT_BURSTS,
    jitter_frac=_DEFAULT_JITTER_FRAC,
)


def host_of(url: str) -> str:
    """The host a wait is kept against, lower-cased."""
    return (urlsplit(url).hostname or "").lower()


RATE_LIMIT_CODES: frozenset[str] = frozenset({"http-429", "rate-limited"})

#: Values no tool has an option for, applied around its run instead (`test_policy` reads it).
AROUND_THE_TOOL: frozenset[str] = frozenset({"pacing.wait_after_too_many_requests"})


def note_too_many_requests(
    url: str,
    policy: RunPolicy,
    *,
    retry_after: float | None = None,
    limiter: HostRateLimiter | None = None,
) -> float:
    """Hold `url`'s host for the setting, or the site's longer `Retry-After`; return the wait."""
    wait = max(policy.pacing.wait_after_too_many_requests, retry_after or 0.0)
    (limiter or LIMITER).note_retry_after(host_of(url), wait)
    return wait


def middleman_backoff(measured: float, policy: RunPolicy | None) -> float:
    """A middleman service's measured backoff, raised only by a wait somebody chose."""
    if policy is None or not policy.pacing.wait_chosen:
        return measured
    return max(measured, policy.pacing.wait_after_too_many_requests)


async def wait_out_backoff(url: str, *, limiter: HostRateLimiter | None = None) -> None:
    """Wait until `url`'s host is no longer held; never inside a request's own time budget."""
    await (limiter or LIMITER).acquire(host_of(url))


class Pacer:
    """The wait between one download's requests, start to start, as both tools count it."""

    def __init__(
        self, seconds: float, *, clock: Clock = time.monotonic, sleep: Sleep = asyncio.sleep
    ) -> None:
        self._seconds = max(0.0, seconds)
        self._clock = clock
        self._sleep = sleep
        self._last: float | None = None
        self._lock = asyncio.Lock()

    @classmethod
    def for_policy(cls, policy: RunPolicy) -> Pacer:
        return cls(policy.pacing.seconds_between_requests)

    async def wait(self) -> None:
        """Wait until the pace allows the next request, then mark it as started."""
        if self._seconds <= 0:
            return
        async with self._lock:
            now = self._clock()
            if self._last is not None:
                due = self._last + self._seconds
                if due > now:
                    await self._sleep(due - now)
                    now = self._clock()
            self._last = now


__all__ = [
    "AROUND_THE_TOOL",
    "DEFAULT_BACKOFF_SEC",
    "LIMITER",
    "RATE_LIMIT_CODES",
    "HostRateLimiter",
    "Pacer",
    "host_of",
    "middleman_backoff",
    "note_too_many_requests",
    "parse_retry_after",
    "wait_out_backoff",
]
