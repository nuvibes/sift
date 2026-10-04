# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading who made something off the page, when the address does not say.

Most of the sites in the registry put the creator's username in the path, and reading it there costs
nothing. A great many others do not: the address is a title and an id, and the only place the
uploader's name appears is the page. Those downloads arrive filed under a site and nobody.

This reads the name out of the page's **schema.org metadata**, which is a published standard rather
than a per-site scrape. A video page that wants to appear properly in a search result carries a
`VideoObject` in a `<script type="application/ld+json">` block, and that object names its `creator`
or its `author`. So one reader serves every site that publishes it, and a site that does not is
simply back where it started: filed under itself, with no name invented. Where a page carries no
such block, the address it gives for itself (`og:url`) is read by the Site's own username rule.

Three rules keep it from inventing people:

**Only a `Person`.** Where the author is an `Organization`, the author is the site, and filing every
video on a site under a Person named after the site is worse than filing them under nobody.

**Never the site's own name.** Some pages declare a Person whose name is the brand. The name is
compared against the organization the same page declares and against the host, and a match is
dropped.

**Best effort, always.** A page that will not load, a block that will not parse, a shape that is not
what was expected: all of them return nothing. This runs inside a download that has already
succeeded, and no failure here may cost somebody their file.
"""

from __future__ import annotations

import contextlib
import html as markup
import json
import re
from typing import Any

from sift.slices.download.sources import curl
from sift.slices.download.sources.hosts import source_host
from sift.slices.download.sources.registry import classify, match_site

#: How long the page is given. This is metadata on a download that has already finished: the file
#: is on disk and the name is a bonus, so a slow site costs a few seconds and not the attribution of
#: everything behind it in the queue.
_TIME_LIMIT = 12.0

#: A ceiling on the page read. Enough for any amount of head metadata; short of loading a document
#: that is mostly an inlined application into memory to look at its first kilobyte.
_MAX_PAGE_BYTES = 4_000_000

_LD_JSON = re.compile(
    r'<script[^>]*\btype\s*=\s*["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)

_META = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
_OG_URL = re.compile(r"""\bproperty\s*=\s*["']og:url["']""", re.IGNORECASE)
_CONTENT = re.compile(r"""\bcontent\s*=\s*(["'])(.*?)\1""", re.IGNORECASE | re.DOTALL)

#: The schema.org types whose creator is worth reading. A `VideoObject` or an `ImageObject` has an
#: uploader; an `Article` has a journalist and a `Product` has a manufacturer, and neither of those
#: is the sense of "who made this" that a media library files things under.
_MEDIA_TYPES = frozenset({"videoobject", "imageobject", "mediaobject", "audioobject"})

#: Where a creator hides, in the order worth trusting. `creator` is the more specific of the two and
#: `author` the more widely emitted; a page carrying both usually agrees with itself.
_CREATOR_KEYS = ("creator", "author", "uploader")

#: Anything at or beyond this is not a username. A name that long is a sentence, and a sentence in
#: the author field means the page put something else there.
_MAX_NAME = 64


def _blocks(html: str) -> list[Any]:
    """Every JSON-LD block on the page, parsed, with the unparseable dropped.

    A page may carry several: an Organization for the site, a BreadcrumbList for the navigation, and
    the media object itself, which is the one wanted. They are returned in document order and sorted
    through by the caller rather than here, because the Organization block is needed too: it is
    what says which name is the site's own.
    """
    found: list[Any] = []
    for match in _LD_JSON.finditer(html):
        with contextlib.suppress(ValueError):
            found.append(json.loads(match.group(1)))
    return found


def _flatten(block: Any) -> list[dict[str, Any]]:
    """Every object inside a block, including the ones inside an `@graph` or a bare list.

    Publishers wrap these three ways interchangeably and all three are correct, so a reader that
    handles only the one shape it first met works on one site and silently finds nothing on the
    next.
    """
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
    """The name on an author or creator value, if it is a Person and it carries one.

    A value that is a bare string is deliberately refused. A page writing `"author": "Some Name"`
    has not said whether that is a human or the publication, and the whole guard here is that
    distinction, so an unlabelled one is treated as unknown rather than assumed.
    """
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
    """The names that are the SITE rather than a person: its Organization, and its own host.

    Both are needed. A page that declares `Organization: PMVHaven` is caught by the first; a page
    that declares no organization but names its author after itself is caught by the second, which
    is the host with its domain suffix removed.
    """
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
    """The uploader's name from a page's schema.org metadata, or None.

    Pure, so the whole of the decision is testable against a saved page with no network involved,
    which matters here more than usual, because the thing being tested is a judgement about somebody
    else's markup and the only honest fixture is the real thing.
    """
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
    """The uploader named by the page's own address (`og:url`), read by the Site's username rule.

    A post link that names nobody (`/p/<code>/`) is published under one that does
    (`/<user>/p/<code>/`). Only an address on the same Site counts, and only a Site whose
    usernames are people.
    """
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
    """Fetch `url` and read the uploader's name off it. Never raises.

    The address goes through the same guard as every other fetch this slice makes, so a page that
    resolves somewhere the server may not be pointed at is refused here exactly as it would be
    anywhere else. Everything after that is swallowed: this decorates a download that has already
    landed, and there is no failure of it worth surfacing to somebody who just wanted the file.
    """
    try:
        fetched = await curl.guarded_get(url, proxy=proxy, time_limit=_TIME_LIMIT)
    except Exception:
        return None
    if fetched.status_code != 200:
        return None
    return creator_in_page(fetched.text[:_MAX_PAGE_BYTES], url=url)
