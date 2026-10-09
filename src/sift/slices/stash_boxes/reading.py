# SPDX-License-Identifier: AGPL-3.0-or-later
"""A stash-box's reply read into Sift's words: people, sites, tags, scenes and their pictures."""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sift.kernel.access.creator_studios import Verdict, read_studio
from sift.kernel.content.perceptual import distance
from sift.kernel.records import FoundRecord, Subject
from sift.kernel.site_icons import is_a_label, name_for, slug_for_name
from sift.kernel.urls import about_somebody, username_in

if TYPE_CHECKING:  # pragma: no cover (a name for the type checker only)
    from sift.slices.stash_boxes.adapter import Box

#: The algorithms whose agreement is an identity rather than a resemblance.
_EXACT_ALGORITHMS = frozenset({"OSHASH", "MD5"})


def _host_of(endpoint: str) -> str:
    from urllib.parse import urlsplit

    return (urlsplit(endpoint).hostname or endpoint).lower()


#: Reference hosts and names in addresses are the kernel's (`sift.kernel.urls`), for the catalog
#: too.


def _site_of(one: Mapping[str, Any]) -> dict[str, Any]:
    """The `site` object beside a URL, or an empty one; read only for its address and category."""
    site = one.get("site")
    return site if isinstance(site, dict) else {}


def _posting_site(one: Mapping[str, Any]) -> bool:
    """Whether this address is somewhere the person posts: the box's category, then the site's own
    address.
    """
    site = _site_of(one)
    return not about_somebody(str(site.get("url") or one.get("url") or ""), site.get("category"))


def _accounts(raw: Mapping[str, Any]) -> list[dict[str, str]]:
    """Their `urls[]`, as the usernames a person posts under.

    The Site is decided by the address's own host (`site_icons.name_for`), never by the `site`
    object's name, which names a kind of link. An unknown host gets a blank site for the writer to
    resolve. Reference databases and bare pages become plain links (`_reference_links`).
    """
    out: list[dict[str, str]] = []
    for one in raw.get("urls") or []:
        if not isinstance(one, dict) or not one.get("url") or not _posting_site(one):
            continue
        url = str(one.get("url"))
        handle = username_in(url)
        if not handle:
            continue
        out.append({"site": name_for(url) or "", "handle": handle, "url": url})
    return out


def _reference_links(raw: Mapping[str, Any]) -> list[str]:
    """Their `urls[]`, minus the ones that became usernames: pages about somebody, and bare pages."""
    return [
        str(one.get("url"))
        for one in raw.get("urls") or []
        if isinstance(one, dict)
        and one.get("url")
        and (not _posting_site(one) or not username_in(str(one.get("url"))))
    ]


def _person(box: Box, raw: Mapping[str, Any], *, confidence: float) -> FoundRecord:
    """Their performer, as Sift's person, in Sift's own field keys."""
    measurements = [
        raw.get("band_size"),
        raw.get("cup_size"),
        raw.get("waist_size"),
        raw.get("hip_size"),
    ]
    fields: dict[str, object] = {
        "name": raw.get("name"),
        "aliases": [one for one in (raw.get("aliases") or []) if isinstance(one, str)],
        # In `fields` too: the attribute is what a chooser draws, this is what an import writes.
        "disambiguation": raw.get("disambiguation"),
        "gender": raw.get("gender"),
        "birth_date": raw.get("birth_date"),
        "country": raw.get("country"),
        "ethnicity": raw.get("ethnicity"),
        "eye_color": raw.get("eye_color"),
        "hair_color": raw.get("hair_color"),
        "height_cm": raw.get("height"),
        "measurements": "-".join(str(one) for one in measurements if one),
        "breast_type": raw.get("breast_type"),
        "career_start_year": raw.get("career_start_year"),
        "career_end_year": raw.get("career_end_year"),
        "tattoos": _marks(raw.get("tattoos")),
        "piercings": _marks(raw.get("piercings")),
        # Split: a page somebody publishes to is a username on a site, a reference page a link.
        "links": _reference_links(raw),
        "accounts": _accounts(raw),
    }
    return FoundRecord(
        source_id=box.id,
        remote_id=str(raw.get("id") or ""),
        subject=Subject.PERSON,
        name=str(raw.get("name") or ""),
        disambiguation=raw.get("disambiguation") or None,
        image_url=_picture(raw.get("images")),
        # Every usable one, for starter references; a studio's pictures are a logo.
        pictures=_pictures(raw.get("images")),
        file_count=raw.get("scene_count"),
        fields={key: value for key, value in fields.items() if value not in (None, "", [])},
        extra=_extra(raw, taken=_PERSON_TAKEN),
        confidence=confidence,
    )


