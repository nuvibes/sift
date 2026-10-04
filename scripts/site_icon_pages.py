# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading a site's own pages for its icons: the declared links, the manifest, the page's logos."""

from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.parse
import urllib.request

#: What Sift says it is when it asks a site for its picture: plainly itself, never a browser, so a
#: maintainer's one-off crawl is not dressed up as somebody's Chrome.
USER_AGENT = "Sift/0.1 (+https://github.com/nuvibes/sift) site icon pack, fetched once"


#: How long one request to somebody else's site may take.
REQUEST_TIMEOUT = 8.0


#: How many DIFFERENT hosts one favicon may be chased across: the site and one hand-off (`www.`, a
#: CDN). Past that a redirect is a request to somebody nobody asked about.
MAX_HOSTS = 2


def host_of(address: str) -> str:
    """The host an address is on, lower case, without `www.`. Empty when there is not one."""
    cleaned = address.strip()
    if not cleaned:
        return ""
    parsed = urllib.parse.urlsplit(cleaned if "//" in cleaned else f"//{cleaned}")
    return (parsed.hostname or "").lower().removeprefix("www.")


# --- fetching one site's picture ------------------------------------------------------------------


class _CountingRedirects(urllib.request.HTTPRedirectHandler):
    """Follows a redirect, and refuses once it has been sent to a third host."""

    def __init__(self, first: str) -> None:
        self.hosts = {first}

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        host = host_of(newurl)
        if host and host not in self.hosts:
            self.hosts.add(host)
        if len(self.hosts) > MAX_HOSTS:
            raise urllib.error.HTTPError(newurl, code, "redirected too far", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _get(url: str, limit: int) -> bytes:
    """Read up to `limit` bytes from a URL, following at most two hosts."""
    if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
        # A `<link href="data:...">` or a `javascript:` is not something to open. Checked before
        # anything is built, so the refusal is the first thing that happens rather than the last.
        raise ValueError("not a web address")
    opener = urllib.request.build_opener(_CountingRedirects(host_of(url)))
    request = urllib.request.Request(  # noqa: S310 (a site's own address, http(s) only)
        url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"}
    )
    with opener.open(request, timeout=REQUEST_TIMEOUT) as answer:
        return bytes(answer.read(limit + 1)[:limit])


_LINK = re.compile(r"<link\b[^>]*>", re.IGNORECASE)


_REL = re.compile(r"\brel\s*=\s*[\"']?([^\"'>]+)", re.IGNORECASE)


_HREF = re.compile(r"\bhref\s*=\s*[\"']([^\"']+)", re.IGNORECASE)


_SIZES = re.compile(r"\bsizes\s*=\s*[\"']?(\d+)", re.IGNORECASE)


_SVG_TYPE = re.compile(r"\btype\s*=\s*[\"']?image/svg\+xml", re.IGNORECASE)


def declared_icons(page: str, base: str) -> list[tuple[int, str]]:
    """Every icon a page declares, largest first, each with the size it claims to be.

    A `<link>` that declares no size is nought: the site did not say. Read with a regular
    expression, four attributes off one kind of element; one it misses costs a fallback to
    `/favicon.ico`."""
    found: list[tuple[int, str]] = []
    for tag in _LINK.findall(page[:200_000]):
        rel = (_REL.search(tag) or [None, ""])[1].lower()
        if "icon" not in rel:
            continue
        href = _HREF.search(tag)
        if href is None:
            continue
        size = _SIZES.search(tag)
        if "mask-icon" in rel:
            # Safari's pinned-tab SILHOUETTE: one flat shape meant to be filled with a colour the
            # page names elsewhere. Drawn as it is, it is a black blob, never the site's logo.
            continue
        # An SVG is WEIGHED -1 only to keep it apart: `picture_sources` takes every declared SVG
        # out and asks for them FIRST, the best picture a site has.
        vector = href.group(1).lower().split("?")[0].endswith(".svg") or bool(_SVG_TYPE.search(tag))
        weight = -1 if vector else int(size.group(1)) if size else 0
        found.append((weight, urllib.parse.urljoin(base, href.group(1))))
    return sorted(found, key=lambda one: -one[0])


_MANIFEST_LINK = re.compile(r"<link\b[^>]*\brel\s*=\s*[\"']?manifest[\"'\s>][^>]*>", re.IGNORECASE)


#: Where a web manifest lives when the page does not say. Both spellings are in wide use and
#: neither is more correct than the other, so both are asked for.
_MANIFEST_PATHS = ("/manifest.json", "/site.webmanifest")


def manifest_addresses(page: str, base: str) -> list[str]:
    """Every address this site's web manifest might be at, the one it declares first.

    The declared one first because it is the only one that is not a guess: Instagram's is at
    `/data/manifest.json`, which neither conventional path would ever have found."""
    found: list[str] = []
    for tag in _MANIFEST_LINK.findall(page[:200_000]):
        href = _HREF.search(tag)
        if href is not None:
            found.append(urllib.parse.urljoin(base, href.group(1)))
    found.extend(urllib.parse.urljoin(base, path) for path in _MANIFEST_PATHS)
    return list(dict.fromkeys(found))


def manifest_icons(raw: bytes, base: str) -> list[tuple[int, str]]:
    """The icons a web manifest declares, largest first. Empty for anything that is not one.

    Where some of the biggest sites keep their real picture. The parse IS the check: a site with no
    manifest answers its HTML for any address. The largest number in `sizes` is read."""
    try:
        held = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return []
    icons = held.get("icons") if isinstance(held, dict) else None
    if not isinstance(icons, list):
        return []
    found: list[tuple[int, str]] = []
    for one in icons:
        if not isinstance(one, dict) or not one.get("src"):
            continue
        sizes = [int(number) for number in re.findall(r"\d+", str(one.get("sizes") or ""))]
        found.append((max(sizes) if sizes else 0, urllib.parse.urljoin(base, str(one["src"]))))
    return sorted(found, key=lambda one: -one[0])


_OG_IMAGE = re.compile(
    r"<meta\b[^>]*(?:property|name)\s*=\s*[\"']og:image[\"'][^>]*>", re.IGNORECASE
)


_CONTENT = re.compile(r"\bcontent\s*=\s*[\"']([^\"']+)", re.IGNORECASE)


def og_image(page: str, base: str) -> str | None:
    """The picture a site shows when a link to it is pasted, or None.

    THE LAST RESORT, the one source not claimed to be an icon, so held to `OG_SQUARENESS` and
    tried after everything else."""
    for tag in _OG_IMAGE.findall(page[:200_000]):
        content = _CONTENT.search(tag)
        if content is not None:
            return urllib.parse.urljoin(base, content.group(1))
    return None


_IMG = re.compile(r"<img\b[^>]*>", re.IGNORECASE)


_SRC = re.compile(r"\b(?:data-src|src)\s*=\s*[\"']([^\"']+)", re.IGNORECASE)


_ALT = re.compile(r"\b(?:alt|title)\s*=\s*[\"']([^\"']*)", re.IGNORECASE)


_LOGO_MARKED = re.compile(
    r"<[a-z][a-z0-9]*\b[^>]*\b(?:class|id)\s*=\s*[\"'][^\"']*logo[^\"']*[\"'][^>]*>", re.IGNORECASE
)


#: Where the logo element's contents stop being its logo: its first close, or the first control:
#: ManyVids marks a header block `...Logo` whose first drawing is the menu button's icon.
_BLOCK_END = re.compile(
    r"</(?:a|div|span|header|h1|figure|picture)>|<(?:button|nav|input|form)\b", re.IGNORECASE
)


_SMALL_SVG = re.compile(
    r"\b(?:width|height)\s*=\s*[\"']?(\d+(?:\.\d+)?)(?:px)?[\"'\s>]", re.IGNORECASE
)


_SVG_START = re.compile(r"<svg\b", re.IGNORECASE)


_SVG_END = re.compile(r"</svg\s*>", re.IGNORECASE)


def _letters(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def page_logos(page: str, base: str, names: tuple[str, ...]) -> list[str]:
    """The logo the site draws in its own page, as addresses: an inline SVG as a `data:` one.

    **Narrow on purpose: a page is full of OTHER brands' logos.** A picture counts only INSIDE an
    element the page marks as its logo (a `class` or `id` with `logo` in it), or when its own `alt`
    or `title` names the site AND the word logo. Still judged like any source, and LOOKED AT."""
    head = page[:400_000]
    words = {_letters(name) for name in names if len(_letters(name)) >= 4}
    found: list[str] = []
    for marked in _LOGO_MARKED.finditer(head):
        after = marked.end()
        close = _BLOCK_END.search(head, after)
        stop = close.start() if close else after + 1500
        stop = min(stop, after + 1500)
        svg = _SVG_START.search(head, marked.start(), stop)
        img = _IMG.search(head, marked.start(), stop)
        if svg is not None and (img is None or svg.start() < img.start()):
            end = _SVG_END.search(head, svg.start())
            opening = head[svg.start() : head.find(">", svg.start()) + 1]
            sizes = [float(size) for size in _SMALL_SVG.findall(opening)]
            if sizes and max(sizes) <= 32:
                # An icon drawn at icon size (a chevron, a burger), not a logo.
                continue
            if end is not None and end.end() - svg.start() < 200_000:
                markup = head[svg.start() : end.end()]
                if "xmlns" not in markup[:300]:
                    markup = markup.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"', 1)
                encoded = base64.b64encode(markup.encode("utf-8")).decode("ascii")
                found.append(f"data:image/svg+xml;base64,{encoded}")
        elif img is not None:
            # And the picture must SAY it is the logo or the site, read off its PATH and alt text,
            # never its host: every picture a site serves is on its own host.
            src = _SRC.search(img.group(0))
            alt = _ALT.search(img.group(0))
            said = _letters(
                (urllib.parse.urlsplit(src.group(1)).path if src else "")
                + (alt.group(1) if alt else "")
            )
            if src is not None and ("logo" in said or any(word in said for word in words)):
                found.append(urllib.parse.urljoin(base, src.group(1)))
    for tag in _IMG.findall(head):
        alt = _ALT.search(tag)
        src = _SRC.search(tag)
        said = _letters(alt.group(1)) if alt else ""
        if src and "logo" in tag.lower() and any(word in said for word in words):
            found.append(urllib.parse.urljoin(base, src.group(1)))
    return [
        one for one in dict.fromkeys(found) if one.startswith(("https://", "http://", "data:"))
    ][:3]
