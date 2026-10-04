# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a resolver learned about a pasted link, before a byte is fetched.

Download is two steps, not one. First a link is *resolved*: a URL that a person can read (a TikTok
post, a Bunkr album, an Instagram reel) is turned into a list of the actual media behind it, each
item either a direct address the fetcher can stream or a job for one of the subprocess tools. Only
then is anything *fetched*. This module is the shape that first step hands the second, and it is
site-neutral on purpose: the fetcher never learns whether a TikTok resolver or a Bunkr extractor
produced an item, so a new site is a new resolver and nothing else.

A `ResolvedItem` is one downloadable thing. A `ResolvedMedia` is everything one link resolved to:
its items, the site it came from, and the username if the resolver could name one, so a
drop yields attribution without a person tagging anything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from sift.slices.download import naming

#: What kind of media an item is, as far as the resolver could tell. `other` is the placeholder a
#: subprocess item carries until its files land. The real per-file type is read from each file's
#: bytes after the tool runs.
MediaType = Literal["video", "image", "gif", "other"]

#: How the fetch step turns an item into bytes.
#:   `direct`: a media address the streaming fetcher pulls itself. One item is one file.
#:   `ytdlp` / `gallerydl`: a subprocess tool run on the *source* URL (not a media address); the
#:   tool finds and writes its own files. One item is the whole tool run, a single resumable unit.
#: The string values match the subprocess-tool registry's `Backend`, so the two agree on the wire.
FetchRoute = Literal["direct", "ytdlp", "gallerydl"]


@dataclass(frozen=True, slots=True)
class ResolvedItem:
    """One unit of downloadable media a resolver found behind a source URL.

    `index` is the item's position within its source link (0 for a lone video, 0..n for the photos
    of a carousel), and with the source URL it names the item stably, so re-resolving on a resumed
    job lands on the same items. The remaining fields are hints and negotiations the fetcher needs:

    * `expected_bytes` is a size hint (a resolver that knows it) for an honest progress bar when the
      CDN omits a length header; `duration_sec` feeds transcode progress. Either may be None.
    * `filename` is a human name a backend supplied, used in place of an opaque URL hash; None falls
      back to the hash rule.
    * `referer`, `accept`, and `cookie` are per-item request details some CDNs demand: a referer they
      check, an `Accept` that stops a media CDN serving an HTML consent page, a `Cookie` a signed
      link needs. Empty/None means the fetcher's defaults.
    * `refetch_url` is the source post to re-resolve for a fresh address when a signed CDN link goes
      stale mid-fetch (TikTok photo URLs expire within seconds); None means no re-resolve.
    * `fallback_backend` is a second subprocess tool to try if the first finds nothing (X/Twitter's
      gallery-dl-then-yt-dlp recovery); None for everything else.
    * `media_key` names this piece of media in a way that outlives one fetch, for the links whose
      contents change. See `stable_key`. A resolver sets it when it knows a genuinely stable id;
      everything else falls back to the address.
    """

    index: int
    url: str
    media_type: MediaType
    ext: str
    referer: str = ""
    expected_bytes: int | None = None
    duration_sec: float | None = None
    filename: str | None = None
    backend: FetchRoute = "direct"
    fallback_backend: FetchRoute | None = None
    refetch_url: str | None = None
    accept: str | None = None
    cookie: str | None = None
    # Last on purpose: every field here has a default, so a caller passing arguments by position
    # would silently land its next argument in whichever one came first.
    media_key: str | None = None
    #: This one file's own ID on the site, where each file of a post has one: a Pixeldrain list
    #: holds files with an ID each. None means the post's ID (`ResolvedMedia.post_id`) stands.
    item_id: str | None = None

    @classmethod
    def subprocess(
        cls,
        *,
        source_url: str,
        backend: FetchRoute,
        fallback_backend: FetchRoute | None = None,
        index: int = 0,
        referer: str = "",
    ) -> ResolvedItem:
        """One subprocess job for a source URL: yt-dlp or gallery-dl runs on `source_url` and writes
        its own files. The per-file type, extension and size are unknown until the tool has run, so
        the item is a placeholder that carries only where to point the tool and which tool to use."""
        return cls(
            index=index,
            url=source_url,
            media_type="other",
            ext="",
            referer=referer,
            backend=backend,
            fallback_backend=fallback_backend,
        )


def stable_key(item: ResolvedItem) -> str:
    """A name for one piece of media that survives being fetched again tomorrow.

    This is what makes a link whose contents change safe to re-paste. Such a link is re-resolved
    every time, and what stops it re-downloading everything is a record of the individual items
    already fetched, so the name has to mean the same thing on the second visit.

    A raw address usually does not. A media host signs its links, so the same photo arrives under a
    different address every few minutes; keyed on that, nothing ever looks familiar and each re-paste
    fetches the lot. So a resolver that knows a stable id says so, and everything else falls back to
    the address with its query string removed: the signature and the expiry live there, and the
    path is what identifies the file.
    """
    if item.media_key:
        return item.media_key
    split = urlsplit(item.url)
    return f"{split.netloc}{split.path}" if split.path else item.url


