# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which sites the site-icon pack holds: the candidates, their layers, and how they merge."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import site_icon_catalog as catalog
from site_icon_pages import host_of

from sift.slices.download.sources.sites.catalog import SITES


@dataclass
class Candidate:
    """A site on its way into the pack: where it lives, what it is called, who named it."""

    host: str
    name: str
    #: `sift` for a site the downloader supports, otherwise the box's word.
    source: str
    #: The studio this came from, where it came from a studio at all.
    studio_id: str | None = None
    #: How many files the network has, where that was measured.
    scenes: int | None = None
    hosts: set[str] = field(default_factory=set)
    #: Old words for it, so a library row still wearing one draws the right picture.
    aliases: tuple[str, ...] = ()
    #: Decided once all are merged, so two sites wanting one word are told apart by the whole set.
    slug: str = ""
    #: The icon each stash-box files this SITE under (`querySites ... icon`), best-known first.
    box_icons: list[str] = field(default_factory=list)
    #: The box's logo for this studio: the one source that tells a label from its network.
    studio_image: str | None = None


#: Sites the three layers cannot reach, each with its reason: `(host, name, older names)`. Plain
#: data, since `merge` writes to the candidates it is handed.
PACK_EXTRAS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    # Neither a downloader nor a stash-box reaches Imgur, yet a library gets a Site called Imgur.
    ("imgur.com", "Imgur", ()),
    # A network's labels: layer three fetches only the network.
    ("vixen.com", "Vixen", ()),
    ("tushy.com", "Tushy", ()),
    ("blacked.com", "Blacked", ()),
    ("deeper.com", "Deeper", ()),
    ("slayed.com", "Slayed", ()),
    ("milfy.com", "Milfy", ()),
    ("blackedraw.com", "BlackedRaw", ("Blacked Raw",)),
    ("tushyraw.com", "TushyRaw", ("Tushy Raw",)),
)


def named_sites() -> list[Candidate]:
    """Layer four: the sites above and `catalog.NAMED_SITES`, as fresh candidates."""
    return [
        Candidate(host=host, name=name, source="named", hosts={host}, aliases=aliases)
        for host, name, aliases in (*PACK_EXTRAS, *catalog.NAMED_SITES)
    ]


def by_name_only() -> list[Candidate]:
    """The entries with no address at all: every link kind (withheld)."""
    return [
        Candidate(host="", name=name, source="kind", slug=slug, aliases=also)
        for slug, name, also in catalog.LINK_KINDS
    ]


#: The generic labels a country's ending carries under it (`.com.br`, `.co.uk`, `.net.au`).
_SECOND_LEVEL = frozenset({"com", "co", "net", "org", "ac", "gov", "edu", "ne", "or", "go"})


def slug_of(host: str, taken: dict[str, str]) -> str:
    """`onlyfans.com` -> `onlyfans`; a second site wanting the word gets its suffix joined on."""
    labels = [part for part in host.split(".") if part]
    # Under a country's two-letter ending, a generic label (`com`, `co`, ...) is part of the suffix.
    if len(labels) > 2 and len(labels[-1]) == 2 and labels[-2] in _SECOND_LEVEL:
        labels = labels[:-1]
    stem = labels[-2] if len(labels) > 1 else labels[0]
    base = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-") or "site"
    if taken.get(base) in (None, host):
        return base
    widened = re.sub(r"[^a-z0-9]+", "-", host.lower()).strip("-")
    return widened


def sift_supported() -> list[Candidate]:
    """Layer one: every site the downloader knows, read off the catalog it is declared in."""
    found: list[Candidate] = []
    for record in SITES:
        hosts = {host_of(one) for one in record.hosts}
        hosts.discard("")
        if not hosts:
            continue
        # The first host is the site's own; the rest are mirrors that answer to the same picture.
        first = host_of(record.hosts[0])
        found.append(Candidate(host=first, name=record.site, source="sift", hosts=hosts))
    return found


