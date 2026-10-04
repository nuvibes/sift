# SPDX-License-Identifier: AGPL-3.0-or-later
"""Resolving a Reddit link, which can carry very different media each best fetched a different way.

A Reddit post might embed a RedGIFs clip, be a still-image gallery, or be a video. So this branches at
resolve time:

1. Embedded RedGIFs (scraped from the post HTML) -> one yt-dlp subprocess item per link.
2. Still images / galleries (read from the post's public JSON) -> direct items the fetcher streams,
   each carrying a Reddit referer and the right Accept header (Reddit's media CDN serves an HTML
   consent page instead of bytes unless a request advertises a media type).
3. Otherwise -> one subprocess item: yt-dlp for a v.redd.it/redgifs video, else gallery-dl with a
   yt-dlp fallback.

The scrape and the JSON read go through the guarded browser client with the saved Reddit login (its
`.json` 403s to an anonymous client), and their outcome feeds the cookie-health killswitch: a run of
auth failures stops the dead cookie being sent. The subreddit is the username tracked for
attribution, read from the URL. Every network step is best-effort (any failure just falls through
to the next path), so a Reddit link always resolves to something.
"""

from __future__ import annotations

import contextlib
import html
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, NamedTuple
from urllib.parse import urlsplit, urlunsplit

from sift.slices.download.sources import cookie_health, curl
from sift.slices.download.sources.hosts import host_matches, source_host
from sift.slices.download.sources.resolved import ResolvedItem, ResolvedMedia
from sift.slices.download.sources.sites.catalog import hosts_of
from sift.slices.download.sources.sites.common import guess_type_and_ext, load_cookie_jar
from sift.slices.download.sources.tuning import RunPolicy

_REDDIT_HOSTS = hosts_of("reddit")
_REDDIT_REFERER = "https://www.reddit.com/"
# Reddit's media CDN serves an HTML consent page unless a request advertises a matching media type.
_ACCEPT_VIDEO = "video/*,*/*;q=0.8"
_ACCEPT_GIF = "image/gif,image/*,*/*;q=0.8"

_REDGIFS_EMBED_RE = re.compile(
    r"https?://(?:www\.)?redgifs\.com/(?:watch|ifr|embed)/([A-Za-z0-9]+)", re.IGNORECASE
)
# The subreddit (Reddit's tracked username) from a `/r/<sub>/` path segment.
_SUBREDDIT_RE = re.compile(r"/r/([A-Za-z0-9_]{2,21})(?:[/?#]|$)")
# The post's own ID, from a `/comments/<id>/` path. Reddit's IDs are short base-36 codes.
_POST_ID_RE = re.compile(r"/comments/([a-z0-9]{1,12})(?:[/?#]|$)", re.IGNORECASE)
# A post Reddit hosts a video for is not a still-image post: its `is_video` flag or these hints mark
# it, and its media is fetched by the yt-dlp route, not by grabbing its preview thumbnail.
_VIDEO_POST_HINTS = frozenset({"hosted:video", "rich:video"})

RedditMediaKind = Literal["video", "gif", "image"]


class RedditMedia(NamedTuple):
    url: str
    kind: RedditMediaKind


class RedditPost(NamedTuple):
    """What a post's JSON says about the post itself, for naming its files. Each may be None."""

    title: str | None = None
    post_id: str | None = None
    posted: datetime | None = None


def handles(url: str) -> bool:
    """Whether this is a Reddit link this resolver owns (suffix/exact host match)."""
    return host_matches(source_host(url), _REDDIT_HOSTS)


def _subreddit_from_url(url: str) -> str | None:
    match = _SUBREDDIT_RE.search(urlsplit(url).path)
    return match.group(1) if match else None


def _unescape(url: str) -> str:
    """Reddit's JSON encodes `&` as `&amp;` in media URLs."""
    return html.unescape(url)


def _kind_from_url(url: str) -> RedditMediaKind | None:
    """The media kind a URL's extension names, or None when it names no media kind (a permalink, a
    link to another site). Read through the kernel's allowlist, so no second extension list lives
    here, and a URL the allowlist does not recognize is NOT quietly treated as an image."""
    media_type, _ext = guess_type_and_ext(url, None)
    if media_type in ("video", "gif", "image"):
        return media_type
    return None


