# SPDX-License-Identifier: AGPL-3.0-or-later
"""The one job this slice runs: fetch a URL, and let the media in through the front door.

The order is fixed and each step guards the next. First the address is checked, because the most
dangerous thing here is being pointed at a private network, so no URL reaches a tool unchecked.
Then the ledger, because a link already fetched should be recognized and skipped, not fetched
again. Then the tool runs, in its own directory, and whatever it wrote is handed to the shared
import pipeline (the same gate, hashing and indexing a dropped file goes through), because a file a
site served is no more trustworthy for having been fetched by Sift. Last, the site and username
the address named are recorded, so a drop yields attribution for free.

A download that needs saved cookies it cannot open (because nobody is signed in and the key that
decrypts them is gone) does not fail. It waits. The job enters the blocked state and returns to the
queue the moment someone signs in, and the attempt it took is handed back so a fortnight signed out
cannot quietly exhaust its retries.

A download somebody PAUSES does not fail either, and it does not start again from nothing. The
fetch is stopped where it stands, what has arrived stays in the job's workspace, and Resume runs
this same job again, so the tools continue the partial they find rather than fetching it twice.

The import pipeline is not this slice's. It is declared here as the shape this job needs and supplied
from outside, so this slice never imports the one that owns it. `verify_ingress` lives inside it:
this job does not run the gate itself, and there is exactly one gate for every way a file gets in.

The job is built in three steps, each in its own module: `attempt`, `landing` and `settling`;
`endings` says how a download that does not land ends, and `job_seams` holds what it is handed.
"""

from __future__ import annotations

import asyncio
from functools import partial
from pathlib import Path

from sift.kernel.jobs import (
    JobContext,
    JobPaused,
    WaitingForPassword,
    backs_off,
    register_handler,
)
from sift.kernel.log import get_logger
from sift.slices.download.attempt import _free_bytes as _free_bytes
from sift.slices.download.attempt import _PauseWanted, attempt
from sift.slices.download.endings import _animated_webp_message as _animated_webp_message
from sift.slices.download.endings import _disk_full_message as _disk_full_message
from sift.slices.download.endings import _give_up
from sift.slices.download.endings import _paused_message as _paused_message
from sift.slices.download.endings import _quarantined_message as _quarantined_message
from sift.slices.download.endings import _record_failure as _record_failure
from sift.slices.download.endings import _tier_of as _tier_of
from sift.slices.download.endings import _transient_message as _transient_message
from sift.slices.download.endings import _truncated_message as _truncated_message
from sift.slices.download.job_seams import _MIN_FREE_DISK_BYTES as _MIN_FREE_DISK_BYTES
from sift.slices.download.job_seams import _NOTHING_SPECIAL as _NOTHING_SPECIAL
from sift.slices.download.job_seams import GIGABYTE as GIGABYTE
from sift.slices.download.job_seams import (
    ArtKeeper,
    CreatorReader,
    Fetcher,
    Filer,
    FloorReader,
    ImportFile,
    MusicReader,
    OptionReader,
    PreferenceReader,
    Router,
    Seams,
    _always_remember,
    _answer,
    _default_floor,
    _direct_only,
    _never,
    _no_creator,
    _no_music,
)
from sift.slices.download.job_seams import Handler as Handler
from sift.slices.download.job_seams import ImportOutcome as ImportOutcome
from sift.slices.download.job_seams import _options_for as _options_for
from sift.slices.download.job_seams import floor_bytes as floor_bytes
from sift.slices.download.landing import land, who_posted
from sift.slices.download.service import DOWNLOAD, DownloadService, JobInput
from sift.slices.download.settling import settle
from sift.slices.download.site_options import SiteOptions
from sift.slices.download.sources import classify, cookie_health, progress, url_hash
from sift.slices.download.sources.creator import creator_of
from sift.slices.download.sources.errors import (
    LoginRequired,
)
from sift.slices.download.sources.hosts import source_host
from sift.slices.download.sources.music import music_of
from sift.slices.download.sources.registry import Attribution
from sift.slices.download.sources.resolve import is_mutable
from sift.slices.download.url_guard import UrlRejected, check_url

log = get_logger(__name__)

_COOKIE_FILENAME = "cookies.txt"


async def _record_login_health(service: DownloadService, url: str, site: str | None) -> None:
    """Tell the saved cookies' row what using them just taught, if anything.

    Nothing at all for a site Sift keeps no health about: there is no honest thing to say about a
    jar it has never had a verdict on, and writing "fine" in that case would make a screen claim
    to have checked something it never looked at.
    """
    if site is None:
        return
    status = cookie_health.status_for(source_host(url))
    if status is None:
        return
    # This runs on the way out of the fetch, including the way out of a failed one, and a note
    # about a login must never replace the failure that was already on its way to being reported.
    # A write that cannot happen is logged and dropped; the next download writes it again.
    try:
        await service.record_login_health(site, status)
    except Exception as exc:
        log.warning("download.login_health_not_recorded", site=site, detail=str(exc))


