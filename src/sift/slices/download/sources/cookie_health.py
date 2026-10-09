# SPDX-License-Identifier: AGPL-3.0-or-later
"""A killswitch for saved cookies that have gone dead, and the health a screen shows for them.

The switch is process memory, so a restart gives the cookies one more chance."""

from __future__ import annotations

from sift.kernel.log import get_logger
from sift.slices.download.sources.sites import catalog

log = get_logger(__name__)

SAVED = "saved"
NEEDS_COOKIES = "needs_cookies"

#: A date is the half a person can act on before anything fails.
STATE_SAVED = "saved"
STATE_ENDING_SOON = "ending_soon"
STATE_EXPIRED = "expired"
#: Client-only: the server has rows only for sites that have cookies.
STATE_NONE = "none"

#: A week: exporting again is a job for when somebody next sits down.
ENDING_SOON_SECONDS = 7 * 24 * 60 * 60

# A lone private post's 403 must not trip it; any success resets the streak.
_TRIP_AT = 3


class _Health:
    __slots__ = ("fails", "tripped")

    def __init__(self) -> None:
        self.tripped = False
        self.fails = 0  # consecutive auth failures, reset on any success


_state: dict[str, _Health] = {}


def reset() -> None:
    """Forget every switch and streak: the state a fresh process starts in."""
    _state.clear()


def site_key(host: str | None) -> str | None:
    """The catalog's key for a hostname's site, every alias folded in; None if unknown."""
    record = catalog.match_host(host)
    return record.key if record is not None else None


def _health(key: str) -> _Health:
    return _state.setdefault(key, _Health())


def should_use_cookies(host: str | None) -> bool:
    """Whether saved cookies may still be sent to `host`: until its switch has tripped."""
    key = site_key(host)
    return key is None or not _health(key).tripped


def status_for(host: str | None) -> str | None:
    """What this site's saved cookies should now be shown as, or None for an unknown site."""
    key = site_key(host)
    if key is None:
        return None
    return NEEDS_COOKIES if _health(key).tripped else SAVED


def state_of(status: str | None, expires_last: int | None, now: int) -> str:
    """What one saved connection is shown as, from its health and the LAST date in its jar."""
    if status == NEEDS_COOKIES:
        return STATE_EXPIRED
    if expires_last is None:
        return STATE_SAVED
    if expires_last <= now:
        return STATE_EXPIRED
    if expires_last - now <= ENDING_SOON_SECONDS:
        return STATE_ENDING_SOON
    return STATE_SAVED


def clear_for_site(site: str) -> None:
    """Forget a site's verdict, or a replaced export would never be sent."""
    record = catalog.by_site(site)
    if record is not None:
        _state.pop(record.key, None)


def record_ok(host: str | None) -> None:
    """A request carrying the cookies succeeded: clear the failure streak."""
    key = site_key(host)
    if key is not None:
        _health(key).fails = 0


def record_auth_failure(host: str | None, *, detail: str = "") -> None:
    """A request carrying the cookies failed on authentication; at the threshold it trips."""
    key = site_key(host)
    if key is None:
        return
    health = _health(key)
    health.fails += 1
    if health.fails >= _TRIP_AT and not health.tripped:
        health.tripped = True
        log.warning(
            "cookie_health.killswitch",
            site=key,
            detail=detail,
            note=(
                f"the {key} cookies look dead or blocked; they will not be sent again this run, "
                "and the cookies screen is told to ask for a fresh export"
            ),
        )


__all__ = [
    "ENDING_SOON_SECONDS",
    "NEEDS_COOKIES",
    "SAVED",
    "STATE_ENDING_SOON",
    "STATE_EXPIRED",
    "STATE_NONE",
    "STATE_SAVED",
    "clear_for_site",
    "record_auth_failure",
    "record_ok",
    "reset",
    "should_use_cookies",
    "site_key",
    "state_of",
    "status_for",
]
