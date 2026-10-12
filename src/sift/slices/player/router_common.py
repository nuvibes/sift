# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the player's routes share: the log, the service, the 404, and opening an asset the viewer may watch."""

from __future__ import annotations

from fastapi import HTTPException, Request, status

from sift.kernel.access import Repository, Viewer
from sift.kernel.content import Asset
from sift.kernel.log import get_logger
from sift.kernel.wiring import part_of
from sift.slices.player.service import SERVICE, PlayerService

log = get_logger(__name__)

_CACHE_PRIVATE = {"Cache-Control": "private, no-cache"}


def _player(request: Request) -> PlayerService:
    return part_of(request, SERVICE)


def _missing() -> HTTPException:
    """The one answer for "no such asset" and "not yours"."""
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")


async def _open(access: Repository, viewer: Viewer, asset_id: str) -> Asset:
    """The asset, if this viewer may actually watch it. 404 otherwise, either way."""
    asset = await access.open_asset(viewer, asset_id)
    if asset is None:
        raise _missing()
    return asset