async def _cookies_for(
    context: JobContext,
    service: DownloadService,
    workspace: Path,
    site: str | None,
) -> Path | None:
    """The decrypted cookie file for a site that needs cookies, or None if it needs none.

    If cookies are saved for this site, opening them needs the master key. When nobody is signed in
    the key is absent, and that is a wait, not a failure: the job blocks and carries on once somebody
    signs in. With the key present the cookies are written to a file in the workspace, BESIDE the
    directory the tool writes into rather than in it: everything in that directory is read back as
    what the download produced, and the jar is not that. The handler deletes it on its way out.
    """
    if site is None:
        return None
    connection = await service.connection_for_site(site)
    if connection is None or connection.secret_id is None:
        return None

    key = await context.master_key()
    if key is None:
        raise WaitingForPassword(f"the saved cookies for {site}")

    cookie = await service.open_cookie(connection.secret_id, key)
    if cookie is None:
        # The key is here but the cookie will not open: the saved cookies are corrupt or were sealed
        # under a different key. Not a wait: it needs replacing.
        raise LoginRequired(
            f"The cookies saved for {site} could not be read. Add them again in "
            "Settings > Sites and Tunnels > Cookies."
        )

    cookie_path = workspace / _COOKIE_FILENAME
    await asyncio.to_thread(cookie_path.write_text, cookie, encoding="utf-8")
    return cookie_path


#: Set on the payload by the "fetch it anyway" route, and read nowhere else.
_ANYWAY_KEY = "ignore_ledger"


def _download_id(context: JobContext) -> str:
    return context.require_str(
        "download_id", "a download job needs a download_id in its payload, and there is not one"
    )


def register_handlers(
    *,
    service: DownloadService,
    downloader: Fetcher,
    import_file: ImportFile,
    may_create_people: PreferenceReader,
    read_creator: CreatorReader = creator_of,
    read_music: MusicReader = music_of,
    read_disk_floor: FloorReader = _default_floor,
    remember_downloads: PreferenceReader = _always_remember,
    router: Router | None = None,
    watching: progress.Registry | None = None,
    read_site_options: OptionReader | None = None,
    keep_art: ArtKeeper | None = None,
    file_under: Filer | None = None,
) -> None:
    """Claim the download job type, with everything it needs bound in. Called once, at boot.

    The pipeline, the downloader and the service are bound here rather than reached for inside the
    handler, which is handed only its context. `import_file` is the slice that owns ingestion,
    supplied by the composition root (`sift/wiring/downloads.py`). This slice never imports it.
    """
    register_handler(
        DOWNLOAD,
        partial(
            download,
            service=service,
            downloader=downloader,
            import_file=import_file,
            may_create_people=may_create_people,
            read_creator=read_creator,
            read_music=read_music,
            read_disk_floor=read_disk_floor,
            remember_downloads=remember_downloads,
            router=router,
            watching=watching,
            read_site_options=read_site_options,
            keep_art=keep_art,
            file_under=file_under,
        ),
        name="Downloading",
    )
    # A dropped connection is still dropped the same second: each retry waits longer.
    backs_off(DOWNLOAD)


async def download(
    context: JobContext,
    *,
    service: DownloadService,
    downloader: Fetcher,
    import_file: ImportFile,
    may_create_people: PreferenceReader = _never,
    read_creator: CreatorReader = _no_creator,
    read_music: MusicReader = _no_music,
    read_disk_floor: FloorReader = _default_floor,
    remember_downloads: PreferenceReader = _always_remember,
    router: Router | None = None,
    watching: progress.Registry | None = None,
    read_site_options: OptionReader | None = None,
    keep_art: ArtKeeper | None = None,
    file_under: Filer | None = None,
) -> None:
    """Run one download from its ledger row. The row id is in the payload; the URL is in the row."""
    download_id = _download_id(context)
    # "Already in your library", and wanted anyway: only the ledger is skipped, never the address
    # check or the library's own content dedup.
    anyway = context.payload.get(_ANYWAY_KEY) is True
    job = await service.job_input(download_id)
    if job is None:
        # The row was deleted between queueing and running. Nothing to fetch, and no retry finds it.
        return

    await service.mark_running(download_id)

    # 0. The address, before anything else; its name and redirects wait for the route.
    try:
        check_url(job.url, here=False)
    except UrlRejected as exc:
        await _give_up(service, download_id, str(exc))

    # 1. The ledger. A permalink already fetched is skipped; a link whose contents change is
    # re-resolved every run, and its items are what the ledger records one level down.
    hash_value = url_hash(job.url)
    mutable = is_mutable(job.url)
    # The paste's choice, else the setting read live. Off, the ledger is written and stops nothing.
    remember = await _answer(job.choices.remember, remember_downloads)
    if remember and not anyway and not mutable and await service.is_already_done(hash_value):
        await service.mark_skipped(download_id)
        log.info("download.skipped", download_id=download_id)
        return

    # The Site read through the site's key, so a Site renamed here is still the one used.
    attribution = await service.filed_as(job.url, classify(job.url))
    seams = Seams(
        may_create_people=may_create_people,
        read_creator=read_creator,
        read_music=read_music,
        read_disk_floor=read_disk_floor,
        remember_downloads=remember_downloads,
        router=router or _direct_only(),
        keep_art=keep_art,
        file_under=file_under,
    )
    await _in_workspace(
        context,
        service,
        downloader,
        import_file,
        download_id=download_id,
        job=job,
        attribution=attribution,
        hash_value=hash_value,
        mutable=mutable,
        watching=watching,
        read_site_options=read_site_options,
        seams=seams,
    )