@dataclass(frozen=True, slots=True)
class ResolvedMedia:
    """Everything one pasted link resolved to: its items, and what the link said about their origin.

    `username` is the uploader when the resolver could name one, so attribution is free. It is
    best-effort: a link shape nobody anticipated yields None rather than a wrong username.
    """

    site: str
    source_url: str
    source_host: str
    items: list[ResolvedItem] = field(default_factory=list)
    username: str | None = None
    title: str | None = None
    #: The post's own ID on the site: a TikTok post number, an Instagram post code, an X post
    #: number. Read from the link where the link carries it, from the site's answer where not.
    post_id: str | None = None
    #: When the post was published, where the site said. A moment where the site gives one, a date
    #: where it gives only that, None where it gives neither, never the moment of the download.
    posted: datetime | date | None = None


@dataclass(frozen=True, slots=True)
class NameFacts:
    """What one fetched file can be NAMED from, beyond its site and its creator.

    Per FILE rather than per link, because a post's files differ in exactly the facts a name needs to
    tell them apart: the third photo of a carousel is `n` 3, and each file of a Pixeldrain list has
    an ID of its own. Everything is optional, for the reason every naming fact is: a site that does
    not say leaves the word empty rather than filling it with a guess.
    """

    id: str | None = None
    n: int | None = None
    title: str | None = None
    posted: datetime | date | None = None


@dataclass(frozen=True, slots=True)
class Fetched:
    """What one download produced: the files, and what fetching them TAUGHT about their origin.

    The second half is the reason this exists. A resolver often learns who posted something that the
    address does not name (YouTube's oEmbed answers with the channel for a `youtu.be` short link,
    RedGIFs' API names its uploader, an Instagram story link carries the username after the route
    word), and every one of those is worked out and put in `ResolvedMedia.username`. A seam that
    handed back a list of paths and nothing else would drop it, and the job would attribute the
    download from the ADDRESS alone and file it under nobody.

    So the seam hands back both. `username` is best effort and is only ever a fallback: an
    address that names somebody is still believed first, because the person pasting a link chose it.

    `item_keys` says which piece of media each file came from, for the links whose contents change.
    Only the files fetched from a known address are in it (a tool run writes its own files and
    nothing here can say which of the source's items each one was), and it is read after the files
    have landed, never before, so an item is only ever recorded as fetched once it really is.
    """

    files: list[Path]
    username: str | None = None
    item_keys: dict[Path, str] = field(default_factory=dict)
    #: What each file can be named from. See `NameFacts`. Keyed by the path the fetch produced, so
    #: it is read BEFORE the file is renamed, the same as `item_keys`. A file missing from it is
    #: named from its site, its creator and its own name.
    names: dict[Path, NameFacts] = field(default_factory=dict)
    #: How many files the link offered from a known address, and how many of them were left out
    #: for good (out of the size settings, or gone from the Site) while the rest landed. Both 0
    #: where the link offered none that way; a tool run says neither.
    offered: int = 0
    left_out: int = 0


def item_name_facts(media: ResolvedMedia, item: ResolvedItem) -> NameFacts:
    """What a file Sift fetched ITSELF can be named from: the post's facts, and which file it is.

    `n` is the item's position counted from 1, and only for a post of more than one file: a lone
    video called "... - 1" says nothing, and the post's own ID already tells two posts apart.
    """
    return NameFacts(
        id=item.item_id or media.post_id,
        n=item.index + 1 if len(media.items) > 1 else None,
        title=media.title,
        posted=media.posted,
    )


#: A tool's file named `<the post's ID>_<number>` is that post's file number `<number>`: the shape
#: gallery-dl gives an X post's pictures (`1802446137950021447_1.jpg`). Anchored on the post's OWN
#: ID, read from the link, so a file that merely ends in an underscore and a number is not taken
#: for a picture number.
_PICTURE_OF_POST = r"{post_id}_(?P<n>\d{{1,4}})"


def tool_file_name_facts(
    media: ResolvedMedia, path: Path, said: NameFacts | None, *, one_of: int
) -> NameFacts:
    """What a file a TOOL wrote can be named from.

    The file's own ID first (a RedGIFs clip in a Reddit post is told apart by the clip's), then
    the one the tool wrote into the staged name (`naming.tool_id`), then the post's. The title
    and the moment are the post's where the resolver read them, the tool's otherwise.

    `one_of` is how many files the tool run produced; a picture number is only kept when there is
    more than one, for the reason `item_name_facts` gives.
    """
    number: int | None = None
    if media.post_id and one_of > 1:
        found = re.fullmatch(_PICTURE_OF_POST.format(post_id=re.escape(media.post_id)), path.stem)
        number = int(found.group("n")) if found else None
    return NameFacts(
        id=(said.id if said else None) or naming.tool_id(path.stem) or media.post_id,
        n=number,
        title=media.title or (said.title if said else None),
        posted=media.posted or (said.posted if said else None),
    )


__all__ = [
    "FetchRoute",
    "Fetched",
    "MediaType",
    "NameFacts",
    "ResolvedItem",
    "ResolvedMedia",
    "item_name_facts",
    "stable_key",
    "tool_file_name_facts",
]
