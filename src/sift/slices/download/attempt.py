# SPDX-License-Identifier: AGPL-3.0-or-later
"""The attempt: one fetch of a link into the job's workspace, watched for a full disk and a pause."""

from __future__ import annotations

import asyncio
import contextlib
import shutil
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from functools import partial
from pathlib import Path
from typing import NoReturn

import structlog

from sift.kernel.destination import resolve_destination
from sift.kernel.ingress import NoDestination
from sift.kernel.jobs import (
    JobBlocked,
    JobContext,
    JobFailedPermanently,
    JobPaused,
)
from sift.kernel.log import get_logger
from sift.kernel.tunnels import DIRECT, Route, TunnelError
from sift.slices.download.endings import (
    _disk_full_message,
    _give_up,
    _paused_message,
    _record_failure,
    _retry_or_give_up,
    _transient_message,
)
from sift.slices.download.job_seams import (
    _MIN_FREE_DISK_BYTES,
    Fetcher,
    ItemCheck,
    Seams,
    Stopping,
    _direct_only,
    floor_bytes,
)
from sift.slices.download.service import DownloadService, JobInput
from sift.slices.download.sources import progress
from sift.slices.download.sources.errors import (
    CookiesNeeded,
    DownloadError,
    LoginRequired,
    NoAnswer,
    NothingFound,
    PrivateNetworkRefused,
    UnsupportedURL,
)
from sift.slices.download.sources.net import guarded_session
from sift.slices.download.sources.resolved import Fetched
from sift.slices.download.sources.subproc import SubprocessError
from sift.slices.download.url_guard import UrlRejected, guard_url, next_hop

log = get_logger(__name__)

_DISK_CHECK_INTERVAL_SECONDS = 2.0

#: The held route opened again for one more request: the tunnel's proxy as it is now, or a refusal.
Reach = Callable[[], AbstractAsyncContextManager[str | None]]

#: The word the queue uses for a stop that keeps what has arrived. Read from `JobContext.stopping`,
#: which answers this, `cancel`, or nothing at all.
_PAUSE = "pause"


class _DiskLow(Exception):
    """Free space on the download disk fell below the floor while a fetch was in flight."""


class _PauseWanted(Exception):
    """Somebody paused this download while the fetch was in flight; the handler makes it `JobPaused`.

    Told apart from `_DiskLow` because the two end a download in opposite ways: a full disk fails
    with a sentence, a pause keeps everything and says nothing.
    """


def _free_bytes(path: Path) -> int:
    """Bytes free on the filesystem `path` sits on. Reads the disk, so it is called off the loop."""
    return shutil.disk_usage(path).free


async def _fetch_guarding_disk(
    downloader: Fetcher,
    url: str,
    *,
    into: Path,
    cookies_file: Path | None,
    proxy: str | None = None,
    already_have: ItemCheck | None = None,
    floor: int = _MIN_FREE_DISK_BYTES,
    report: progress.Report = progress.nowhere,
    stopping: Stopping,
) -> Fetched:
    """Run the fetch, stopping it if free space on the download disk runs low or somebody pauses it.

    Every download path writes into `into` (the direct fetcher and the shell-out tools alike), so
    watching the space there and cancelling the fetch covers all of them at once. Cancelling stops
    the transfer and kills the tool (the shared subprocess runner reaps its child on cancellation),
    so a link that streams without end, or resolves to something far larger than the disk, cannot
    fill it out from under the database. Raises `_DiskLow` when it does; otherwise returns, or
    re-raises, exactly what the fetch did.

    A PAUSE is stopped the same way, here because this is the one place awake while a fetch runs.
    Raises `_PauseWanted`, which keeps everything already written where it is.

    The reason is read on the same beat as the disk, so a pause takes effect within one interval
    rather than at the end of the fetch. A `cancel` is not read here at all: cancelling fences the
    worker's claim, which stops the job whether or not the handler notices.
    """
    fetch = asyncio.ensure_future(
        downloader.fetch(
            url,
            into=into,
            cookies_file=cookies_file,
            proxy=proxy,
            already_have=already_have,
            report=report,
        )
    )
    try:
        while True:
            done, _ = await asyncio.wait({fetch}, timeout=_DISK_CHECK_INTERVAL_SECONDS)
            if fetch in done:
                return fetch.result()
            if stopping() == _PAUSE:
                raise _PauseWanted
            if await asyncio.to_thread(_free_bytes, into) < floor:
                raise _DiskLow
    finally:
        if not fetch.done():
            fetch.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await fetch


async def _walk(url: str, proxy: str | None) -> None:
    async with guarded_session(proxy=proxy) as session:
        await guard_url(url, follow=partial(next_hop, session), here=proxy is None)


