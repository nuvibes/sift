# SPDX-License-Identifier: AGPL-3.0-or-later
"""From an asset to a file a decoder can open."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from sift.kernel import heif
from sift.kernel.config import Settings
from sift.kernel.content import Asset, ContentStore, Location, LocationStatus
from sift.kernel.log import get_logger

log = get_logger("sift.kernel.media")


class MissingAsset(Exception):
    """The asset is not there any more: permanent, so not retried."""


class NoReadableCopy(Exception):
    """Every copy of this asset is somewhere Sift cannot read right now, such as an offline NAS."""


@dataclass(frozen=True, slots=True)
class Source:
    """An asset, a file of it a decoder can open (`path`), and the user's own file (`original`)."""

    asset: Asset
    location: Location
    path: Path
    original: Path


async def resolve(store: ContentStore, asset_id: str) -> Source:
    """Find an asset and the first copy that opens; a location known to be missing is not tried."""
    asset = await store.get(asset_id)
    if asset is None:
        raise MissingAsset(f"asset {asset_id} no longer exists")

    locations = await store.locations(asset_id)
    for location in locations:
        if location.status is not LocationStatus.PRESENT:
            continue
        try:
            path = await store.path_of(location)
        except (LookupError, ValueError) as exc:
            log.warning("media.location_unusable", location_id=location.id, reason=str(exc))
            continue
        if await asyncio.to_thread(path.is_file):
            return Source(asset=asset, location=location, path=path, original=path)

    raise NoReadableCopy(
        f"none of the {len(locations)} known copies of this file could be opened. "
        "A drive or network share it lives on is probably not connected."
    )


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
