# SPDX-License-Identifier: AGPL-3.0-or-later
"""How each stash-box service files its creators, decided from its address, never a setting."""

from __future__ import annotations

from urllib.parse import urlsplit

#: Stored as `site` because SQLite cannot alter the CHECK constraint naming it.
SITES_ARE_SITES = "site"

SITES_ARE_PEOPLE = "person"

#: Matched by exact host (and `www.`), so a lookalike domain inherits nothing.
_FILES_CREATORS_AS_STUDIOS: frozenset[str] = frozenset({"pmvstash.org"})


def sites_are_for(endpoint: str) -> str:
    """How the stash-box at this address files its creators; any address, even a malformed one."""
    host = (urlsplit(endpoint).hostname or "").lower().removeprefix("www.")
    return SITES_ARE_PEOPLE if host in _FILES_CREATORS_AS_STUDIOS else SITES_ARE_SITES


#: Typed in queries (`enriched:fansdb`), so stable and lower case.
_SLUGS: dict[str, str] = {
    "stashdb.org": "stashdb",
    "pmvstash.org": "pmvstash",
    "fansdb.cc": "fansdb",
}


def slug_for(endpoint: str) -> str | None:
    """The word this address is known by here, or None for a box Sift has never heard of."""
    host = (urlsplit(endpoint).hostname or "").lower().removeprefix("www.")
    return _SLUGS.get(host)


_SPELLED: dict[str, str] = {
    "stashdb": "StashDB",
    "pmvstash": "PMVStash",
    "fansdb": "FansDB",
}

#: Sorted so every install draws the Auto-enrich menu alike.
KNOWN_BOXES: tuple[tuple[str, str], ...] = tuple(
    (slug, _SPELLED[slug]) for slug in sorted(set(_SLUGS.values()))
)
