# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Derived from cyberdrop-dl (GPL-3.0); see the package header and NOTICE.
"""Shared parts the site extractors lean on: host matching, the referer a CDN expects, and the
turning of an extractor's finds into the site-neutral `ResolvedMedia` the fetch stage consumes.

An extractor stays small: it produces `ExtractedFile`s (a direct address, and optionally a name or
a cookie the address needs), and this module derives each one's media type and extension and builds
the result. When the extension is not clear from the name or the path, it is left empty and the fetch
stage settles it from the response content type; the ingress gate decides what a file really is from
its bytes regardless.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from sift.kernel.ingress import ALLOWED_MEDIA
from sift.slices.download.sources.errors import NothingFound
from sift.slices.download.sources.hosts import host_matches, source_host
from sift.slices.download.sources.progress import Report, nowhere
from sift.slices.download.sources.resolved import MediaType, ResolvedItem, ResolvedMedia
from sift.slices.download.sources.tuning import QUALITY_BEST, QUALITY_COMPATIBLE

# The key a JPG5 (Chevereto) page XORs its media URLs with before base64+hex encoding them. It is
# hard-coded in the site's own JavaScript: a fragility the extractor accepts, and the reason this is
# here rather than a secret.
_JPG5_KEY = b"seltilovessimpcity@simpcityhatesscrapers"

# Which kind of media each extension names, read from the kernel's one media allowlist rather than a
# second copy of it, so a format the gate does not accept is not classified as media here either.
# The kernel's kind values ("video"/"image"/"gif") are exactly the resolver's media types.
_KIND_BY_EXTENSION: dict[str, MediaType] = {
    extension: media.kind.value for media in ALLOWED_MEDIA for extension in media.extensions
}


@dataclass(frozen=True, slots=True)
class ExtractContext:
    """Everything an extractor needs beyond the URL and the session."""

    user_agent: str
    cookies_file: Path | None = None
    #: Where an extractor says how far through an ALBUM it is.
    #:
    #: Resolving is not always quick. An album is one page listing its contents and then one request
    #: per item to sign each address, and a two hundred item album is therefore several minutes of
    #: work before a single byte of media has been asked for. A row that only said it was running
    #: all that while would look exactly like a download that had hung.
    #:
    #: The listing page is the fast half and it already knows the total, so this is a real count
    #: rather than a spinner from the first second.
    report: Report = nowhere
    #: Which of the two Video quality answers this download takes, for a reader offering rungs.
    quality: str = QUALITY_COMPATIBLE


#: The tallest rung "Best compatible" takes. The big video sites' H.264 ladder stops here, which is
#: what the answer means for the tools as well (`argv._SORT_BY_QUALITY`).
COMPATIBLE_HEIGHT = 1080


def pick_rung[T](rungs: list[tuple[int, T]], quality: str) -> T | None:
    """One rung of a quality ladder, `(height, what)` pairs, as the Video quality setting says.

    "Best available" takes the tallest. "Best compatible" takes the tallest at or under 1080p, and
    where every rung is taller, the shortest of them, the nearest to what was asked. A rung whose
    height is unknown (0) ranks below every one that says.
    """
    if not rungs:
        return None
    if quality == QUALITY_BEST:
        return max(rungs, key=lambda rung: rung[0])[1]
    within = [rung for rung in rungs if rung[0] <= COMPATIBLE_HEIGHT]
    if within:
        return max(within, key=lambda rung: rung[0])[1]
    return min(rungs, key=lambda rung: rung[0])[1]


@dataclass(frozen=True, slots=True)
class ExtractedFile:
    """One direct media address an extractor found.

    `filename` is an API-supplied human name when the site gave one; `cookie` is a raw Cookie header
    the address needs on the fetch (a signed link that checks a guest token). Both are usually None.
    `id` is the file's own ID on the site where it has one, for the `{id}` naming word. `page` is
    the file's own page where the address was signed from it, so a signature that runs out before
    the file is fetched can be made again (`ResolvedItem.refetch_url`); `title` fills `{title}`.
    """

    url: str
    filename: str | None = None
    cookie: str | None = None
    id: str | None = None
    page: str | None = None
    title: str | None = None


def decrypt_jpg5_xor(encrypted: str) -> str:
    """Undo a JPG5 obfuscated URL: `base64 -> hex -> XOR with the site key`. Returns "" on any decode
    failure, so a malformed blob is simply not a URL rather than an error."""
    try:
        raw = bytes.fromhex(base64.b64decode(encrypted).decode())
    except (ValueError, UnicodeDecodeError):
        return ""
    key = _JPG5_KEY
    return bytes(byte ^ key[i % len(key)] for i, byte in enumerate(raw)).decode("utf-8", "ignore")


def load_cookie_jar(cookies_file: Path | None, domain_suffix: str) -> dict[str, str]:
    """All cookies (name -> value) for a domain from a Netscape `cookies.txt` (the decrypted site
    login). Tab-separated `domain flag path secure expiration name value`; the `#HttpOnly_` line
    prefix some exporters use is a real cookie, not a comment. Empty when there is no file."""
    jar: dict[str, str] = {}
    if cookies_file is None:
        return jar
    try:
        text = cookies_file.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return jar
    suffix = domain_suffix.lstrip(".")
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_") :]
        elif not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        cookie_domain, _flag, _path, _secure, _expiry, name, value = parts[:7]
        if cookie_domain.lstrip(".").endswith(suffix):
            jar[name] = value
    return jar


def load_session_cookie(cookies_file: Path | None, domain_suffix: str) -> str | None:
    """The `session` cookie value for a domain from a Netscape `cookies.txt`, or None."""
    return load_cookie_jar(cookies_file, domain_suffix).get("session")


def source_origin(url: str) -> str:
    """`scheme://host/`: the referer most of these CDNs expect on the download."""
    parts = urlsplit(url)
    return f"{parts.scheme or 'https'}://{parts.hostname or ''}/"


def guess_type_and_ext(url: str, filename: str | None) -> tuple[MediaType, str]:
    """A best-effort media type and extension from the filename, then the URL path. An extension the
    allowlist does not name yields `("other", "")`: the fetch stage finalises it from the response
    content type, and the ingress gate decides what the bytes really are regardless."""
    for candidate in (filename, urlsplit(url).path):
        if not candidate:
            continue
        ext = Path(candidate).suffix.lower()
        kind = _KIND_BY_EXTENSION.get(ext)
        if kind is not None:
            return kind, ext
    return "other", ""


def build_resolved_media(
    source_url: str, site: str, extracted: list[ExtractedFile], *, referer: str
) -> ResolvedMedia:
    """Turn an extractor's finds into `ResolvedMedia`. Raises `NothingFound` when nothing usable was
    found: a private album, a deleted file, or markup that changed under the extractor."""
    items: list[ResolvedItem] = []
    for found in extracted:
        if not found.url:
            continue
        media_type, ext = guess_type_and_ext(found.url, found.filename)
        items.append(
            ResolvedItem(
                index=len(items),
                url=found.url,
                media_type=media_type,
                ext=ext,
                referer=referer,
                filename=found.filename,
                cookie=found.cookie,
                item_id=found.id,
                refetch_url=found.page,
            )
        )
    if not items:
        raise NothingFound(f"Nothing could be downloaded from {site}.")
    return ResolvedMedia(
        site=site,
        source_url=source_url,
        source_host=source_host(source_url),
        items=items,
        title=next((found.title for found in extracted if found.title), None),
    )


__all__ = [
    "COMPATIBLE_HEIGHT",
    "ExtractContext",
    "ExtractedFile",
    "build_resolved_media",
    "decrypt_jpg5_xor",
    "guess_type_and_ext",
    "host_matches",
    "load_cookie_jar",
    "load_session_cookie",
    "pick_rung",
    "source_host",
    "source_origin",
]
