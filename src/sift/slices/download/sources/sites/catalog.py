# SPDX-License-Identifier: AGPL-3.0-or-later
"""One record per site, read by both the fetch path and attribution, so they cannot disagree.

Hosts match on a label boundary, never a substring: a look-alike cannot borrow a site's Cookies."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal
from urllib.parse import urlsplit

import aiohttp

from sift.kernel.urls import (
    INSTAGRAM_NOT_A_USERNAME,
    INSTAGRAM_ROUTE_THEN_USERNAME,
    INSTAGRAM_ROUTES,
)
from sift.slices.download.sources.hosts import host_matches, source_host
from sift.slices.download.sources.sites import extractors
from sift.slices.download.sources.sites.common import ExtractContext, ExtractedFile


class Backend(StrEnum):
    """The external tool that fetches an address. Each is run as a separate process."""

    YTDLP = "ytdlp"
    GALLERYDL = "gallerydl"


class CookieNeed(StrEnum):
    """What a Site does with no cookies saved, measured by running it, never read off code."""

    REQUIRED = "required"
    PARTIAL = "partial"
    NOT_NEEDED = "not_needed"


#: Neither downloader reads both, so a mixed site needs a fallback or an extractor.
MediaKind = Literal["video", "images"]

UsernameRule = Callable[[Sequence[str]], str | None]

#: A shape within a site, never the whole site: a post is fixed, a story tray is not.
MutableRule = Callable[[Sequence[str]], bool]

ExtractFn = Callable[[str, aiohttp.ClientSession, ExtractContext], Awaitable[list[ExtractedFile]]]


@dataclass(frozen=True, slots=True)
class Wall:
    """A refusal one site gives in words, written only where somebody has seen the site say it."""

    #: Declared, not derived from the sentence: a code must not change with the wording.
    kind: str
    pattern: str
    sentence: str
    a_tunnel_would_help: bool = False


@dataclass(frozen=True, slots=True)
class Meaning:
    """What one failure code means on one site, written only where it was seen (`seen`)."""

    code: str
    meaning: str
    seen: str
    #: Replaces what the code alone would say: a 410 can be a region refusal.
    a_tunnel_would_help: bool = False


_A_PERSONS_SEGMENT = ("model", "pornstar", "users", "channels")


def _segment_after(segments: Sequence[str], names: Sequence[str]) -> str | None:
    """The segment following one of `names`, which is where these sites put the uploader."""
    for index, segment in enumerate(segments[:-1]):
        if segment.lower() in names:
            return segments[index + 1] or None
    return None


def _uploader_username(segments: Sequence[str]) -> str | None:
    """Who posted, on a site whose profile pages live under a named segment."""
    return _segment_after(segments, _A_PERSONS_SEGMENT)


def _at_username(segments: Sequence[str]) -> str | None:
    """The first path segment written as an @username, less the @. TikTok and YouTube use these."""
    for segment in segments:
        if segment.startswith("@") and len(segment) > 1:
            return segment[1:]
    return None


_X_ROUTES = frozenset(
    {"i", "home", "explore", "search", "notifications", "messages", "settings", "intent", "hashtag"}
)


def _instagram_username(segments: Sequence[str]) -> str | None:
    """The username in an Instagram address; none for a post link, which names nobody."""
    if not segments:
        return None
    first = segments[0].lower()
    if first in INSTAGRAM_ROUTE_THEN_USERNAME:
        if len(segments) < 2 or segments[1].lower() in INSTAGRAM_NOT_A_USERNAME:
            return None
        return segments[1]
    if first in INSTAGRAM_ROUTES:
        return None
    return segments[0]


def _instagram_mutable(segments: Sequence[str]) -> bool:
    """Whether an Instagram address is under `stories`, whose contents keep changing."""
    return bool(segments) and segments[0].lower() == "stories"


def _redgifs_username(segments: Sequence[str]) -> str | None:
    """The uploader in a `redgifs.com/users/<name>` address, and nothing anywhere else."""
    if len(segments) >= 2 and segments[0].lower() == "users":
        return segments[1]
    return None


def _x_username(segments: Sequence[str]) -> str | None:
    """The username in an X address, or nothing for the site's own routes."""
    if not segments or segments[0].lower() in _X_ROUTES:
        return None
    return segments[0]