async def _in_workspace(
    context: JobContext,
    service: DownloadService,
    downloader: Fetcher,
    import_file: ImportFile,
    *,
    download_id: str,
    job: JobInput,
    attribution: Attribution,
    hash_value: str,
    mutable: bool,
    watching: progress.Registry | None,
    read_site_options: OptionReader | None,
    seams: Seams,
) -> None:
    """The run inside the job's own workspace, which the queue keeps for a paused job.

    The media goes in a directory of its own inside it and the cookie jar beside that, so nothing
    the tool did not write is read back as what it produced. The jar goes whichever way this ends.
    """
    workspace = context.workspace
    staging = workspace / _MEDIA_DIRECTORY
    held = False
    await asyncio.to_thread(staging.mkdir, parents=True, exist_ok=True)
    try:
        # A saved jar with nobody signed in waits; one that will not open is recorded.
        try:
            cookies_file = await _cookies_for(context, service, workspace, attribution.site)
        except LoginRequired as exc:
            await _give_up(service, download_id, str(exc))
        try:
            await _run(
                context,
                service,
                downloader,
                import_file,
                download_id=download_id,
                job=job,
                staging=staging,
                cookies_file=cookies_file,
                attribution=attribution,
                hash_value=hash_value,
                mutable=mutable,
                report=(
                    watching.reporter(download_id) if watching is not None else progress.nowhere
                ),
                options=await _options_for(read_site_options, job.url),
                seams=seams,
            )
        except (JobPaused, _PauseWanted):
            # Held, not settled: the last figures stay on the record so a paused row says what
            # is kept while it waits.
            held = True
            if watching is not None:
                watching.hold(download_id)
            raise
        finally:
            # Whatever happened, write down what using the cookies taught; a failure is exactly
            # when a dead jar is discovered. And no longer in flight, unless it is held.
            if cookies_file is not None:
                await _record_login_health(service, job.url, attribution.site)
            if watching is not None and not held:
                watching.forget(download_id)
    finally:
        await asyncio.to_thread(_forget_the_jar, workspace)


#: Where the tool writes: a directory of its own inside the job's workspace.
_MEDIA_DIRECTORY = "media"


def _forget_the_jar(workspace: Path) -> None:
    """Delete the decrypted cookie file, if this run wrote one. Reads the disk, so it runs off the
    loop. Missing is the ordinary case: most downloads need no cookies at all."""
    # Sift's own file: the jar this run decrypted into the job's own workspace, which nobody put
    # there and nothing indexes. Not a library file, so not the deleter's.
    # nosemgrep: sift-no-file-removal-outside-delete-trash
    (workspace / _COOKIE_FILENAME).unlink(missing_ok=True)


async def _run(
    context: JobContext,
    service: DownloadService,
    downloader: Fetcher,
    import_file: ImportFile,
    *,
    download_id: str,
    job: JobInput,
    staging: Path,
    cookies_file: Path | None,
    attribution: Attribution,
    hash_value: str,
    mutable: bool,
    report: progress.Report = progress.nowhere,
    options: SiteOptions = _NOTHING_SPECIAL,
    seams: Seams,
) -> None:
    """The attempt, the landing and the record, on the one route the attempt holds."""
    # Where a drop said to put it wins; a per-site default is what applies when nobody chose.
    dest_folder_id = job.dest_folder_id or options.dest_folder_id
    async with attempt(
        context,
        service,
        downloader,
        download_id=download_id,
        job=job,
        staging=staging,
        cookies_file=cookies_file,
        dest_folder_id=dest_folder_id,
        hash_value=hash_value,
        mutable=mutable,
        report=report,
        seams=seams,
    ) as (fetched, reach):
        if not fetched.files and mutable:
            # A link whose contents change, with nothing new behind it: done, never a duplicate, since
            # the username behind it may post again. Only the address has named anybody by now.
            await service.mark_done(
                download_id,
                asset_id=None,
                site=attribution.site,
                username=attribution.username,
                username_from="address",
            )
            log.info("download.nothing_new", download_id=download_id)
            return
        who = await who_posted(attribution, fetched, job.url, reach, seams.read_creator)
        landed = await land(
            context,
            service,
            import_file,
            fetched,
            download_id=download_id,
            staging=staging,
            options=options,
            site=attribution.site,
            username=who.username,
            dest_folder_id=dest_folder_id,
            hash_value=hash_value,
        )
        if landed is None:
            return
        await settle(
            service,
            download_id=download_id,
            job=job,
            attribution=attribution,
            who=who,
            landed=landed,
            fetched=fetched,
            reach=reach,
            mutable=mutable,
            seams=seams,
        )