def _site(box: Box, raw: Mapping[str, Any], *, confidence: float) -> FoundRecord:
    """Their studio, as Sift's Site, with the parent as a name for a person to resolve."""
    parent = raw.get("parent") if isinstance(raw.get("parent"), dict) else {}
    own = str(raw.get("name") or "").strip()
    aliases = [one for one in (raw.get("aliases") or []) if isinstance(one, str)]
    name = own if parent else network_name(own)
    if name != own:
        aliases.append(own)
    box_parent = str((parent or {}).get("name") or "").strip()
    parent_name = network_name(box_parent) if box_parent else ""
    # A network and its flagship of one name are one Site, which has no parent.
    if parent_name.casefold() == (name or own).casefold():
        parent_name = ""
    fields: dict[str, object] = {
        "name": name or None,
        "aliases": aliases,
        "parent": parent_name or None,
        "links": [
            one.get("url")
            for one in (raw.get("urls") or [])
            if isinstance(one, dict) and one.get("url")
        ],
    }
    # The box's id for the parent, so a parent Site invented here can be linked (`enrich.linkable`).
    parent_id = str((parent or {}).get("id") or "")
    refs = (
        {Subject.SITE.value: dict.fromkeys((parent_name, box_parent), parent_id)}
        if parent_name and parent_id
        else {}
    )
    return FoundRecord(
        source_id=box.id,
        remote_id=str(raw.get("id") or ""),
        subject=Subject.SITE,
        name=str(raw.get("name") or ""),
        disambiguation=(parent or {}).get("name"),
        image_url=_picture(raw.get("images")),
        fields={key: value for key, value in fields.items() if value not in (None, "", [])},
        confidence=confidence,
        refs=refs,
    )


_NETWORK_BRACKET = re.compile(r"\s*\(\s*network\s*\)\s*$", re.IGNORECASE)


def network_name(name: str) -> str:
    """What Sift calls a network a box names: its name without the box's "(Network)" bracket."""
    cleaned = name.strip()
    bare = _NETWORK_BRACKET.sub("", cleaned).strip()
    return bare or cleaned


def _person_from_studio(box: Box, raw: Mapping[str, Any], *, confidence: float) -> FoundRecord:
    """A studio entry, as Sift's person, on a box that keeps its creators there.

    Only `aliases`, `links` and the picture are mapped; the parent is dropped, since a person has no
    such field.
    """
    parent = raw.get("parent") if isinstance(raw.get("parent"), dict) else {}
    fields: dict[str, object] = {
        "name": raw.get("name"),
        "aliases": [one for one in (raw.get("aliases") or []) if isinstance(one, str)],
        "links": [
            one.get("url")
            for one in (raw.get("urls") or [])
            if isinstance(one, dict) and one.get("url")
        ],
    }
    return FoundRecord(
        source_id=box.id,
        remote_id=str(raw.get("id") or ""),
        subject=Subject.PERSON,
        name=str(raw.get("name") or ""),
        disambiguation=(parent or {}).get("name"),
        image_url=_picture(raw.get("images")),
        fields={key: value for key, value in fields.items() if value not in (None, "", [])},
        confidence=confidence,
    )