@dataclass(frozen=True, slots=True, kw_only=True)
class SiteRecord:
    """Everything both paths need to know about one site; keyword-only, so some have no default."""

    #: Stable across renames and domain moves; settings key on it.
    key: str
    hosts: tuple[str, ...]
    site: str
    backend: Backend
    media: tuple[MediaKind, ...]
    fallback_backend: Backend | None = None
    username_rule: UsernameRule | None = None
    #: False by default: on a forum the username is a board, not a person.
    username_is_a_person: bool = False
    #: Its own fact: reading every page on the chance of a song would cost a request per file.
    names_music: bool = False
    #: For these, "fetched this address" no longer means "fetched what is behind it".
    mutable: MutableRule | None = None
    #: Off only where a site punishes it: Instagram locks the account.
    bulk: bool = True
    #: False for a record kept only so its traffic can be sent through a tunnel.
    supported: bool = True
    #: Only once downloads have been run and found working.
    tested: bool = False
    #: Whether a refusal waits for Cookies rather than failing.
    cookies_help: bool = False
    #: No default: every default here would be a claim nobody measured.
    cookies: CookieNeed
    cookies_why: str
    #: The need under yt-dlp or gallery-dl where it differs from Sift's own method.
    cookies_with_a_tool: CookieNeed | None = None
    walls: tuple[Wall, ...] = ()
    failure_words: tuple[Meaning, ...] = ()

    #: Measured: some sites refuse yt-dlp's own TLS handshake, which no tunnel changes.
    impersonate: bool = False

    extract: ExtractFn | None = None
    #: Only to read a creator's picture; never guessed.
    profile_url: str | None = None

    #: No default; EMPTY keeps the imported name, which `{name}` would strip of accents.
    default_naming: str
    #: Words beyond `EVERY_SITE_FILLS` and `creator`; no default, so a new Site says.
    name_words: tuple[str, ...]


_BUNKR_HOSTS = (
    "bunkr.ac",
    "bunkr.ax",
    "bunkr.black",
    "bunkr.cat",
    "bunkr.ci",
    "bunkr.cr",
    "bunkr.fi",
    "bunkr.is",
    "bunkr.la",
    "bunkr.media",
    "bunkr.nu",
    "bunkr.ph",
    "bunkr.pk",
    "bunkr.ps",
    "bunkr.red",
    "bunkr.ru",
    "bunkr.se",
    "bunkr.si",
    "bunkr.site",
    "bunkr.sk",
    "bunkr.to",
    "bunkr.ws",
    "bunkrr.ru",
    "bunkrr.su",
    "bunkrrr.org",
)

_LOGIN_PATTERN = (
    r"login required|requested content is not available|rate.?limit reached|"
    r"restricted video|sign ?up to continue|please log in"
)

_LOGIN_WALL = Wall(
    kind="login",
    pattern=_LOGIN_PATTERN,
    sentence="Cookies needed.",
)

_LOGIN_WALL_INSTAGRAM = Wall(
    kind="login",
    pattern=_LOGIN_PATTERN,
    sentence="Cookies needed if download method is changed from Sift.",
)

_AGE_PATTERN = (
    r"age.?verif|confirm your age|18 u\.?s\.?c|adult content warning"
    r"|this is an adult website|age.?restricted material|i am 18 or older"
)

_AGE_WALL_LOGIN = Wall(
    kind="age",
    pattern=_AGE_PATTERN,
    sentence="Age restricted content requires cookies.",
)

_REGION_WALL_BEHIND_AN_AGE_GATE = Wall(
    kind="region",
    pattern=_AGE_PATTERN,
    sentence="Georestricted in some locations. Adding this site to a download Tunnel can help "
    "bypass those restrictions. Adding cookies may help.",
    # The sentence already names the tunnel; true would append a second one (`jobs.py`).
    a_tunnel_would_help=False,
)


