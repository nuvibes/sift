# SPDX-License-Identifier: AGPL-3.0-or-later
"""Turning the download preferences into the one object a tool run is given.

**Sift owns the value; the capable side applies it.** A gallery of four hundred images is four
hundred requests inside a process Sift launched and cannot see into, so it cannot pace them from out
here. What it can do is decide the number in one place and hand it to the only side able to act on
it. This module is that one place: it reads the preferences and builds the policy, and the command
builders take that policy and nothing else.

Read live, per download, for the reason every other live-read in the slice is: a change made while a
queue is draining should take effect on the next download and not at the next restart. The reads are
cheap: a handful of rows.

Every value is guarded on the way through, the way the free-space floor is. A stored row can outlive
the code that wrote it (a restored backup, a setting whose bounds narrowed in an update), and the
answer to something unreadable is the default. Whether ZERO is unreadable depends on the setting, and
each one says which: a timeout of zero expires immediately and a cap of nothing is no cap, so those two
fall back; a wait of zero between requests and zero retries are things somebody chose on purpose
(the registry offers 0 for both), so those two are honored rather than silently replaced with the
default.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sift.slices.download.sources.tuning import (
    FILTERS,
    PACING,
    POLICY,
    QUALITY_BEST,
    QUALITY_COMPATIBLE,
    Filters,
    Pacing,
    RunPolicy,
)

#: Reads one global preference by key. Declared as a shape rather than imported from the feature
#: that stores preferences: a feature never imports a feature.
SettingReader = Callable[[str], Awaitable[object]]
#: Whether a setting holds a value somebody chose, as against its default. See `Pacing.wait_chosen`.
StoredReader = Callable[[str], Awaitable[bool]]

#: Builds the policy for a run. What the downloader is given, so a test hands it a fixed one.
PolicyReader = Callable[[], Awaitable[RunPolicy]]


#: Milliseconds rather than seconds because a preference holds whole numbers, and half a second is
#: the value that matters here. The tools take seconds, so it is divided on the way out.
PACE_MS_KEY = "download.pace_ms"

#: The longest wait between requests somebody can choose, in milliseconds. Named because a read
#: with a time ceiling leaves room for it (`sites.extractors._within`), and the registration's own
#: maximum is this number.
PACE_MS_MAX = 10_000

#: How many times a tool retries a request of its own.
RETRIES_KEY = "download.retries"

#: How long to wait on a connection that is not answering, in seconds.
TIMEOUT_KEY = "download.timeout_seconds"

#: How long to wait after a site says it is being asked too often, in seconds.
BACKOFF_KEY = "download.backoff_seconds"

#: The ceiling on how much of the connection a download may take, in kilobytes a second. Zero is no
#: ceiling: the option is then not passed at all, rather than passed as a cap of nothing.
BANDWIDTH_KEY = "download.bandwidth_kbps"

#: Sizes not worth fetching, in megabytes. Zero at either end means no bound at that end.
SKIP_SMALLER_KEY = "download.skip_smaller_mb"
SKIP_LARGER_KEY = "download.skip_larger_mb"

#: Whether the tools are asked to explain themselves.
VERBOSE_KEY = "download.verbose"

#: Which of the two quality answers a video download takes.
QUALITY_KEY = "download.quality"

_MEGABYTE = 1024 * 1024
_KILOBYTE = 1024

#: Every setting this module reads, and where on the policy it lands.
#:
#: Written out because each setting must mean one thing on every Site, and a download is made two
#: ways: a tool run told the value on its command line (`argv.CONCERNS`), and Sift's own fetch
#: (`fetcher`, `net`, `ratelimit`). The gate in `test_fetcher` holds every entry to both.
LANDS_AT: dict[str, str] = {
    PACE_MS_KEY: "pacing.seconds_between_requests",
    RETRIES_KEY: "pacing.retries",
    TIMEOUT_KEY: "pacing.timeout_seconds",
    BACKOFF_KEY: "pacing.wait_after_too_many_requests",
    BANDWIDTH_KEY: "pacing.bytes_per_second",
    SKIP_SMALLER_KEY: "filters.at_least_bytes",
    SKIP_LARGER_KEY: "filters.at_most_bytes",
    VERBOSE_KEY: "verbose",
    QUALITY_KEY: "quality",
}

#: Values on the policy that no setting holds, each with the setting it is read off: whether the
#: wait after a rate limit was chosen is a fact about that setting, not a number told to a tool.
READ_OFF_A_SETTING: dict[str, str] = {"pacing.wait_chosen": BACKOFF_KEY}


def _whole(stored: object, fallback: int) -> int:
    """A stored whole number above zero, or the default if what is stored is not one.

    For the settings where zero cannot be meant: a timeout of zero seconds fails every download at
    once, and the registry's own minimum for both of these is above zero. Not a coercion for
    tidiness: this is on the live path, and a value that slipped the decoder would otherwise be
    handed to the tools, which honour exactly what they are given.
    """
    try:
        number = int(str(stored))
    except (TypeError, ValueError):
        return fallback
    return number if number > 0 else fallback


def _count(stored: object, fallback: int) -> int:
    """A stored whole number where ZERO is an answer, or the default if what is stored is not one.

    For the wait between requests and the retries. Zero there means "none", which is a real choice
    (the registry's minimum for both is 0) and both tools take it as meant: `--sleep-requests 0`
    and `--retries 0`. Only something that is not a number, or below zero, falls back: a chosen
    0 is never silently replaced by the default.
    """
    try:
        number = int(str(stored))
    except (TypeError, ValueError):
        return fallback
    return number if number >= 0 else fallback


def _bound(stored: object) -> int | None:
    """A limit in megabytes as a count of bytes, where zero and nonsense both mean no limit."""
    try:
        megabytes = int(str(stored))
    except (TypeError, ValueError):
        return None
    return megabytes * _MEGABYTE if megabytes > 0 else None


async def read_policy(get_app: SettingReader, is_stored: StoredReader | None = None) -> RunPolicy:
    """Everything Sift has decided about how the next tool run behaves, from the preferences.

    `is_stored` says whether the wait after a rate limit was chosen by somebody: only then does it
    raise a middleman service's own measured floor. Without it (a caller with no settings store)
    nothing counts as chosen."""
    quality = await get_app(QUALITY_KEY)
    wait_chosen = bool(await is_stored(BACKOFF_KEY)) if is_stored is not None else False
    bandwidth = _bound_kbps(await get_app(BANDWIDTH_KEY))
    return RunPolicy(
        pacing=Pacing(
            # A whole number of milliseconds becomes a fraction of a second, which is what both
            # tools take. Rounded to three places so the command line carries `0.5` rather than a
            # long tail of binary fraction.
            seconds_between_requests=round(
                _count(await get_app(PACE_MS_KEY), int(PACING.seconds_between_requests * 1000))
                / 1000,
                3,
            ),
            retries=_count(await get_app(RETRIES_KEY), PACING.retries),
            timeout_seconds=float(_whole(await get_app(TIMEOUT_KEY), int(PACING.timeout_seconds))),
            wait_after_too_many_requests=float(
                _whole(await get_app(BACKOFF_KEY), int(PACING.wait_after_too_many_requests))
            ),
            bytes_per_second=bandwidth,
            wait_chosen=wait_chosen,
        ),
        filters=Filters(
            at_least_bytes=_bound(await get_app(SKIP_SMALLER_KEY)),
            at_most_bytes=_bound(await get_app(SKIP_LARGER_KEY)),
        ),
        quality=quality if quality in (QUALITY_COMPATIBLE, QUALITY_BEST) else POLICY.quality,
        verbose=await get_app(VERBOSE_KEY) is True,
    )


def _bound_kbps(stored: object) -> int | None:
    """A cap in kilobytes a second as bytes a second, where zero and nonsense both mean no cap."""
    try:
        kilobytes = int(str(stored))
    except (TypeError, ValueError):
        return None
    return kilobytes * _KILOBYTE if kilobytes > 0 else None


#: Defaults as the preferences declare them, so the registration and the fallbacks above cannot
#: disagree about what "unset" means.
DEFAULTS: dict[str, object] = {
    PACE_MS_KEY: int(PACING.seconds_between_requests * 1000),
    RETRIES_KEY: PACING.retries,
    TIMEOUT_KEY: int(PACING.timeout_seconds),
    BACKOFF_KEY: int(PACING.wait_after_too_many_requests),
    BANDWIDTH_KEY: 0,
    SKIP_SMALLER_KEY: 0 if FILTERS.at_least_bytes is None else FILTERS.at_least_bytes // _MEGABYTE,
    SKIP_LARGER_KEY: 0 if FILTERS.at_most_bytes is None else FILTERS.at_most_bytes // _MEGABYTE,
    VERBOSE_KEY: False,
    QUALITY_KEY: QUALITY_COMPATIBLE,
}
