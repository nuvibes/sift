# SPDX-License-Identifier: AGPL-3.0-or-later
"""Taking one watermark filing back: a reverser with no card, since nothing here waits on a
person."""

from __future__ import annotations

import json

from sift.kernel.access import Viewer
from sift.kernel.workbench import ASSET, Preview
from sift.slices.watermarks.service import QUEUE, WatermarkService

PREVIEW = 4


class WatermarkFilings:
    name = QUEUE
    reversible = True

    def __init__(self, service: WatermarkService) -> None:
        self._service = service

    async def pictures_of(self, viewer: Viewer, payload: str) -> tuple[Preview, ...]:
        """A still of the file one filing was about, scoped to whoever is reading the record."""
        wanted = _record(payload)[2][:PREVIEW]
        if not wanted:
            return ()
        allowed = await self._service.visible_of(viewer, wanted)
        return tuple(
            Preview(kind=ASSET, id=one, href=f"/asset/{one}") for one in wanted if one in allowed
        )

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        """Take one filing off its file; an unreadable record answers False, never an error."""
        username_id, site, assets = _record(payload)
        if not assets or not (username_id or site):
            return False
        return await self._service.take_back(
            username_id=username_id or None, site=site or None, asset_ids=assets
        )


def _record(payload: str) -> tuple[str, str, list[str]]:
    """One filing's username, site and files from its record; one of the first two may be empty."""
    try:
        found = json.loads(payload)
    except (TypeError, ValueError):
        return "", "", []
    if not isinstance(found, dict) or found.get("kind") != "filed":
        return "", "", []
    assets = found.get("assets")
    return (
        str(found.get("username_id") or ""),
        # Older receipts use the old key.
        str(found.get("site") or found.get("platform") or ""),
        [str(one) for one in assets] if isinstance(assets, list) else [],
    )
