# SPDX-License-Identifier: AGPL-3.0-or-later
"""What Sift knows about the stash-boxes themselves, as opposed to about one install's boxes.

On nearly every box a studio is a production company (a Site here) and a person is a performer, but
some file creators as studios, and asked the wrong way round a search for somebody comes back empty
from a box that has them. That has one right answer per service, which nobody could discover from a
screen, so it is decided here from the box's address, never a setting. An unknown box gets the
ordinary answer; another kind of box is another entry here.
"""

from __future__ import annotations

from urllib.parse import urlsplit

#: What this box files as a studio is a SITE here, as almost every box means. The stored word stays
#: `site` because only a CHECK constraint names a column's words and SQLite cannot alter one (see
#: `schema.py`).
SITES_ARE_SITES = "site"

#: What it files as a studio is a PERSON here.
SITES_ARE_PEOPLE = "person"

#: Hosts whose studios are the creators. Keyed by host, where requests go, not by the name somebody
#: typed; matched exactly (and its `www.` form), never on a substring, so a lookalike domain
#: inherits nothing.
_FILES_CREATORS_AS_STUDIOS: frozenset[str] = frozenset({"pmvstash.org"})


def sites_are_for(endpoint: str) -> str:
    """How the stash-box at this address files its creators.

    Answers for any address, including a malformed one: this is consulted while a box is being
    added, and refusing here would turn "that URL has a typo" into an error about a Site.
    """
    host = (urlsplit(endpoint).hostname or "").lower().removeprefix("www.")
    return SITES_ARE_PEOPLE if host in _FILES_CREATORS_AS_STUDIOS else SITES_ARE_SITES


#: The short word a box is known by, keyed and matched by host as above. It is typed in queries
#: (`enriched:fansdb`) and carried in facet rows, so it must be stable and lower case; a box's own
#: name is whatever somebody typed and differs per install.
_SLUGS: dict[str, str] = {
    "stashdb.org": "stashdb",
    "pmvstash.org": "pmvstash",
    "fansdb.cc": "fansdb",
}


def slug_for(endpoint: str) -> str | None:
    """The word this address is known by here, or None for a box Sift has never heard of.

    None is stored as such: a self-hosted box still enriches and creates and is drawn with its own
    name, and a word invented from its host would mean nothing on the next install.
    """
    host = (urlsplit(endpoint).hostname or "").lower().removeprefix("www.")
    return _SLUGS.get(host)


#: How each word is spelled on screen. A word in `_SLUGS` with nothing here raises at import rather
#: than reaching a menu as its bare slug.
_SPELLED: dict[str, str] = {
    "stashdb": "StashDB",
    "pmvstash": "PMVStash",
    "fansdb": "FansDB",
}

#: Every box with a word, as `(word, spelling)`, sorted so every install draws the Auto-enrich menu
#: alike. A self-hosted box has no word and is reached only by choosing every box: a stored id moves
#: on re-adding and a stored name is whatever was typed.
KNOWN_BOXES: tuple[tuple[str, str], ...] = tuple(
    (slug, _SPELLED[slug]) for slug in sorted(set(_SLUGS.values()))
)
