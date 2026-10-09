# SPDX-License-Identifier: AGPL-3.0-or-later
"""Resolving an Instagram post, reel or story through the instasave service, never an account.

Media is fetched through instasave's proxy links, which outlive Instagram's signed ones."""

from __future__ import annotations

import base64
import contextlib
import json
import re
from dataclasses import replace
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from sift.kernel.log import get_logger
from sift.slices.download.sources import curl
from sift.slices.download.sources.errors import (
    DownloadError,
    NothingFound,
    UnsupportedURL,
    busy_message,
    nothing_message,
)
from sift.slices.download.sources.hosts import host_matches, source_host
from sift.slices.download.sources.ratelimit import (
    DEFAULT_BACKOFF_SEC,
    LIMITER,
    middleman_backoff,
    parse_retry_after,
)
from sift.slices.download.sources.resolved import MediaType, ResolvedItem, ResolvedMedia
from sift.slices.download.sources.sites.catalog import hosts_of
from sift.slices.download.sources.sites.common import guess_type_and_ext
from sift.slices.download.sources.tuning import RunPolicy

log = get_logger(__name__)

_INSTAGRAM_HOSTS = hosts_of("instagram")

INSTASAVE_MEDIA_API = "https://api.instasave.website/media"
INSTASAVE_STORY_API = "https://api.instasave.website/story"
INSTASAVE_ORIGIN = "https://instasave.website"
INSTASAVE_SITE = "https://instasave.website/"
INSTASAVE_STORY_SITE = "https://instasave.website/instagram-stories-downloader"
INSTASAVE_HOST = "instasave.website"
#: instasave gates its proxy CDN links on its own origin.
INSTASAVE_REFERER = "https://instasave.website/"
_TIMEOUT = 45.0

_PROXY_RE = re.compile(r"https://cdn\.instasave\.website/\?token=[A-Za-z0-9._\-]+")
_PROXY_PREFIX = "https://cdn.instasave.website/?token="
_USERNAME_RE = re.compile(
    r"instagram\.com/([A-Za-z0-9._]{1,30})/(?:p|reel|reels|tv)/", re.IGNORECASE
)
_STORIES_RE = re.compile(r"instagram\.com/stories/([A-Za-z0-9._]{1,30})", re.IGNORECASE)
_CANON_RE = re.compile(r"/(p|reel|reels|tv)/([A-Za-z0-9_-]+)", re.IGNORECASE)
# A reel is one video, so instasave's cover image beside it is dropped.
_SINGLE_VIDEO_RE = re.compile(r"instagram\.com/(?:[^/?#]+/)?(?:reel|reels|tv)/", re.IGNORECASE)
_STORY_ID_RE = re.compile(r"instagram\.com/stories/[A-Za-z0-9._]{1,30}/(\d+)", re.IGNORECASE)

# Both clear on a retry; a 404's body simply holds no links.
_TRANSIENT_STATUSES = frozenset({429, 500, 502, 503, 504})
_MAX_ATTEMPTS = 3
_BACKOFF_CAP_SEC = 8.0
_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"


def handles(url: str) -> bool:
    """Whether this is an Instagram link this resolver owns."""
    return host_matches(source_host(url), _INSTAGRAM_HOSTS)


def _stories_username(url: str) -> str | None:
    """The username a stories link posts to the story endpoint; None for `highlights`."""
    match = _STORIES_RE.search(url)
    if match is None:
        return None
    username = match.group(1)
    return None if username.lower() == "highlights" else username


def is_instagram_story(url: str) -> bool:
    """Whether this is a story tray, whose contents change, so never a repeat by its URL."""
    return _stories_username(url) is not None


def _username_from_url(source_url: str) -> str | None:
    """The uploader from the URL, or None."""
    match = _USERNAME_RE.search(source_url)
    if match:
        return match.group(1)
    return _stories_username(source_url)


def _canonicalize(url: str) -> str:
    """A clean `/reel/<id>/` for instasave, which fails on `/<user>/reel/<id>`; else unchanged."""
    match = _CANON_RE.search(url)
    if match is None:
        return url
    kind = match.group(1).lower()
    if kind == "reels":
        kind = "reel"
    return f"https://www.instagram.com/{kind}/{match.group(2)}/"


