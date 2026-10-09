# SPDX-License-Identifier: AGPL-3.0-or-later
"""Turning two spellings of one link into one string.

`normalize_url` is the ledger key: widening what it strips makes stored links download again."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

# A referrer, campaign or share, never the resource; anything prefixed `utm_` goes too.
_TRACKING_PARAMS = frozenset(
    {
        "fbclid",
        "gclid",
        "dclid",
        "msclkid",
        "yclid",
        "igshid",
        "igsh",
        "mc_cid",
        "mc_eid",
        "ref",
        "ref_src",
        "ref_url",
        "referrer",
        "source",
        "s",
        "si",
        "t",
        "_r",
        "is_copy_url",
        "is_from_webapp",
        "sender_device",
        "web_id",
    }
)
_TRACKING_PREFIXES = ("utm_",)

# Signed hosts regenerate these on every page load; dropped from the key only, never from the
# stored address, which answers 404 without them.
_SIGNED_HOSTS = frozenset({"cdn.discordapp.com", "media.discordapp.net"})
_SIGNATURE_PARAMS = frozenset({"ex", "is", "hm", "sc"})


def _is_signature(host: str, name: str) -> bool:
    return host in _SIGNED_HOSTS and name.lower() in _SIGNATURE_PARAMS


def without_signature(url: str) -> str:
    """The same link without expiry and signature, for showing only: the host refuses it."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if host not in _SIGNED_HOSTS:
        return url
    kept = [
        (name, value) for name, value in parse_qsl(parts.query) if not _is_signature(host, name)
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), parts.fragment))


_DEFAULT_PORTS = {"http": "80", "https": "443"}


def _is_tracking(name: str, also: frozenset[str]) -> bool:
    lowered = name.lower()
    return lowered in _TRACKING_PARAMS or lowered in also or lowered.startswith(_TRACKING_PREFIXES)


def normalize_url(url: str) -> str:
    """Reduce a URL to what identifies the media; fixed, as it is the ledger key."""
    return _reduced(url, frozenset())


def _reduced(url: str, also: frozenset[str], *, drop_signature: bool = True) -> str:
    """The shared reduction; a signed host's signature leaves the key but stays in an address."""
    parts = urlsplit(url.strip())

    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()

    netloc = host
    if parts.port is not None and _DEFAULT_PORTS.get(scheme) != str(parts.port):
        netloc = f"{host}:{parts.port}"

    # A trailing slash never identifies anything, the bare host's included.
    path = parts.path.rstrip("/")

    kept = [
        (name, value)
        for name, value in parse_qsl(parts.query)
        if not _is_tracking(name, also) and not (drop_signature and _is_signature(host, name))
    ]
    query = urlencode(sorted(kept))

    return urlunsplit((scheme, netloc, path, query, ""))


# Shown addresses only: adding to `_TRACKING_PARAMS` would re-key every stored URL.
_RECORD_ONLY_TRACKING = frozenset({"from"})


def cleaned_for_record(url: str) -> str:
    """The address as stored on a file's record: normalised, plus `from` taken off."""
    return _reduced(url, _RECORD_ONLY_TRACKING, drop_signature=False)


# Instagram's route words share a profile's shape; read as names they would invent People.

#: First segments that are the site's own routes; `/s/` is an opaque shortlink.
INSTAGRAM_ROUTES: frozenset[str] = frozenset(
    {"p", "reel", "reels", "tv", "explore", "accounts", "direct", "s"}
)

#: `/stories/<user>/` is that user's tray.
INSTAGRAM_ROUTE_THEN_USERNAME: frozenset[str] = frozenset({"stories"})

#: `/stories/highlights/<id>` names no one.
INSTAGRAM_NOT_A_USERNAME: frozenset[str] = frozenset({"highlights"})

#: Every word above, none of which a one-segment address may be read as.
INSTAGRAM_WORDS: frozenset[str] = (
    INSTAGRAM_ROUTES | INSTAGRAM_ROUTE_THEN_USERNAME | INSTAGRAM_NOT_A_USERNAME
)


# The last piece of a page's path is often a tab, a channel id or a record id, not a name.

#: Sites that write about people rather than ones people post on; short, as a miss only
#: costs an unwanted Username.
REFERENCE_HOSTS: frozenset[str] = frozenset(
    {
        "iafd.com",
        "indexxx.com",
        "data18.com",
        "thenude.com",
        "boobpedia.com",
        "wikipedia.org",
        "wikidata.org",
        "imdb.com",
        "adultfilmdatabase.com",
        "egafd.com",
        "eurobabeindex.com",
        "freeones.com",
        # The stash-boxes' own performer pages are about people, never places they post.
        "theporndb.net",
        "stashdb.org",
        "fansdb.cc",
        "fansdb.xyz",
        # Its person pages are numbered slugs that would otherwise become usernames.
        "themoviedb.org",
        "babepedia.com",
        "javstash.org",
    }
)

#: Words in a box's category name for sites about somebody, where it files categories.
REFERENCE_CATEGORY_WORDS = ("database", "stash-box")


