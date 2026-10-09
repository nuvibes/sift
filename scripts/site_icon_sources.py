# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where a site's picture is found, best first, and how one becomes its icon."""

from __future__ import annotations

import base64
import json
import re
import threading
import urllib.error
import urllib.parse
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace

import site_icon_art as art
import site_icon_catalog as catalog
from site_icon_pages import (
    _get,
    declared_icons,
    manifest_addresses,
    manifest_icons,
    og_image,
    page_logos,
)
from site_icon_sites import Candidate, withheld_reason

#: Addresses one site is fetched from; a picture reaching `ICON_PIXELS` stops it early.
HOW_MANY_TRIES = 6


#: How far from square an `og:image` may be: most are banners, a correct picture of the wrong thing.
OG_SQUARENESS = 1.25


#: A favicon is a few kilobytes; a megabyte of one is a page pretending to be one.
MAX_ICON_BYTES = 1024 * 1024


#: Said of bytes that do not decode: some sites answer a missing favicon with their home page.
_NOT_A_PICTURE = "could not be read as a picture"


_GENERATED = re.compile(r"/(?:favicon-\d+x\d+|apple-touch-icon[^/]*|android-chrome-\d+x\d+)\.png$")


def _generator_siblings(declared: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """The two big files a favicon generator writes beside the small ones a page declares."""
    found: list[tuple[int, str]] = []
    for _, address in declared:
        path = urllib.parse.urlsplit(address).path
        if _GENERATED.search(path):
            folder = urllib.parse.urljoin(address, ".")
            found += [
                (512, f"{folder}android-chrome-512x512.png"),
                (192, f"{folder}android-chrome-192x192.png"),
            ]
    return found


def icon_tiers(
    host: str,
    declared: list[tuple[int, str]],
    *,
    from_manifest: list[tuple[int, str]] | None = None,
    og: str | None = None,
) -> tuple[list[str], list[str]]:
    """Where a site's picture may be: the trusted sources by size, then the last resort in order.

    A bigger file is not a better logo: a site's `/favicon.png` can be a valid 404 picture bigger
    than its real mark, so the last resort is taken first-that-decodes, never by size.
    """
    trusted = [
        *declared,
        *_generator_siblings(declared),
        *(from_manifest or []),
        # The one conventional address with a size convention behind it.
        (180, f"https://{host}/apple-touch-icon.png"),
    ]
    # Stable, so a tie keeps the order above.
    trusted.sort(key=lambda one: -one[0])
    resort = [f"https://{host}/favicon.png", f"https://{host}/favicon.ico", *([og] if og else [])]
    return (
        list(dict.fromkeys(url for _, url in trusted)),
        list(dict.fromkeys(resort)),
    )


def icon_candidates(
    host: str,
    declared: list[tuple[int, str]],
    *,
    from_manifest: list[tuple[int, str]] | None = None,
    og: str | None = None,
) -> list[str]:
    """Every address this site will be asked for its picture, in order: `icon_tiers` flattened."""
    trusted, resort = icon_tiers(host, declared, from_manifest=from_manifest, og=og)
    return list(dict.fromkeys([*trusted, *resort]))


_COMMONS_API = "https://commons.wikimedia.org/w/api.php"


_SIMPLE_ICONS = "https://cdn.jsdelivr.net/npm/simple-icons@{version}/{path}"


class Elsewhere:
    """Wikimedia Commons and Simple Icons, asked lazily and once, shared by every thread."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._commons: dict[str, str | None] = {}
        self._brands: dict[str, tuple[str, str]] | None = None

    def commons(self, title: str) -> str | None:
        """The file address of a Commons title, or None when there is no such file."""
        with self._lock:
            if title in self._commons:
                return self._commons[title]
        query = urllib.parse.urlencode(
            {
                "action": "query",
                "format": "json",
                "prop": "imageinfo",
                "iiprop": "url",
                "titles": f"File:{title}",
            }
        )
        found: str | None = None
        try:
            answer = json.loads(_get(f"{_COMMONS_API}?{query}", 200_000))
            for page in answer["query"]["pages"].values():
                info = page.get("imageinfo") or []
                if info:
                    found = str(info[0]["url"])
        except (urllib.error.URLError, TimeoutError, ValueError, OSError, KeyError):
            found = None
        with self._lock:
            self._commons[title] = found
        return found

    def brands(self) -> dict[str, tuple[str, str]]:
        """Simple Icons' brands: lower-case title (and every alias) -> (slug, hex colour)."""
        with self._lock:
            if self._brands is not None:
                return self._brands
        index: dict[str, tuple[str, str]] = {}
        try:
            address = _SIMPLE_ICONS.format(
                version=catalog.SIMPLE_ICONS_VERSION, path="data/simple-icons.json"
            )
            for one in json.loads(_get(address, 4_000_000)):
                pair = (str(one["slug"]), str(one["hex"]))
                index.setdefault(str(one["title"]).strip().lower(), pair)
                index.setdefault(f"slug:{one['slug']}", pair)
        except (urllib.error.URLError, TimeoutError, ValueError, OSError, KeyError, TypeError):
            index = {}
        with self._lock:
            self._brands = index
        return index

    def simple_icon(self, slug: str, hex_colour: str) -> bytes | None:
        """One Simple Icons glyph as an SVG, filled with its brand colour."""
        address = _SIMPLE_ICONS.format(
            version=catalog.SIMPLE_ICONS_VERSION, path=f"icons/{slug}.svg"
        )
        raw, _ = _read(address)
        if raw is None or not re.fullmatch(r"[0-9A-Fa-f]{6}", hex_colour):
            return None
        return raw.replace(b"<svg ", f'<svg fill="#{hex_colour}" '.encode("ascii"), 1)


def keys_of(one: Candidate) -> list[str]:
    """What the hand-kept tables are keyed by for this entry: its hosts, then its slug."""
    return [*sorted(one.hosts | ({one.host} if one.host else set())), one.slug]


def simple_icon_for(one: Candidate, brands: dict[str, tuple[str, str]]) -> tuple[str, str] | None:
    """The Simple Icons brand by hand-kept slug, or by name, never for a long-tail studio."""
    for key in keys_of(one):
        slug = catalog.SIMPLE_ICONS.get(key)
        if slug and f"slug:{slug}" in brands:
            return brands[f"slug:{slug}"]
    if one.studio_id is not None or one.source == "kind":
        return None
    for word in (one.name, *one.aliases):
        found = brands.get(word.strip().lower())
        if found:
            return found
    return None


@dataclass(frozen=True, slots=True)
class Made:
    """One site's finished icon and where it came from."""

    icon: art.Icon
    #: Which source it came from, in the words `picture_sources` uses.
    source: str
    #: The address it was read from.
    url: str


def picture_sources(
    one: Candidate,
    page: str,
    elsewhere: Elsewhere,
    manifest_of: Callable[[], list[tuple[int, str]]] = lambda: [],
) -> Iterator[tuple[str, str]]:
    """Every place this site's picture may be, best first, as `(source, address)`.

    Lazy, so a site that answers on its first source costs one request. The order: picked by hand,
    a studio's box logo, the site's SVG, Commons, its raster icons, the box's site icon, Simple
    Icons, its page logo, then the last resort. `catalog.NOT_ITS_MARK` skips the site's own.
    """
    host = one.host
    yield from _picked(one)
    theirs = not any(key in catalog.NOT_ITS_MARK for key in keys_of(one))
    # Only for a studio nobody knows as a site: a fan site's box logo is a wordmark.
    studio_first = bool(one.studio_image) and one.source != "sift" and not one.box_icons
    if studio_first and one.studio_image:
        yield ("stash-box studio logo", one.studio_image)
    declared = declared_icons(page, f"https://{host}/") if host and page and theirs else []
    for weight, address in declared:
        if weight < 0:
            yield ("site svg", address)
    yield from _on_commons(one, elsewhere)
    if host and theirs:
        from_manifest = manifest_of()
        rasters = [(weight, address) for weight, address in declared if weight >= 0]
        og = og_image(page, f"https://{host}/")
        trusted, resort = icon_tiers(host, rasters, from_manifest=from_manifest, og=og)
        for address in trusted[:HOW_MANY_TRIES]:
            yield ("site", address)
    else:
        og, resort = None, []
    # The box's icon for a site is a copy of the site's own favicon, so it is skipped with them.
    for address in one.box_icons if theirs else []:
        yield ("stash-box site icon", address)
    yield ("simple icons", "")
    if host and page:
        for address in page_logos(page, f"https://{host}/", (one.name, *one.aliases)):
            yield ("page logo", address)
    for address in resort:
        yield ("og image" if address == og else "site fallback", address)


def _picked(one: Candidate) -> Iterator[tuple[str, str]]:
    for key in keys_of(one):
        picked = catalog.PICKED.get(key)
        if picked:
            yield ("picked", picked[0])


def _on_commons(one: Candidate, elsewhere: Elsewhere) -> Iterator[tuple[str, str]]:
    for key in keys_of(one):
        title = catalog.COMMONS.get(key)
        if title:
            address = elsewhere.commons(title)
            if address:
                yield ("wikimedia commons", address)


#: Sources that never claimed to be an icon: an opaque square from one is a photograph or banner.
_LAST_RESORT = ("og image", "page logo")


def make_picture(
    one: Candidate, renderer: art.Renderer, elsewhere: Elsewhere
) -> tuple[Made | None, str]:
    """This site's icon, or None and why: the first `high` one wins, else the largest low one."""
    # Refused here too: a photograph fetched by a second caller would be a face in the repository.
    reason = withheld_reason(one)
    if reason is not None:
        return None, f"withheld: {reason}"
    page, why = _home_page(one)

    def manifest_of() -> list[tuple[int, str]]:
        for address in manifest_addresses(page, f"https://{one.host}/")[:2]:
            raw, _ = _read(address)
            found = manifest_icons(raw, address) if raw else []
            if found:
                return found
        return []

    plate_off = any(key in catalog.PLATE_OFF for key in keys_of(one))
    best: Made | None = None
    tried: set[str] = set()
    for source, address in picture_sources(one, page, elsewhere, manifest_of):
        raw, address, failed = _fetched(one, source, address, elsewhere, tried)
        if raw is None:
            why = failed or why
            continue
        made, failed = _judged(one, source, address, raw, renderer, plate_off)
        if made is None:
            why = failed
            continue
        if made.icon.quality == "high" or source == "picked":
            # A picked picture is final even when small: somebody looked at it and chose it.
            return (made, "")
        if best is None or made.icon.source_pixels > best.icon.source_pixels:
            best = made
    return (best, "") if best is not None else (None, why)


def _home_page(one: Candidate) -> tuple[str, str]:
    """The site's home page, or empty and why not."""
    page = ""
    why = "nothing to try"
    if one.host:
        try:
            page = _get(f"https://{one.host}/", 400_000).decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as failure:
            why = f"its home page: {type(failure).__name__}"
    return page, why


def _fetched(
    one: Candidate, source: str, address: str, elsewhere: Elsewhere, tried: set[str]
) -> tuple[bytes | None, str, str]:
    """One source's bytes and address, or None and why (empty when skipped without a reason)."""
    if source == "simple icons":
        brand = simple_icon_for(one, elsewhere.brands())
        if brand is None:
            return None, address, ""
        raw = elsewhere.simple_icon(*brand)
        address = _SIMPLE_ICONS.format(
            version=catalog.SIMPLE_ICONS_VERSION, path=f"icons/{brand[0]}.svg"
        )
        if raw is None:
            return None, address, "simple icons: not found"
        return raw, address, ""
    if address in tried:
        return None, address, ""
    tried.add(address)
    raw, failed = _read(address)
    if raw is None:
        return None, address, f"{source}: {failed}"
    return raw, address, ""


def _judged(
    one: Candidate,
    source: str,
    address: str,
    raw: bytes,
    renderer: art.Renderer,
    plate_off: bool,
) -> tuple[Made | None, str]:
    """The icon these bytes make, or None and why they make none."""
    pixels = renderer.read(raw, svg=art.looks_like_svg(raw))
    if pixels is None:
        return None, f"{source}: {_NOT_A_PICTURE}"
    if source == "og image":
        tall, wide = pixels.shape[:2]
        if max(tall, wide) > min(tall, wide) * OG_SQUARENESS:
            return None, "its only large picture is a banner rather than a mark"
    icon = art.make_icon(pixels, plate_off=plate_off)
    if icon is None:
        return None, f"{source}: the picture is empty"
    if source in _LAST_RESORT and (icon.tile or art.fills_its_box(icon.rgba)):
        return None, f"{source}: a photograph rather than a mark"
    if not icon.tile and art.fills_its_box(icon.rgba):
        # From a source that calls it an icon, a full-bleed square is the brand's own tile.
        icon = replace(icon, tile=True)
    # An inline SVG's address is the whole drawing; the manifest names the page it was in.
    where = (
        f"https://{one.host}/ (inline svg in the page)" if address.startswith("data:") else address
    )
    return Made(icon, source, where), ""


def _read(address: str) -> tuple[bytes | None, str]:
    """The bytes at one address, or None and the sentence saying why not."""
    if address.startswith("data:image/svg+xml;base64,"):
        # An inline SVG lifted out of a page this run already fetched (`page_logos`): no request.
        try:
            return (base64.b64decode(address.split(",", 1)[1], validate=True), "")
        except ValueError:
            return (None, "an inline svg that is not base64")
    try:
        raw = _get(address, MAX_ICON_BYTES)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as failure:
        return (None, f"{type(failure).__name__}: {str(failure)[:60]}")
    return (raw, "") if raw else (None, "the file was empty")
