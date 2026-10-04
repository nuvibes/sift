# SPDX-License-Identifier: AGPL-3.0-or-later
"""The shapes the download job is handed: the importer, the fetcher, and the live reads."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from sift.kernel.ingress import Origin
from sift.kernel.jobs import (
    JobContext,
)
from sift.kernel.tunnels import EgressRouter
from sift.slices.download.site_options import SiteOptions
from sift.slices.download.sources import progress
from sift.slices.download.sources.registry import match_site
from sift.slices.download.sources.resolved import Fetched

#: What applies to a site nobody has given anything of its own: one shared instance, never modified.
_NOTHING_SPECIAL = SiteOptions()

#: The default free-space floor a running fetch may not take the download disk below: headroom for
#: the database, which shares the disk, and never a size limit on the download itself.
_MIN_FREE_DISK_BYTES = 5 * 1024 * 1024 * 1024

#: One gigabyte in bytes: the setting is in gigabytes, the filesystem answers in bytes.
GIGABYTE = 1024 * 1024 * 1024

#: Asks the job whether it has been told to stop, and why (`JobContext.stopping`).
Stopping = Callable[[], str | None]


class ImportOutcome(Protocol):
    """What the import pipeline reports back. Read structurally, so any named result satisfies it."""

    @property
    def asset_id(self) -> str: ...
    @property
    def was_duplicate(self) -> bool: ...


class ImportFile(Protocol):
    """The shared import pipeline, declared as the shape this job needs."""

    async def __call__(
        self,
        *,
        path: Path,
        origin: Origin,
        dest_folder_id: str | None,
        ctx: JobContext,
    ) -> ImportOutcome: ...


#: Asks whether one piece of media behind a changing link has already been fetched and kept.
ItemCheck = Callable[[str], Awaitable[bool]]


class Fetcher(Protocol):
    """The downloader seam this job runs media through. A test stands a fake in its place."""

    def handles(self, url: str) -> bool: ...

    async def fetch(
        self,
        url: str,
        *,
        into: Path,
        cookies_file: Path | None = None,
        proxy: str | None = None,
        already_have: ItemCheck | None = None,
        report: progress.Report = progress.nowhere,
    ) -> Fetched: ...


#: Reading one preference, live, at the moment it matters.
PreferenceReader = Callable[[], Awaitable[bool]]

#: The free-space floor, in gigabytes, read live for the same reason.
FloorReader = Callable[[], Awaitable[object]]


async def _default_floor() -> object:
    """The floor when nobody supplied a reader: the handler is called directly in tests."""
    return _MIN_FREE_DISK_BYTES // GIGABYTE


def floor_bytes(stored: object) -> int:
    """The stored gigabytes in bytes; anything unusable falls back to the default."""
    try:
        gigabytes = int(str(stored))
    except (TypeError, ValueError):
        return _MIN_FREE_DISK_BYTES
    if gigabytes <= 0:
        return _MIN_FREE_DISK_BYTES
    return gigabytes * GIGABYTE


async def _never() -> bool:
    """The default for the handler: attribute the file, invent nobody."""
    return False


async def _always_remember() -> bool:
    """The default for the handler: the ledger stops a re-paste."""
    return True


async def _answer(chosen: bool | None, setting: PreferenceReader) -> bool:
    """This download's own answer where the paste gave one, else the setting as it stands now."""
    if chosen is not None:
        return chosen
    return bool(await setting())


#: Decides which way out a download takes and holds that route while it runs.
Router = EgressRouter


def _direct_only() -> EgressRouter:
    """The router when nobody wired one: every site goes out directly."""
    return EgressRouter({})


#: Reads the uploader's name off a page, for the downloads whose address does not carry one.
CreatorReader = Callable[..., Awaitable[str | None]]


async def _no_creator(url: str, *, proxy: str | None = None) -> str | None:
    """The default: ask nobody. See `_never`: same reasoning, same conservative direction."""
    return None


#: Reads the track a video is set to off its page, for the sites that record one.
MusicReader = Callable[..., Awaitable[str | None]]


async def _no_music(url: str, *, proxy: str | None = None) -> str | None:
    """The default: ask nobody. Same reasoning and same direction as `_no_creator`."""
    return None


Handler = Callable[[JobContext], Awaitable[None]]

#: Reads what one site does differently: its naming rule and where its files land.
OptionReader = Callable[[str | None], Awaitable[SiteOptions]]


async def _options_for(read: OptionReader | None, url: str) -> SiteOptions:
    """What this site does differently, or nothing at all when no reader was wired."""
    if read is None:
        return SiteOptions()
    record = match_site(url)
    return await read(record.key if record is not None else None)


#: Fetches and keeps the picture a site is shown with, once per site; absent in a test.
ArtKeeper = Callable[[str, str | None, str | None], Awaitable[None]]

#: Filing what arrived under the thing a link was dropped ON: one call of `kernel.seams.FilingSeam`.
Filer = Callable[..., Awaitable[int]]


@dataclass(frozen=True)
class Seams:
    """The live reads and the other slices' doors one download is handed, bound at boot."""

    may_create_people: PreferenceReader = _never
    read_creator: CreatorReader = _no_creator
    read_music: MusicReader = _no_music
    read_disk_floor: FloorReader = _default_floor
    remember_downloads: PreferenceReader = _always_remember
    router: Router | None = None
    keep_art: ArtKeeper | None = None
    file_under: Filer | None = None
