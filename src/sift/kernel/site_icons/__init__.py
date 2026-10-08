# SPDX-License-Identifier: AGPL-3.0-or-later
"""The site logos that ship with Sift: one small picture per site, found by host name.

A Site in a library is a row with a name on it and, nearly always, nothing to draw. Sift can
only show a picture of a site when somebody has chosen one, and nobody chooses one for four hundred
Sites, so a wall of Sites is a wall of coloured letters, on a screen whose whole job is to
be recognised at a glance. Every site already has a picture of itself, and it is the same picture
everywhere: its favicon.

## They are FILES here, fetched once, never at run time

The pictures are in this directory and they were fetched by `scripts/build_site_icons.py`, by hand,
before the release. Nothing in Sift ever asks a site for its icon while somebody is using it, and
that is the whole shape of the feature rather than an optimisation:

- **A run-time fetch is a disclosure.** Drawing a Site wall would make one request per row to a
  list of adult sites, from the machine the library is on, every time the wall was drawn. The
  request says which sites are in somebody's library, to those sites.
- **A run-time fetch fails.** A site that has moved, a machine with no internet, a favicon behind a
  login: each of those is a picture that is there on one install and missing on the next, for
  reasons nobody can see.
- **Already there after installation.** No first run that populates anything, no cache to warm, no
  setting to find.

The cost is stated rather than hidden: the pack is a snapshot. A site that redesigns its logo keeps
the old one here until somebody runs the build script again and ships the result.

## The lookup is an EXACT host, and deliberately not a suffix match

`hosts.host_matches` in the download slice matches a host against a site's domains on a label
boundary (`cdn.bunkr.cr` matches `bunkr.cr`), and it is written that way because what turns on it
is which saved login a pasted address is sent to. Nothing like that turns on this. What is being
chosen here is a PICTURE, and the safe direction for a picture is to draw nothing rather than to
draw somebody else's logo, so the match is an exact host with a leading `www.` taken off and no
inference of any kind. A site with more than one domain lists each of them in the manifest.

## Some sites are KNOWN and deliberately have NO picture

The manifest's `withheld` list names sites the pack knows and ships nothing for, each with its
reason: a site whose own picture is a photograph of a real person, a site whose mark is a drawn
figure, and the link kinds ("Home", "Official Website") that are not sites at all. They answer
exactly as a site with no logo does (`icon_for`, `tone_for` and `icon_token` say None, so a tile
draws the site's first letter and a link its plain link glyph), and they are NOT in the path
allowlist, so no file of theirs is ever served even if one were left on the disk. Their hosts and
names stay in the lookup: "which site is this" (`slug_for`) still has its answer, and a row called
"Studio" is known as a kind rather than matched to some site that happens to share the word.

That also keeps this in the kernel without dragging a slice's module up with it: two things read the
pack (the route that serves one icon, and the cover that falls through to it), and they live in
places that may not import one another.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from urllib.parse import urlsplit

from sift.kernel.version import app_version

_HERE = Path(__file__).resolve().parent

#: Where the pictures are. One PNG per entry, named for its slug.
ICONS_DIR = _HERE / "icons"

#: What is in the pack, and where each entry came from. Written by `scripts/build_site_icons.py`.
MANIFEST = _HERE / "manifest.json"

#: One size, not a ladder, and the reason is what draws these.
#:
#: A second, smaller file would halve the bytes of the small case and double the size of the
#: repository, of the manifest, and of the number of things that can be missing. So: one PNG, and
#: the browser scales it.
#:
#: **256, and EXACTLY 256: a size, not a cap.** A Sites card draws a mark at `30cqi`, about 96 CSS
#: pixels, and an entity header at about 59; on a screen at a device pixel ratio of 2 that card is
#: 192 device pixels, so anything smaller is upscaled and drawn jagged.
#: `scripts/build_site_icons.py` makes every icon this size from a vector or by shrinking something
#: bigger, and records the few it could only make by enlarging as `quality: low`.
ICON_PIXELS = 256


@dataclass(frozen=True, slots=True)
class SiteIcon:
    """One entry in the pack: a picture, the hosts it stands for, and what the site is called."""

    slug: str
    #: What the site calls itself, for a caption and for whoever reads the manifest.
    name: str
    #: Every host this icon answers for, lower case, without `www.`.
    hosts: tuple[str, ...]
    #: What else this site has been called, and every word a stash-box files a link to it under
    #: ("reddit user", "PMV Haven video"). Matched exactly as the name is (see `_by_name`).
    aliases: tuple[str, ...] = ()
    #: What the visible part of the mark is: `light`, `dark` or `colour` (see `tone_for`).
    tone: str = "colour"
    #: `high` where the build drew the 256 from a vector or by shrinking something bigger, `low`
    #: where it could only enlarge a small picture (see `ICON_PIXELS`). Read by `ships_a_good_one`.
    quality: str = "high"


#: The words the manifest records a mark's tone in (`scripts/site_icon_art.py tone_of`).
#: `light` and `dark` are ONE-colour marks (a white wordmark, a black glyph), and `colour` is
#: everything else, which reads on any ground. Anything else in a manifest is read as `colour`,
#: the answer that changes nothing about how the mark is drawn.
TONES = frozenset({"light", "dark", "colour"})


def _read() -> dict[str, object]:
    try:
        return dict(json.loads(MANIFEST.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        # An install with no pack, or one whose manifest did not survive being copied. Every caller
        # already draws a letter when there is no picture, so an empty pack is a Sift that looks
        # exactly like the one before this feature rather than one that refuses to start.
        return {}


@cache
def every() -> tuple[SiteIcon, ...]:
    """Every entry the pack ships a PICTURE for, in the order the manifest lists them.

    Cached because the pack is part of the installed application: it cannot change while Sift is
    running, and re-reading a file per Site per wall is a read per row for an answer that was
    decided when the release was built.
    """
    return _entries("icons")


def withheld() -> tuple[SiteIcon, ...]:
    """Every entry the pack KNOWS and ships no picture for (see the module's note).

    NOT cached, on purpose: the only callers at run time are `_by_host` and `_by_name`, which are
    cached themselves and read this once each. A cache of its own would be one more reader that
    every test standing up a pack of its own has to know to clear, and forgetting it would leak
    the real pack's withheld hosts into that test's index, silently.
    """
    return _entries("withheld")


def _entries(key: str) -> tuple[SiteIcon, ...]:
    """One of the manifest's lists of sites, read into entries; a malformed row is skipped."""
    entries = _read().get(key)
    if not isinstance(entries, list):
        return ()
    found: list[SiteIcon] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        slug = str(entry.get("slug") or "")
        hosts = entry.get("hosts")
        if not slug or not isinstance(hosts, list):
            continue
        also = entry.get("aliases")
        found.append(
            SiteIcon(
                slug=slug,
                name=str(entry.get("name") or slug),
                hosts=tuple(str(host).lower() for host in hosts if host),
                aliases=tuple(str(one) for one in also if one) if isinstance(also, list) else (),
                tone=tone if (tone := str(entry.get("tone") or "")) in TONES else "colour",
                # An entry that does not say is read as `low`: the question asked of it is whether
                # a fetch may be skipped, and skipping on a guess would leave a Site with less.
                quality="high" if entry.get("quality") == "high" else "low",
            )
        )
    return tuple(found)


@cache
def _slugs() -> frozenset[str]:
    """Every slug the pack ships a picture for. The allowlist `path_of` compares against.

    Never a withheld slug (`withheld`): a file of one left on the disk is still never served.
    """
    return frozenset(icon.slug for icon in every())


@cache
def _by_slug() -> dict[str, SiteIcon]:
    """Slug -> entry, for the questions asked of an entry once the lookup has found it."""
    index: dict[str, SiteIcon] = {}
    for icon in every():
        index.setdefault(icon.slug, icon)
    return index


@cache
def _by_host() -> dict[str, str]:
    """Host -> slug. Built once from the manifest, for the same reason `every` is cached.

    The withheld entries' hosts are in it (after the shipped ones), so a withheld site's address
    is recognised as that site, whose picture `path_of` then refuses.
    """
    index: dict[str, str] = {}
    for icon in (*every(), *withheld()):
        for host in icon.hosts:
            # The first entry wins. Two entries claiming one host is a fault in the pack rather
            # than a question to answer at run time, and a test refuses one.
            index.setdefault(host, icon.slug)
    return index


@cache
def _by_name() -> dict[str, str]:
    """Name -> slug, compared without case. The second way in, and the one that carries the pack.

    **Hardly any Site in a real library has an address on it.** A Site is made by name when a
    download arrives (`catalog._INSERT_SITE` writes a name and nothing else), so `site_url`
    (a Site's first link, since catalog v66) is filled in only by somebody typing it into the record
    form or by a stash-box filling it. The sites a library has most of are therefore exactly the
    ones a host lookup cannot reach, which is the case this pack exists for.

    A name is a weaker key than a host and it is good enough here, for a reason particular to what
    is in the pack: every name in it is a real site's own name, so a Site in a library called
    `OnlyFans` IS OnlyFans. The worst a collision can do is draw the wrong logo beside a name, which
    is visible at a glance and is undone by choosing a picture.

    **AND BY WHAT IT USED TO BE CALLED.** A site that renames itself leaves rows behind under the
    old word: X is one site with two domains and one picture, and a library that enriched a person
    from a stash-box before the rename holds a Site called `Twitter` with hundreds of usernames,
    beside a `X` with one. The pack knows both words for one picture, so the old row is drawn as the
    site it is rather than as a coloured letter, which is the whole of what the pack is for.

    It does NOT merge the two rows. Folding a Site into another moves usernames, files, grants, tags
    and a sealed login, and `slices/people/site_merge.py` is the one place that knows how; a picture
    lookup that appeared to do it would be a second answer to a question with a dangerous one.

    The name wins over the alias where both are claimed, because a site's own name is what it calls
    itself today. Within each pass the first entry wins, exactly as the host index decides.
    """
    index: dict[str, str] = {}
    known = (*every(), *withheld())
    for icon in known:
        index.setdefault(icon.name.strip().lower(), icon.slug)
    for icon in known:
        for alias in icon.aliases:
            index.setdefault(alias.strip().lower(), icon.slug)
    return index


def host_of(address: str) -> str:
    """The host an address is on, lower case, without a leading `www.`, or empty.

    Takes a bare host as readily as a URL: a Site's stored address is usually a full one and is
    occasionally just the domain somebody typed, and both mean the same site.
    """
    cleaned = address.strip()
    if not cleaned:
        return ""
    host = urlsplit(cleaned if "//" in cleaned else f"//{cleaned}").hostname or ""
    return host.lower().removeprefix("www.")


#: Hosts that are DATABASES and INDEXES of sites rather than sites: a studio's record on a
#: stash-box lists its page on these among its links, first as often as not, and the pack knows
#: each of them as a site of its own. Read from a record's links, such a link names where the
#: studio is written about, never the studio, so the icon it would give is the index's.
INDEX_HOSTS = frozenset(
    {
        "theporndb.net",
        "stashdb.org",
        "fansdb.cc",
        "pmvstash.org",
        "iafd.com",
        "freeones.com",
        "indexxx.com",
        "babepedia.com",
        "boobpedia.com",
        "data18.com",
        "adultfilmdatabase.com",
        "wikipedia.org",
        "imdb.com",
    }
)


def is_index_host(address: str) -> bool:
    """Whether an address is on a database or index of sites, itself or a subdomain of one."""
    host = host_of(address)
    return any(host == one or host.endswith(f".{one}") for one in INDEX_HOSTS) if host else False


def slug_for(address: str) -> str | None:
    """The pack's slug for the site an address is on, or None when the pack does not know it.

    A WITHHELD site answers with its slug: this says which site, not whether it has a picture:
    `path_of` is the question that answers that, and it answers None for a withheld slug.
    """
    host = host_of(address)
    return _by_host().get(host) if host else None


#: The reason the manifest gives a withheld entry that is a KIND of link ("Home", "Studio Profile",
#: "Modeling Agency") rather than a site. Every other withheld entry is a real site that simply
#: ships no picture (a mascot, a photograph) and is a site like any other for `name_for`.
LINK_KIND = "link kind"


@dataclass(frozen=True, slots=True)
class _Names:
    """What the pack says about site NAMES, read once per manifest. See `_names_in`."""

    #: Host -> what Sift calls the site on it.
    by_host: dict[str, str]
    #: Every word, folded, that is a stash-box's label for a link rather than a site's name.
    labels: frozenset[str]


@cache
def _names_in(manifest: Path) -> _Names:
    """The pack's answer to "what is this site called", keyed on the manifest it was read from.

    Cached on the PATH rather than on nothing, and that is for the tests rather than for speed: a
    test that stands a pack of its own up points `MANIFEST` somewhere else, and gets its own entry
    here instead of one of the real pack's leaking into it (or the reverse). The readers above are
    cached on nothing, and every such test has to know to clear each of them by name.

    **Three lists, and the third is the one `every` does not read.** `icons` and `withheld` are the
    sites the pack has an entry for; `missing` is the sites the build found and could not fetch a
    picture for, each with its one host and its name. A site with no picture is still a site with
    a name, so it answers here although it has no slug. The link kinds are the only entries left
    out of the host index (they carry no hosts anyway), and they are what `labels` is made of.
    """
    try:
        raw = dict(json.loads(manifest.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        raw = {}
    by_host: dict[str, str] = {}
    names: set[str] = set()
    aliases: set[str] = set()
    link_kinds: set[str] = set()
    for key in ("icons", "withheld"):
        entries = raw.get(key)
        for entry in entries if isinstance(entries, list) else ():
            if not isinstance(entry, dict) or not str(entry.get("name") or "").strip():
                continue
            name = str(entry["name"]).strip()
            also = entry.get("aliases")
            words = (
                [str(one).strip() for one in also if str(one).strip()]
                if isinstance(also, list)
                else []
            )
            if entry.get("reason") == LINK_KIND:
                link_kinds.update(word.casefold() for word in (name, *words))
                continue
            names.add(name.casefold())
            aliases.update(word.casefold() for word in words)
            hosts = entry.get("hosts")
            for host in hosts if isinstance(hosts, list) else ():
                # The first entry wins, exactly as `_by_host` decides.
                by_host.setdefault(host_of(str(host)), name)
    missing = raw.get("missing")
    for entry in missing if isinstance(missing, list) else ():
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        host = host_of(str(entry.get("host") or ""))
        if name and host:
            names.add(name.casefold())
            by_host.setdefault(host, name)
    by_host.pop("", None)
    return _Names(
        by_host=by_host,
        labels=frozenset(link_kinds | (aliases - names)),
    )


def name_for(address: str) -> str | None:
    """What Sift calls the site an address is on, or None when the pack does not know it as a site.

    **The question a stash-box's list of addresses is filed by.** A box names the site beside each
    address with its own word for the KIND of link ("Reddit User", "Studio Profile", "Modeling
    Agency", "Home"), and a Site made from that word holds reddit.com, kink.com and a dozen
    agencies under one label that is not a site at all. The address's own host is the fact; this is
    Sift's name for it.

    **A host OR ANY DOMAIN ABOVE IT, and that is deliberately not what `slug_for` does.** A picture
    is matched on the exact host because drawing nothing is the safe way for a picture to be wrong.
    A name is a different question: `profiles.myfreecams.com` and `en.pornopedia.com` ARE those
    sites, and an exact match would call them unknown and turn a person's page on them into a bare
    link. Matched on a label boundary, never a substring (`notreddit.com` is not `reddit.com`),
    which is how the download slice answers the same question (`hosts.host_matches`). A bare last
    label (`com`) never matches anything.

    A link kind never answers: "Home" is what a box calls a person's own domain, and the domain is
    not a site Sift knows.
    """
    host = host_of(address)
    if not host:
        return None
    known = _names_in(MANIFEST).by_host
    labels = host.split(".")
    for start in range(len(labels) - 1):
        found = known.get(".".join(labels[start:]))
        if found is not None:
            return found
    return None


def is_a_label(name: str) -> bool:
    """True when a Site's NAME is a stash-box's word for a kind of link, not what any site is called.

    Two kinds of word, both read from the pack and neither written out here. The `link kind`
    entries: "Home", "Studio Profile", "Modeling Agency" and their spellings. And every ALIAS
    that is not also some site's own name: "Reddit User" and "Eporner profile" are words a box files
    a link to a known site under, and "Twitter" is what the site the pack calls X used to be called.
    Each of those names a site Sift calls something else, so a Site row wearing one is filed under
    a word rather than under a site. Compared without case, like every name in the pack.
    """
    folded = name.strip().casefold()
    return bool(folded) and folded in _names_in(MANIFEST).labels


def path_of(slug: str) -> Path | None:
    """The file for one slug, or None when the pack does not have it.

    **The manifest is the allowlist, and that is what makes this safe to call with a slug off the
    wire.** The route that serves an icon takes the slug from the address, so the alternative
    (composing a path and then checking where it landed) would put a confinement check between a
    stranger's string and the filesystem. Here the string is compared against a fixed list first,
    and the path is built from the manifest's own slug, never from the string that asked.
    """
    if slug not in _slugs():
        return None
    candidate = ICONS_DIR / f"{_by_slug()[slug].slug}.png"
    return candidate if candidate.is_file() else None


def slug_for_name(name: str) -> str | None:
    """The pack's slug for a site called this, or None. Compared without case. See `_by_name`."""
    return _by_name().get(name.strip().lower()) if name.strip() else None


def icon_for(address: str | None, name: str | None = None) -> Path | None:
    """The picture for one site: by its address where it has one, otherwise by what it is called.

    The address first, because it is the stronger key: two sites can share a name and no two share
    a host, but only an address that is the site's own front door (`_slug_of` says why). The
    name second, because most Sites in a library have no address at all.

    This is the one call a cover falls through to, and the one the icon route's neighbours use, so
    that "which picture does this site get" has a single answer rather than one per caller.
    """
    slug = _slug_of(address, name)
    return None if slug is None else path_of(slug)


def ships_a_good_one(address: str | None, name: str | None = None) -> bool:
    """Whether the pack already draws this site WELL: a `high` picture, matched as `icon_for` does.

    **The question asked before a stash-box is asked for a Site's picture.** A Site with no cover
    of its own falls through to the pack (`icon_for`), so a box's logo of a studio the pack holds
    at full quality is a request to somebody else's server for a picture nobody will see. A `low`
    entry (a small icon enlarged) is still worth a box's better picture, so it answers False, and
    so does a site the pack does not know or withholds.
    """
    slug = _slug_of(address, name)
    icon = _by_slug().get(slug) if slug is not None else None
    return icon is not None and icon.quality == "high" and path_of(icon.slug) is not None


def _slug_of(address: str | None, name: str | None) -> str | None:
    """The ONE match both `icon_for` and `tone_for` read: the address first, the name second.

    Shared rather than repeated, because the tone is a fact about the picture `icon_for` answers
    with: a second copy of the lookup could find one entry's picture and another entry's tone.

    **Only a site's ROOT address is its address** (`is_a_sites_own`). A Site's stored address is
    the first of its links, and a stash-box fills those in whatever order it holds them: a studio's
    links can come with its ThePornDB page first, so its row would carry that page and be drawn with
    ThePornDB's pink silhouette. A page is a page ON a site, very often a site about this one;
    the name decides then, and a page with no name to go by draws nothing rather than a guess.
    """
    slug = slug_for(address) if address and is_a_sites_own(address) else None
    if slug is None and name:
        slug = slug_for_name(name)
    return slug


def is_a_sites_own(address: str) -> bool:
    """True when an address is a site's front door (a bare host, or its root), not a page on it.

    A query string does not make a page (`?ref=...` on a home page is still the home page); any
    path beyond `/` does. `slug_for` is NOT held to this: it answers "which site is this link
    on", which a page on a site answers perfectly well; this is the narrower question of whether a
    Site row's address says which site the ROW is.
    """
    cleaned = address.strip()
    path = urlsplit(cleaned if "//" in cleaned else f"//{cleaned}").path
    return path in ("", "/")


def tone_for(address: str | None, name: str | None = None) -> str | None:
    """The tone of the picture `icon_for` answers this site with, or None when there is no picture.

    `light` or `dark` for a ONE-colour mark and `colour` for the rest. What reads it is the
    screen's ground: a white wordmark on a dark page and a black glyph on a light one are there and
    cannot be seen, so a screen draws a one-colour mark on the opposite shade. The pack records
    it (`scripts/site_icon_art.py tone_of`, weighted by opacity) because only the build has the
    pixels to hand; a screen that measured it would decode every logo twice.

    None, never a guess, where the pack has no picture: the answer is about a picture, and there
    is none to be drawn on anything.
    """
    slug = _slug_of(address, name)
    if slug is None or path_of(slug) is None:
        return None
    entry = _by_slug().get(slug)
    return None if entry is None else entry.tone


@cache
def token_of(path: Path) -> str:
    """The name of one shipped picture as it is served: the release, and a digest of its bytes.

    This is what lets a Site's logo be KEPT by the browser. A keepable reply promises that its
    address will not come to mean something else, and a Site's cover address falls through to the
    pack only while nobody has chosen a picture, so the address has to say which picture it is,
    exactly as a chosen cover's does (`kernel/covers.py names_its_cover`). Without a name the logo
    was answered the careful way, and a warm second visit to the Sites wall re-asked every one.

    **The bytes, because they are what the browser would keep.** A slug or a site's name says
    which ENTRY of the pack this is; it does not move when the build script fetches a new picture
    for the same site, and a logo kept under it would stay the old one for a week after an update.
    **And the release, because the reply is more than the bytes**: its type and its headers are
    this code's, and a release may change how a shipped file is served without changing the file.
    One re-ask per logo per release is the whole price of that.

    Cached per file for the life of the process: the pack is part of the installed application and
    cannot change while Sift runs (the same reason `every` is cached), so the file is read once.
    Sixteen hex digits of SHA-256, because this is a cache key and never a capability: nothing
    is decided by it except whether a reply may be kept.
    """
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        # Unreadable is answered by the file route as a miss; a token nobody can match is the
        # careful answer here, never an exception on a listing.
        digest = "unread"
    return f"{app_version() or 'dev'}-{digest}"


def icon_token(address: str | None, name: str | None = None) -> str | None:
    """The token naming the picture `icon_for` answers for this site, or None when there is none.

    Asked by the same two keys in the same order as `icon_for`, because it IS that answer's name:
    a site whose name or address changes to point at another entry gets another token, and a site
    the pack does not know gets none, which a listing reads as "no logo" exactly as it read the
    old flag.
    """
    found = icon_for(address, name)
    return None if found is None else token_of(found)


__all__ = [
    "ICONS_DIR",
    "ICON_PIXELS",
    "LINK_KIND",
    "MANIFEST",
    "TONES",
    "SiteIcon",
    "every",
    "host_of",
    "icon_for",
    "icon_token",
    "is_a_label",
    "is_a_sites_own",
    "name_for",
    "path_of",
    "ships_a_good_one",
    "slug_for",
    "slug_for_name",
    "token_of",
    "tone_for",
    "withheld",
]