def merge(layers: list[list[Candidate]]) -> list[Candidate]:
    """One entry per host over every host it claims, the earliest layer's name winning."""
    by_host: dict[str, Candidate] = {}
    for layer in layers:
        for one in layer:
            for host in list(one.hosts | {one.host}):
                joined = catalog.HOST_JOINS.get(host)
                if joined:
                    one.hosts.add(joined)
                    if one.host == host:
                        one.host = joined
            held = next(
                (by_host[host] for host in sorted(one.hosts | {one.host}) if host in by_host), None
            )
            if held is None:
                held = one
            elif held is not one:
                held.hosts |= one.hosts
                # A later layer's name becomes an alias of the one that won.
                held.aliases = tuple(dict.fromkeys((*held.aliases, *one.aliases, one.name)))
                held.box_icons = list(dict.fromkeys((*held.box_icons, *one.box_icons)))
                held.studio_image = held.studio_image or one.studio_image
                if one.scenes is not None and (held.scenes or 0) < one.scenes:
                    held.scenes = one.scenes
                    held.studio_id = held.studio_id or one.studio_id
            for host in held.hosts | {held.host}:
                by_host[host] = held
    # By identity: a Candidate is mutable, and two sites can share every field.
    once = {id(one): one for one in by_host.values()}
    _name_by_the_catalog(once.values())
    return sorted(
        once.values(),
        key=lambda one: (0 if one.source == "sift" else 1, -(one.scenes or 0), one.host),
    )


def _name_by_the_catalog(merged: Iterable[Candidate]) -> None:
    """The catalog's name for each site, and every other word it answers to as an alias."""
    for one in merged:
        named = next((catalog.NAMES[h] for h in sorted(one.hosts) if h in catalog.NAMES), None)
        if named and named != one.name:
            one.aliases = (*one.aliases, one.name)
            one.name = named
        extra = [word for host in sorted(one.hosts) for word in catalog.ALIASES.get(host, ())]
        own = one.name.strip().lower()
        one.aliases = tuple(
            word
            for word in dict.fromkeys((*one.aliases, *extra))
            if word.strip() and word.strip().lower() != own
        )


def held_sites(held: dict[str, Any]) -> list[Candidate]:
    """The pack as it stands, as candidates: what a run without the stash-box keys rebuilds."""
    found: list[Candidate] = []
    # Withheld entries too, so the lookup still knows the site it has no picture of.
    for entry in [*held.get("icons", []), *held.get("withheld", [])]:
        if not isinstance(entry, dict):
            continue
        hosts = [str(one) for one in entry.get("hosts") or []]
        if not hosts:
            continue
        # Off the manifest itself: `BoxPictures`' working cache can be gone.
        picture, address = str(entry.get("picture") or ""), str(entry.get("picture_url") or "")
        found.append(
            Candidate(
                host=hosts[0],
                name=str(entry.get("name") or entry["slug"]),
                source=str(entry.get("source") or "held"),
                studio_id=entry.get("studio_id"),
                scenes=entry.get("files"),
                hosts=set(hosts),
                aliases=tuple(str(one) for one in entry.get("aliases") or []),
                slug=str(entry["slug"]),
                studio_image=address if picture == "stash-box studio logo" else None,
                box_icons=[address] if picture == "stash-box site icon" else [],
            )
        )
    # And every site the last run could not picture: it may have a source now.
    for entry in held.get("missing", []):
        host = str(entry.get("host") or "") if isinstance(entry, dict) else ""
        if "." in host:
            found.append(
                Candidate(
                    host=host, name=str(entry.get("name") or host), source="held", hosts={host}
                )
            )
    return found


def withheld_reason(one: Candidate) -> str | None:
    """Why this entry ships no picture (`catalog.WITHHELD_WHY`), or None when it ships one."""
    return catalog.withheld_reason(one.hosts | {one.host} - {""}, kind=one.source == "kind")


def withheld_entry(one: Candidate, reason: str) -> dict[str, object]:
    """One `withheld` record: all `held_sites` needs to withhold it again, and why."""
    return {
        "slug": one.slug,
        "name": one.name,
        "hosts": sorted(one.hosts - {""}),
        **({"aliases": sorted(one.aliases)} if one.aliases else {}),
        "source": one.source,
        "studio_id": one.studio_id,
        "files": one.scenes,
        "reason": reason,
        "why": catalog.WITHHELD_WHY[reason],
    }
