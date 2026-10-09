# SPDX-License-Identifier: AGPL-3.0-or-later
"""The site logos that ship with Sift, found by exact host or by name.

Shipped as files, never fetched at run time: a fetch would tell sites what a library holds."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from urllib.parse import urlsplit

from sift.kernel.version import app_version

_HERE = Path(__file__).resolve().parent

ICONS_DIR = _HERE / "icons"

#: Written by `scripts/build_site_icons.py`.
MANIFEST = _HERE / "manifest.json"

#: Exactly 256, a size and not a cap: a card's mark is about 192 device pixels at ratio 2.
ICON_PIXELS = 256


@dataclass(frozen=True, slots=True)
class SiteIcon:
    """One entry in the pack: a picture, the hosts it stands for, and what the site is called."""

    slug: str
    name: str
    #: Every host this icon answers for, lower case, without `www.`.
    hosts: tuple[str, ...]
    #: Former names and a stash-box's link words for it, matched exactly as the name is.
    aliases: tuple[str, ...] = ()
    tone: str = "colour"
    #: `low` where the build could only enlarge a small picture.
    quality: str = "high"


#: `light` and `dark` are one-colour marks; anything unknown is read as `colour`.
TONES = frozenset({"light", "dark", "colour"})


def _read() -> dict[str, object]:
    try:
        return dict(json.loads(MANIFEST.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        # No pack, or a damaged manifest: every caller already draws a letter instead.
        return {}


@cache
def every() -> tuple[SiteIcon, ...]:
    """Every entry the pack ships a picture for, cached as it cannot change while running."""
    return _entries("icons")


def withheld() -> tuple[SiteIcon, ...]:
    """Every entry the pack knows and ships no picture for; uncached so tests need not clear it."""
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
                # Unsaid is `low`: skipping a fetch on a guess would leave a Site with less.
                quality="high" if entry.get("quality") == "high" else "low",
            )
        )
    return tuple(found)


@cache
def _slugs() -> frozenset[str]:
    """Every slug the pack ships a picture for: the allowlist, never a withheld slug."""
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
    """Host -> slug, withheld hosts after shipped ones so a withheld site is still recognised."""
    index: dict[str, str] = {}
    for icon in (*every(), *withheld()):
        for host in icon.hosts:
            # The first entry wins; a test refuses two claiming one host.
            index.setdefault(host, icon.slug)
    return index


@cache
def _by_name() -> dict[str, str]:
    """Name -> slug without case, names before aliases; most Sites in a library have no address."""
    index: dict[str, str] = {}
    known = (*every(), *withheld())
    for icon in known:
        index.setdefault(icon.name.strip().lower(), icon.slug)
    for icon in known:
        for alias in icon.aliases:
            index.setdefault(alias.strip().lower(), icon.slug)
    return index


def host_of(address: str) -> str:
    """The host an address or bare domain is on, lower case, without a leading `www.`, or empty."""
    cleaned = address.strip()
    if not cleaned:
        return ""
    host = urlsplit(cleaned if "//" in cleaned else f"//{cleaned}").hostname or ""
    return host.lower().removeprefix("www.")


#: Databases and indexes of sites: a link on one names where a studio is written about.
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
    """The pack's slug for an address's site, withheld ones included, or None."""
    host = host_of(address)
    return _by_host().get(host) if host else None


#: The withheld reason marking a kind of link ("Home") rather than a site.
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
    """What the pack calls each site, cached per manifest path so tests stay apart."""
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
    """What Sift calls an address's site, matched on the host or any domain above it, or None."""
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
    """True when a Site's name is a stash-box's word for a kind of link, not any site's name."""
    folded = name.strip().casefold()
    return bool(folded) and folded in _names_in(MANIFEST).labels


def path_of(slug: str) -> Path | None:
    """The file for one slug, or None; the manifest is the allowlist, so a wire slug is safe."""
    if slug not in _slugs():
        return None
    candidate = ICONS_DIR / f"{_by_slug()[slug].slug}.png"
    return candidate if candidate.is_file() else None


def slug_for_name(name: str) -> str | None:
    """The pack's slug for a site called this, or None. Compared without case. See `_by_name`."""
    return _by_name().get(name.strip().lower()) if name.strip() else None


def icon_for(address: str | None, name: str | None = None) -> Path | None:
    """The picture for one site: by its own root address, otherwise by its name."""
    slug = _slug_of(address, name)
    return None if slug is None else path_of(slug)


def ships_a_good_one(address: str | None, name: str | None = None) -> bool:
    """Whether the pack draws this site at `high` quality, so no stash-box need be asked."""
    slug = _slug_of(address, name)
    icon = _by_slug().get(slug) if slug is not None else None
    return icon is not None and icon.quality == "high" and path_of(icon.slug) is not None


def _slug_of(address: str | None, name: str | None) -> str | None:
    """The one match `icon_for` and `tone_for` share: a root address first, then the name."""
    slug = slug_for(address) if address and is_a_sites_own(address) else None
    if slug is None and name:
        slug = slug_for_name(name)
    return slug


def is_a_sites_own(address: str) -> bool:
    """True when an address is a site's front door (a bare host or its root), not a page."""
    cleaned = address.strip()
    path = urlsplit(cleaned if "//" in cleaned else f"//{cleaned}").path
    return path in ("", "/")


def tone_for(address: str | None, name: str | None = None) -> str | None:
    """The tone of the picture `icon_for` answers with, recorded by the build; None without one."""
    slug = _slug_of(address, name)
    if slug is None or path_of(slug) is None:
        return None
    entry = _by_slug().get(slug)
    return None if entry is None else entry.tone


@cache
def token_of(path: Path) -> str:
    """The release and a digest of the picture's bytes, so the browser may keep the logo."""
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        # The file route answers a miss; a token nobody matches is the careful answer here.
        digest = "unread"
    return f"{app_version() or 'dev'}-{digest}"


def icon_token(address: str | None, name: str | None = None) -> str | None:
    """The token naming the picture `icon_for` answers for this site, or None."""
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
