# SPDX-License-Identifier: AGPL-3.0-or-later
"""One record per site: who it is, which tool reads it, and how to find the username in the URL.

Two different questions are asked about a pasted address (which tool or extractor fetches it, and
what the address says about who made the media), and they are answered by two different pieces of
code on purpose. What they must never disagree about is the SITE: its host group, its display name,
and whether a username on it is a person.

Two copies of a site's host group or name drift apart silently: half its mirrors resolve and are
then filed under a guessed site name, and nothing fails: downloads simply arrive attributed to
the wrong place, or to nothing.

So the data lives once, here, and both paths read it. Behavior stays separate: the extractor is a
field on the record rather than a membership in a second list, and a site with none simply leaves it
empty.

Adding a site is one record. Host matching is suffix/label-boundary, never substring, so a
look-alike domain cannot borrow a real site's extractor or its saved login.
"""

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
    """What a Site does for somebody with no cookies saved for it: measured, never read off code.

    The question the cookies sheet and the supported list answer in one badge per Site, before
    anything has failed: must I add cookies before this Site gives me anything, would they only
    get me some of it, or are they no use here at all. Three answers, and there is no fourth.

    ## Why there is no "not known"

    A reading of the code alone gets these wrong: RedGIFs and Kemono look as though cookies
    unlock more, and they do not; Sift's own method for Instagram never sends any. So each answer
    below is settled by running Sift's own default tool against the Site with no cookies, and where
    a Site could not be reached the answer rests on where Sift's code sends a jar at all, which is
    certain whatever the Site does: a reader that never reads the file cannot be helped by one.
    Every record carries the sentence that says which (`cookies_why`), and the record's comment
    says what was seen. A Site added later has to be run the same way; a record without a
    declaration does not construct (the fields have no default).

    It is not the same fact as `cookies_help`, and that is why both exist. `cookies_help` decides
    what a REFUSAL does (wait for cookies, or fail); this says what the Site does. They may not
    contradict each other (a Site whose need is Required or Partial, under Sift's method or
    under a tool somebody chose for it, must wait for cookies when refused, and one that needs
    them under neither must not), and a test holds the two to that.
    """

    #: Sift's method fails without them on ordinary public content: nothing downloads until they
    #: are added. A download is held for them before anything is asked of the Site.
    REQUIRED = "required"
    #: Public content downloads without them; some content needs them: `cookies_why` says which.
    PARTIAL = "partial"
    #: Everything Sift downloads from it comes without cookies, or nothing Sift sends there
    #: carries them at all.
    NOT_NEEDED = "not_needed"


#: What a site serves. A site with both is mixed, which is the case a single tool cannot cover:
#: neither downloader reads images and video, so a mixed site needs a fallback or an extractor.
MediaKind = Literal["video", "images"]

#: A rule that reads a username out of a URL's path segments, or returns None when there is
#: nothing there to read.
UsernameRule = Callable[[Sequence[str]], str | None]

#: A rule that says whether one address on a site stands for a set whose CONTENTS CHANGE.
#:
#: It is a shape within a site, never the whole site: an Instagram post is fixed forever and an
#: Instagram story tray is different by the afternoon, and both are Instagram. So the rule reads the
#: path, exactly as `username_rule` does, and a site with no such shapes leaves it empty.
MutableRule = Callable[[Sequence[str]], bool]

#: Reads a page or an API and returns the direct media addresses behind it.
ExtractFn = Callable[[str, aiohttp.ClientSession, ExtractContext], Awaitable[list[ExtractedFile]]]


@dataclass(frozen=True, slots=True)
class Wall:
    """A refusal one site gives in words, where no status code carries the meaning.

    This is the whole reason a site record has anything to say about failures. A large adult site's
    age gate answers **403**, byte for byte the same as any other 403; several big sites answer
    **200** with a page that says no. So the only place the real reason exists is as prose in the
    tool's output, and the only honest way to read it is per site.

    Written only where somebody has seen the site say it. A phrase guessed from documentation is
    worse than no phrase: it produces a confident, specific, wrong explanation, which is the one
    thing an error message must never be.

    `a_tunnel_would_help` marks the refusals that are about WHERE the request came from: a region
    block, an address the site has decided it does not like. Those are the only failures the other
    half of this slice can actually fix, and saying so turns a dead end into one setting to change.
    """

    #: What kind of wall this is: `age`, `region`, `login`. Short, stable, and the thing a row and
    #: a search match on, so it is declared here rather than derived from the sentence: a sentence
    #: gets reworded, and a code that changes when the wording does is not a code.
    kind: str
    #: Matched against the tool's output, case-insensitively.
    pattern: str
    #: What a person reads. Says what to do wherever there is anything to do.
    sentence: str
    a_tunnel_would_help: bool = False


