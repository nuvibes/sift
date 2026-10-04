# SPDX-License-Identifier: AGPL-3.0-or-later
"""Resolving an Instagram post, reel, or story to its media, through the instasave service.

Instagram hands its media to nobody who is not signed in, and signing in with the operator's own
account to scrape it is exactly what gets an account banned. instasave is a middleman that does the
fetch server-side and answers with the media, so the operator's account is never involved. It takes
the post URL as a form POST and replies with an HTML/JS blob; inside are `cdn.instasave.website`
proxy anchors, one per media, each a JWT whose payload names the file. Sift downloads through the
proxy anchor rather than the direct Instagram CDN URL the JWT also carries: instasave keeps the
proxy's auth fresh, so it outlives the short-lived signed CDN link.

The parsing is a pure function of the response body, split from the request so it is tested from a
captured blob with no network. The username is read from the URL when the link carries one
(`/<user>/p/<id>/`, `/stories/<user>/`); for a bare `/p/<id>/` or `/reel/<id>/` link the download
reads it off the post's page afterwards (`creator.creator_in_address`).
"""

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
#: instasave gates its proxy CDN links on its own origin, so the fetch carries this referer.
INSTASAVE_REFERER = "https://instasave.website/"
_TIMEOUT = 45.0

# The proxy link instasave returns: a JWT of three base64url segments (header.payload.signature).
# Scanning the WHOLE body for these is the fallback, not the answer. See `_download_links`.
_PROXY_RE = re.compile(r"https://cdn\.instasave\.website/\?token=[A-Za-z0-9._\-]+")
#: What every one of those links begins with. Read off an anchor's `href` rather than searched for.
_PROXY_PREFIX = "https://cdn.instasave.website/?token="
# The uploader when the link carries it (`/<user>/p/<id>/`); a bare link's is read off its page.
_USERNAME_RE = re.compile(
    r"instagram\.com/([A-Za-z0-9._]{1,30})/(?:p|reel|reels|tv)/", re.IGNORECASE
)
# A stories URL carries the username posted to the story endpoint.
_STORIES_RE = re.compile(r"instagram\.com/stories/([A-Za-z0-9._]{1,30})", re.IGNORECASE)
# The post/reel id, for canonicalising `/<user>/reel/<id>` (which instasave fails on) down to the
# bare `/reel/<id>/` form it parses reliably.
_CANON_RE = re.compile(r"/(p|reel|reels|tv)/([A-Za-z0-9_-]+)", re.IGNORECASE)
# A reel / IGTV link is always a single video, and instasave returns its auto-generated cover image
# alongside; those are dropped. A `/p/` post may be a genuine image carousel, so it is left untouched.
_SINGLE_VIDEO_RE = re.compile(r"instagram\.com/(?:[^/?#]+/)?(?:reel|reels|tv)/", re.IGNORECASE)
# One story's own number, where a story link names a single story (`/stories/<user>/<number>/`).
_STORY_ID_RE = re.compile(r"instagram\.com/stories/[A-Za-z0-9._]{1,30}/(\d+)", re.IGNORECASE)

# instasave soft-throttles a burst with 429, and its upstream to the Instagram CDN blips 5xx; both
# clear on a retry. Anything else (a 404 for a deleted post) is not retried: its body simply holds
# no anchors, so it surfaces as "nothing found" without hanging the resolve.
_TRANSIENT_STATUSES = frozenset({429, 500, 502, 503, 504})
_MAX_ATTEMPTS = 3
_BACKOFF_CAP_SEC = 8.0
_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"


def handles(url: str) -> bool:
    """Whether this is an Instagram link this resolver owns (suffix/exact host match)."""
    return host_matches(source_host(url), _INSTAGRAM_HOSTS)


