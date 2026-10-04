# SPDX-License-Identifier: AGPL-3.0-or-later
"""A killswitch for saved cookies that have gone dead, and the health a screen shows for them.

Saved cookies for a site like Reddit eventually stop working: they are signed out, they expire, or
the account is action-blocked. Every request that carries them then fails the same way, and without a
signal that would be a silent, pointless retry storm against a credential that no longer works (and,
on a touchy site, a way to get the account flagged harder).

So the cookies are watched. A run of consecutive authentication failures for a site trips its switch;
from then on Sift stops sending that site's cookies and resolves without them. Any success clears the
streak, so a single private or protected post among working ones never trips it: only a genuine
"everything is failing" window does. No cookie value is ever logged; only the site and its health.

**The switch is process memory, and it stays that way.** What a restart does is give the cookies one
more chance, which is the right answer more often than it looks: a rate limit counts toward the trip
as an authentication failure does, because from here the two are indistinguishable, and a busy hour
must not condemn cookies that were never broken. Cookies that really are dead trip again within three
requests.

**What DOES survive a restart is the last thing Sift knew**, written to the connection's own row so
the cookies screen can say a site needs fresh cookies instead of showing every site as equally fine.
It is a record, not the switch: a later success clears it, and so does saving a fresh export.

**And the row's own dates are the other half of the answer**, which is why `state_of` is here rather
than on a screen. Whether cookies are saved, ending soon or finished is one question with one answer,
and a client working it out from a date would be a second copy of the rule that drifts the day the
window changes. The word on every surface is cookies, never a login: Sift never holds an account
name or a password for a site, only an exported jar, and calling that a login would send people
looking for a sign-in form that does not exist.

Which sites exist, and which hostnames belong to which, is not written down here. It is read from the
site catalog, so a site's cookie health cannot end up attached to a different site than its downloads
are.
"""

from __future__ import annotations

from sift.kernel.log import get_logger
from sift.slices.download.sources.sites import catalog

log = get_logger(__name__)

#: What a saved connection's row says about its cookies. Two values, because there are only two
#: things a person can do about them: nothing, or export a fresh file and replace them.
#:
#: The stored word is `needs_cookies` (download schema 18 rewrote the older `needs_login` in one
#: UPDATE), because it is read straight onto a screen and the screen says cookies.
SAVED = "saved"
NEEDS_COOKIES = "needs_cookies"

#: What a connection is shown AS, which is the health above with the jar's own dates folded in.
#:
#: Four words rather than two because a date is the half a person can act on BEFORE anything fails:
#: cookies that run out on Friday are worth replacing on Thursday, and nothing about the health
#: above can ever say that: it only knows what has already gone wrong.
STATE_SAVED = "saved"
STATE_ENDING_SOON = "ending_soon"
STATE_EXPIRED = "expired"
#: Only ever synthesised by a client for a supported site with no saved cookies at all. The server
#: never returns it, because the server only has rows for sites that HAVE cookies. It is named
#: here so the one list of words lives in one place.
STATE_NONE = "none"

#: How much warning "ending soon" gives. A week, because exporting cookies again is a two minute job
#: somebody does when they next sit down rather than the moment they are told, and a shorter window
#: would put the warning up after the last chance to act on it calmly.
ENDING_SOON_SECONDS = 7 * 24 * 60 * 60

# Consecutive auth failures before the switch trips. A little lenient: a lone private post's 403 should
# not trip it; three in a row is the genuine "the cookie is dead" signal, and any success resets first.
_TRIP_AT = 3


class _Health:
    __slots__ = ("fails", "tripped")

    def __init__(self) -> None:
        self.tripped = False
        self.fails = 0  # consecutive auth failures, reset on any success


#: Per site, and only for the sites something has actually reported on. Empty at boot, which is the
#: state that lets every saved jar of cookies be tried once more after a restart.
_state: dict[str, _Health] = {}


def reset() -> None:
    """Forget every switch and streak: the state a fresh process starts in."""
    _state.clear()


def site_key(host: str | None) -> str | None:
    """The catalog's name for the site a hostname belongs to, or None for a site Sift has no record
    of. Every alias of one site folds into the same answer, so old.reddit.com and v.redd.it are one
    site's cookie health and not three."""
    record = catalog.match_host(host)
    return record.key if record is not None else None


def _health(key: str) -> _Health:
    return _state.setdefault(key, _Health())


def should_use_cookies(host: str | None) -> bool:
    """Whether saved cookies may still be sent to `host`: always for a site with no record of
    trouble, and for a watched one until its switch has tripped."""
    key = site_key(host)
    return key is None or not _health(key).tripped


def status_for(host: str | None) -> str | None:
    """What this site's saved cookies should now be shown as, or None for a site Sift does not
    recognize: there is nothing honest to say about cookies it has never used."""
    key = site_key(host)
    if key is None:
        return None
    return NEEDS_COOKIES if _health(key).tripped else SAVED


def state_of(status: str | None, expires_last: int | None, now: int) -> str:
    """What one saved connection is SHOWN as, from its health and the last date in its jar.

    Worked out here and sent down already decided, so the screen draws a word rather than deriving
    one. Two readings of the same two values is two chances to disagree, and the one on the screen
    is the one nobody can see is wrong.

    **The LAST expiry and never the first**, and that is the whole of why this takes the field it
    does. A site behind a challenge issues a clearance cookie good for half an hour beside a session
    cookie good for a year, so the soonest expiry in the jar is nearly always the throwaway the site
    reissues on the next request. Read as the answer it would mark every such site "ending soon"
    for ever, which is a warning that means nothing by the second time it is seen. The last one is
    the point past which nothing in the jar is any use, which is the date somebody can act on. See
    `CookieSummary`, which reports both for exactly this reason.

    A jar with no dates at all is ordinary rather than a fault: session cookies carry none. Nothing
    is claimed about a date that was never given, so it reads as saved until something fails.
    """
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
    """Forget what was known about a site's cookies. Called when fresh ones are saved, and when a
    check finds the site still accepts the ones that are there.

    Without this a replaced export would keep the old one's verdict: the switch would still be
    tripped, so the new cookies would never be sent, and the screen would go on asking for the very
    thing that had just been provided.
    """
    record = catalog.by_site(site)
    if record is not None:
        _state.pop(record.key, None)


def record_ok(host: str | None) -> None:
    """A request that carried the cookies succeeded -> clear the failure streak; they still work."""
    key = site_key(host)
    if key is not None:
        _health(key).fails = 0


def record_auth_failure(host: str | None, *, detail: str = "") -> None:
    """A request that carried the cookies failed on authentication (a dead jar or a blocked
    account). Counts toward the trip; at the threshold the switch trips and stays tripped."""
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