@dataclass(frozen=True, slots=True)
class Meaning:
    """What one failure code MEANS on one site, where the site gives it a meaning of its own.

    A status code's standard phrase is the same everywhere and says nothing about the site: "410
    Gone" is true on every server in the world. What it means HERE (that this site answers 410
    for a video it has taken down, or that its "Unavailable" covers a post made private as well as
    one deleted) is a fact about the site, and so it lives on the site's record beside its walls.

    The failure reader looks the code up here after it has worked out what the code IS, and the
    sentence a row shows is then "Pornhub answered 410 Gone: it refuses the downloader's own way
    of connecting, so Sift has to connect the way a browser does." rather than the standard phrase
    alone, and not "gone", which is what a 410 means on most servers and is exactly wrong on
    this one.

    Held to the rule the walls are held to: written only where somebody has seen the site answer
    it. `seen` says where, so a later reader can tell a measured meaning from a remembered one and
    a test refuses an entry that does not say.
    """

    #: The code as the failure reader writes it: `http-410`, `gone`, `private`.
    code: str
    #: What it means on this site, as the second half of a sentence: lower case, no full stop.
    meaning: str
    #: Where the site was seen giving it. Never shown; kept so the claim can be checked.
    seen: str
    #: Whether this code, on this site, is a refusal about WHERE the request came from, the one
    #: kind a tunnel answers. It replaces what the code alone would say: a 410 is "gone" on most
    #: servers and is a region refusal on a site that answers it to a whole region.
    a_tunnel_would_help: bool = False


#: The path segments that introduce a person on a site that files uploads under one.
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


# Instagram's route words (which first segments are the site's own and never a profile) are in
# the kernel (`kernel/urls.py`, `INSTAGRAM_ROUTES` and the two beside it), because the reader of a
# picture's own metadata has to refuse the same words and may not import this slice.

# The same trouble on X, whose own links are all under `/i/`. `intent` and the rest are the site's
# furniture; none of them is somebody's username.
_X_ROUTES = frozenset(
    {"i", "home", "explore", "search", "notifications", "messages", "settings", "intent", "hashtag"}
)


def _instagram_username(segments: Sequence[str]) -> str | None:
    """The username in an Instagram address, or nothing where the address does not carry one.

    Nothing is the right answer for a post link. The shortcode in `/p/<code>` identifies the post
    and says nothing about who posted it (that is on the page, not in the address), so the
    honest result is no username, which still records the site.
    """
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
    """Whether an Instagram address stands for something whose contents change.

    Everything under `stories` does, and for two different reasons. A tray (`/stories/<user>/`) is
    the last day of somebody's posting and is a different set of media by the evening. A highlight
    (`/stories/highlights/<id>/`) is permanent, which is what made it look safe, but the username
    behind it keeps adding, so the address stays the same while what is behind it grows.

    A post, a reel and a profile are none of that: one address, one set of media, forever.
    """
    return bool(segments) and segments[0].lower() == "stories"