def _post_code(url: str) -> str | None:
    """The post's own code, or one story's number, from the link; None for a whole tray."""
    match = _CANON_RE.search(url)
    if match is not None:
        return match.group(2)
    story = _STORY_ID_RE.search(url)
    return story.group(1) if story is not None else None


def _is_single_video_post(url: str) -> bool:
    return _SINGLE_VIDEO_RE.search(url) is not None


def _decode_js_escapes(text: str) -> str:
    """Decode the JS string-literal escapes instasave wraps its response in."""
    text = re.sub(r"\\x([0-9A-Fa-f]{2})", lambda m: chr(int(m.group(1), 16)), text)
    text = re.sub(r"\\u([0-9A-Fa-f]{4})", lambda m: chr(int(m.group(1), 16)), text)
    return text.replace("\\/", "/").replace('\\"', '"').replace("\\'", "'")


def _decode_jwt_payload(jwt: str) -> dict[str, object] | None:
    """The JWT's payload segment as JSON, or None if the token is not well formed."""
    parts = jwt.split(".")
    if len(parts) < 2:
        return None
    payload_b64 = parts[1]
    pad = "=" * (-len(payload_b64) % 4)
    try:
        raw = base64.urlsafe_b64decode(payload_b64 + pad)
        data = json.loads(raw)
    except ValueError:  # binascii.Error and json.JSONDecodeError both subclass this
        return None
    return data if isinstance(data, dict) else None


def _download_links(html: str) -> list[str]:
    """Every proxy link in a Download anchor: a video's cover appears only as a thumbnail."""
    soup = BeautifulSoup(html, "html.parser")
    found: list[str] = []
    for tag in soup.find_all("a", href=True):
        href = tag.get("href")
        if isinstance(href, str) and href.startswith(_PROXY_PREFIX):
            found.append(href)
    return found


def _classify(filename: str | None, direct_url: str | None) -> tuple[MediaType, str]:
    """A media type and extension from the JWT filename, then the CDN URL; video if unknown."""
    for candidate in (filename, direct_url):
        if not candidate:
            continue
        media_type, ext = guess_type_and_ext(candidate, filename)
        if media_type != "other":
            return media_type, ext
    return "video", ".mp4"


def _own_brand_off(filename: str | None) -> str | None:
    """The service's own name taken off the front of the file name it supplied."""
    if not filename:
        return filename
    cleaned = filename.lstrip("._-/ ")
    lowered = cleaned.casefold()
    if not lowered.startswith(INSTASAVE_HOST):
        return filename
    rest = cleaned[len(INSTASAVE_HOST) :].lstrip("._-/ ")
    return rest or filename


def _media_key(direct_url: str | None, token: str) -> str:
    """A name for one item that means the same tomorrow: the signed CDN URL without its query."""
    if direct_url:
        split = urlsplit(direct_url)
        if split.path:
            return f"{split.netloc}{split.path}"
    return token


def _videos_only(items: list[ResolvedItem]) -> list[ResolvedItem]:
    """Keep the videos and renumber them 0..n, so the staged names have no gap."""
    return [
        replace(item, index=new_index)
        for new_index, item in enumerate(one for one in items if one.media_type == "video")
    ]


def _without_video_covers(items: list[ResolvedItem]) -> list[ResolvedItem]:
    """Drop each image listed right before a video in a story: instasave puts its cover there."""
    keep = [
        item
        for position, item in enumerate(items)
        if not (
            item.media_type == "image"
            and position + 1 < len(items)
            and items[position + 1].media_type == "video"
        )
    ]
    return [replace(item, index=new_index) for new_index, item in enumerate(keep)]


