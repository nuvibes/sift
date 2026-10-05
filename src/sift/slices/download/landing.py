# SPDX-License-Identifier: AGPL-3.0-or-later
"""The landing: who posted a download, and every file it fetched named and let in through the gate."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from sift.kernel.ingress import IngressRejected, NoDestination, Origin, Reason
from sift.kernel.jobs import (
    JobContext,
)
from sift.slices.download import naming
from sift.slices.download.attempt import Reach, on_route
from sift.slices.download.endings import (
    _animated_webp_message,
    _give_up,
    _quarantined_message,
    _retry_or_give_up,
    _truncated_message,
)
from sift.slices.download.job_seams import CreatorReader, ImportFile, ImportOutcome
from sift.slices.download.service import DownloadService
from sift.slices.download.site_options import SiteOptions
from sift.slices.download.sources.registry import Attribution
from sift.slices.download.sources.resolved import Fetched, NameFacts
from sift.slices.download.url_guard import confine_to


@dataclass
class Who:
    """Who posted a download, and how that was learned, which nothing can work out afterwards."""

    username: str | None
    named_by_resolver: bool = False
    from_page: bool = False


async def who_posted(
    attribution: Attribution,
    fetched: Fetched,
    url: str,
    reach: Reach,
    read_creator: CreatorReader,
) -> Who:
    """Who posted it, settled after the fetch and before the first file is named.

    The same answer names the file, files it and fills the row. The address wins where it names
    somebody; else what resolving it learned; else the page is asked, once, on the held route.
    """
    site = attribution.site
    username = attribution.username
    named_by_resolver = False
    if site is not None and username is None and fetched.username:
        username = fetched.username
        named_by_resolver = True

    from_page = False
    if site is not None and username is None and attribution.names_creators:
        username = await on_route(
            reach, lambda proxy: read_creator(url, proxy=proxy), what="creator"
        )
        from_page = username is not None
    return Who(username, named_by_resolver=named_by_resolver, from_page=from_page)


@dataclass
class Landed:
    """What arrived: the files let in, the names they landed under, and what the last was named from."""

    arrived: list[str] = field(default_factory=list)
    names: list[str] = field(default_factory=list)
    #: Kept on the row so the naming preview shows a real name from this Site.
    named_from: naming.Facts | None = None
    all_duplicate: bool = True


async def land(
    context: JobContext,
    service: DownloadService,
    import_file: ImportFile,
    fetched: Fetched,
    *,
    download_id: str,
    staging: Path,
    options: SiteOptions,
    site: str | None,
    username: str | None,
    dest_folder_id: str | None,
    hash_value: str,
) -> Landed | None:
    """Every file, named and then handed to the shared pipeline; None once the gate quarantined one.

    Every file, not the last one: an album is one paste and many files, and each needs its site,
    username and person.
    """
    landed = Landed()
    for fetched_file in fetched.files:
        confine_to(staging, fetched_file)
        # Both keyed by the path the fetcher produced, so read before the rename.
        media_key = fetched.item_keys.get(fetched_file)
        about = fetched.names.get(fetched_file, NameFacts())
        # Named in the staging directory, immediately before it is handed over: the import takes
        # its name from the file, and a template never decides where a tool writes.
        landed.named_from = naming.Facts(
            site=site,
            username=username,
            original=naming.without_tool_id(fetched_file.stem),
            id=about.id,
            n=about.n,
            title=about.title,
            posted=about.posted,
        )
        produced = await naming.rename(
            fetched_file, options.naming or naming.DEFAULT_TEMPLATE, landed.named_from
        )
        confine_to(staging, produced)
        outcome = await _let_in(
            context, service, import_file, produced, download_id, dest_folder_id
        )
        if outcome is None:
            return None
        landed.arrived.append(outcome.asset_id)
        landed.names.append(produced.name)
        landed.all_duplicate = landed.all_duplicate and outcome.was_duplicate
        # Recorded once the file has been let in, never when merely resolved. Every file gets a
        # row; the asset's own id stands in where the fetcher gave no key.
        await service.record_item(
            hash_value, media_key or f"asset:{outcome.asset_id}", asset_id=outcome.asset_id
        )
    return landed


async def _let_in(
    context: JobContext,
    service: DownloadService,
    import_file: ImportFile,
    produced: Path,
    download_id: str,
    dest_folder_id: str | None,
) -> ImportOutcome | None:
    """One file through the gate, hashing and indexing; None where the gate quarantined it."""
    try:
        return await import_file(
            path=produced,
            origin=Origin.DOWNLOAD,
            dest_folder_id=dest_folder_id,
            ctx=context,
        )
    except NoDestination as gone:
        # The bytes arrived intact and the place they were going is not there. The pipeline's own
        # sentence names the folder and says what to check.
        await _give_up(service, download_id, str(gone))
    except IngressRejected as exc:
        if exc.reason is Reason.NOT_DECODABLE:
            await _retry_or_give_up(context, service, download_id, _truncated_message())
            raise
        # Quarantined, not failed: the file arrived in full and the gate refused what was inside,
        # so a retry would fetch the same bytes and quarantine them again.
        message = (
            _animated_webp_message()
            if exc.reason is Reason.ANIMATED_WEBP_UNREADABLE
            else _quarantined_message()
        )
        await service.mark_quarantined(download_id, error=message)
        return None