def _redgifs_username(segments: Sequence[str]) -> str | None:
    """The uploader in a RedGIFs address: `redgifs.com/users/<name>`, and nothing anywhere else.

    A profile link is the whole of what the address can say here. `/watch/<id>` identifies one clip
    and names nobody (who posted it is on the page and in the site's own API, which is where the
    resolver reads it), so a route word yields nothing rather than a person called "watch".
    """
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
    """Everything both paths need to know about one site.

    Keyword-only, so a field may be declared without a default anywhere in it, which is how the
    cookie need is REQUIRED of every record rather than defaulted to an answer nobody gave.
    """

    #: A stable name for the site, independent of its display name and its domains. Settings and
    #: the support matrix key on this, so a site can be renamed or move domain without orphaning
    #: whatever an admin chose for it.
    key: str
    hosts: tuple[str, ...]
    #: The display name. The Site a download is filed under.
    site: str
    #: The tool that fetches the source URL when no resolver or extractor takes it first.
    backend: Backend
    #: What the site serves. Both kinds means mixed.
    media: tuple[MediaKind, ...]
    #: A second tool to try when the first finds nothing.
    #:
    #: Neither downloader reads images and video, so a mixed site fetched by one tool silently
    #: returns nothing for the kind that tool does not read: one tool, no fallback, and a quiet
    #: half-answer.
    fallback_backend: Backend | None = None
    username_rule: UsernameRule | None = None
    #: Whether the username this site yields NAMES A PERSON.
    #:
    #: The distinction is the whole reason this field exists. On a creator site the username in the
    #: address is the person who made what is being fetched, and treating it as their name is right.
    #: On a forum it is the board the thing was posted to; on a file host there is no username at
    #: all. Without saying which is which, a rule that turns usernames into People turns every
    #: subreddit into a human being.
    #:
    #: It also decides whether the PAGE is worth reading for a creator when the address named
    #: nobody, so it is set on a creator site that has no `username_rule` at all.
    #:
    #: Defaults to false, so a site added later has to say yes deliberately. That is the safe
    #: direction: the cost of a missed one is a person somebody links by hand, and the cost of a
    #: wrong one is a list of People that has to be cleaned up.
    username_is_a_person: bool = False
    #: Whether this site records WHAT TRACK a video is set to, somewhere its page can be read.
    #:
    #: Its own fact rather than something inferred, because it decides whether a page is fetched
    #: at all. Reading every page of every download on the chance a song is named would be one
    #: extra request per file, forever, for the handful of sites that say, and the reader would
    #: return nothing for all the rest, so nothing would ever reveal the waste.
    #:
    #: Defaults to false, so a site added later has to say yes deliberately.
    names_music: bool = False
    #: Which of this site's addresses stand for a set whose contents change over time.
    #:
    #: The ledger asks this before it decides a link has already been fetched. For an ordinary
    #: permalink that decision is right and saves a pointless second download; for one of these it is
    #: silent data loss, because the answer to "have I fetched this address" stopped being the answer
    #: to "have I fetched what is behind it".
    mutable: MutableRule | None = None
    #: Whether a whole profile, channel or playlist may be taken in one go.
    #:
    #: On by default, because taking a creator's whole output is the ordinary reason to point a
    #: downloader at a creator page, and most of these sites exist to be read that way.
    #:
    #: Off is for a site that punishes it. Instagram is the one, and it is not a guess: it watches
    #: for exactly this pattern and answers by locking the account rather than by refusing the
    #: download. A site with this off still fetches anything pasted one link at a time.
    bulk: bool = True
    #: Whether Sift CLAIMS this site works, as opposed to merely recognising it.
    #:
    #: A record exists for two different reasons and they must not be confused. Most are here
    #: because the site is meant to be downloaded from: the address is read, the site is filed,
    #: and the shape of the site has been looked at. Some are here only so that traffic to them can
    #: be sent through a chosen tunnel, which needs the address recognised and nothing else.
    #:
    #: False says the second: paste a link and Sift will try, exactly as it tries any address it has
    #: never seen, and nothing about it has been checked. The matrix says so rather than leaving
    #: somebody to infer support from the site being listed at all.
    supported: bool = True
    #: Whether somebody has actually run downloads against this site and found it working.
    #:
    #: False everywhere until it has been done, deliberately: a matrix that claims a site is tested
    #: because the code looks right is worth less than no matrix, because it is believed.
    tested: bool = False
    #: Whether a jar of cookies exported from a signed-in browser is the answer when this site
    #: refuses to serve something.
    #:
    #: It decides what HAPPENS to a refused download, which is why it is a fact about the site
    #: rather than a reading of the failure. A 401 or a 403 from a site that says yes here, with
    #: nothing saved for it, is a download WAITING for cookies: the row says so, offers Add
    #: cookies, and continues once they are saved. The same refusal from a site that says no is a
    #: failure, because there is nothing anybody can add.
    #:
    #: Defaults to false, so a site added later has to say yes deliberately, and a test refuses a
    #: record that does not spell it either way: the cost of a wrong yes is a row that waits for
    #: something that would not have helped, and the cost of a wrong no is a failed row whose own
    #: sentence asks for cookies while its only verb is Try again.
    cookies_help: bool = False
    #: What this Site does for somebody with no cookies saved, under SIFT'S OWN METHOD for it.
    #: See `CookieNeed`. The badge the cookies sheet and the supported list draw. No default: a
    #: record that does not say does not construct, because every default here would be a claim
    #: nobody measured.
    cookies: CookieNeed
    #: The sentence under that badge: what the answer means on THIS Site, in the Site's own terms:
    #: which content needs cookies where the need is Partial, and why none do where it is Not
    #: needed. Screen copy, so it is short, and ends with a full stop.
    cookies_why: str
    #: The need when somebody points this Site at yt-dlp or gallery-dl instead of Sift's own
    #: method, where that differs. None where it does not.
    #:
    #: Only a Site whose own method is a middleman service has a second answer, and the difference
    #: is total: Instagram through the service never sends a cookie and downloads public posts
    #: without one, while both tools refuse the same public post without a signed-in jar. One badge
    #: cannot say both, and the one that decides whether a download is held
    #: for cookies is the one for the method actually in force. See `cookie_need`.
    cookies_with_a_tool: CookieNeed | None = None
    #: The refusals this site gives in words rather than in a status code.
    #:
    #: Empty for most sites, and that is the honest default: a site with no entries here gets the
    #: status code, the conditions every site shares, and the tool's own last lines. Filling this in
    #: is per-site work that only somebody who has watched the site refuse a download can do.
    walls: tuple[Wall, ...] = ()
    #: What a failure code means on this site, where it means something the code alone does not
    #: say. See `Meaning`. Empty for most sites, which then get the code's own words.
    failure_words: tuple[Meaning, ...] = ()

    #: Whether yt-dlp must connect to this site the way a browser does (`--impersonate chrome`).
    #:
    #: A fact about the site measured against it, never a preference: some sites refuse the TLS
    #: handshake yt-dlp's own client makes and answer every address with a status that reads as
    #: something else entirely: Pornhub answers 410 Gone, which is "removed" on most servers and
    #: is easily mistaken for a region refusal. Only this switch changes that answer, and a tunnel
    #: does not. It needs a yt-dlp built with curl_cffi, which the vendored one is (its
    #: `--list-impersonate-targets` lists Chrome); one without it refuses the flag outright rather
    #: than quietly connecting the old way.
    #:
    #: Defaults to false, so a site added later has to say yes deliberately, and only on a
    #: measurement: a browser's handshake is not free to pretend to and is not the ordinary case.
    impersonate: bool = False

    #: The extractor that reads this site's pages directly, where neither tool reads it cleanly.
    #: Empty for a site a tool handles.
    extract: ExtractFn | None = None
    #: Where a creator's own page lives on this site, as a template taking their username.
    #:
    #: Only so their picture can be read off it: the page a download came from names a file, and
    #: the picture worth showing beside a name is the one on the profile. Absent means this site's
    #: creators simply have no picture: the address cannot be guessed at from the username without
    #: knowing the site's own shape, and a
    #: guess costs a request to somebody else's machine for a page that is not there.
    profile_url: str | None = None

    #: What Sift names this Site's files when nobody has typed a rule of their own for it: a
    #: template in the words `naming.TOKENS` lists, or EMPTY for "keep the name the file arrived
    #: with", which is a real answer, and not the same as having none.
    #:
    #: No default, for the reason `cookies` has none: a record that does not say does not
    #: construct. The name a Site's files deserve differs per Site (a TikTok file arrives called
    #: a random code, a file host's arrives called the only meaningful thing there is), so an
    #: inherited answer would be a template nobody chose for that Site.
    #:
    #: Its place in the order is `SiteOptionStore.resolve`'s: a rule somebody typed for the Site
    #: wins, then this, then the rule for all Sites. So the rule for all Sites reaches an address
    #: the catalog does not know and nothing else. A test holds every one to the words this Site
    #: can fill (`words_filled`): a word that always fills empty only makes the name shorter than
    #: the one on the screen said it would be.
    #:
    #: EMPTY, and not `{name}`, where the table says keep the name. The two differ: a filled
    #: template goes through `kernel.naming._tidy`, which keeps only unaccented letters, digits and a few
    #: marks, so `{name}` would turn a title with an accent or a non-Latin script into spaces.
    default_naming: str
    #: The template words a download from this Site can fill BEYOND the four every download fills
    #: (`EVERY_SITE_FILLS`) and `creator`, which follows `username_is_a_person`.
    #:
    #: Declared per Site because each comes from that Site's own reader (a post's ID, its title,
    #: when it was posted, which file of a post this is), and a word a Site never fills is one the
    #: screen should not offer and a shipped name must not use. No default, so a Site added later
    #: has to say.
    name_words: tuple[str, ...]


