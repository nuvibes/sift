# SPDX-License-Identifier: AGPL-3.0-or-later
"""Taking a whole playlist or channel, one download at a time.

Pasting a creator's channel is a reasonable thing to want and a dangerous thing to do carelessly, so
this is deliberately two steps. First Sift asks the site what is behind the address (a listing
request that fetches no media and takes about a second) and reports how many it found. Nothing is
queued until somebody has seen that number and said yes.

**One download per item, never one job for the whole list.** A two hundred video channel taken as a
single job means one failure kills all of it, no per-video progress, no per-video retry, and nothing
to resume from. Queued individually, every existing part of the slice works unchanged: the ledger
recognizes the ones already fetched, the pacing applies per run, and a failure is one video.

**Only where it has been shown to work.** Asking a site for everything a creator has ever posted is
the most conspicuous thing a downloader can do, and on the sites that watch for it the price is the
account rather than the download. So it is offered per site, from the catalog, and refused by name
everywhere else, including where a resolver deliberately refuses a whole profile already, which
this must not quietly undo.
"""

from __future__ import annotations

from sift.kernel.log import get_logger
from sift.slices.download.sources import argv, subproc
from sift.slices.download.sources.errors import PRIVATE_NETWORK_REFUSED
from sift.slices.download.sources.registry import match_site
from sift.slices.download.sources.sites.catalog import Backend, SiteRecord
from sift.slices.download.sources.subproc import SubprocessError
from sift.slices.download.sources.tuning import POLICY, RunPolicy

log = get_logger(__name__)

#: A listing request answers in about a second for a channel of hundreds, because nothing is being
#: fetched. Anything past this is a site refusing to answer rather than a long list.
_LISTING_TIMEOUT_SECONDS = 120.0

#: How many items one paste may queue. Not a limit on the library (a longer list is taken by
#: pasting it again) but a floor under how badly a mis-pasted address can go: a channel with tens
#: of thousands of videos should ask a second time rather than fill the queue in one go.
MAX_ITEMS = 500


class BulkRefused(Exception):
    """A whole-profile download that will not be attempted. The message is shown to a person."""


def bulk_site(url: str) -> SiteRecord:
    """The site record for an address that may be taken whole, or a refusal saying why not.

    A site the video tool cannot list is answered here, before anything is run: asking it would be
    a request to the site that can only fail, made to arrive at a sentence known in advance.
    """
    record = match_site(url)
    if record is None:
        raise BulkRefused(
            "Sift doesn't recognize this site, so it can't ask what a whole profile holds. "
            "Paste a single post or video from it instead."
        )
    if not record.bulk:
        raise BulkRefused(
            f"Sift doesn't ask {record.site} for a whole profile. That site watches for it and "
            "locks the account instead of refusing the download. Open the posts you want and "
            "paste those links instead."
        )
    if not can_count(record):
        raise BulkRefused(
            f"{record.site} can't count what is in a link first, and doesn't need to. Paste the "
            "album or profile link into the box above, and Sift downloads all of it in one go."
        )
    return record


def can_count(record: SiteRecord) -> bool:
    """Whether this site can be asked what is behind an address without fetching any of it.

    Only one kind can: the sites the video tool reads, where a playlist or a channel is a list it
    prints without fetching anything. A site read by the gallery tool or by its own extractor has
    no such step: a whole album or profile is simply what an ordinary paste of that link already
    fetches, in one go.
    """
    return record.backend is Backend.YTDLP and record.extract is None


async def find_items(
    url: str, *, proxy: str | None = None, policy: RunPolicy = POLICY
) -> list[str]:
    """Ask what is behind a playlist or channel address, without fetching any of it.

    Returns every address found, in the order the site gave them, uncapped, so the caller can say
    how many there really are before taking the first `MAX_ITEMS` of them. Raises `BulkRefused` when
    the site is not one Sift takes whole, or when the listing came back empty, which is what a
    private, deleted or mistyped address looks like from here.
    """
    record = bulk_site(url)
    command = argv.build_enumerate_argv(
        url, proxy=proxy, policy=policy, as_a_browser=record.impersonate
    )
    try:
        result = await subproc.run(command, time_limit=_LISTING_TIMEOUT_SECONDS)
    except SubprocessError as exc:
        # The tool would not run at all: it is missing, or it hung and was killed. Everywhere
        # else in the slice that is a download failure somebody reads on the queue; here there is
        # no download yet, so without this it leaves the screen with a server error and no sentence.
        log.warning("bulk.listing_tool_failed", url=url, detail=str(exc))
        raise BulkRefused(
            "Sift could not ask that site what is in the address. Try again in a moment, and if "
            "it keeps happening the download tools may not be installed correctly."
        ) from exc
    if result.returncode != 0:
        # A connection the proxy refused reads, in the tool's words, like the site refusing the
        # listing; the proxy's own record says what happened, and that is what is said.
        traffic = argv.proxy_traffic(command)
        if traffic is not None and traffic.refused:
            log.warning("bulk.listing_refused_private_network", url=url)
            raise BulkRefused(PRIVATE_NETWORK_REFUSED)
        log.warning("bulk.listing_failed", url=url, detail=result.stderr)
        raise BulkRefused(
            "That address could not be read as a playlist or a channel. It may be private, "
            "deleted, or a link to something else."
        )
    found = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not found:
        raise BulkRefused("There is nothing in that playlist or channel to download.")
    return found


__all__ = ["MAX_ITEMS", "BulkRefused", "bulk_site", "can_count", "find_items"]