SITES: tuple[SiteRecord, ...] = (
    SiteRecord(
        key="tiktok",
        default_naming="{creator} - {posted} - {id}",
        name_words=("id", "title", "posted", "n"),
        cookies_help=False,
        cookies=CookieNeed.NOT_NEEDED,
        cookies_why="Sift's own method never sends cookies, and yt-dlp downloads without them too.",
        cookies_with_a_tool=CookieNeed.NOT_NEEDED,
        hosts=("tiktok.com",),
        site="TikTok",
        backend=Backend.YTDLP,
        media=("video",),
        username_rule=_at_username,
        username_is_a_person=True,
        profile_url="https://www.tiktok.com/@{username}",
    ),
    SiteRecord(
        key="youtube",
        default_naming="{creator} - {name}",
        name_words=("id", "title", "posted"),
        cookies_help=True,
        cookies=CookieNeed.PARTIAL,
        cookies_why="Age-restricted videos need cookies.",
        hosts=("youtube.com", "youtu.be"),
        site="YouTube",
        backend=Backend.YTDLP,
        media=("video",),
        username_rule=_at_username,
        username_is_a_person=True,
        walls=(_AGE_WALL_LOGIN,),
        profile_url="https://www.youtube.com/@{username}",
    ),
    SiteRecord(
        key="instagram",
        default_naming="{creator} - {id}",
        name_words=("id", "n"),
        cookies_help=True,
        cookies=CookieNeed.NOT_NEEDED,
        cookies_why=(
            "Sift's own method never sends cookies. Set Instagram to yt-dlp or gallery-dl and "
            "every post needs them."
        ),
        cookies_with_a_tool=CookieNeed.REQUIRED,
        tested=True,
        hosts=("instagram.com",),
        site="Instagram",
        backend=Backend.YTDLP,
        media=("video", "images"),
        fallback_backend=Backend.GALLERYDL,
        username_rule=_instagram_username,
        username_is_a_person=True,
        mutable=_instagram_mutable,
        walls=(_LOGIN_WALL_INSTAGRAM,),
        # Instagram locks the account over a whole-profile pull.
        bulk=False,
        profile_url="https://www.instagram.com/{username}/",
    ),
    SiteRecord(
        key="x",
        default_naming="{creator} - {name}",
        name_words=("id", "posted", "n"),
        cookies_help=True,
        cookies=CookieNeed.PARTIAL,
        cookies_why="Profiles and posts marked sensitive need cookies.",
        hosts=("x.com", "twitter.com"),
        site="X",
        backend=Backend.GALLERYDL,
        media=("video", "images"),
        fallback_backend=Backend.YTDLP,
        username_rule=_x_username,
        username_is_a_person=True,
        walls=(_LOGIN_WALL,),
        failure_words=(
            Meaning(
                code="gone",
                meaning="the post is private or deleted",
                seen="a status link the site had taken down: gallery-dl said 'Unavailable' and "
                "yt-dlp 'Video #1 is unavailable', both without cookies",
            ),
        ),
        profile_url="https://x.com/{username}",
    ),
    SiteRecord(
        key="redgifs",
        default_naming="{creator} - {id}",
        name_words=("id", "title", "posted"),
        cookies_help=False,
        cookies=CookieNeed.NOT_NEEDED,
        cookies_why="Everything downloads without them.",
        hosts=("redgifs.com", "redgifs.app"),
        site="RedGIFs",
        backend=Backend.YTDLP,
        media=("video",),
        username_rule=_redgifs_username,
        username_is_a_person=True,
        profile_url="https://www.redgifs.com/users/{username}",
    ),
    SiteRecord(
        key="reddit",
        default_naming="{site} - {title}",
        name_words=("id", "title", "posted", "n"),
        cookies_help=True,
        cookies=CookieNeed.PARTIAL,
        cookies_why="Communities behind a content warning, and private ones, need cookies.",
        hosts=("reddit.com", "redd.it"),
        site="Reddit",
        backend=Backend.YTDLP,
        media=("video", "images"),
        fallback_backend=Backend.GALLERYDL,
        walls=(_LOGIN_WALL,),
        profile_url="https://www.reddit.com/user/{username}/",
    ),
    SiteRecord(
        key="goonbox",
        default_naming="{creator} - {name}",
        name_words=("n",),
        cookies_help=False,
        cookies=CookieNeed.NOT_NEEDED,
        cookies_why="Everything downloads without them.",
        tested=True,
        hosts=("goonbox.cr", "cuckcapital.cr"),
        site="GoonBox",
        backend=Backend.YTDLP,
        media=("video", "images"),
        fallback_backend=Backend.GALLERYDL,
        username_is_a_person=True,
    ),
    SiteRecord(
        key="pmvhaven",
        default_naming="{creator} - {name}",
        name_words=("title", "n"),
        cookies_help=False,
        cookies=CookieNeed.NOT_NEEDED,
        cookies_why="Everything downloads without them.",
        hosts=("pmvhaven.com",),
        site="PMVHaven",
        backend=Backend.YTDLP,
        media=("video",),
        username_is_a_person=True,
        names_music=True,
        extract=extractors.extract_pmvhaven,
        profile_url="https://pmvhaven.com/profile/{username}",
    ),
    SiteRecord(
        key="fapello",
        default_naming="{creator} - {name}",
        name_words=("n",),
        cookies_help=False,
        cookies=CookieNeed.NOT_NEEDED,
        cookies_why="Everything downloads without them.",
        hosts=("fapello.com", "fapello.su"),
        site="Fapello",
        backend=Backend.GALLERYDL,
        media=("video", "images"),
        username_is_a_person=True,
        extract=extractors.extract_fapello,
    ),
    SiteRecord(
        key="coomer",
        default_naming="",
        name_words=("n",),
        cookies_help=False,
        cookies=CookieNeed.NOT_NEEDED,
        cookies_why="Posts download without them.",
        # Both domains: a host left out would read as the Site refusing.
        hosts=("coomer.st", "coomer.su"),
        site="Coomer",
        backend=Backend.GALLERYDL,
        media=("video", "images"),
        supported=False,
        extract=extractors.extract_coomer,
    ),
    SiteRecord(
        key="kemono",
        default_naming="",
        name_words=("n",),
        cookies_help=False,
        cookies=CookieNeed.NOT_NEEDED,
        cookies_why="Posts download without them.",
        hosts=("kemono.su", "kemono.cr"),
        site="Kemono",
        backend=Backend.GALLERYDL,
        media=("video", "images"),
        extract=extractors.extract_kemono,
    ),
    SiteRecord(
        key="pornhub",
        default_naming="",
        name_words=("id", "title", "posted"),
        cookies_help=True,
        cookies=CookieNeed.PARTIAL,
        cookies_why="Private and Premium videos need cookies.",
        hosts=("pornhub.com",),
        site="Pornhub",
        backend=Backend.YTDLP,
        media=("video",),
        impersonate=True,
        username_rule=_uploader_username,
        username_is_a_person=True,
        walls=(_REGION_WALL_BEHIND_AN_AGE_GATE,),
        failure_words=(
            Meaning(
                code="http-410",
                meaning="it refuses the downloader's own way of connecting, so Sift has to "
                "connect the way a browser does",
                seen="yt-dlp's default client got 410 for real, invented and front-page addresses "
                "alike; the same request with --impersonate chrome got the video (and 404 for an "
                "invented one); a plain request from a walled region got a 302 to the front page.",
                a_tunnel_would_help=False,
            ),
        ),
    ),
    SiteRecord(
        key="hqporner",
        default_naming="",
        name_words=("title", "n"),
        cookies_help=False,
        cookies=CookieNeed.NOT_NEEDED,
        cookies_why="Nothing on it needs cookies.",
        hosts=("hqporner.com",),
        site="HQporner",
        backend=Backend.YTDLP,
        media=("video",),
        extract=extractors.extract_hqporner,
        tested=True,
    ),
    SiteRecord(
        key="redtube",
        default_naming="",
        name_words=("id", "title", "posted"),
        cookies_help=False,
        cookies=CookieNeed.NOT_NEEDED,
        cookies_why="Nothing on it needs cookies.",
        hosts=("redtube.com",),
        site="RedTube",
        backend=Backend.YTDLP,
        media=("video",),
        supported=False,
    ),
    SiteRecord(
        key="xvideos",
        default_naming="",
        name_words=("id", "title", "posted"),
        cookies_help=False,
        cookies=CookieNeed.NOT_NEEDED,
        cookies_why="Nothing on it needs cookies.",
        hosts=("xvideos.com",),
        site="XVideos",
        backend=Backend.YTDLP,
        media=("video",),
        supported=False,
    ),
    SiteRecord(
        key="bunkr",
        default_naming="",
        name_words=("n",),
        cookies_help=False,
        cookies=CookieNeed.NOT_NEEDED,
        cookies_why="A file host \u2014 nothing Sift downloads from it carries cookies.",
        tested=True,
        hosts=_BUNKR_HOSTS,
        site="Bunkr",
        backend=Backend.GALLERYDL,
        media=("video", "images"),
        extract=extractors.extract_bunkr,
    ),
    SiteRecord(
        key="cyberdrop",
        default_naming="",
        name_words=("n",),
        cookies_help=False,
        cookies=CookieNeed.NOT_NEEDED,
        cookies_why="A file host \u2014 nothing Sift downloads from it carries cookies.",
        tested=True,
        hosts=("cyberdrop.me", "cyberdrop.cr", "cyberdrop.to"),
        site="Cyberdrop",
        backend=Backend.GALLERYDL,
        media=("video", "images"),
        extract=extractors.extract_cyberdrop,
    ),
    SiteRecord(
        key="cyberfile",
        default_naming="",
        name_words=("n",),
        cookies_help=False,
        cookies=CookieNeed.NOT_NEEDED,
        cookies_why="A file host \u2014 nothing Sift downloads from it carries cookies.",
        tested=True,
        hosts=("cyberfile.me",),
        site="Cyberfile",
        backend=Backend.GALLERYDL,
        media=("video", "images"),
        extract=extractors.extract_cyberfile,
    ),
    SiteRecord(
        key="gofile",
        default_naming="",
        name_words=("n",),
        cookies_help=False,
        cookies=CookieNeed.NOT_NEEDED,
        cookies_why="A file host \u2014 nothing Sift downloads from it carries cookies.",
        tested=True,
        hosts=("gofile.io",),
        site="GoFile",
        backend=Backend.GALLERYDL,
        media=("video", "images"),
        extract=extractors.extract_gofile,
    ),
    SiteRecord(
        key="discord",
        default_naming="",
        name_words=("posted",),
        cookies_help=False,
        cookies=CookieNeed.NOT_NEEDED,
        cookies_why="An attachment link is signed \u2014 cookies never reach it.",
        tested=True,
        # The attachment hosts only: a `discord.com` channel is a conversation no tool can fetch.
        hosts=("discordapp.com", "discordapp.net"),
        site="Discord",
        backend=Backend.YTDLP,
        media=("video", "images"),
        fallback_backend=Backend.GALLERYDL,
        username_is_a_person=False,
        bulk=False,
    ),
    SiteRecord(
        key="pixeldrain",
        default_naming="",
        name_words=("id", "n"),
        cookies_help=False,
        cookies=CookieNeed.NOT_NEEDED,
        cookies_why="A file host \u2014 nothing Sift downloads from it carries cookies.",
        hosts=("pixeldrain.com",),
        site="Pixeldrain",
        backend=Backend.GALLERYDL,
        media=("video", "images"),
        extract=extractors.extract_pixeldrain,
    ),
    SiteRecord(
        key="xbunkr",
        default_naming="",
        name_words=("n",),
        cookies_help=False,
        cookies=CookieNeed.NOT_NEEDED,
        cookies_why="A file host \u2014 nothing Sift downloads from it carries cookies.",
        hosts=("xbunkr.com", "xbunker.nu"),
        site="XBunkr",
        backend=Backend.GALLERYDL,
        media=("images",),
        extract=extractors.extract_xbunkr,
    ),
    SiteRecord(
        key="jpg5",
        default_naming="",
        name_words=("n",),
        cookies_help=False,
        cookies=CookieNeed.NOT_NEEDED,
        cookies_why="A file host \u2014 nothing Sift downloads from it carries cookies.",
        tested=True,
        hosts=(
            "jpg1.su",
            "jpg2.su",
            "jpg3.su",
            "jpg4.su",
            "jpg5.su",
            "jpg6.su",
            "jpg.fish",
            "jpg.church",
            "jpg.pet",
            "jpeg.pet",
            "host.church",
        ),
        site="JPG5",
        backend=Backend.GALLERYDL,
        media=("images",),
        extract=extractors.extract_jpg5,
    ),
    SiteRecord(
        key="turbovid",
        default_naming="",
        name_words=("n",),
        cookies_help=False,
        cookies=CookieNeed.NOT_NEEDED,
        cookies_why="A file host \u2014 nothing Sift downloads from it carries cookies.",
        hosts=("turbovid.cr", "turbo.cr"),
        site="TurboVid",
        backend=Backend.YTDLP,
        media=("video",),
        extract=extractors.extract_turbovid,
    ),
    SiteRecord(
        key="saint",
        default_naming="",
        name_words=("n",),
        cookies_help=False,
        cookies=CookieNeed.NOT_NEEDED,
        cookies_why="A file host \u2014 nothing Sift downloads from it carries cookies.",
        hosts=("saint.to", "saint2.su", "saint2.cr"),
        site="Saint",
        backend=Backend.YTDLP,
        media=("video",),
        extract=extractors.extract_turbovid,
    ),
    SiteRecord(
        key="imgur",
        default_naming="{site} - {id}",
        name_words=("id", "title", "posted"),
        cookies_help=False,
        cookies=CookieNeed.NOT_NEEDED,
        cookies_why="Everything downloads without them.",
        hosts=("imgur.com", "imgur.io"),
        site="Imgur",
        backend=Backend.YTDLP,
        media=("video", "images"),
        fallback_backend=Backend.GALLERYDL,
        username_is_a_person=False,
    ),
)


