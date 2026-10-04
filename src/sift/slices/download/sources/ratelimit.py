# SPDX-License-Identifier: AGPL-3.0-or-later
"""How fast Sift asks each site for a link, and what it does when one says stop.

The limiter itself is `sift.kernel.ratelimit`, which more than one caller uses. What is here is this
slice's own configuration of it (which hosts are paced, how hard, and how big a burst each one
tolerates), because those numbers are facts about those sites and about nothing else.

`HostRateLimiter`, `parse_retry_after` and `DEFAULT_BACKOFF_SEC` are re-exported for the resolvers
that take them from this module.
"""

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

# tikwm and instasave are free middleman APIs with soft caps; everything else is unpaced (low-volume,
# diverse pastes). The backoff still applies to any host that reports one.
_DEFAULT_INTERVALS: dict[str, float] = {
    "www.tikwm.com": 1.0,
    "tikwm.com": 1.0,
    # instasave's real limit is a token bucket (measured refill ~4s); 4.5s leaves clear margin. The
    # burst below lets a small paste through at once before the pacing takes over.
    "instasave.website": 4.5,
    "api.instasave.website": 4.5,
}

# Token-bucket capacity per host (default 1 = plain min-interval spacing). instasave's limit really is
# a bucket (measured ~3), so a one-to-three link paste resolves at once and only the fourth request
# waits the refill; everything else stays at 1.
_DEFAULT_BURSTS: dict[str, int] = {
    "instasave.website": 3,
    "api.instasave.website": 3,
}

# Default pacing jitter (+/-30%) for every paced host, an irregular beat at no net time cost.
_DEFAULT_JITTER_FRAC = 0.3

#: Process-wide limiter shared by the resolvers.
LIMITER = HostRateLimiter(
    default_interval=0.0,
    intervals=_DEFAULT_INTERVALS,
    bursts=_DEFAULT_BURSTS,
    jitter_frac=_DEFAULT_JITTER_FRAC,
)


# --- the download settings' side of this: the wait between requests, and the wait after a refusal
#
# The two waits the Downloads settings name, applied to what Sift asks a Site ITSELF. The tools are
# told the same two numbers on their command lines (`argv.CONCERNS`); everything below is the half
# that reaches the Sites Sift reads and fetches on its own, which is sixteen of them: TikTok,
# Instagram, Reddit's pictures, GoonBox and every file host.


def host_of(url: str) -> str:
    """The host a wait is kept against. Lower-cased, so two spellings of one host are one host."""
    return (urlsplit(url).hostname or "").lower()


#: What a "too many requests" answer is called once it has been read: the status as the failure
#: reader codes it, and the reader's own code for a site that says it in words. Named here because
#: this module is what acts on them, and `test_ratelimit` holds both to what `failures` really says.
RATE_LIMIT_CODES: frozenset[str] = frozenset({"http-429", "rate-limited"})

#: The policy values Sift applies AROUND a tool's run rather than on its command line, because the
#: tool has no option for them. Read by the gate in `test_policy`: a value a tool cannot be told
#: must be named here, or it reaches that tool nowhere and nothing says so.
#:
#: yt-dlp has no "wait this long after a 429": it answers one through its own retry handling. What
#: Sift can do from outside is what a person would: not start that Site again until the wait is
#: over. A run that is refused marks the Site's host (`note_too_many_requests`) and every later run
#: or request to that host waits it out first (`wait_out_backoff`), whichever downloader it goes
#: through. The run itself cannot be paused from outside; the next one can be held.
AROUND_THE_TOOL: frozenset[str] = frozenset({"pacing.wait_after_too_many_requests"})


def note_too_many_requests(
    url: str,
    policy: RunPolicy,
    *,
    retry_after: float | None = None,
    limiter: HostRateLimiter | None = None,
) -> float:
    """Record that `url`'s host said it is being asked too often, and return how long it is held.

    The wait is the setting, or the site's own `Retry-After` where that is LONGER: the setting is
    the chosen floor, and a site that names a longer wait has said in as many words that the
    shorter one would be refused again. Every downloader waits it out: this only records it.
    """
    wait = max(policy.pacing.wait_after_too_many_requests, retry_after or 0.0)
    (limiter or LIMITER).note_retry_after(host_of(url), wait)
    return wait


def middleman_backoff(measured: float, policy: RunPolicy | None) -> float:
    """How long a middleman service is left alone after it said it is being asked too often.

    tikwm and instasave are not the Site: their own limits were measured, and their backoff is that
    measurement. It is the FLOOR. A longer "Wait after a rate limit" raises it, because somebody who
    chose a longer wait meant every service a download asks; a shorter one never lowers it, because
    the service would only refuse again. The DEFAULT raises nothing: nobody chose it, and at sixty
    seconds it would make every TikTok limit wait a minute where five seconds were measured
    (`Pacing.wait_chosen`). Without a policy (not a download) the measurement stands.
    """
    if policy is None or not policy.pacing.wait_chosen:
        return measured
    return max(measured, policy.pacing.wait_after_too_many_requests)


async def wait_out_backoff(url: str, *, limiter: HostRateLimiter | None = None) -> None:
    """Wait until `url`'s host is no longer held by a refusal, then go on at once.

    Only ever a wait for a HOLD: a host with no pace of its own has an interval of zero here, so an
    unrefused host is never slowed by this. Called before a request or a tool run, never inside a
    request's own time budget: a wait of minutes inside a request with a thirty-second ceiling
    would read as the site not answering.
    """
    await (limiter or LIMITER).acquire(host_of(url))


class Pacer:
    """The wait between one request and the next, for one download's requests.

    One per download, and shared by every request that download makes (the reads of a Site's
    pages, each redirect, each file of an album, each retry), because that is what "between one
    request and the next" means to the Site on the other end. The first request goes at once.

    Spacing is kept from the START of one request to the start of the next, the way both tools
    count it, so a slow answer does not add a second wait on top of itself.
    """

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
