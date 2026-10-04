# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Portions of this package (the per-site extractors and the crypto/cookie helpers they use) are
# DERIVED from cyberdrop-dl (https://github.com/jbsparrow/CyberDropDownloader), which is licensed
# under the GNU General Public License v3.0. Its endpoints, page selectors, signing flow and the
# JPG5/Bunkr crypto were followed to read these file-host sites. Sift ships under AGPL-3.0, which is
# compatible with GPL-3.0 (AGPLv3 section 13 <-> GPLv3 section 13); see NOTICE.
"""The file-host site extractors: read a page or an API, return direct media addresses.

A handful of image- and file-host sites (Bunkr, Cyberdrop, GoFile, Pixeldrain, and their kin) do not
hand their media to yt-dlp or gallery-dl cleanly: some are not supported at all, some hide the real
address behind a page script or a signing API. So Sift reads them itself: the site catalog names the
extractor that knows a host's shape, the extractor fetches over the guarded session and returns the
direct addresses it found, and the fetch stage streams each one. Adding a site is one catalog record
and one extractor.

The host groups and the site names come from that catalog rather than from a second list here,
so the extractor path and the tool registry cannot disagree about which domains a site owns or
what it is called.

Host matching is suffix/exact, never substring, so a look-alike domain cannot borrow a real site's
extractor. An extractor that finds nothing (a private album, a deleted file, a page whose markup
changed) yields an empty list, which becomes the ordinary "nothing to download" for the person.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import aiohttp

from sift.slices.download.sources import failures, ratelimit
from sift.slices.download.sources.errors import DownloadError, NothingFound
from sift.slices.download.sources.net import DEFAULT_USER_AGENT, guarded_session
from sift.slices.download.sources.progress import Report, nowhere
from sift.slices.download.sources.resolved import ResolvedMedia
from sift.slices.download.sources.sites.catalog import SiteRecord
from sift.slices.download.sources.sites.catalog import match as match_catalog
from sift.slices.download.sources.sites.common import (
    ExtractContext,
    build_resolved_media,
    source_origin,
)
from sift.slices.download.sources.tuning import POLICY, RunPolicy

_TOO_MANY_REQUESTS = 429


def match_site(url: str) -> SiteRecord | None:
    """The catalog record owning this URL, but only when it carries an extractor of its own."""
    record = match_catalog(url)
    return record if record is not None and record.extract is not None else None


def is_site(url: str) -> bool:
    """Whether a dedicated extractor handles this URL's host."""
    return match_site(url) is not None


def site_site(url: str) -> str | None:
    """The display site for a handled site, or None."""
    record = match_site(url)
    return record.site if record is not None else None


async def resolve_site(
    url: str,
    *,
    cookies_file: Path | None = None,
    proxy: str | None = None,
    report: Report = nowhere,
    policy: RunPolicy | None = None,
) -> ResolvedMedia:
    """Resolve a supported file-host URL to its direct media over the guarded session.

    `cookies_file` is a decrypted site login, supplied only when one is saved for this site; an
    extractor that reads a gated post (Coomer/Kemono) uses it, the rest ignore it. Raises
    `NothingFound` (from `build_resolved_media`) when the extractor finds nothing, and a retryable
    `DownloadError` when the site could not be reached at all.

    `policy` is the download's settings. The reads a Site's pages take are requests like the file's
    own, so they are paced and timed the same way (`net.guarded_session`). A Site still held by a
    rate limit is waited out BEFORE the session opens (never inside a read, whose ceiling is
    seconds), and a read the Site answered with 429 holds it for the next download.
    """
    record = match_site(url)
    if record is None or record.extract is None:  # pragma: no cover (callers gate on is_site)
        raise DownloadError("Sift has no downloader for this site.")
    ctx = ExtractContext(
        user_agent=DEFAULT_USER_AGENT,
        cookies_file=cookies_file,
        report=report,
        quality=(policy or POLICY).quality,
    )
    answers = Answers()
    await ratelimit.wait_out_backoff(url)
    try:
        async with guarded_session(
            proxy=proxy, observe=(answers.trace(),), policy=policy
        ) as session:
            extracted = await record.extract(url, session, ctx)
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise DownloadError(
            f"{record.site} could not be reached. Try again in a few minutes."
        ) from exc
    if policy is not None and answers.limited:
        ratelimit.note_too_many_requests(url, policy)
    if not any(found.url for found in extracted):
        raise answers.why_nothing(url, record.site)
    return build_resolved_media(url, record.site, extracted, referer=source_origin(url))


class Answers:
    """What the site answered while a reader was asking it, kept so an empty result can say why.

    An extractor that finds nothing returns an empty list, whatever the reason: the file server
    down (a signing API answering 521), a folder the site will not show to a guest (a 401), or an
    address the reader does not read at all and never asked about. "Nothing could be downloaded"
    is true of each and says nothing about any of them.

    It watches rather than asks: every extractor keeps its own `if not r.ok: return []`, and nothing
    here changes what one does. Changing twenty of them to raise would be twenty places to get the
    same thing right, and the session is the one place every request already passes through.
    """

    def __init__(self) -> None:
        self.asked = 0
        #: The last refusal, as a status. The last because a reader that tried two ways reports the
        #: one that decided it last, the same rule a tool's own output is read by.
        self.refused: int | None = None
        #: Whether ANY answer was a 429, whatever came after it: the Site asked to be left alone,
        #: and that holds for the next download even when this one went on to find something.
        self.limited = False

    def trace(self) -> aiohttp.TraceConfig:
        trace = aiohttp.TraceConfig()
        trace.on_request_end.append(self._ended)
        return trace

    async def _ended(
        self,
        _session: aiohttp.ClientSession,
        _ctx: SimpleNamespace,
        params: aiohttp.TraceRequestEndParams,
    ) -> None:
        self.asked += 1
        if params.response.status >= 400:
            self.refused = params.response.status
        if params.response.status == _TOO_MANY_REQUESTS:
            self.limited = True

    def why_nothing(self, url: str, site: str) -> DownloadError:
        """The failure an empty result is, from what the site said on the way to it.

        Final answers (a 404, a 410, an address the reader never asked about) are recorded once
        and not retried; anything else the site said (a server down, a rate limit) is a run that
        did not finish and is retried, and then recorded with the code if it never recovers.
        """
        if self.asked == 0:
            return NothingFound(
                f"{site}: Sift reads single posts and videos there, and this address is not one. "
                "Paste the link of one post or video.",
                code=failures.CODE_ADDRESS_NOT_READ,
                tier=2,
            )
        if self.refused is None:
            return NothingFound(
                f"Nothing could be downloaded from {site}: every page it asked for answered, and "
                "none of them held a file."
            )
        reading = failures.explain_status(url, self.refused)
        kind = NothingFound if reading.final else DownloadError
        return kind(
            reading.sentence,
            code=reading.code,
            tier=reading.tier,
            a_tunnel_would_help=reading.a_tunnel_would_help,
        )


__all__ = ["Answers", "SiteRecord", "is_site", "match_site", "resolve_site", "site_site"]