def _gallery_media(entry: Any) -> RedditMedia | None:
    """The best media for one gallery item, typed by which source key it came from. A GIF item
    exposes `s.mp4`/`s.gif` and no `s.u`; the mp4 is preferred so a gif is delivered as playable video
    (its path still ends `.gif`, so the key it came from is what types it, not the extension)."""
    if not isinstance(entry, dict):
        return None
    source = entry.get("s") or {}
    mp4 = source.get("mp4")
    if isinstance(mp4, str) and mp4:
        return RedditMedia(mp4, "video")
    gif = source.get("gif")
    if isinstance(gif, str) and gif:
        return RedditMedia(gif, "gif")
    still = source.get("u")
    if isinstance(still, str) and still:
        return RedditMedia(still, "image")
    previews = entry.get("p")
    if previews:
        preview_url = previews[-1].get("u")
        if isinstance(preview_url, str) and preview_url:
            return RedditMedia(preview_url, "image")
    return None


def collect_reddit_images(post: Any, out: list[RedditMedia]) -> None:
    """Append the media found on a Reddit post object (gallery, then a single direct image, then the
    largest preview), each carrying its kind so the resolver types it correctly."""
    if not isinstance(post, dict):
        return
    if "gallery_data" in post and "media_metadata" in post:
        items = (post.get("gallery_data") or {}).get("items", [])
        meta = post.get("media_metadata") or {}
        for item in items:
            media = _gallery_media(meta.get(item.get("media_id")))
            if media is not None:
                out.append(media._replace(url=_unescape(media.url)))
        return
    direct = post.get("url_overridden_by_dest") or post.get("url")
    if isinstance(direct, str):
        kind = _kind_from_url(direct)
        if kind == "image" or kind == "gif":
            out.append(RedditMedia(_unescape(direct), kind))
            return
    if post.get("is_video") or post.get("post_hint") in _VIDEO_POST_HINTS:
        # A video post: leave it for the subprocess (yt-dlp) route rather than delivering its
        # preview still as "the media": the thumbnail is not what was asked for.
        return
    images = (post.get("preview") or {}).get("images", [])
    if images:
        source_url = (images[0].get("source") or {}).get("url")
        if source_url:
            out.append(RedditMedia(_unescape(source_url), _kind_from_url(source_url) or "image"))


def parse_reddit_post(text: str) -> RedditPost:
    """The post's title, ID and posting time from its `.json` body. Nothing on a shape that is not
    the expected post listing: a name is a convenience, and it never fails a download."""
    try:
        post = json.loads(text)[0]["data"]["children"][0]["data"]
    except (ValueError, TypeError, KeyError, IndexError):
        return RedditPost()
    if not isinstance(post, dict):
        return RedditPost()
    title = post.get("title")
    post_id = post.get("id")
    created = post.get("created_utc")
    posted: datetime | None = None
    if isinstance(created, (int, float)) and not isinstance(created, bool) and created > 0:
        try:
            posted = datetime.fromtimestamp(created, UTC)
        except (OverflowError, OSError, ValueError):
            posted = None
    return RedditPost(
        title=(title.strip() or None) if isinstance(title, str) else None,
        post_id=post_id if isinstance(post_id, str) and post_id else None,
        posted=posted,
    )


def parse_reddit_json(text: str) -> list[RedditMedia]:
    """Read a post's `.json` body into its media (crossposts first), deduped by URL with order
    kept. A shape that is not the expected post listing yields nothing."""
    media: list[RedditMedia] = []
    try:
        data = json.loads(text)
        post = data[0]["data"]["children"][0]["data"]
        for crosspost in post.get("crosspost_parent_list", []):
            collect_reddit_images(crosspost, media)
        collect_reddit_images(post, media)
    except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        return []
    seen: set[str] = set()
    deduped: list[RedditMedia] = []
    for item in media:
        if item.url not in seen:
            seen.add(item.url)
            deduped.append(item)
    return deduped


def _media_item(index: int, media: RedditMedia) -> ResolvedItem:
    """A direct Reddit media item typed by the KIND the collector picked, not the URL extension (a
    gallery mp4's path lies, ending `.gif`)."""
    url, kind = media
    if kind == "video":
        return ResolvedItem(
            index=index,
            url=url,
            media_type="video",
            ext=".mp4",
            referer=_REDDIT_REFERER,
            accept=_ACCEPT_VIDEO,
        )
    if kind == "gif":
        return ResolvedItem(
            index=index,
            url=url,
            media_type="gif",
            ext=".gif",
            referer=_REDDIT_REFERER,
            accept=_ACCEPT_GIF,
        )
    # The extension goes through the kernel's allowlist rather than being taken raw from the URL: a
    # remote host must not get to choose an arbitrary trailing suffix on the staged filename. An
    # unrecognized or absent one becomes `.jpg`: the ingress gate settles what the bytes really are
    # regardless.
    _, ext = guess_type_and_ext(url, None)
    return ResolvedItem(
        index=index, url=url, media_type="image", ext=ext or ".jpg", referer=_REDDIT_REFERER
    )


