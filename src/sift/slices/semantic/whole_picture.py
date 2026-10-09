# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which HEIF photographs were described from one tile, and taking those descriptions back."""

from __future__ import annotations

from sift.kernel.access.repository import MAX_PAGE_SIZE
from sift.kernel.content import ContentStore
from sift.kernel.wiring import Part
from sift.slices.semantic.records import Records


class WholePicture:
    """The HEIF photographs whose description was read from one tile, and the look again at them."""

    def __init__(self, *, content: ContentStore, records: Records) -> None:
        self._content = content
        self._records = records

    async def read_from_a_tile(
        self, *, after: str = "", limit: int = MAX_PAGE_SIZE
    ) -> tuple[list[str], str]:
        """One page of HEIF stills walked: those described from one tile, and the last id walked."""
        page = await self._content.heif_stills(after=after, limit=limit)
        if not page:
            return [], ""
        described = await self._records.described_at_of([asset_id for asset_id, _ in page])
        tiles = [
            asset_id
            for asset_id, copied_at in page
            if asset_id in described
            and (copied_at is None or described[asset_id] < copied_at * 1000)
        ]
        return tiles, page[-1][0]

    async def tile_pass_owed(self) -> bool:
        """Whether any HEIF still's description was read from one tile: what a start asks."""
        after = ""
        while True:
            tiles, after = await self.read_from_a_tile(after=after)
            if tiles:
                return True
            if not after:
                return False

    async def take_back(self, asset_id: str) -> None:
        """Forget one file's description, so the describing job reads it again. See the module."""
        await self._records.forget(asset_id)


WHOLE_PICTURE: Part[WholePicture] = Part("semantic_whole_picture")