# Bunkr rotates across a long list of mirror domains; they are one site and one extractor, and the
# list lives here once, so no second copy can come to hold different contents.
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

# The refusals a handful of sites give in words. Deliberately short: each entry is a claim that this
# site says this, and a claim nobody has watched the site make is not worth making. Sites with
# nothing here fall back to the status code and the tool's own output, which is honest.
#
# Age gates and region blocks are the entries worth having, because both come back as an ordinary
# 403 (indistinguishable from every other 403), and a region block is the one failure a tunnel
# actually fixes.
_LOGIN_PATTERN = (
    r"login required|requested content is not available|rate.?limit reached|"
    r"restricted video|sign ?up to continue|please log in"
)

_LOGIN_WALL = Wall(
    kind="login",
    pattern=_LOGIN_PATTERN,
    # Four words, because this is read in a table of twenty-five rows as often as it is read after
    # a failure, and in the table a paragraph per row is a table nobody reads. What to do about it
    # is one word of it: a login for a site is a set of cookies, saved under Connections.
    sentence="Cookies needed.",
)

# "This is an adult website" and "I am 18 or older - Enter" are the interstitial the large adult
# sites put in front of everything, seen on Pornhub. It is a page rather than a status: the request
# succeeds, and what comes back is the door instead of the video.
_LOGIN_WALL_INSTAGRAM = Wall(
    kind="login",
    pattern=_LOGIN_PATTERN,
    sentence="Cookies needed if download method is changed from Sift.",
)