def _host(address: str) -> str:
    return (urlsplit(address.strip()).hostname or "").lower()


#: Where a known stash-box shows its pages, by API host; an unknown box gets no link.
STASH_BOX_PAGES: dict[str, str] = {
    "stashdb.org": "https://stashdb.org",
    "fansdb.cc": "https://fansdb.cc",
    "pmvstash.org": "https://pmvstash.org",
}


def stash_box_page(endpoint: str, shelf: str, remote_id: str | None) -> str | None:
    """The page on its stash-box of one thing on one shelf, or None; the id is quoted whole."""
    if not remote_id:
        return None
    origin = STASH_BOX_PAGES.get(_host(endpoint).removeprefix("www."))
    return None if origin is None else f"{origin}/{shelf}/{quote(remote_id, safe='')}"


def scene_page(endpoint: str, remote_id: str | None) -> str | None:
    """The page on its stash-box of the scene a match names."""
    return stash_box_page(endpoint, "scenes", remote_id)


def _on(host: str, known: str) -> bool:
    """A host is on a known one when it is that host or a domain below it (a label boundary)."""
    return host == known or host.endswith("." + known)


def is_a_reference_page(address: str) -> bool:
    """Whether this address is a page about somebody in a reference database."""
    host = _host(address)
    return bool(host) and any(_on(host, known) for known in REFERENCE_HOSTS)


def about_somebody(address: str, category: object = None) -> bool:
    """Whether a listed address is about somebody; the box's category word decides if given."""
    named = category.get("name") if isinstance(category, dict) else None
    word = named.casefold() if isinstance(named, str) else ""
    return any(one in word for one in REFERENCE_CATEGORY_WORDS) or is_a_reference_page(address)


#: Sites where one creator sells her own clips: one sign of a creator's studio, never all.
CREATOR_STORE_HOSTS: frozenset[str] = frozenset(
    {
        "clips4sale.com",
        "manyvids.com",
        "iwantclips.com",
        "onlyfans.com",
        "fansly.com",
        "loyalfans.com",
        "fancentro.com",
        "justfor.fans",
        "modelhub.com",
        "admireme.vip",
        "4based.com",
        "loverfans.com",
        "mydirtyhobby.com",
    }
)


def is_a_creator_store(address: str) -> bool:
    """Whether this address is a creator's own page on a store (`CREATOR_STORE_HOSTS`)."""
    host = _host(address)
    return bool(host) and any(_on(host, known) for known in CREATOR_STORE_HOSTS)


#: A profile page's tabs, taken off the end before the name is read.
PAGE_TABS: frozenset[str] = frozenset(
    {
        "about",
        "videos",
        "posts",
        "media",
        "featured",
        "comments",
        "submitted",
        "store",
        "items",
        "club",
        "photos",
        "shorts",
        "streams",
        "community",
        "playlists",
        "likes",
        "tagged",
        "highlights",
        "reels",
        "live",
    }
)

#: YouTube's routes, never a name; `/channel/<id>` carries no name at all.
_YOUTUBE_ROUTES: frozenset[str] = frozenset(
    {"channel", "watch", "playlist", "shorts", "embed", "results", "feed", "live", "hashtag"}
)
_YOUTUBE_NAME_NEXT: frozenset[str] = frozenset({"c", "user"})

#: A record id: the UUID every stash-box gives its own rows. A page named by one names nobody.
_RECORD_ID = re.compile(
    r"\A[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z"
)

#: The site's number, a dash, then the name; four digits or more, so a name may start small.
_NUMBERED_SLUG = re.compile(r"\A\d{4,}-(?P<name>.+)\Z")


def _name_piece(host: str, pieces: list[str]) -> str:
    """Which piece of the path is the name, by the site's own shape. Blank for none."""
    first = pieces[0].casefold()
    if _on(host, "youtube.com"):
        if pieces[0].startswith("@"):
            return pieces[0]
        if first in _YOUTUBE_NAME_NEXT:
            return pieces[1] if len(pieces) > 1 else ""
        return pieces[0] if len(pieces) == 1 and first not in _YOUTUBE_ROUTES else ""
    if _on(host, "manyvids.com"):
        # The name is never last here.
        if first == "profile":
            return pieces[2] if len(pieces) > 2 else ""
        if first == "activity":
            return pieces[1] if len(pieces) > 1 else ""
    return pieces[-1]


def username_in(address: str) -> str:
    """The name a person goes by on a site, read off their page's address; blank for none."""
    parts = urlsplit(address.strip())
    pieces = [piece for piece in parts.path.split("/") if piece]
    while pieces and pieces[-1].casefold() in PAGE_TABS:
        pieces.pop()
    # A dotted piece before the last is a script, and what follows are its arguments.
    if not pieces or any("." in piece for piece in pieces[:-1]):
        return ""
    name = _name_piece((parts.hostname or "").lower(), pieces)
    if not name or name.isdigit() or "." in name or _RECORD_ID.match(name):
        return ""
    numbered = _NUMBERED_SLUG.match(name)
    return numbered.group("name") if numbered else name