def _tag(box: Box, raw: Mapping[str, Any], *, confidence: float) -> FoundRecord:
    """Their tag, as Sift's, with its category as one word."""
    category = raw.get("category") if isinstance(raw.get("category"), dict) else {}
    fields: dict[str, object] = {
        "name": raw.get("name"),
        "description": raw.get("description"),
        "aliases": [one for one in (raw.get("aliases") or []) if isinstance(one, str)],
        "category": (category or {}).get("name"),
    }
    return FoundRecord(
        source_id=box.id,
        remote_id=str(raw.get("id") or ""),
        subject=Subject.TAG,
        name=str(raw.get("name") or ""),
        disambiguation=(category or {}).get("name"),
        fields={key: value for key, value in fields.items() if value not in (None, "", [])},
        confidence=confidence,
    )


def _evidence(raw: Mapping[str, Any], sent: Mapping[str, str]) -> tuple[bool, int | None]:
    """Why a scene came back, from its own fingerprints: an exact hash sent, and the nearest perceptual
    distance.
    """
    exact = False
    nearest: int | None = None
    for one in raw.get("fingerprints") or ():
        if not isinstance(one, Mapping):
            continue
        algorithm = str(one.get("algorithm") or "").upper()
        value = str(one.get("hash") or "").lower()
        if not value:
            continue
        if algorithm in _EXACT_ALGORITHMS and sent.get(algorithm) == value:
            exact = True
        elif algorithm == "PHASH" and "PHASH" in sent:
            apart = distance(sent["PHASH"], value)
            if apart is not None and (nearest is None or apart < nearest):
                nearest = apart
    return exact, nearest


def _scene(box: Box, raw: Mapping[str, Any], *, confidence: float) -> FoundRecord:
    """Their scene, as one of Sift's files.

    On a box whose studios are people, the studio leads `people`, and `creator` names it. A creator
    filed as a studio under a network (`creator_account`), or a creator's own store crediting her
    (`creator_store`), is a username in `accounts`, not a Site. `disambiguation` keeps the studio
    name.
    """
    studio = raw.get("studio") if isinstance(raw.get("studio"), dict) else {}
    creator = (studio or {}).get("name") if box.studios_are_people else None
    people = [
        one.get("as") or (one.get("performer") or {}).get("name")
        for one in (raw.get("performers") or [])
        if isinstance(one, dict)
    ]
    # A creator's studio under a network, or her own store, is a username; never on a box whose
    # studios are people.
    account = (
        None
        if box.studios_are_people
        else creator_account(studio or {}) or creator_store(studio or {}, _credited(raw))
    )
    # The box's own id for each credit, keyed by the name that goes into `people`.
    refs = _scene_refs(box, raw, studio or {}, creator, account)
    fields: dict[str, object] = {
        "title": raw.get("title"),
        "details": raw.get("details"),
        "release_date": raw.get("release_date"),
        "production_date": raw.get("production_date"),
        "site_code": raw.get("code"),
        "duration_ms": (raw.get("duration") or 0) * 1000 or None,
        # Absent, not empty, where there is no site to invent: the username carries its Site.
        "site": None
        if box.studios_are_people or account is not None
        else (studio or {}).get("name"),
        "accounts": [account.entry()] if account is not None else None,
        "creator": creator,
        "people": [one for one in [creator, *people] if one],
        # A word each; a tag's own record is fetched when it is linked.
        "tags": [
            str(one.get("name"))
            for one in (raw.get("tags") or [])
            if isinstance(one, dict) and one.get("name")
        ],
        # Under `links`; `download_url` is where Sift fetched a copy.
        "links": [
            one.get("url")
            for one in (raw.get("urls") or [])
            if isinstance(one, dict) and one.get("url")
        ],
    }
    return FoundRecord(
        source_id=box.id,
        remote_id=str(raw.get("id") or ""),
        subject=Subject.ASSET,
        name=str(raw.get("title") or "Untitled"),
        disambiguation=(studio or {}).get("name"),
        image_url=_picture(raw.get("images")),
        fields={key: value for key, value in fields.items() if value not in (None, "", [])},
        confidence=confidence,
        refs=refs,
    )


