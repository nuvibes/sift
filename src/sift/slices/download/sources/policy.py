# SPDX-License-Identifier: AGPL-3.0-or-later
"""Turning the download preferences into the one policy a tool run is given, read per download.

An unreadable stored value falls back to the default; a chosen zero is kept where it means none."""

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

#: A shape, not an import: a feature never imports a feature.
SettingReader = Callable[[str], Awaitable[object]]
StoredReader = Callable[[str], Awaitable[bool]]

PolicyReader = Callable[[], Awaitable[RunPolicy]]


#: Milliseconds: a preference holds whole numbers, and half a second matters.
PACE_MS_KEY = "download.pace_ms"

#: A read with a time ceiling leaves room for this (`sites.extractors._within`).
PACE_MS_MAX = 10_000

RETRIES_KEY = "download.retries"

TIMEOUT_KEY = "download.timeout_seconds"

BACKOFF_KEY = "download.backoff_seconds"

BANDWIDTH_KEY = "download.bandwidth_kbps"

SKIP_SMALLER_KEY = "download.skip_smaller_mb"
SKIP_LARGER_KEY = "download.skip_larger_mb"

VERBOSE_KEY = "download.verbose"

QUALITY_KEY = "download.quality"

_MEGABYTE = 1024 * 1024
_KILOBYTE = 1024

#: Each setting must mean one thing for a tool run and Sift's own fetch; `test_fetcher` holds both.
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

READ_OFF_A_SETTING: dict[str, str] = {"pacing.wait_chosen": BACKOFF_KEY}


def _whole(stored: object, fallback: int) -> int:
    """A stored whole number above zero, or the default: a zero timeout fails every download."""
    try:
        number = int(str(stored))
    except (TypeError, ValueError):
        return fallback
    return number if number > 0 else fallback


def _count(stored: object, fallback: int) -> int:
    """A stored whole number where zero means none (a real choice), or the default."""
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
    """The policy for the next tool run; `is_stored` says whether the rate-limit wait was chosen."""
    quality = await get_app(QUALITY_KEY)
    wait_chosen = bool(await is_stored(BACKOFF_KEY)) if is_stored is not None else False
    bandwidth = _bound_kbps(await get_app(BANDWIDTH_KEY))
    return RunPolicy(
        pacing=Pacing(
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


#: As the preferences declare them, so registration and fallbacks agree on "unset".
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