def _reddit_www(url: str) -> str:
    """Point a reddit.com URL at `www`, its canonical API host, so the reads below resolve without a
    redirect: the guarded client never follows one. A non-reddit.com host (a `redd.it`/`v.redd.it`
    short link) is left unchanged; it will simply fail the read and fall through to the subprocess
    route, which follows the redirect itself."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host_matches(host, ("reddit.com",)):
        return urlunsplit(("https", "www.reddit.com", parts.path, parts.query, ""))
    return url


def _json_url(url: str) -> str:
    base = url.split("?", 1)[0].rstrip("/")
    return f"{base}.json?raw_json=1" if not base.endswith(".json") else f"{base}?raw_json=1"


async def _scrape_redgifs(
    url: str, cookies: dict[str, str], proxy: str | None, policy: RunPolicy | None
) -> list[str]:
    """Embedded RedGIFs watch URLs from the post HTML, deduped. Best-effort: empty on any failure."""
    with contextlib.suppress(Exception):
        fetched = await curl.guarded_get(
            _reddit_www(url), cookies=cookies or None, proxy=proxy, policy=policy
        )
        if fetched.status_code == 200:
            ids = dict.fromkeys(_REDGIFS_EMBED_RE.findall(fetched.text))
            return [f"https://www.redgifs.com/watch/{gif_id}" for gif_id in ids]
    return []


async def _read_images(
    url: str, cookies: dict[str, str], proxy: str | None, policy: RunPolicy | None
) -> tuple[list[RedditMedia], RedditPost]:
    """The post's media from its public JSON, and what the same body says about the post: one
    read, so naming a file costs Reddit no second request. A 401/403/429 on a read that CARRIED a
    login feeds the cookie-health killswitch (a cookieless read's 403 is Reddit's anti-bot wall, not
    a dead login); a success with a login clears the streak. Best-effort: empty on any failure."""
    with contextlib.suppress(Exception):
        fetched = await curl.guarded_get(
            _json_url(_reddit_www(url)), cookies=cookies or None, proxy=proxy, policy=policy
        )
        if fetched.status_code != 200:
            if cookies and fetched.status_code in (401, 403, 429):
                cookie_health.record_auth_failure(
                    "reddit.com", detail=f"reddit .json HTTP {fetched.status_code}"
                )
            return [], RedditPost()
        if cookies:
            cookie_health.record_ok("reddit.com")
        return parse_reddit_json(fetched.text), parse_reddit_post(fetched.text)
    return [], RedditPost()


async def resolve_reddit(
    url: str,
    *,
    cookies_file: Path | None = None,
    proxy: str | None = None,
    policy: RunPolicy | None = None,
) -> ResolvedMedia:
    """Resolve a Reddit URL to its media via the branching above."""
    cookies = (
        load_cookie_jar(cookies_file, "reddit.com")
        if cookie_health.should_use_cookies("reddit.com")
        else {}
    )
    items: list[ResolvedItem]
    redgifs = await _scrape_redgifs(url, cookies, proxy, policy)
    if redgifs:
        items = [
            ResolvedItem.subprocess(source_url=link, backend="ytdlp", index=index)
            for index, link in enumerate(redgifs)
        ]
        # One request more than the clips need, so a clip is named by the post's title as every
        # other file from Reddit is, rather than by the clip's own tags.
        _clips, post = await _read_images(url, cookies, proxy, policy)
    else:
        images, post = await _read_images(url, cookies, proxy, policy)
        if images:
            items = [_media_item(index, media) for index, media in enumerate(images)]
        else:
            host = source_host(url)
            video_host = "v.redd.it" in host or "redgifs.com" in host
            items = [
                ResolvedItem.subprocess(
                    source_url=url,
                    backend="ytdlp" if video_host else "gallerydl",
                    fallback_backend=None if video_host else "ytdlp",
                )
            ]
    return ResolvedMedia(
        site="Reddit",
        source_url=url,
        source_host="reddit.com",
        items=items,
        username=_subreddit_from_url(url),
        title=post.title,
        post_id=_post_id_from_url(url) or post.post_id,
        posted=post.posted,
    )


def _post_id_from_url(url: str) -> str | None:
    """The post's ID from a `/comments/<id>/` link, or None for a link shape that carries none."""
    match = _POST_ID_RE.search(urlsplit(url).path)
    return match.group(1) if match else None


__all__ = [
    "RedditMedia",
    "RedditPost",
    "collect_reddit_images",
    "handles",
    "parse_reddit_json",
    "parse_reddit_post",
    "resolve_reddit",
]