def _stories_username(url: str) -> str | None:
    """The username a stories link posts to the story endpoint, or None. `/stories/highlights/<id>/`
    is NOT a story: highlights are permanent saved reels, so `highlights` is not a username. The
    link is left to resolve as a permalink through the media endpoint instead. This is the single
    source of truth for "is this a story", so the routing and the dedup gate cannot disagree."""
    match = _STORIES_RE.search(url)
    if match is None:
        return None
    username = match.group(1)
    return None if username.lower() == "highlights" else username


def is_instagram_story(url: str) -> bool:
    """Whether this is an ephemeral Instagram story: a `/stories/<user>/` link whose content is the
    user's current tray, which changes through the day. Such a link must never be recognized as
    already-fetched by its URL: a repeat paste days later is a request for the current tray, not the
    one that expired. Highlights are permanent, not ephemeral, so they are not stories (see
    `_stories_username`)."""
    return _stories_username(url) is not None


def _username_from_url(source_url: str) -> str | None:
    """The uploader from the URL, or None. A stories URL's user segment is the username."""
    match = _USERNAME_RE.search(source_url)
    if match:
        return match.group(1)
    return _stories_username(source_url)


def _canonicalize(url: str) -> str:
    """Strip a leading `/<user>/` and any query so instasave gets a clean `/reel/<id>/`. Returned
    unchanged when the URL carries no post/reel id (a `/stories/<user>/` link)."""
    match = _CANON_RE.search(url)
    if match is None:
        return url
    kind = match.group(1).lower()
    if kind == "reels":
        kind = "reel"
    return f"https://www.instagram.com/{kind}/{match.group(2)}/"


def _post_code(url: str) -> str | None:
    """The post's own code (`Cx7QpL2mNfA` in `/p/Cx7QpL2mNfA/`), or one story's number, from the
    link. None for a link naming neither: a whole story tray, whose stories have no shared code."""
    match = _CANON_RE.search(url)
    if match is not None:
        return match.group(2)
    story = _STORY_ID_RE.search(url)
    return story.group(1) if story is not None else None


def _is_single_video_post(url: str) -> bool:
    return _SINGLE_VIDEO_RE.search(url) is not None


def _decode_js_escapes(text: str) -> str:
    """Decode the `\\xHH` / `\\uHHHH` and backslash escapes instasave wraps its response in (a JS
    string literal). Then the proxy anchors inside can be read as plain URLs."""
    text = re.sub(r"\\x([0-9A-Fa-f]{2})", lambda m: chr(int(m.group(1), 16)), text)
    text = re.sub(r"\\u([0-9A-Fa-f]{4})", lambda m: chr(int(m.group(1), 16)), text)
    return text.replace("\\/", "/").replace('\\"', '"').replace("\\'", "'")


def _decode_jwt_payload(jwt: str) -> dict[str, object] | None:
    """The JWT's middle (payload) segment as JSON, or None if it is not a well-formed `a.b.c` token
    with a base64url JSON payload."""
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
    """Every proxy link the page offers as a DOWNLOAD, in the order it offers them.

    ## The distinction this rests on, and it is the whole of it

    instasave draws one card per medium, and every card carries TWO of these links:

        <div class="download-items__thumb"><img src="...?token=A" alt="Thumb"></div>
        <div class="download-items__btn"><a href="...?token=B">Download</a></div>

    For a photograph A and B are the same file, so a dedup on the media's own address collapses
    them. For a video the thumbnail is the auto-generated cover (a different file entirely), so
    a scan of every link would bring it into the library beside the clip.

    instasave does not append a cover: the page shows one preview per medium, and a loose scan
    counts previews as media. Reading the anchor is exact. Every real medium has one; a preview
    never does. On real bodies, cards / `img` / `a` / a loose scan read: an 11-photograph carousel
    **11 / 11 / 11 / 22**, a single video post **1 / 1 / 1 / 2**.

    ## Why a parser and not a tighter regex

    Because the thing being told apart is which ELEMENT the link is an attribute of, and that is
    what a parser answers and what a regex can only approximate. `bs4` is already a dependency and
    already used a few files away for exactly this.
    """
    soup = BeautifulSoup(html, "html.parser")
    found: list[str] = []
    for tag in soup.find_all("a", href=True):
        href = tag.get("href")
        if isinstance(href, str) and href.startswith(_PROXY_PREFIX):
            found.append(href)
    return found


