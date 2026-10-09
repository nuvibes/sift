# SPDX-License-Identifier: AGPL-3.0-or-later
"""Taking a whole playlist or channel: count first, then one download per item.

Offered only on sites in the catalog that allow it; some lock the account over it."""

from __future__ import annotations

from sift.kernel.log import get_logger
from sift.slices.download.sources import argv, subproc
from sift.slices.download.sources.errors import PRIVATE_NETWORK_REFUSED
from sift.slices.download.sources.registry import match_site
from sift.slices.download.sources.sites.catalog import Backend, SiteRecord
from sift.slices.download.sources.subproc import SubprocessError
from sift.slices.download.sources.tuning import POLICY, RunPolicy

log = get_logger(__name__)

#: A listing takes about a second; past this the site is refusing to answer.
_LISTING_TIMEOUT_SECONDS = 120.0

#: A floor under how badly a mis-pasted address can go, not a library limit.
MAX_ITEMS = 500


class BulkRefused(Exception):
    """A whole-profile download that will not be attempted. The message is shown to a person."""


def bulk_site(url: str) -> SiteRecord:
    """The site record for an address that may be taken whole, or a refusal saying why not."""
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
    """Whether the video tool can list this site's address without fetching any of it."""
    return record.backend is Backend.YTDLP and record.extract is None


async def find_items(
    url: str, *, proxy: str | None = None, policy: RunPolicy = POLICY
) -> list[str]:
    """Every address behind a playlist or channel, uncapped and unfetched; `BulkRefused` if none."""
    record = bulk_site(url)
    command = argv.build_enumerate_argv(
        url, proxy=proxy, policy=policy, as_a_browser=record.impersonate
    )
    try:
        result = await subproc.run(command, time_limit=_LISTING_TIMEOUT_SECONDS)
    except SubprocessError as exc:
        # No download exists yet, so without this the screen gets a server error.
        log.warning("bulk.listing_tool_failed", url=url, detail=str(exc))
        raise BulkRefused(
            "Sift could not ask that site what is in the address. Try again in a moment, and if "
            "it keeps happening the download tools may not be installed correctly."
        ) from exc
    if result.returncode != 0:
        # A proxy refusal reads like the site refusing; the proxy's record says which.
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
