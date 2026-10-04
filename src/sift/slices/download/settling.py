# SPDX-License-Identifier: AGPL-3.0-or-later
"""The record: what a download that landed leaves behind, and its row marked done."""

from __future__ import annotations

from sift.kernel.log import get_logger
from sift.slices.download.endings import _give_up, _transient_message
from sift.slices.download.job_seams import Seams
from sift.slices.download.landing import Landed, Who
from sift.slices.download.service import DownloadService, JobInput
from sift.slices.download.sources.registry import Attribution
from sift.slices.download.sources.resolved import Fetched

log = get_logger(__name__)


async def settle(
    service: DownloadService,
    *,
    download_id: str,
    job: JobInput,
    attribution: Attribution,
    who: Who,
    landed: Landed,
    fetched: Fetched,
    proxy: str | None,
    mutable: bool,
    seams: Seams,
) -> None:
    """What a download that landed leaves behind: whose it is, where it was dropped, its
    Site's pictures, its music, and the row marked done."""
    arrived = landed.arrived
    asset_id = arrived[-1] if arrived else None
    # Read from the file that was imported, so a row still says what it fetched once it is deleted.
    produced_name = landed.names[-1] if landed.names else None
    if asset_id is None:  # pragma: no cover (an empty file list is a NothingFound above)
        await _give_up(service, download_id, _transient_message())
    site = attribution.site
    if site is not None:
        await _attribute(service, job, attribution, who, arrived, seams, site=site)
    await _file(service, download_id, job, arrived, seams)
    # The pictures the Site and the creator are shown with, once ever; never worth failing for.
    if seams.keep_art is not None and site is not None:
        await seams.keep_art(job.url, proxy, who.username)
    # The track it is set to, only where the Site says it records one; seeded, never set, so a
    # value somebody typed is never argued with.
    if attribution.names_music and arrived:
        track = await seams.read_music(job.url, proxy=proxy)
        if track:
            for one in arrived:
                await service.seed_music(one, track, url=job.url)
            log.info("download.music_read", download_id=download_id, files=len(arrived))
    await service.mark_done(
        download_id,
        asset_id=asset_id,
        site=site,
        username=who.username,
        # How the name was learned: the address first, because it wins.
        username_from=(
            "page" if who.from_page else "resolver" if who.named_by_resolver else "address"
        ),
        filename=produced_name,
        # Never for a link whose contents change: "already in your library" is about the LINK.
        was_duplicate=landed.all_duplicate and not mutable,
        named_from=landed.named_from,
        offered=fetched.offered,
        left_out=fetched.left_out,
    )
    log.info("download.done", download_id=download_id, was_duplicate=landed.all_duplicate)


async def _attribute(
    service: DownloadService,
    job: JobInput,
    attribution: Attribution,
    who: Who,
    arrived: list[str],
    seams: Seams,
    *,
    site: str,
) -> None:
    """Every file that arrived, filed under its Site and whoever posted it.

    The Site alone is enough to record. WHO is the username settled before the files were named,
    so the name and the attribution cannot disagree; the preference is read live.
    """
    creating = await seams.may_create_people()
    for one in arrived:
        await service.attribute(
            asset_id=one,
            site=site,
            username=who.username,
            address=job.url,
            # A username the resolver named is a person on the same Sites one in the address
            # would have been; a board is never a person by either route.
            username_is_a_person=(
                attribution.username_is_a_person
                or who.from_page
                or (who.named_by_resolver and attribution.names_creators)
            ),
            may_create_people=creating,
        )


async def _file(
    service: DownloadService,
    download_id: str,
    job: JobInput,
    arrived: list[str],
    seams: Seams,
) -> None:
    """What arrived, filed under what the link was dropped on; it may not fail the download.

    After attribution, because filing is somebody's explicit answer and lands on top of what the
    address implied; every kind adds, none replaces. A gallery's pictures land as files in their
    folder and nothing groups them.
    """
    aim = await service.aim_of(download_id)
    if seams.file_under is not None and arrived and aim is not None:
        kind, target, user_id = aim
        try:
            filed = await seams.file_under(
                kind=kind, target_id=target, asset_ids=arrived, for_user=user_id
            )
            log.info("download.filed", download_id=download_id, kind=kind, files=filed)
        except Exception:
            log.warning("download.not_filed", download_id=download_id, kind=kind)