def _classify(filename: str | None, direct_url: str | None) -> tuple[MediaType, str]:
    """A media type and extension from the JWT filename, then the direct CDN URL. Instagram is mostly
    reels, so an unknown pair defaults to video. Classification goes through the kernel's allowlist."""
    for candidate in (filename, direct_url):
        if not candidate:
            continue
        media_type, ext = guess_type_and_ext(candidate, filename)
        if media_type != "other":
            return media_type, ext
    return "video", ".mp4"


def _own_brand_off(filename: str | None) -> str | None:
    """The service's own name taken off the front of the name it supplied.

    The middleman answers with names like `instasave.website_512048337_...jpg`, and staging takes
    its name from whatever the resolver hands over, so every file it fetched would arrive in the
    library carrying the service's brand, permanently, in front of the name Instagram gave it.

    Nobody chose that. A person pasting a link is downloading from Instagram; which middleman did
    the fetching is Sift's business and not part of what the file is called. What is left is the
    name the site itself uses, which is what the rest of the download path already expects.

    Only the leading brand, and only this service's: the point is to stop advertising, not to start
    editing names a site chose.
    """
    if not filename:
        return filename
    cleaned = filename.lstrip("._-/ ")
    lowered = cleaned.casefold()
    if not lowered.startswith(INSTASAVE_HOST):
        return filename
    rest = cleaned[len(INSTASAVE_HOST) :].lstrip("._-/ ")
    # If the brand was the whole name there is nothing better to fall back to, so it stays and the
    # download keeps a name rather than losing one.
    return rest or filename


def _media_key(direct_url: str | None, token: str) -> str:
    """A name for one story or photo that means the same thing tomorrow.

    Instagram's own address for a file is signed and expires, so the query string is different every
    time the same media is asked for, while the path carries the media's id and does not change.
    Dropping the query is therefore what makes a story tray re-pastable: the items already fetched
    are recognized on the second visit instead of arriving as a fresh set.

    It also tightens the dedup within one response: the same media listed under two tokens
    collapses to one item rather than being fetched twice.
    """
    if direct_url:
        split = urlsplit(direct_url)
        if split.path:
            return f"{split.netloc}{split.path}"
    return token


def _videos_only(items: list[ResolvedItem]) -> list[ResolvedItem]:
    """Keep the videos and renumber them 0..n.

    The index is what the staging step numbers a file by, so a gap in it becomes a gap in the names
    on disk. Its own function because two rules below end the same way, and two copies of a
    re-index is how one of them comes to be missing it.
    """
    return [
        replace(item, index=new_index)
        for new_index, item in enumerate(one for one in items if one.media_type == "video")
    ]


def _without_video_covers(items: list[ResolvedItem]) -> list[ResolvedItem]:
    """Drop each image that is the auto-generated COVER of the video listed right after it.

    ## Why a story needs its own rule

    The reel rule drops the images, which is safe because a reel is one video. A story tray is a
    list of independent slides, some of them genuine photographs, so that blunt rule cannot be
    reused, and without a rule, a story link on the fallback path delivers a still of the first
    frame of every video in the tray, beside the video.

    ## Why adjacency, and what it rests on

    instasave lists a video's cover IMMEDIATELY BEFORE that video, which is the same pairing the
    reel rule relies on: every image with a video next in the list is a cover, and every image with
    another image next is a real slide.

    It is a heuristic, and this is what it rests on: covers come back **640x1136** and genuine
    slides **640x1138**: 1138 is the true 9:16 story canvas and 1136 the slightly different crop a
    cover is made at. That size is not used here (it is not known until the bytes arrive, and this
    runs before any fetch), but it agrees with the positional rule pair for pair.

    **What it would cost to be wrong:** a genuine photograph posted directly before a video in the
    same tray is not fetched. That is deliberately the small side of the trade.

    ## Only a STORY, on purpose

    A `/p/` carousel is left alone: it is a genuine mixture of photographs and clips. A highlight
    is left alone too, and that one is unverified rather than decided: it is the same content as a
    story and very likely behaves the same way, but it resolves through a different endpoint. What
    would settle it is one highlight pull with the pairs counted.
    """
    keep = [
        item
        for position, item in enumerate(items)
        if not (
            item.media_type == "image"
            and position + 1 < len(items)
            and items[position + 1].media_type == "video"
        )
    ]
    # Re-indexed 0..n, exactly as the reel rule does: the index is what the staging step numbers a
    # file by, so a gap in it turns into a gap in the names on disk.
    return [replace(item, index=new_index) for new_index, item in enumerate(keep)]


