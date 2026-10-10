# SPDX-License-Identifier: AGPL-3.0-or-later
"""From an asset to a file a decoder can open."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile

from sift.kernel import heif
from sift.kernel.archives import ArchiveRefused
from sift.kernel.config import Settings
from sift.kernel.content import Asset, ContentStore, Location, LocationStatus
from sift.kernel.jobs.queue_rows import JobFailedPermanently, JobHeld
from sift.kernel.jobs.retrying import WaitingForSpace, is_disk_full
from sift.kernel.log import get_logger

log = get_logger("sift.kernel.media")


#: How long a copy's drive or share may take to say whether the file is there, in seconds. A share
#: that stopped answering otherwise holds the job, and a thread, for the client's own timeout.
ANSWER_WITHIN = 20.0

#: How long a job whose copies are all out of reach waits before it looks again, attempt handed back.
AWAY_WAIT = 600.0


class MissingAsset(JobFailedPermanently):
    """The asset is not there any more: permanent, so not retried."""


class NoReadableCopy(Exception):
    """No copy of this asset could be read: one of the two below."""


class CopiesAway(NoReadableCopy, JobHeld):
    """A drive or share a copy lives on is not answering: the job waits for it, attempt handed back."""

    def __init__(self, message: str, *, retry_in: float = AWAY_WAIT) -> None:
        super().__init__(message, retry_in=retry_in)


class CopiesGone(NoReadableCopy, JobFailedPermanently):
    """Every copy is gone from where Sift last saw it, its folder answering: no retry finds it."""


@dataclass(frozen=True, slots=True)
class Source:
    """An asset, a file of it a decoder can open (`path`), and the user's own file (`original`).

    Where the file was read from a share at its take-in and that copy is still kept, both are the
    local copy: the same bytes, so no pass after the take-in reads the share again."""

    asset: Asset
    location: Location
    path: Path
    original: Path


_AWAY = "away"
_GONE = "gone"


async def resolve(store: ContentStore, asset_id: str) -> Source:
    """Find an asset and the first copy that opens; a location known to be missing is not tried."""
    asset = await store.get(asset_id)
    if asset is None:
        raise MissingAsset(f"asset {asset_id} no longer exists")

    locations = await store.locations(asset_id)
    present = [one for one in locations if one.status is LocationStatus.PRESENT]
    if present and (kept := await store.local_copy(asset_id)) is not None:
        return Source(asset=asset, location=present[0], path=kept, original=kept)
    away = 0
    for location in locations:
        if location.status is not LocationStatus.PRESENT:
            continue
        found = await _answered(store, location)
        if found is None:
            continue
        if found == _AWAY:
            away += 1
        elif isinstance(found, Path):
            path = await _opened(store, location, found)
            if path is not None:
                return Source(asset=asset, location=location, path=path, original=path)
            away += 1

    if away:
        raise CopiesAway(
            f"none of the {len(locations)} known copies of this file could be opened. "
            "A drive or network share it lives on is probably not connected, so it waits for it."
        )
    raise CopiesGone(
        f"none of the {len(locations)} known copies of this file is where it was. It may have been "
        "moved or deleted; a scan of its folder brings the library up to date."
    )


async def _answered(store: ContentStore, location: Location) -> Path | str | None:
    """`_where`, `_AWAY` where it stopped answering, or None where the location cannot be used."""
    try:
        async with asyncio.timeout(ANSWER_WITHIN):
            return await _where(store, location)
    except TimeoutError:
        log.warning("media.copy_stopped_answering", location_id=location.id)
        return _AWAY
    except (LookupError, ValueError) as exc:
        log.warning("media.location_unusable", location_id=location.id, reason=str(exc))
        return None


async def _where(store: ContentStore, location: Location) -> Path | str:
    """The file holding this copy, or whether its drive is away or the file gone from it."""
    if location.inside_an_archive:
        container = await store.container_path_of(location)
    else:
        container = await store.path_of(location)
    if await asyncio.to_thread(container.is_file):
        return container
    depth = len(PurePosixPath(location.archive_rel_path or location.rel_path).parts)
    root = container.parents[depth - 1] if 0 < depth <= len(container.parents) else None
    if root is not None and await asyncio.to_thread(root.is_dir):
        return _GONE
    return _AWAY


async def _opened(store: ContentStore, location: Location, container: Path) -> Path | None:
    """A path a decoder can open for a copy whose file is there; None if reading it out failed
    on the way, as a share that drops does."""
    if not location.inside_an_archive:
        return container
    try:
        return await store.path_of(location)
    except ArchiveRefused as refused:
        if is_disk_full(refused):
            raise WaitingForSpace(
                "Waiting for space: the disk Sift keeps its cache on is full, so a picture "
                "inside an archive could not be read out. Free some space and it carries on."
            ) from refused
        if isinstance(refused.__cause__, OSError):
            log.warning("media.archive_unread", location_id=location.id, reason=str(refused))
            return None
        if refused.__cause__ is not None and not isinstance(
            refused.__cause__, (KeyError, BadZipFile)
        ):
            raise
        raise JobFailedPermanently(str(refused)) from refused


async def resolve_decodable(store: ContentStore, asset_id: str, *, settings: Settings) -> Source:
    """`resolve`, and a readable copy where a decoder cannot read the file itself: an animated WebP,
    a HEIF photograph, a JPEG a browser turns."""
    source = await resolve(store, asset_id)
    # Here, not at the top: the WebP module imports this one.
    from sift.kernel import jpeg_turn, webp

    # ffmpeg reads one tile of a HEIF photograph; libheif reads it whole.
    if heif.is_heif_still(source.asset):
        readable = await heif.readable_copy(store, source.asset, source.path, settings=settings)
    elif webp.needs_a_readable_copy(source.asset):
        readable = await webp.readable_copy(store, source.asset, source.path, settings=settings)
    elif turned := await jpeg_turn.as_the_browser_draws(store, source, settings=settings):
        readable = turned
    else:
        return source
    return Source(
        asset=source.asset, location=source.location, path=readable, original=source.original
    )
