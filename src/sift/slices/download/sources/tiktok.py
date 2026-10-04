# SPDX-License-Identifier: AGPL-3.0-or-later
"""Resolving a TikTok link to its media, through the tikwm service.

TikTok does not hand a video to anyone who asks; a plain fetch gets a watermarked mess or nothing.
tikwm is a free middleman that does the awkward part and answers with clean JSON: a no-watermark
video, or the images of a photo slideshow, plus the username and size hints. Sift asks it over
the guarded browser client and turns the answer into direct addresses the fetcher streams itself. The
service fetches the post server-side, so the operator's own account is never used.

The parsing is a pure function of the JSON, split from the request so it is tested from a captured
response with no network. The free tier paces at about one request a second and says so in the body
rather than with a proper status, so both forms of "slow down" are read and the limiter waits out the
backoff before a single retry.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from sift.slices.download.sources import curl
from sift.slices.download.sources.errors import (
    DownloadError,
    NothingFound,
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
from sift.slices.download.sources.resolved import ResolvedItem, ResolvedMedia
from sift.slices.download.sources.sites.catalog import hosts_of
from sift.slices.download.sources.tuning import RunPolicy

TIKWM_API = "https://www.tikwm.com/api/"
TIKWM_ORIGIN = "https://www.tikwm.com"
TIKWM_HOST = "www.tikwm.com"
#: tikwm's CDN links want a tikwm referer on the fetch.
TIKWM_REFERER = "https://www.tikwm.com/"
_TIMEOUT = 30.0
_TIKTOK_HOSTS = hosts_of("tiktok")

_USERNAME_RE = re.compile(r"tiktok\.com/@([A-Za-z0-9._]+)", re.IGNORECASE)
# The post's own number, where the link carries it (`/@name/video/<number>`, `/photo/<number>`). A
# short `vm.tiktok.com/<code>` link does not, and tikwm's answer is read for it instead.
_POST_ID_RE = re.compile(r"tiktok\.com/(?:@[^/?#]+/)?(?:video|photo)/(\d+)", re.IGNORECASE)
# tikwm's free tier answers 200 with a body like {"code":-1,"msg":"Free Api Limit: ..."}, a soft
# limit in the body, not a 429. Detect it so the retry backs off rather than treating it as no media.
_RATE_LIMIT_RE = re.compile(r"\b(?:limit|rate|too many)\b", re.IGNORECASE)


def handles(url: str) -> bool:
    """Whether this is a TikTok link this resolver owns (suffix/exact host match)."""
    return host_matches(source_host(url), _TIKTOK_HOSTS)


def _username_from_url(source_url: str, data: dict[str, Any]) -> str | None:
    """Prefer the username in the URL (`/@name/`), present even when the API omits author info,
    and fall back to the author the API named."""
    match = _USERNAME_RE.search(source_url)
    if match:
        return match.group(1)
    author = data.get("author")
    if isinstance(author, dict):
        uid = author.get("unique_id")
        if isinstance(uid, str) and uid:
            return uid
    return None


def _post_id(source_url: str, data: dict[str, Any]) -> str | None:
    """The post's number: from the link where it carries one, else the `id` tikwm answered with.

    The link first for the reason the username is read from it first: it is what was pasted, and
    it is there before any answer comes back. tikwm's `id` is its copy of the same number.
    """
    match = _POST_ID_RE.search(source_url)
    if match:
        return match.group(1)
    said = data.get("id")
    if isinstance(said, int) and not isinstance(said, bool) and said > 0:
        return str(said)
    return said if isinstance(said, str) and said.isdigit() else None


def _posted(data: dict[str, Any]) -> datetime | None:
    """When the post went up: tikwm's `create_time`, in seconds. None when it gives none."""
    moment = data.get("create_time")
    if not isinstance(moment, int) or isinstance(moment, bool) or moment <= 0:
        return None
    try:
        return datetime.fromtimestamp(moment, UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _abs_url(url: str) -> str:
    """tikwm sometimes returns a site-relative media path; make it absolute."""
    return TIKWM_ORIGIN + url if url.startswith("/") else url


def _coerce_bytes(value: Any) -> int | None:
    return value if isinstance(value, int) and value > 0 else None


def _coerce_duration(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and value > 0 else None


def parse_tikwm_response(payload: dict[str, Any], source_url: str) -> ResolvedMedia:
    """Turn a tikwm `/api` JSON body into `ResolvedMedia`. Raises `NothingFound` when the API reports
    failure or exposes neither a video nor any images.

    A photo slideshow (`images`) wins over a video field: a photo post can still carry a rendered
    `play`, but the person pasted a photo post, so its images are delivered, each its own item. For a
    video, the HD variant is preferred; both are watermark-free.
    """
    if payload.get("code") != 0:
        raise NothingFound(nothing_message("TikTok"))

    data = payload.get("data")
    if not isinstance(data, dict):
        raise NothingFound(nothing_message("TikTok"))

    username = _username_from_url(source_url, data)
    title = data.get("title") if isinstance(data.get("title"), str) else None
    items: list[ResolvedItem] = []

    images = data.get("images")
    if isinstance(images, list) and images:
        for index, image in enumerate(images):
            if isinstance(image, str) and image:
                items.append(
                    ResolvedItem(
                        index=index,
                        url=_abs_url(image),
                        media_type="image",
                        ext=".jpg",
                        referer=TIKWM_REFERER,
                        # Photo-mode signed CDN links expire within seconds; let the fetch stage
                        # re-resolve this post for a fresh address if it goes stale.
                        refetch_url=source_url,
                    )
                )
    else:
        items.append(_video_item(data))

    if not items:
        raise NothingFound(nothing_message("TikTok"))

    return ResolvedMedia(
        site="TikTok",
        source_url=source_url,
        source_host="tiktok.com",
        items=items,
        username=username,
        # The caption, read by the `{title}` word: the one place a TikTok post's own words can
        # reach its file's name.
        title=title,
        post_id=_post_id(source_url, data),
        posted=_posted(data),
    )


def _video_item(data: dict[str, Any]) -> ResolvedItem:
    """The single video item, HD preferred over the no-watermark default. Both are watermark-free.

    Whichever Video quality says: TikTok's ladder stops at 1080p, so its HD address is the tallest
    rung at or under 1080p, which is what "Best compatible" takes, and the tallest of all as well.
    """
    hdplay, play = data.get("hdplay"), data.get("play")
    if isinstance(hdplay, str) and hdplay:
        video_url, size = hdplay, _coerce_bytes(data.get("hd_size"))
    elif isinstance(play, str) and play:
        video_url, size = play, _coerce_bytes(data.get("size"))
    else:
        raise NothingFound(nothing_message("TikTok"))
    return ResolvedItem(
        index=0,
        url=_abs_url(video_url),
        media_type="video",
        ext=".mp4",
        referer=TIKWM_REFERER,
        expected_bytes=size,
        duration_sec=_coerce_duration(data.get("duration")),
    )


async def resolve_tiktok(
    url: str, *, proxy: str | None = None, policy: RunPolicy | None = None
) -> ResolvedMedia:
    """Resolve a TikTok URL to direct media via tikwm. Retries once when tikwm reports its soft limit
    (a 429, or the in-body message), with the limiter enforcing the backoff first. A persistent limit
    becomes a retryable `DownloadError`; a bad or empty answer becomes `NothingFound`.

    `policy` is the download's settings: the ask is timed and retried by them (`curl`), and a longer
    wait after a rate limit raises tikwm's own backoff (`ratelimit.middleman_backoff`)."""
    for _ in range(2):
        await LIMITER.acquire(TIKWM_HOST)
        fetched = await curl.guarded_get(
            TIKWM_API,
            proxy=proxy,
            params={"url": url, "hd": "1"},
            headers={"Accept": "application/json"},
            time_limit=_TIMEOUT,
            policy=policy,
        )
        if fetched.status_code == 429:
            retry = parse_retry_after(fetched.headers.get("Retry-After"))
            LIMITER.note_retry_after(
                TIKWM_HOST, middleman_backoff(retry or DEFAULT_BACKOFF_SEC, policy)
            )
            continue
        try:
            payload = json.loads(fetched.text)
        except (ValueError, TypeError) as exc:
            raise NothingFound(nothing_message("TikTok")) from exc
        if not isinstance(payload, dict):
            raise NothingFound(nothing_message("TikTok"))
        if payload.get("code") != 0 and _RATE_LIMIT_RE.search(str(payload.get("msg") or "")):
            LIMITER.note_retry_after(TIKWM_HOST, middleman_backoff(DEFAULT_BACKOFF_SEC, policy))
            continue
        return parse_tikwm_response(payload, url)
    raise DownloadError(busy_message("TikTok"))


__all__ = ["TIKWM_REFERER", "handles", "parse_tikwm_response", "resolve_tiktok"]
