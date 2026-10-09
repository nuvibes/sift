# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading who made something off the page's schema.org metadata, when the address does not say.

Only a Person, never the site's own name, and best effort: no failure here costs a file."""

from __future__ import annotations

import contextlib
import html as markup
import json
import re
from typing import Any

from sift.slices.download.sources import curl
from sift.slices.download.sources.hosts import source_host
from sift.slices.download.sources.registry import classify, match_site

#: The file is already on disk; the name is a bonus, not worth stalling the queue.
_TIME_LIMIT = 12.0

_MAX_PAGE_BYTES = 4_000_000

_LD_JSON = re.compile(
    r'<script[^>]*\btype\s*=\s*["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)

_META = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
_OG_URL = re.compile(r"""\bproperty\s*=\s*["']og:url["']""", re.IGNORECASE)
_CONTENT = re.compile(r"""\bcontent\s*=\s*(["'])(.*?)\1""", re.IGNORECASE | re.DOTALL)

#: An Article's author is a journalist, not the uploader a library files under.
_MEDIA_TYPES = frozenset({"videoobject", "imageobject", "mediaobject", "audioobject"})

_CREATOR_KEYS = ("creator", "author", "uploader")

#: A name that long is a sentence the page put in the wrong field.
_MAX_NAME = 64


def _blocks(html: str) -> list[Any]:
    """Every JSON-LD block on the page, parsed, in document order, with the unparseable dropped."""
    found: list[Any] = []
    for match in _LD_JSON.finditer(html):
        with contextlib.suppress(ValueError):
            found.append(json.loads(match.group(1)))
    return found


def _flatten(block: Any) -> list[dict[str, Any]]:
    """Every object inside a block, `@graph` and bare lists included: publishers use all three."""
    if isinstance(block, list):
        return [item for entry in block for item in _flatten(entry)]
    if not isinstance(block, dict):
        return []
    found = [block]
    graph = block.get("@graph")
    if isinstance(graph, list):
        found.extend(item for entry in graph for item in _flatten(entry))
    return found


def _types_of(node: dict[str, Any]) -> set[str]:
    """The `@type` of a node, folded and always a set: it is legally a string or a list."""
    raw = node.get("@type")
    if isinstance(raw, str):
        return {raw.lower()}
    if isinstance(raw, list):
        return {entry.lower() for entry in raw if isinstance(entry, str)}
    return set()


def _person_name(value: Any) -> str | None:
    """The name on an author value only if it is typed a Person; a bare string is unknown."""
    if isinstance(value, list):
        for entry in value:
            name = _person_name(entry)
            if name is not None:
                return name
        return None
    if not isinstance(value, dict) or "person" not in _types_of(value):
        return None
    name = value.get("name")
    return name.strip() if isinstance(name, str) and name.strip() else None


def _site_names(nodes: list[dict[str, Any]], url: str) -> set[str]:
    """The names that are the site rather than a person: its Organization, and its own host."""
    names = {
        node["name"].strip().casefold()
        for node in nodes
        if ("organization" in _types_of(node) or "website" in _types_of(node))
        and isinstance(node.get("name"), str)
        and node["name"].strip()
    }
    host = source_host(url) or ""
    label = host.removeprefix("www.").split(".")[0]
    if label:
        names.add(label.casefold())
    return names


def creator_in_page(html: str, *, url: str) -> str | None:
    """The uploader's name from a page's schema.org metadata, else from its `og:url`, or None."""
    nodes = [node for block in _blocks(html) for node in _flatten(block)]
    forbidden = _site_names(nodes, url) if nodes else set()
    for node in nodes:
        if not (_types_of(node) & _MEDIA_TYPES):
            continue
        for key in _CREATOR_KEYS:
            name = _person_name(node.get(key))
            if name is None or len(name) > _MAX_NAME:
                continue
            if name.casefold() in forbidden:
                continue
            return name
    return creator_in_address(html, url=url)


def creator_in_address(html: str, *, url: str) -> str | None:
    """The uploader named by the page's own `og:url` on the same Site, if its users are people."""
    for tag in _META.findall(html):
        content = _CONTENT.search(tag)
        if _OG_URL.search(tag) is None or content is None:
            continue
        own = markup.unescape(content.group(2)).strip()
        asked = match_site(url)
        if asked is None or match_site(own) is not asked:
            return None
        said = classify(own)
        return said.username if said.username_is_a_person else None
    return None


async def creator_of(url: str, *, proxy: str | None = None) -> str | None:
    """Fetch `url` through the guard and read the uploader's name off it. Never raises."""
    try:
        fetched = await curl.guarded_get(url, proxy=proxy, time_limit=_TIME_LIMIT)
    except Exception:
        return None
    if fetched.status_code != 200:
        return None
    return creator_in_page(fetched.text[:_MAX_PAGE_BYTES], url=url)