def _scene_refs(
    box: Box,
    raw: Mapping[str, Any],
    studio: Mapping[str, Any],
    creator: object,
    account: CreatorAccount | None = None,
) -> dict[str, dict[str, str]]:
    """The box's id for each person, site and username a scene names, by kind and then by name."""
    people: dict[str, str] = {}
    sites: dict[str, str] = {}
    usernames: dict[str, str] = {}
    studio_id = str(studio.get("id") or "")
    if creator and studio_id:
        people[str(creator)] = studio_id
    elif account is not None and studio_id:
        usernames[account.handle] = studio_id
    elif not box.studios_are_people and studio.get("name") and studio_id:
        sites[str(studio["name"])] = studio_id
    for one in raw.get("performers") or []:
        if not isinstance(one, dict):
            continue
        performer = one.get("performer") if isinstance(one.get("performer"), dict) else {}
        name = one.get("as") or (performer or {}).get("name")
        remote_id = str((performer or {}).get("id") or "")
        if name and remote_id:
            people.setdefault(str(name), remote_id)
    refs: dict[str, dict[str, str]] = {}
    if people:
        refs[Subject.PERSON.value] = people
    if sites:
        refs[Subject.SITE.value] = sites
    if usernames:
        refs[USERNAME_REFS] = usernames
    return refs


#: The kind a creator's username is keyed under in `FoundRecord.refs`; not a `Subject`.
USERNAME_REFS = "username"

#: A studio named for a creator on a network: "<handle> (<network>)", the network in brackets.
_CREATOR_STUDIO = re.compile(r"(?P<handle>[^()]+?)\s+\((?P<network>[^()]+)\)")