async def on_route[T](
    reach: Reach, read: Callable[[str | None], Awaitable[T]], *, what: str
) -> T | None:
    """One read made after the tool, on the route the download holds; None if its tunnel went down."""
    try:
        async with reach() as proxy:
            return await read(proxy)
    except TunnelError as exc:
        log.warning("download.read_refused", read=what, detail=str(exc))
        return None


@asynccontextmanager
async def attempt(
    context: JobContext,
    service: DownloadService,
    downloader: Fetcher,
    *,
    download_id: str,
    job: JobInput,
    staging: Path,
    cookies_file: Path | None,
    dest_folder_id: str | None,
    hash_value: str,
    mutable: bool,
    report: progress.Report,
    seams: Seams,
) -> AsyncIterator[tuple[Fetched, Reach]]:
    """The fetch: the folder it lands in, the way out it takes, and what a failure of it means.

    Yields what was fetched with the route still held, until the caller's last request for the
    link. Every failure of the fetch ends here: given up, waiting, paused, or left to a retry.
    """
    # Written down as it is resolved and before the fetch, so a running row names the folder it is
    # going into; every run writes it again.
    await service.record_folder(download_id, dest_folder_id)
    # Asked BEFORE the fetch, as the import asks after it: a link with nowhere to land would be
    # fetched in full and then refused. No retry finds a folder either.
    try:
        await resolve_destination(context.library, dest_folder_id)
    except NoDestination as nowhere_to_land:
        await _give_up(service, download_id, str(nowhere_to_land))

    async def already_have(media_key: str) -> bool:
        return await service.item_already_done(hash_value, media_key)

    # Bound before the route is taken, so a failure on the way to it reads as a direct one.
    proxy: str | None = None
    way_out = seams.router or _direct_only()
    async with contextlib.AsyncExitStack() as held:
        try:
            # Held from the walk to the last read: a tunnel that is not up refuses here, so nothing
            # goes out directly.
            taken: Route = await held.enter_async_context(way_out.take(job.url))
            proxy = taken.proxy
            await service.record_route(download_id, taken.label, address=taken.address)
            # Every line logged while the route is held names it: the tunnel's id and name, never
            # its address, bound to this task's context only.
            held.enter_context(
                structlog.contextvars.bound_contextvars(
                    download_id=download_id, route=taken.tunnel_id or DIRECT, via=taken.label
                )
            )
            await _walk(job.url, proxy)
            fetched = await _fetch_guarding_disk(
                downloader,
                job.url,
                into=staging,
                cookies_file=cookies_file,
                proxy=proxy,
                # Only for a link whose contents change; a permalink was settled by its address.
                already_have=already_have if mutable else None,
                floor=floor_bytes(await seams.read_disk_floor()),
                report=report,
                stopping=context.stopping,
            )
        except Exception as exc:
            await held.aclose()
            await _fetch_failed(context, service, download_id, exc, direct=proxy is None)
        # The tunnel asked again by id, never the Site's setting: a stopped one refuses.
        yield fetched, partial(way_out.through, taken.tunnel_id)


async def _fetch_failed(
    context: JobContext,
    service: DownloadService,
    download_id: str,
    exc: Exception,
    *,
    direct: bool,
) -> NoReturn:
    """End the job the way this failure of the fetch calls for; anything unforeseen goes through."""
    if isinstance(exc, TunnelError):
        # A tunnel that is down stays down until somebody turns it on: the thing to go and fix.
        await _give_up(service, download_id, str(exc))
    if isinstance(exc, _PauseWanted):
        # What arrived stays in the workspace the queue keeps, and Resume continues from it.
        raise JobPaused(_paused_message()) from exc
    if isinstance(exc, _DiskLow):
        # A full disk stays full until somebody frees space.
        await _give_up(service, download_id, _disk_full_message())
    if isinstance(exc, CookiesNeeded):
        # No cookies saved for a Site that only serves with them: the row waits, it does not fail.
        raise JobBlocked(str(exc)) from exc
    if isinstance(
        exc, LoginRequired | NothingFound | PrivateNetworkRefused | UnsupportedURL | UrlRejected
    ):
        # As permanent as a refusal of the pasted link: recorded, not retried.
        said = await _record_failure(service, download_id, exc, direct=direct)
        raise JobFailedPermanently(said) from exc
    if isinstance(exc, NoAnswer):
        # Another attempt would sit through the same wait; Try again is the next ask.
        said = await _record_failure(service, download_id, exc, direct=direct)
        raise JobFailedPermanently(said) from exc
    if isinstance(exc, DownloadError | SubprocessError):
        # Retried; on the last attempt written down with what was learned about it.
        if isinstance(exc, DownloadError) and context.attempt >= context.job.max_attempts:
            await _record_failure(service, download_id, exc, direct=direct)
        else:
            await _retry_or_give_up(context, service, download_id, _transient_message())
    raise exc