_AGE_PATTERN = (
    r"age.?verif|confirm your age|18 u\.?s\.?c|adult content warning"
    r"|this is an adult website|age.?restricted material|i am 18 or older"
)

# One pattern, two kinds, which is the point of a wall being per site rather than global. The same
# words on the page mean different things on the two sites that show them: one lifts its door for a
# saved jar of cookies, the other lifts it for some countries and not others.
#
# The second is a region wall recognized by an age gate's words, and is declared as a region wall:
# the kind is what a row and a search match on, and a refusal cookies cannot fix must not be filed
# under the same code as one they do.
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
    # False although this is the tunnel case, because what a person reads already says so: the
    # `wall-region` line in the sentence table. Left true it would append a second, near-identical
    # tunnel sentence to the same failure. See `jobs.py`, which adds one to any failure carrying
    # this flag.
    a_tunnel_would_help=False,
)

# No region wall on YouTube, Reddit or RedGIFs: a region block there comes back 403 or 451, both of
# which are in `_LOOKS_LIKE_A_WALL`, so the failure gains the tunnel sentence `jobs.py` appends.

SITES: tuple[SiteRecord, ...] = (
    # --- The sites whose username in the address is the person who posted -----------------------
    SiteRecord(
        key="tiktok",
        # The name a TikTok file arrives with is a random code, so the default is built from
        # what the post itself says: who posted it, when, and its own ID.
        default_naming="{creator} - {posted} - {id}",
        name_words=("id", "title", "posted", "n"),
        # Without cookies: a public video through the middleman service (which never sends a jar:
        # `resolve.py` hands it none) and through yt-dlp, both land.
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
        # Without cookies: an age-restricted video answers "Sign in to confirm your age". A public
        # video lands with a current yt-dlp; a stale build can answer it 403 partway through, which
        # is the tool being out of date, not the Site wanting cookies.
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
        # Not `{posted}`: the service Sift reads Instagram through answers with a download link
        # and a file name for each item and nothing that says when the post was made (`_items`
        # in `instagram.py`), so a date in this name would always be empty.
        default_naming="{creator} - {id}",
        name_words=("id", "n"),
        # Without cookies: a public post through the middleman service lands (it is handed no jar,
        # `resolve.py`); the same post through yt-dlp ("login required") and gallery-dl ("redirect
        # to login page") is refused. Hence the second answer, and why a refusal still waits for
        # cookies here: it can only come from a tool.
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
        # The one site that is not asked for a whole profile. Instagram watches for exactly that
        # pattern and answers by locking the account rather than by refusing the download, and the
        # resolver already turns a bare profile link away with guidance for the same reason.
        bulk=False,
        profile_url="https://www.instagram.com/{username}/",
    ),
    # X carries images gallery-dl reads and video yt-dlp reads, and neither tool does both.
    SiteRecord(
        key="x",
        default_naming="{creator} - {name}",
        name_words=("id", "posted", "n"),
        # Without cookies: a public post lands through gallery-dl's guest token; a profile's media
        # answers "Login required". A post marked sensitive is refused to a guest as "NSFW Tweet"
        # (gallery-dl `twitter.py`, the `NsfwLoggedOut` branch), read from the tool's source.
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
        # The clip's own name is its tags, or the Site's name; the ID is the only unique part.
        default_naming="{creator} - {id}",
        name_words=("id", "title", "posted"),
        # Without cookies: a clip lands. yt-dlp reads it with a temporary token it mints itself
        # (`redgifs.py`, `_fetch_oauth_token`) and has no sign-in path at all.
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
    # Reddit's username is the SUBREDDIT, which is a board and not a human. Kept, because knowing
    # where something was posted is worth having, and deliberately not a person.
    SiteRecord(
        key="reddit",
        # The username is a board, never a person, so the post's own title names the file.
        default_naming="{site} - {title}",
        name_words=("id", "title", "posted", "n"),
        # Without cookies: a public video post and an over-18 post both land; a quarantined
        # community answers "No results" to a guest. "Quarantined" is Reddit's own word and is not
        # said on screen: the sentence below says what it means.
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
    # --- Creator sites read by their own extractor or resolver ----------------------------------
    SiteRecord(
        key="goonbox",
        default_naming="{creator} - {name}",
        name_words=("n",),
        # Without cookies: an image lands. Its resolver is handed no jar.
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
        # Creator, then name, as for a creator site: the file arrives under the page's own title
        # (`extractors.pmvhaven_title`), which names no uploader, rather than under its storage
        # name (the uploader and the title run together with an upload time and a random code).
        default_naming="{creator} - {name}",
        name_words=("title", "n"),
        # Without cookies: a video lands. Its extractor never reads a jar.
        cookies_help=False,
        cookies=CookieNeed.NOT_NEEDED,
        cookies_why="Everything downloads without them.",
        hosts=("pmvhaven.com",),
        site="PMVHaven",
        backend=Backend.YTDLP,
        media=("video",),
        username_is_a_person=True,
        # The one site here that records it. See `names_music`.
        names_music=True,
        extract=extractors.extract_pmvhaven,
        profile_url="https://pmvhaven.com/profile/{username}",
    ),
    SiteRecord(
        key="fapello",
        default_naming="{creator} - {name}",
        name_words=("n",),
        # Without cookies: a profile and posts land. Its 403 is a Cloudflare bot check, not a
        # sign-in, and a browser's clearance cookie is
        # bound to that browser's own address and user agent, so a saved jar does not get past it.
        cookies_help=False,
        cookies=CookieNeed.NOT_NEEDED,
        cookies_why="Everything downloads without them.",
        # BOTH domains, and .com is the one the site answers on. Listing only .su would leave a
        # .com address matching no record at all: it would take the unrecognised path, be handed
        # to the video tool, and come back as a Cloudflare 403 that read like the site refusing a
        # download rather than like Sift never having recognised the address.
        hosts=("fapello.com", "fapello.su"),
        site="Fapello",
        backend=Backend.GALLERYDL,
        media=("video", "images"),
        username_is_a_person=True,
        extract=extractors.extract_fapello,
    ),
    SiteRecord(
        key="coomer",
        # Keep the name, not "creator, then name": the creator is never known here (no username
        # rule, and the uploader is not read as a person), so `{creator}`
        # would always be empty and `{name}` alone would only lose what `_tidy` drops.
        default_naming="",
        name_words=("n",),
        # Without cookies the post API answers in full; where the n*.coomer.st file hosts time out
        # on connect, no jar changes that. gallery-dl signs in here only for favorites
        # (`kemono.py`).
        cookies_help=False,
        cookies=CookieNeed.NOT_NEEDED,
        cookies_why="Posts download without them.",
        hosts=("coomer.st", "coomer.su"),
        site="Coomer",
        backend=Backend.GALLERYDL,
        media=("video", "images"),
        supported=False,
        extract=extractors.extract_coomer,
    ),
    # Kemono is its own site, with its own registry entry, so a download is always filed under one
    # name.
    SiteRecord(
        key="kemono",
        default_naming="",
        name_words=("n",),
        # Without cookies the post API answers in full; where the n*.kemono.cr file hosts time out
        # on connect, no jar changes that. gallery-dl signs in here only for favorites (`kemono.py`,
        # `KemonoFavoriteExtractor`).
        cookies_help=False,
        cookies=CookieNeed.NOT_NEEDED,
        cookies_why="Posts download without them.",
        hosts=("kemono.su", "kemono.cr"),
        site="Kemono",
        backend=Backend.GALLERYDL,
        media=("video", "images"),
        extract=extractors.extract_kemono,
    ),
    # --- Video sites whose uploads are filed under whoever posted them --------------------------
    SiteRecord(
        key="pornhub",
        default_naming="",
        name_words=("id", "title", "posted"),
        # Two walls, and only one of them is about where the request comes from. From a region the
        # site has withdrawn from it sends a browser to its front page (the wall below). And from
        # every address, a tunnel exiting where the site does serve included, it answers yt-dlp's
        # own client 410 Gone, while the same request made with a browser's handshake gets the
        # video: hence `impersonate`. From yt-dlp's `pornhub.py`: it sets the age-disclaimer
        # cookies itself for every request, and signs in only for private and Premium videos.
        cookies_help=True,
        cookies=CookieNeed.PARTIAL,
        cookies_why="Private and Premium videos need cookies.",
        hosts=("pornhub.com",),
        site="Pornhub",
        backend=Backend.YTDLP,
        media=("video",),
        impersonate=True,
        # The uploader sits after model, pornstar, users or channels, depending on which kind of
        # page it is. The last of those is a studio rather than a person, which is why nothing here
        # writes a profile address: the username alone does not say which of the four it came from,
        # and guessing costs a request to somebody else's machine for a page that is not there.
        username_rule=_uploader_username,
        username_is_a_person=True,
        # Seen on the site: a full-page interstitial before anything is served, with no login
        # involved. The tools clear it by sending the cookie the button sets, so a failure here
        # usually means that did not happen, and a saved login for the site always sets it.
        walls=(_REGION_WALL_BEHIND_AN_AGE_GATE,),
        failure_words=(
            # Not "removed" and not a region refusal: this site answers yt-dlp's own client 410 for
            # every video address, invented ones included, and from every region. The 410 is about
            # how the request connects, not where from. The region wall is real and separate: it is
            # the 302 to the front page, which `walls` above covers.
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
    # Tested because a download was run and landed, which is the whole of what moves a record out
    # of the recognized-only block below: the claim is earned by having been run, never by the code
    # looking right. No tool reads this Site, so it was run through Sift's own reader: without
    # cookies, `Downloader.fetch` on a 7 min 50 s video lands the tallest rung of its 360/720/1080
    # ladder, byte for byte the size the file server declares for it. A removed video answers 404
    # and says so.
    SiteRecord(
        key="hqporner",
        default_naming="",
        name_words=("title", "n"),
        # Without cookies, yt-dlp answers "Unsupported URL" (no tool reads it), and the Site has
        # nothing to sign in to.
        cookies_help=False,
        cookies=CookieNeed.NOT_NEEDED,
        cookies_why="Nothing on it needs cookies.",
        hosts=("hqporner.com",),
        site="HQporner",
        # Named because a record must name a tool, and only reached when somebody points the Site
        # at a tool by hand, which then answers "Unsupported URL", as above. Sift's own
        # reader is `extract`, and it is the only thing that fetches this Site.
        backend=Backend.YTDLP,
        media=("video",),
        extract=extractors.extract_hqporner,
        tested=True,
    ),
    # --- Recognised so they can be given a way out, and for nothing else ------------------------
    #
    # These carry `supported=False`. They are here because an address has to be recognised before
    # traffic to it can be sent through a chosen tunnel, and for no other reason: nothing about
    # fetching from them has been looked at or tried. A paste is attempted exactly as a paste from
    # an address Sift has never seen is attempted, and the matrix says so on the row.
    SiteRecord(
        key="redtube",
        default_naming="",
        name_words=("id", "title", "posted"),
        # Without cookies yt-dlp can fail to find the video on the page, which is not a sign-in; its
        # `redtube.py` has no sign-in path.
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
        # Without cookies: a video lands.
        cookies_help=False,
        cookies=CookieNeed.NOT_NEEDED,
        cookies_why="Nothing on it needs cookies.",
        hosts=("xvideos.com",),
        site="XVideos",
        backend=Backend.YTDLP,
        media=("video",),
        supported=False,
    ),
    # --- File and image hosts. The uploader is a bucket, never a person -------------------------
    SiteRecord(
        key="bunkr",
        default_naming="",
        name_words=("n",),
        # Without cookies: a file lands. Its extractor never reads a jar.
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
        # Its extractor never reads a jar. When it fails, it fails as the Site's own fault (no
        # answer, or a 500 from the API), not a sign-in.
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
        # Its extractor never reads a jar. A file can be password protected, which is a password and
        # not cookies.
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
        # Its extractor makes its own guest token and never reads a jar.
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
        # An expired attachment link answers 404 to both tools; a live one is signed in its own
        # address.
        cookies_help=False,
        cookies=CookieNeed.NOT_NEEDED,
        cookies_why="An attachment link is signed \u2014 cookies never reach it.",
        tested=True,
        # The ATTACHMENT hosts, and deliberately not the app's own.
        #
        # Listing `discord.com` would make every channel address look supported. An attachment
        # link is one file and downloads; `discord.com/channels/1/2` is a conversation, and reading
        # one needs an authenticated client this does not have and would not want. So a channel
        # link would match a record that promised to fetch it and never could, which is a worse
        # answer than not recognising it: an unrecognised address says plainly that it is not
        # supported, and a recognised one that fails says the download is broken.
        hosts=("discordapp.com", "discordapp.net"),
        site="Discord",
        backend=Backend.YTDLP,
        media=("video", "images"),
        fallback_backend=Backend.GALLERYDL,
        # An attachment address names the channel it was posted in, never the person who posted it.
        username_is_a_person=False,
        # There is no profile to take. An attachment is one file, and reading a channel is a
        # different thing entirely that needs an account Sift does not have.
        bulk=False,
    ),
    SiteRecord(
        key="pixeldrain",
        default_naming="",
        name_words=("id", "n"),
        # Its extractor never reads a jar. The API answers a guest (404 for a deleted file).
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
        # Its extractor never reads a jar.
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
        # Its extractor never reads a jar. The image page answers a guest 200.
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
        # Without cookies: a file lands. Its extractor never reads a jar.
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
        # Its extractor never reads a jar. Its hosts can answer 521 or 502, which is not a sign-in.
        cookies_help=False,
        cookies=CookieNeed.NOT_NEEDED,
        cookies_why="A file host \u2014 nothing Sift downloads from it carries cookies.",
        hosts=("saint.to", "saint2.su", "saint2.cr"),
        site="Saint",
        backend=Backend.YTDLP,
        media=("video",),
        extract=extractors.extract_turbovid,
    ),
    # An image and video host with no uploader worth naming. Without a record its files would be
    # named after the page's own title, which is the word "imgur.com" for most posts, with the only
    # unique part, the post's ID, taken off. The catch-all already files its downloads under "Imgur"
    # (`registry._site_from_host`), so the record's display name is the same word and earlier rows
    # need no step.
    SiteRecord(
        key="imgur",
        default_naming="{site} - {id}",
        name_words=("id", "title", "posted"),
        # Without cookies, yt-dlp reads a single post and an album's video (ID, title and posting
        # date each come back).
        cookies_help=False,
        cookies=CookieNeed.NOT_NEEDED,
        cookies_why="Everything downloads without them.",
        # The hosts both tools read, measured against the vendored gallery-dl 1.32.13: imgur.com
        # with its i. and m. subdomains, and imgur.io. A host matches on a label boundary, so the
        # subdomains need no entry of their own.
        hosts=("imgur.com", "imgur.io"),
        site="Imgur",
        # yt-dlp, which fetches it as an unknown address too; gallery-dl behind it for the pictures
        # yt-dlp does not read.
        backend=Backend.YTDLP,
        media=("video", "images"),
        fallback_backend=Backend.GALLERYDL,
        # An address names a post or an album, and a person's page is not where a download
        # starts: no username rule, and not a person.
        username_is_a_person=False,
    ),
)


def match(url: str) -> SiteRecord | None:
    """The record whose host group owns this URL, or None."""
    return match_host(source_host(url))


def site_key_of(url: str) -> str | None:
    """The key of the Site a URL belongs to, or None when it is none this catalog knows.

    The question the egress router asks to find a URL's own route (`kernel.tunnels.egress`). The
    router is the kernel's and may not read this catalog itself, so this is handed to it at wiring.
    """
    record = match(url)
    return record.key if record is not None else None


def match_host(host: str | None) -> SiteRecord | None:
    """The record whose host group owns this hostname, or None.

    A leading dot is tolerated because that is how a cookie file writes a domain, and the match is
    on a label boundary rather than a substring, so a look-alike domain cannot borrow a real
    site's extractor, its saved login, or the health Sift keeps about that login.
    """
    if not host:
        return None
    lowered = host.lower().lstrip(".")
    for record in SITES:
        if host_matches(lowered, record.hosts):
            return record
    return None


def by_site(site: str) -> SiteRecord | None:
    """The record shown under this display name, or None. Names are compared without case, because
    a display name is typed by a person on the connections screen."""
    wanted = site.strip().lower()
    for record in SITES:
        if record.site.lower() == wanted:
            return record
    return None


def is_mutable(url: str) -> bool:
    """Whether this address stands for a set whose contents change, rather than one fixed post.

    False for anything the catalog does not know, which is the safe direction: an unknown address is
    treated as a permalink, so at worst a re-paste is skipped, where guessing the other way would
    re-download whole albums every time somebody pasted one twice.
    """
    record = match(url)
    if record is None or record.mutable is None:
        return False
    return record.mutable([segment for segment in urlsplit(url).path.split("/") if segment])


def cookie_need(record: SiteRecord, *, tool_chosen: bool) -> CookieNeed:
    """What this Site needs, for the method a download is actually going to use.

    `tool_chosen` is whether the Site has been pointed at a tool by hand. That swaps a middleman
    service for the tool, and the tool's need is its own (`cookies_with_a_tool`); every other Site
    needs the same either way.
    """
    if tool_chosen and record.cookies_with_a_tool is not None:
        return record.cookies_with_a_tool
    return record.cookies


#: The words every download fills, whatever its Site: the Site's name, the name the file arrived
#: with, and the moment it was downloaded.
EVERY_SITE_FILLS: tuple[str, ...] = ("site", "name", "date", "time")


def words_filled(record: SiteRecord) -> tuple[str, ...]:
    """Every template word a download from this Site can fill, in the order the screen lists them.

    The four every download fills, `creator` where the Site names a person, then the Site's own
    (`name_words`). One answer for the screen that offers the words and for the test that holds
    each shipped name to them, so the two cannot disagree about what a Site can say.
    """
    creator = ("creator",) if record.username_is_a_person else ()
    return (*EVERY_SITE_FILLS, *creator, *record.name_words)


def by_key(key: str) -> SiteRecord | None:
    """The record stored settings are written under, or None for a key the catalog does not have:
    a Site removed since, or a database written by a newer Sift."""
    for record in SITES:
        if record.key == key:
            return record
    return None


def hosts_of(key: str) -> tuple[str, ...]:
    """The host group of one site, by key. The resolvers read their own hosts from here rather than
    keeping a second copy, which could come to differ."""
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
