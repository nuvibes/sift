# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which tool fetches a given address, and what the address says about who made the media.

Two questions are answered from one URL. The first is which downloader handles it: the site catalog
names one per site, with a catch-all behind it, so a video host goes to yt-dlp, an image gallery to
gallery-dl, and anything unrecognized falls through to yt-dlp, which reads a great many sites on
its own. A site that serves both kinds names a second tool to try, because neither reads both.

The second is attribution: the site the media is from, and the username inside the URL.
The catalog already knows the site, and the username is usually sitting in the path, so a drop
of a link yields a Site and a username with no work asked of anyone, which is the whole point of
doing it here. It is best-effort: a URL shape nobody anticipated yields no username rather than a
wrong one.

The site data itself is not here. It is one record per site in the catalog, read by this and by the
extractors alike, so the two cannot end up disagreeing about what a site is called or which domains
belong to it.
"""

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

    #: What is stored. Empty string is "no opinion", which is what nearly every Site keeps.
    value: str
    label: str
    help: str


#: The tools a Site can be pointed at, and the answer that means "leave it to Sift".
#:
#: Derived from `Backend` rather than written beside it: a third tool added to that enum and
#: forgotten here would be a tool the code can run and nobody can choose, which is the quieter half
#: of the same fault as a name stored that nothing runs.
#:
#: The order is the order the screen offers them, and Sift's own answer is first because it is the
#: right one until something is actually failing.
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
    """The tool a stored choice names, or None for "leave it to Sift".

    An unrecognized name reads as None rather than raising. The route refuses one on the way in, so
    the only way to hold one is a database written by a newer Sift or edited by hand, and falling
    back to the built-in answer is the behaviour that still downloads.
    """
    if not value:
        return None
    return next((backend for backend in Backend if backend.value == value), None)


@dataclass(frozen=True, slots=True)
class Attribution:
    """What a URL says about the media's origin. Either field may be absent."""

    site: str | None
    username: str | None
    #: Whether `username` names a person rather than a board or a bucket. False whenever there is no
    #: username, and false for a host the catalog does not know: nothing about an unrecognized
    #: address says its first path segment is somebody's name.
    username_is_a_person: bool = False
    #: Whether an uploader on this site would be a PERSON, independently of whether the address
    #: happened to name one.
    #:
    #: `username_is_a_person` cannot answer this, and deliberately: it is false whenever the username
    #: is absent, because a flag beside an absent username is a claim about nobody. This is the
    #: standing fact about the site, and it is what decides whether looking for a creator ON THE
    #: PAGE is worth doing at all.
    #:
    #: True for the creator sites and for a host the catalog does not know: an unknown site has
    #: had no decision made about it, and its page is the only thing that can say. FALSE for a known
    #: site that yields no person on purpose: Reddit's uploader is a board, a file host's is a
    #: bucket, and going to the page to find a human there overrides that decision silently.
    names_creators: bool = False
    #: Whether this site records the track a video is set to, on a page worth reading for it.
    #:
    #: False for an unknown host, unlike `names_creators` above, and the asymmetry is the point.
    #: A creator is published in a standard every video page may carry, so an unknown site is
    #: worth asking. A track is not standardised anywhere (reading one means knowing that
    #: site's own shape), so asking a site nobody has looked at can only ever cost a request.
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
    """The second tool to try when the first finds nothing, or None.

    A site that serves both images and video needs one: neither downloader reads both, so a mixed
    site run through a single tool comes back empty for the kind that tool cannot see, and empty
    is indistinguishable from a post that held nothing.
    """
    record = match(url)
    return record.fallback_backend if record is not None else None


#: Endings that are two labels rather than one, so the label before them is the country's registry
#: and not the site. Without these `example.co.uk` would be read as `Co`. Not the whole public list,
#: which is thousands long and changes: the handful a household actually pastes, and anything
#: missed is read by the one-label rule.
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
    """A display name for an unrecognized host: its registrable label, capitalised.

    `www.example.com` becomes `Example`, and so does `cdn.example.co.uk`. Best-effort, for the
    catch-all: it is better to attribute a download to `Vimeo` than to nothing, and a host with no
    obvious label yields None rather than a guess that reads as noise.
    """
    labels = [label for label in host.split(".") if label not in {"www", "m", "mobile"}]
    if len(labels) < 2:
        return None
    if len(labels) > 2 and ".".join(labels[-2:]) in _TWO_LABEL_ENDINGS:
        return labels[-3].capitalize()
    return labels[-2].capitalize()


def site_key(url: str) -> str | None:
    """What stays the same about the site an address is on, whatever its Site is called here.

    The catalog's own key where Sift knows the site (`reddit`), and otherwise the host with a
    leading `www.` taken off, marked so the two can never collide (`@example.com`, the kernel's
    `host_key`, which a watermark's address is keyed by too). No colon in either, because a creator
    picture's scope is `<key>:<username>` and is cut at its first colon. A Site's NAME is
    somebody's to change, so the library remembers which Site each of these files under
    (`download_sites`, read through `kernel.access.sites`) rather than looking a Site up by the
    name the catalog gives it: a Site renamed from `Reddit` is still where a Reddit download
    belongs. None for an address with no host at all.
    """
    record = match(url)
    if record is not None:
        return record.key
    return host_key(source_host(url))


def classify(url: str) -> Attribution:
    """What a URL says about its media's origin: the site, and the username if present.

    A recognised site names its site and knows where the username sits; an unrecognized host gets
    a best-effort site from its domain and no username. Either field may come back None.
    """
    record = match(url)
    if record is None:
        return Attribution(
            site=_site_from_host(source_host(url)), username=None, names_creators=True
        )

    username = record.username_rule(_segments_of(url)) if record.username_rule is not None else None
    return Attribution(
        site=record.site,
        username=username,
        # Only when there IS one. A site whose usernames name people, asked about an address that
        # carries no username, still yields nothing, and a flag set beside an absent username is a
        # claim about nobody that some later caller will read as a claim about somebody.
        username_is_a_person=record.username_is_a_person and username is not None,
        names_creators=record.username_is_a_person,
        names_music=record.names_music,
    )