def parse_instasave_response(body: str, source_url: str) -> ResolvedMedia:
    """Map an instasave response body to `ResolvedMedia`.

    Decodes the JS-string escapes, finds each `cdn.instasave.website/?token=` proxy anchor (deduped
    by the underlying CDN URL, since instasave sometimes lists one media under several tokens; order
    kept for carousels), and reads each JWT for the human filename and media type. Raises
    `NothingFound` when the body exposes no anchor: a private or deleted post, or a layout change.
    """
    decoded = _decode_js_escapes(body)

    # The download links, which is what the page actually offers. See `_download_links`.
    #
    # The fallback is deliberate. instasave is somebody else's page and its markup is not a
    # contract: the day it stops drawing anchors the way it
    # draws them today, an exact reader finds nothing, and "nothing" here becomes "this post is
    # private, deleted, or holds no media": a message that reads as a bug when the truth is that
    # Sift stopped being able to read the page. So a body with links in it but none of them in an
    # anchor is read loosely, with a warning, and the cover rules that path needs are applied.
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
    # ONLY ON THE DEGRADED PATH. Every rule below drops an image on the strength of where it sits
    # in the list, and each one can therefore throw away a photograph somebody posted. Read
    # exactly, there is nothing for them to do (a preview is not in the list at all), so they do
    # not run, and a carousel keeps every slide in it whatever order its author chose.
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
    """POST `{url: field_value}` to `api` and parse the proxy-anchor body. Retries a 429 or transient
    5xx with the limiter holding the shared backoff first (a burst trips instasave's per-IP limit, so
    every Instagram resolve rides it out together); a persistent one becomes a retryable
    `DownloadError`. A `/story` POST warms the landing page first (the endpoint expects it)."""
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
            # A refusal for asking too often is the one the setting speaks to; a server error
            # keeps the measured backoff alone.
            if fetched.status_code == 429:
                backoff = middleman_backoff(backoff, policy)
            LIMITER.note_retry_after(INSTASAVE_HOST, backoff)
            continue
        return parse_instasave_response(fetched.text, source_url)
    raise DownloadError(busy_message("Instagram"))


async def resolve_instagram(
    url: str, *, proxy: str | None = None, policy: RunPolicy | None = None
) -> ResolvedMedia:
    """Resolve an Instagram post, reel, or story to direct media via instasave.

    A story (`/stories/<user>/`) posts the bare username to the story endpoint; a post or reel is
    canonicalised and posted to the media endpoint. The pasted URL stays the source, so attribution
    and the pipeline's url-hash are read from it.
    """
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
    # A true story is handled above; a post/reel/tv link carries an id `_CANON_RE` finds; a
    # highlights link (`/stories/highlights/<id>/`, a permalink `_stories_username` deliberately
    # leaves for the media endpoint) still matches `_STORIES_RE`. Anything with none of those is not
    # a single item: a bare `/<user>/` profile, or a `/reels`, `/tagged`, `/explore` tab. There is
    # nothing there to fetch (a whole profile is many posts, not a download), so this is refused
    # with guidance rather than posted to instasave, which would come back empty and surface as
    # "private, deleted, or holds no media", a message that reads as a bug when the truth is the link
    # was the wrong shape.
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
