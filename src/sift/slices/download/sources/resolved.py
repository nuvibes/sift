# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a resolver learned about a pasted link, before a byte is fetched.

Site-neutral on purpose: a new site is a new resolver and nothing else."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from sift.slices.download import naming

MediaType = Literal["video", "image", "gif", "other"]

#: Values match the subprocess-tool registry's `Backend`.
FetchRoute = Literal["direct", "ytdlp", "gallerydl"]


@dataclass(frozen=True, slots=True)
class ResolvedItem:
    """One unit of downloadable media; `index` and the source URL name it across resumes."""

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
    # Last: every field has a default, so a positional caller would fill the wrong one.
    media_key: str | None = None
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
        """A placeholder item that points yt-dlp or gallery-dl at `source_url`."""
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
    """A name for one piece of media that survives a re-resolve: the address without its query."""
    if item.media_key:
        return item.media_key
    split = urlsplit(item.url)
    return f"{split.netloc}{split.path}" if split.path else item.url


@dataclass(frozen=True, slots=True)
class ResolvedMedia:
    """Everything one pasted link resolved to; `username` is best effort, never a guess."""

    site: str
    source_url: str
    source_host: str
    items: list[ResolvedItem] = field(default_factory=list)
    username: str | None = None
    title: str | None = None
    post_id: str | None = None
    posted: datetime | date | None = None


@dataclass(frozen=True, slots=True)
class NameFacts:
    """What one fetched file can be named from; a site that does not say leaves a word empty."""

    id: str | None = None
    n: int | None = None
    title: str | None = None
    posted: datetime | date | None = None


@dataclass(frozen=True, slots=True)
class Fetched:
    """The files one download produced, and the uploader the resolver learned along the way."""

    files: list[Path]
    username: str | None = None
    item_keys: dict[Path, str] = field(default_factory=dict)
    #: Keyed by the path the fetch produced, so read before the rename.
    names: dict[Path, NameFacts] = field(default_factory=dict)
    offered: int = 0
    left_out: int = 0


def item_name_facts(media: ResolvedMedia, item: ResolvedItem) -> NameFacts:
    """What a file Sift fetched itself can be named from; `n` only for a post of several files."""
    return NameFacts(
        id=item.item_id or media.post_id,
        n=item.index + 1 if len(media.items) > 1 else None,
        title=media.title,
        posted=media.posted,
    )


#: Anchored on the post's own id, so a stray `_<number>` is not a picture number.
_PICTURE_OF_POST = r"{post_id}_(?P<n>\d{{1,4}})"


def tool_file_name_facts(
    media: ResolvedMedia, path: Path, said: NameFacts | None, *, one_of: int
) -> NameFacts:
    """What a file a tool wrote can be named from: its own id, the staged one, then the post's."""
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
