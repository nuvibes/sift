# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which tool fetches a given address, and what the address says about who made the media."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from sift.kernel.access.sites import host_key
from sift.slices.download.sources.hosts import source_host
from sift.slices.download.sources.sites.catalog import SITES, Backend, SiteRecord, match

__all__ = [
    "CHOOSABLE_DOWNLOADERS",
    "SITES",
    "Attribution",
    "Backend",
    "ChoosableDownloader",
    "SiteRecord",
    "backend_for",
    "chosen_backend",
    "classify",
    "fallback_backend_for",
    "is_a_downloader",
    "match_site",
    "site_key",
]


@dataclass(frozen=True, slots=True)
class ChoosableDownloader:
    """One tool somebody may point a Site at by hand, and how to describe it."""

    value: str
    label: str
    help: str


#: Derived from `Backend`, so a new tool cannot be runnable yet unchoosable.
CHOOSABLE_DOWNLOADERS: tuple[ChoosableDownloader, ...] = (
    ChoosableDownloader(
        value="",
        label="Sift",
        help="Whatever suits the Site. For a few, that is a service that returns a clean file.",
    ),
    ChoosableDownloader(
        value=Backend.YTDLP.value,
        label="yt-dlp",
        help="Reads a great many video sites. Slower, and copes with more.",
    ),
    ChoosableDownloader(
        value=Backend.GALLERYDL.value,
        label="gallery-dl",
        help="Reads image galleries and the sites that serve sets of pictures.",
    ),
)


def is_a_downloader(value: str) -> bool:
    """Whether a stored name is a tool Sift actually ships."""
    return any(one.value == value for one in CHOOSABLE_DOWNLOADERS if one.value)


def chosen_backend(value: str | None) -> Backend | None:
    """The tool a stored choice names, or None; an unknown name falls back rather than raising."""
    if not value:
        return None
    return next((backend for backend in Backend if backend.value == value), None)


@dataclass(frozen=True, slots=True)
class Attribution:
    """What a URL says about the media's origin. Either field may be absent."""

    site: str | None
    username: str | None
    username_is_a_person: bool = False
    #: The site's standing fact, true for an unknown host: is the page worth asking for a creator.
    names_creators: bool = False
    #: False for an unknown host: a track has no standard shape to read.
    names_music: bool = False


def _segments_of(url: str) -> list[str]:
    return [segment for segment in urlsplit(url).path.split("/") if segment]


def match_site(url: str) -> SiteRecord | None:
    """The catalog record for a URL's host, or None if nothing matches it."""
    return match(url)


def backend_for(url: str) -> Backend:
    """Which tool fetches this URL. A matched site names its own; everything else is yt-dlp's."""
    record = match(url)
    return record.backend if record is not None else Backend.YTDLP


def fallback_backend_for(url: str) -> Backend | None:
    """The second tool to try when the first finds nothing: neither reads both kinds."""
    record = match(url)
    return record.fallback_backend if record is not None else None


#: Only the endings a household pastes; anything missed falls to the one-label rule.
_TWO_LABEL_ENDINGS = frozenset(
    {
        "co.uk",
        "org.uk",
        "me.uk",
        "com.au",
        "net.au",
        "org.au",
        "co.nz",
        "co.jp",
        "co.kr",
        "co.za",
        "com.br",
        "com.mx",
        "com.tr",
        "com.cn",
        "co.in",
        "com.sg",
    }
)


def _site_from_host(host: str) -> str | None:
    """A display name for an unrecognized host: its registrable label, capitalised."""
    labels = [label for label in host.split(".") if label not in {"www", "m", "mobile"}]
    if len(labels) < 2:
        return None
    if len(labels) > 2 and ".".join(labels[-2:]) in _TWO_LABEL_ENDINGS:
        return labels[-3].capitalize()
    return labels[-2].capitalize()


def site_key(url: str) -> str | None:
    """A site's stable key: the catalog's, else `@host`; never a colon, where scopes are cut."""
    record = match(url)
    if record is not None:
        return record.key
    return host_key(source_host(url))


def classify(url: str) -> Attribution:
    """What a URL says about its media's origin: the site, and the username if present."""
    record = match(url)
    if record is None:
        return Attribution(
            site=_site_from_host(source_host(url)), username=None, names_creators=True
        )

    username = record.username_rule(_segments_of(url)) if record.username_rule is not None else None
    return Attribution(
        site=record.site,
        username=username,
        username_is_a_person=record.username_is_a_person and username is not None,
        names_creators=record.username_is_a_person,
        names_music=record.names_music,
    )