#: The word a box puts after a network's name, in brackets or not: "<name> (network)".
_NETWORK_WORD = re.compile(r"\s*\(?\s*network\s*\)?\s*$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class CreatorAccount:
    """A creator a box files as a studio, read as what it is: a username on a Site."""

    #: What Sift calls the network's Site, from the creator's own page where the box gave one.
    site: str
    handle: str
    url: str | None

    def entry(self) -> dict[str, str]:
        """The username as an `accounts` entry: the shape a person's usernames are offered in."""
        return {"site": self.site, "handle": self.handle, "url": self.url or ""}


def creator_account(studio: Mapping[str, Any]) -> CreatorAccount | None:
    """The username a studio stands for, or None where the studio is a studio.

    The shape is the test: a parent, and a name carrying the parent's in brackets. The Site is the
    host of the creator's own page, or the network's name where the icon pack knows it.
    """
    parent = studio.get("parent") if isinstance(studio.get("parent"), dict) else {}
    network = _NETWORK_WORD.sub("", str((parent or {}).get("name") or "")).strip()
    shaped = _CREATOR_STUDIO.fullmatch(str(studio.get("name") or "").strip())
    if not network or shaped is None:
        return None
    if shaped["network"].strip().casefold() != network.casefold():
        return None
    handle = shaped["handle"].strip()
    for one in studio.get("urls") or []:
        url = str(one.get("url") or "").strip() if isinstance(one, dict) else ""
        site = name_for(url) if url else None
        if site:
            return CreatorAccount(site=site, handle=handle, url=url)
    if is_a_label(network) or slug_for_name(network) is None:
        return None
    return CreatorAccount(site=network, handle=handle, url=None)


def _credited(raw: Mapping[str, Any]) -> list[str]:
    """Every name a scene credits a performer by: the name it used, and the performer's own."""
    names: list[str] = []
    for one in raw.get("performers") or []:
        if not isinstance(one, dict):
            continue
        performer = one.get("performer") if isinstance(one.get("performer"), dict) else {}
        for name in (one.get("as"), (performer or {}).get("name")):
            if isinstance(name, str) and name.strip():
                names.append(name)
    return names


def creator_store(studio: Mapping[str, Any], credited: list[str]) -> CreatorAccount | None:
    """The username a creator's own studio stands for, read off one scene; None for a Site."""
    name = str(studio.get("name") or "").strip()
    if not name:
        return None
    aliases = [one for one in (studio.get("aliases") or []) if isinstance(one, str)]
    links = [
        str(one.get("url"))
        for one in (studio.get("urls") or [])
        if isinstance(one, dict) and one.get("url")
    ]
    reading = read_studio(name, aliases, links, [credited])
    if reading.verdict is not Verdict.USERNAME or reading.home is None:
        return None
    home = reading.home
    return CreatorAccount(site=home.site, handle=home.handle, url=home.url)


#: Every key of a performer that lands somewhere in Sift, written out, for `_extra`.
_PERSON_TAKEN = frozenset(
    {
        "id",
        "name",
        "disambiguation",
        "aliases",
        "gender",
        "birth_date",
        "country",
        "ethnicity",
        "eye_color",
        "hair_color",
        "height",
        "cup_size",
        "band_size",
        "waist_size",
        "hip_size",
        "breast_type",
        "career_start_year",
        "career_end_year",
        "tattoos",
        "piercings",
        "images",
        "urls",
        "merged_into_id",
    }
)


def _extra(raw: Mapping[str, Any], *, taken: frozenset[str]) -> dict[str, object]:
    """Everything the stash-box sent that nothing in Sift claims, empty values dropped."""
    out: dict[str, object] = {}
    for key, value in raw.items():
        if key in taken or value is None or value == "" or value == [] or value == {}:
            continue
        out[key] = value
    return out


def ranked(found: list[FoundRecord], term: str) -> list[FoundRecord]:
    """The entries that answer everything that was typed, first, and the rest marked as not.

    Public, because a cached answer is marked too. Marked rather than dropped: a person filed under
    a spelling nobody here would type is found only by the fuzzy half. Every name an entry answers
    to counts.
    """
    words = [one for one in term.casefold().split() if one]
    if not words:
        return found
    marked = [
        dataclasses.replace(one, every_word=all(word in _every_name(one) for word in words))
        for one in found
    ]
    # A stable partition, keeping the service's own ranking within each half.
    return [one for one in marked if one.every_word] + [one for one in marked if not one.every_word]


def _every_name(found: FoundRecord) -> str:
    """Every spelling an entry answers to, folded and run together, for a substring test."""
    aliases = found.fields.get("aliases")
    spellings = [found.name, found.disambiguation or ""]
    if isinstance(aliases, list):
        spellings.extend(str(one) for one in aliases)
    return " ".join(spellings).casefold()


def _marks(raw: Any) -> list[str]:
    """Tattoos and piercings, each as one readable line."""
    out = []
    for one in raw or []:
        if not isinstance(one, dict):
            continue
        where, what = one.get("location"), one.get("description")
        out.append(f"{where}: {what}" if where and what else str(where or what or ""))
    return [one for one in out if one]


def _picture(images: Any) -> str | None:
    """The largest usable picture; a `width: -1` picture ranks after every sized one."""
    every = _pictures(images)
    return every[0] if every else None


def _pictures(images: Any) -> tuple[str, ...]:
    """Every usable picture, largest first, then those the box gives no size for (a vector logo)."""
    sized: list[dict[str, Any]] = []
    unsized: list[dict[str, Any]] = []
    for one in images or []:
        if not isinstance(one, dict) or not one.get("url"):
            continue
        width, height = one.get("width"), one.get("height")
        if isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0:
            sized.append(one)
        else:
            unsized.append(one)
    ranked = sorted(sized, key=lambda one: -(one["width"] * one["height"])) + unsized
    return tuple(dict.fromkeys(str(one["url"]) for one in ranked))