def match(url: str) -> SiteRecord | None:
    """The record whose host group owns this URL, or None."""
    return match_host(source_host(url))


def site_key_of(url: str) -> str | None:
    """The key of the Site a URL belongs to, or None; handed to the kernel's egress router."""
    record = match(url)
    return record.key if record is not None else None


def match_host(host: str | None) -> SiteRecord | None:
    """The record whose host group owns this hostname (a cookie domain's dot allowed), or None."""
    if not host:
        return None
    lowered = host.lower().lstrip(".")
    for record in SITES:
        if host_matches(lowered, record.hosts):
            return record
    return None


def by_site(site: str) -> SiteRecord | None:
    """The record shown under this display name, compared without case, or None."""
    wanted = site.strip().lower()
    for record in SITES:
        if record.site.lower() == wanted:
            return record
    return None


def is_mutable(url: str) -> bool:
    """Whether this address's contents change; False for an unknown one, the safe direction."""
    record = match(url)
    if record is None or record.mutable is None:
        return False
    return record.mutable([segment for segment in urlsplit(url).path.split("/") if segment])


def cookie_need(record: SiteRecord, *, tool_chosen: bool) -> CookieNeed:
    """What this Site needs, for the method a download is actually going to use."""
    if tool_chosen and record.cookies_with_a_tool is not None:
        return record.cookies_with_a_tool
    return record.cookies


EVERY_SITE_FILLS: tuple[str, ...] = ("site", "name", "date", "time")


def words_filled(record: SiteRecord) -> tuple[str, ...]:
    """Every template word a download from this Site can fill, in the screen's order."""
    creator = ("creator",) if record.username_is_a_person else ()
    return (*EVERY_SITE_FILLS, *creator, *record.name_words)


def by_key(key: str) -> SiteRecord | None:
    """The record stored settings are written under, or None for a key no longer in the catalog."""
    for record in SITES:
        if record.key == key:
            return record
    return None


def hosts_of(key: str) -> tuple[str, ...]:
    """The host group of one site, by key, so the resolvers keep no second copy."""
    for record in SITES:
        if record.key == key:
            return record.hosts
    raise KeyError(key)


__all__ = [
    "EVERY_SITE_FILLS",
    "SITES",
    "Backend",
    "CookieNeed",
    "ExtractFn",
    "Meaning",
    "MediaKind",
    "SiteRecord",
    "UsernameRule",
    "by_key",
    "by_site",
    "cookie_need",
    "hosts_of",
    "is_mutable",
    "match",
    "match_host",
    "site_key_of",
    "words_filled",
]