def parse_instasave_response(body: str, source_url: str) -> ResolvedMedia:
    """Map an instasave response body to `ResolvedMedia`; `NothingFound` when it has no links."""
    decoded = _decode_js_escapes(body)

    # instasave's markup is not a contract: with no anchors, read loosely and warn.
    links = _download_links(decoded)
    read_exactly = bool(links)
    if not read_exactly:
        links = _PROXY_RE.findall(decoded)
        if links:
            log.warning("instagram.no_download_anchors", found=len(links))

    items: list[ResolvedItem] = []
    seen_keys: set[str] = set()
    for proxy_url in links:
        token = proxy_url.split("token=", 1)[1]
        payload = _decode_jwt_payload(token)
        filename: str | None = None
        direct_url: str | None = None
        if payload is not None:
            name = payload.get("filename")
            filename = _own_brand_off(name) if isinstance(name, str) and name else None
            direct = payload.get("url")
            direct_url = direct if isinstance(direct, str) and direct else None
        key = _media_key(direct_url, token)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        media_type, ext = _classify(filename, direct_url)
        items.append(
            ResolvedItem(
                index=len(items),
                url=proxy_url,
                media_type=media_type,
                ext=ext,
                referer=INSTASAVE_REFERER,
                filename=filename,
                media_key=key,
            )
        )
    # Only on the loose path: these rules drop images by position.
    if not read_exactly:
        if _is_single_video_post(source_url) and any(one.media_type == "video" for one in items):
            items = _videos_only(items)
        elif is_instagram_story(source_url):
            items = _without_video_covers(items)
    if not items:
        raise NothingFound(nothing_message("Instagram"))
    return ResolvedMedia(
        site="Instagram",
        source_url=source_url,
        source_host="instagram.com",
        items=items,
        username=_username_from_url(source_url),
        post_id=_post_code(source_url),
    )


async def _post_instasave(
    source_url: str,
    *,
    field_value: str,
    api: str,
    referer: str,
    prewarm: bool = False,
    proxy: str | None = None,
    policy: RunPolicy | None = None,
) -> ResolvedMedia:
    """POST to instasave and parse the body, waiting out a 429 or 5xx with the shared backoff."""
    headers = {"Accept": _ACCEPT, "Origin": INSTASAVE_ORIGIN, "Referer": referer}
    for attempt in range(_MAX_ATTEMPTS):
        await LIMITER.acquire(INSTASAVE_HOST)  # also waits out a prior 429's backoff
        if prewarm:
            with contextlib.suppress(Exception):
                await curl.guarded_get(referer, proxy=proxy, time_limit=_TIMEOUT, policy=policy)
        fetched = await curl.guarded_post(
            api,
            data={"url": field_value},
            headers=headers,
            proxy=proxy,
            time_limit=_TIMEOUT,
            policy=policy,
        )
        if fetched.status_code in _TRANSIENT_STATUSES:
            retry = (
                parse_retry_after(fetched.headers.get("Retry-After"))
                if fetched.status_code == 429
                else None
            )
            backoff = retry or min(DEFAULT_BACKOFF_SEC * (attempt + 1), _BACKOFF_CAP_SEC)
            if fetched.status_code == 429:
                backoff = middleman_backoff(backoff, policy)
            LIMITER.note_retry_after(INSTASAVE_HOST, backoff)
            continue
        return parse_instasave_response(fetched.text, source_url)
    raise DownloadError(busy_message("Instagram"))


async def resolve_instagram(
    url: str, *, proxy: str | None = None, policy: RunPolicy | None = None
) -> ResolvedMedia:
    """Resolve an Instagram post, reel or story via instasave; a whole profile is refused."""
    username = _stories_username(url)
    if username is not None:
        return await _post_instasave(
            url,
            proxy=proxy,
            policy=policy,
            field_value=username,
            api=INSTASAVE_STORY_API,
            referer=INSTASAVE_STORY_SITE,
            prewarm=True,
        )
    # A profile or a tab is not one item; instasave would answer empty, which reads as a bug.
    if _CANON_RE.search(url) is None and _STORIES_RE.search(url) is None:
        raise UnsupportedURL(
            "Downloading a whole Instagram profile is not supported. Open a single post or reel and "
            "paste that link (it has /p/ or /reel/ in it), and Sift will fetch it."
        )
    return await _post_instasave(
        url,
        proxy=proxy,
        policy=policy,
        field_value=_canonicalize(url),
        api=INSTASAVE_MEDIA_API,
        referer=INSTASAVE_SITE,
    )


__all__ = [
    "INSTASAVE_REFERER",
    "handles",
    "is_instagram_story",
    "parse_instasave_response",
    "resolve_instagram",
]
