# SPDX-License-Identifier: AGPL-3.0-or-later
"""Turning two spellings of one link into one string.

A link dropped twice is rarely dropped identically. It picks up a tracking parameter on the way
through somebody's feed, or arrives with a trailing slash the first one lacked, or with the host in
a different case. None of that changes which video it points at.

So a URL is reduced to the part that identifies the media and nothing else: the scheme and host in
lower case, the path without a trailing slash, and the query with its tracking parameters removed
and the rest put in a fixed order. The fragment goes entirely: it is a position within a page,
never a different resource.

The tracking-parameter list is a denylist on purpose, not an allowlist. A parameter this has never
heard of is kept, because dropping it might change which media the link resolves to.

## Why this is in the kernel, and what that buys

It has two callers: the
ledger, which hashes `normalize_url` into the key that recognises a re-dropped link, and a file's
own record, which shows the address a file was fetched from. Two slices cannot import each other,
and the honest answer to "who owns a URL normaliser" is nobody in particular, so it sits here,
with one definition of the rules and no second copy to drift.

**IMPORTANT: `normalize_url` IS the ledger key.** `url_hash` is a hash of what this returns, so
widening what it strips re-keys every stored URL that carries the newly stripped parameter: the
check that recognises a re-drop stops matching and those links download again, silently. That is why
the record's cleaner is a SECOND function with its own list rather than an argument to this one: a
parameter that changes a key is an invitation, and the invitation only has to be accepted once.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

# Parameters that identify a referrer, a campaign, or a share, never the resource. Removed so that
# the same link shared through two feeds hashes the same. Anything with the `utm_` prefix goes too.
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

# Hosts that hand out a SIGNED link: the same file, addressed with an expiry and a signature that
# are regenerated every time somebody loads the page it is on. Two people copying one attachment
# get two different addresses for one file, and so does the same person an hour apart.
#
# Scoped to the hosts rather than added to the list above, because these names are short and common:
# `is` and `ex` mean something entirely different elsewhere, and dropping them everywhere could
# quietly change which media a link resolves to. Here they are provably not part of the identity:
# the path already names the channel, the attachment and the filename.
#
# The signature is removed from the KEY only. It is what makes the link work at all (without it
# the host answers 404), so the stored address keeps every character of it.
_SIGNED_HOSTS = frozenset({"cdn.discordapp.com", "media.discordapp.net"})
_SIGNATURE_PARAMS = frozenset({"ex", "is", "hm", "sc"})


def _is_signature(host: str, name: str) -> bool:
    return host in _SIGNED_HOSTS and name.lower() in _SIGNATURE_PARAMS


def without_signature(url: str) -> str:
    """The same link with the expiry and signature taken off, for showing to somebody.

    Not for fetching, and not stored: the host refuses the short form. It is the address a person
    reads, on a screen where the real one is forty characters of hex that says nothing.
    """
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
    """Reduce a URL to the part that identifies the media, spelled one canonical way.

    Lower-cases the scheme and host, drops a default port, removes the fragment, strips tracking
    parameters, and orders what query remains. The path keeps its case (a path is often
    case-sensitive on the far end), but loses a single trailing slash, which never is.

    **IMPORTANT: This is the ledger key.** It takes no arguments, and that is deliberate: what it
    strips is fixed, so the key a URL hashes to today is the key it hashed to a year ago. A caller
    that wants more taken off wants `cleaned_for_record`, which is a separate function for that
    reason.
    """
    return _reduced(url, frozenset())


def _reduced(url: str, also: frozenset[str], *, drop_signature: bool = True) -> str:
    """The shared reduction.

    `also` names extra parameters to strip, beyond the standing list. `drop_signature` is what
    separates a KEY from an ADDRESS: a signed host's expiry and signature are not part of what a
    link identifies, so the key is computed without them, and they ARE what makes the link work,
    so an address shown to somebody keeps every character of them or it answers 404.
    """
    parts = urlsplit(url.strip())

    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()

    netloc = host
    if parts.port is not None and _DEFAULT_PORTS.get(scheme) != str(parts.port):
        netloc = f"{host}:{parts.port}"

    # A trailing slash is never part of what a link identifies, the bare host included: `/watch/1/`
    # and `/watch/1` are one resource, and so are `example.com/` and `example.com`.
    path = parts.path.rstrip("/")

    kept = [
        (name, value)
        for name, value in parse_qsl(parts.query)
        if not _is_tracking(name, also) and not (drop_signature and _is_signature(host, name))
    ]
    query = urlencode(sorted(kept))

    return urlunsplit((scheme, netloc, path, query, ""))


# Parameters stripped from an address SHOWN on a record, and deliberately not from the key.
#
# `from` is a referrer under another name: it says which of the operator's own screens produced
# the link, which is a fact about them rather than about the media, and a guest can read this
# field.
#
# IMPORTANT: Kept apart from `_TRACKING_PARAMS` above because that set feeds `url_hash` through
# `normalize_url`. Adding a name there changes the key of every URL already stored that carries it,
# so the ledger stops recognising the link, the skip-a-re-drop check misses, and the file is
# fetched a second time with nothing on any screen to say why.
_RECORD_ONLY_TRACKING = frozenset({"from"})


def cleaned_for_record(url: str) -> str:
    """The address as it is stored on a file's record: normalised, plus `from` taken off.

    Everything `normalize_url` does, for the same reasons, and one more parameter removed. It stays
    a working page address: per-parameter, never "drop the query", because a signed host's link
    404s without its signature.

    Blank in, blank out. A file Sift did not fetch has no address, and an empty string is the
    truthful answer rather than something to raise about. That falls out of the reduction rather
    than being guarded for here: `normalize_url` beside this has never guarded it either, and a
    check that cannot change the answer is a check nobody can test.
    """
    return _reduced(url, _RECORD_ONLY_TRACKING, drop_signature=False)


# --- the words Instagram's addresses use that are nobody's name ----------------------------------
#
# Instagram puts the username first for a profile and a route word first for everything else, and
# the two are the same shape: one path segment. Reading the first segment blind therefore invents a
# person called "p" from every post link and one called "stories" from every story, and where a
# username becomes a Person, those would go into the People list as human beings.
#
# Here, in the kernel, because two areas that may not import each other read an Instagram address
# for a username: the download's site reader (`download/sources/sites/catalog.py`), and the reader
# of the address a picture carries in its own metadata (`suggestions/metadata.py`). Two copies of
# these words would be "the same words" only by a promise nothing holds them to.

#: First segments that are a route of the site's own and never a profile.
#:
#: `s` is here rather than below. A `/s/<code>` link is a share shortlink whose next segment is an
#: opaque blob, so reading it as a username would produce a username that was a base64 string, and
#: on a site whose usernames become People, a person named after one too.
INSTAGRAM_ROUTES: frozenset[str] = frozenset(
    {"p", "reel", "reels", "tv", "explore", "accounts", "direct", "s"}
)

#: First segments whose NEXT segment is the username. `stories` is the one route that is not
#: simply a dead end: `/stories/<user>/` is that user's tray.
INSTAGRAM_ROUTE_THEN_USERNAME: frozenset[str] = frozenset({"stories"})

#: The segments that can follow `stories` and are NOT somebody's name.
#:
#: `/stories/highlights/<id>` is a saved collection and the segment after `stories` is the word
#: "highlights" itself: read as a username it makes a username, and then a Person, called
#: Highlights. The address genuinely does not name who it belongs to; that is on the page.
INSTAGRAM_NOT_A_USERNAME: frozenset[str] = frozenset({"highlights"})

#: Every word above: what a ONE-segment Instagram address may not be read as a username for. The
#: reader that only ever accepts a bare profile address (`instagram.com/<name>`) asks this, since a
#: lone `stories` or `highlights` names nobody either.
INSTAGRAM_WORDS: frozenset[str] = (
    INSTAGRAM_ROUTES | INSTAGRAM_ROUTE_THEN_USERNAME | INSTAGRAM_NOT_A_USERNAME
)


# --- the name somebody goes by, read off the address of their page -------------------------------
#
# A stash-box lists a person's pages as bare addresses, and the name they go by on each site is
# read out of the address. Here, in the kernel, because two areas read it that may not import each
# other's code: the stash-box adapter, which reads it live, and the catalog's one-time repair of
# the usernames an older reading got wrong (catalog v65), which must give the rows already written
# the answer the live reader gives today.
#
# The LAST piece of every path is very often not a name: a username read that way is often one
# of the shapes below: a YouTube channel id, a page tab ("Videos", "About", "posts"), a
# reference database's numbered slug, a record id. Each would become a username, and then an alias
# on a person, because joining a username to a person writes it on as one.

#: Sites that WRITE ABOUT people rather than sites people post on.
#:
#: A stash-box's `urls[]` mixes the two and the difference decides where each one lands: a page
#: somebody publishes to is a Username under a Site, and a page in a reference database is a link.
#: Filed the wrong way round, a library ends up with a Site called "Wikipedia" and a person filed as
#: posting to it.
#:
#: Matched on the HOST, and short on purpose. It is a list of the reference databases these
#: stash-boxes actually carry, and anything not on it is treated as a place somebody posts, which
#: is the commoner case by a wide margin and the one that is useful. Getting it wrong in that
#: direction makes a Username nobody wanted; getting it wrong the other way loses a username, and a
#: username is what a library can be filed by.
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
        # The stash-boxes' own performer pages. ThePornDB, StashDB and FansDB are the reference
        # databases themselves, and each box's `urls[]` carries the others' page for the same
        # person: a page ABOUT them, never a place they post. Filed as usernames, a library would
        # grow Sites called "ThePornDB", "StashDB" and "FansDB": catalog v62 turns those back
        # into links. `fansdb.xyz` is a second FansDB address a box still carries.
        "theporndb.net",
        "stashdb.org",
        "fansdb.cc",
        "fansdb.xyz",
        # The film database. Its person pages are numbered slugs (`/person/1000017-<name>`), which
        # would otherwise become usernames under a Site called "TMDB" and aliases (the number
        # and all) on the people they describe.
        "themoviedb.org",
        "babepedia.com",
        "javstash.org",
    }
)

#: Words in a box's own name for a category of its sites that holds pages ABOUT somebody: StashDB's
#: "Third-party databases" and "Other stash-boxes", FansDB's "Databases". Read where a box files its
#: sites in categories; the hosts above answer for a box that files none.
REFERENCE_CATEGORY_WORDS = ("database", "stash-box")


def _host(address: str) -> str:
    return (urlsplit(address.strip()).hostname or "").lower()


#: WHERE A STASH-BOX SHOWS ONE OF ITS SCENES, for the boxes whose web pages Sift knows the shape of.
#:
#: Keyed by the host the box's API answers on (`stash_boxes.endpoint`): StashDB, FansDB and
#: PMVStash all run the stash-box application, which serves its site from the same host as its API
#: and draws a scene at `/scenes/<id>`, the id being the one a match stores (`remote_id`). A box
#: this does not name gets no address, deliberately: a self-hosted box may serve its pages
#: anywhere, and a link that lands on a missing page is worse than none (the rule the History
#: readers keep for every link). The way to give one an address is an entry here. The value is
#: the site's origin; every shelf of the stash-box application (scenes, performers, studios, tags)
#: hangs off it as `/<shelf>/<id>`, which is why one table answers for a scene's page and for an
#: entry's page alike.
STASH_BOX_PAGES: dict[str, str] = {
    "stashdb.org": "https://stashdb.org",
    "fansdb.cc": "https://fansdb.cc",
    "pmvstash.org": "https://pmvstash.org",
}


def stash_box_page(endpoint: str, shelf: str, remote_id: str | None) -> str | None:
    """The page on its stash-box of one thing on one shelf, or None where there is none to name.

    The box is matched on the exact host of its API endpoint and its `www.` form, never on a
    substring, for the reason the box tables in `slices/stash_boxes/known_boxes.py` give. The id
    is quoted whole: it is somebody else's service's word, it goes into an address, and quoting is
    what keeps it the last segment of the page whatever it contains.
    """
    if not remote_id:
        return None
    origin = STASH_BOX_PAGES.get(_host(endpoint).removeprefix("www."))
    return None if origin is None else f"{origin}/{shelf}/{quote(remote_id, safe='')}"


def scene_page(endpoint: str, remote_id: str | None) -> str | None:
    """The page on its stash-box of the scene a match names (`stash_box_page` on the scenes shelf)."""
    return stash_box_page(endpoint, "scenes", remote_id)


def _on(host: str, known: str) -> bool:
    """A host is on a known one when it is that host or a domain below it (a label boundary)."""
    return host == known or host.endswith("." + known)


def is_a_reference_page(address: str) -> bool:
    """Whether this address is a page ABOUT somebody, in a reference database (`REFERENCE_HOSTS`)."""
    host = _host(address)
    return bool(host) and any(_on(host, known) for known in REFERENCE_HOSTS)


def about_somebody(address: str, category: object = None) -> bool:
    """Whether an address a stash-box lists is a page ABOUT somebody rather than one they post to.

    `category` is the box's `category` object for the address's site, or None. The box's own word
    for it decides where it has one; either answer that says reference is taken.
    """
    named = category.get("name") if isinstance(category, dict) else None
    word = named.casefold() if isinstance(named, str) else ""
    return any(one in word for one in REFERENCE_CATEGORY_WORDS) or is_a_reference_page(address)


#: Sites where ONE CREATOR sells or posts her own clips from a page of her own: a store, not a
#: studio's home and not a page about her.
#:
#: The question a stash-box studio is read by (`kernel/access/creator_studios.py`). A box files many
#: creators as a studio of their own, named after them, and the links on such a studio are her
#: pages on these sites. A studio that sells on one of them is not thereby a creator (a producer
#: can sell on Clips4Sale too), so this is one sign of two and never the whole answer.
#:
#: Matched on the HOST, like `REFERENCE_HOSTS` beside it, and short on purpose: the sites these
#: boxes actually link a creator's studio to. A social network is not here (a producer has an X
#: page as readily as a creator does), and neither is a tube site, whose pages about a performer
#: are written by the site rather than kept by her.
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


#: The TABS of a profile page: a trailing piece that says which part of the page is showing, never
#: whose page it is. `/@name/videos`, `/user/name/submitted`, `/name/posts`. Taken off the end
#: before the name is read, however many there are (`/Store/Videos`).
#:
#: Each word here is one a stash-box's addresses end in, which read naively becomes a username and
#: an alias: "Videos", "posts" and "About" most often, and the other tabs of the same pages.
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

#: YouTube's own first pieces: a route of the site, never a channel's name. `channel` is here and
#: is the important one: `/channel/UC...` names a channel by its ID, 24 characters nobody is
#: called, so that address carries no name at all and stays a plain link. The names are in
#: `/@name`, `/c/name`, `/user/name` and the old bare `/name`.
_YOUTUBE_ROUTES: frozenset[str] = frozenset(
    {"channel", "watch", "playlist", "shorts", "embed", "results", "feed", "live", "hashtag"}
)
_YOUTUBE_NAME_NEXT: frozenset[str] = frozenset({"c", "user"})

#: A record id: the UUID every stash-box gives its own rows. A page named by one names nobody.
_RECORD_ID = re.compile(
    r"\A[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z"
)

#: A NUMBERED SLUG: the site's own number for a page, a dash, and then the name:
#: `/profil/4206129-<name>`, `/pornstar/8131-<name>`. The name is the part after the number.
#: Four digits or more, so a name that merely starts with a small number keeps it.
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
        # `/Profile/<number>/<name>/...` and `/Activity/<name>/<number>/...`: the name is never
        # last, and the number beside it is the site's own for the page.
        if first == "profile":
            return pieces[2] if len(pieces) > 2 else ""
        if first == "activity":
            return pieces[1] if len(pieces) > 1 else ""
    return pieces[-1]


def username_in(address: str) -> str:
    """The name a person goes by on a site, read off the address of their page there.

    On nearly every site it is the last piece of the path (`example.com/models/esmewrenfield`), once
    the query, the fragment and any page TABS (`PAGE_TABS`) are gone. Two sites put it elsewhere
    and are read by their own shape: YouTube (`/@name`, `/c/name`, `/user/name`; never
    `/channel/<id>`) and ManyVids (`/Profile/<number>/<name>`).

    A blank answer is a perfectly good one and means "this address does not carry a name". The
    caller keeps the address as a plain link on the person rather than inventing a name, because a
    Username spelled `www`, `Videos` or `UC_vjT9...` is worse than no Username at all, and it
    becomes an alias on a person, where it reads as something they are called. Blank for: nothing
    after the host; a number; a file (`index.html`), or a path through one (`details.php/id/7`); a
    record id. A numbered slug answers with the name after the number.
    """
    parts = urlsplit(address.strip())
    pieces = [piece for piece in parts.path.split("/") if piece]
    while pieces and pieces[-1].casefold() in PAGE_TABS:
        pieces.pop()
    # A piece with a dot before the last one is a script the rest of the path is handed to, and
    # what follows it is that script's arguments: an id, never a name.
    if not pieces or any("." in piece for piece in pieces[:-1]):
        return ""
    name = _name_piece((parts.hostname or "").lower(), pieces)
    if not name or name.isdigit() or "." in name or _RECORD_ID.match(name):
        return ""
    numbered = _NUMBERED_SLUG.match(name)
    return numbered.group("name") if numbered else name
