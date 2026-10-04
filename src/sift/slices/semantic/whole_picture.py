# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which HEIF photographs were described from one tile of them, and taking those descriptions back.

A HEIF photograph is a grid of tiles, and a reader that takes the first tile for the picture
describes one tile. The HEIF door writes a copy of the whole picture and a description reads that
copy, so a description OLDER than the copy, or of a file with no copy yet, describes one tile.

The walk is over the HEIF stills (the content store's own list, usually a short one), with each
page's description times read by primary key. Nothing here
reads the assets table, for the reason `records` gives.

Taking a description back is forgetting its record, the door every reader already treats as "not
described": the file leaves Similar and is counted as waiting until it is described again, and
the describing job's own guard (a file described since it was last read is not described again)
has nothing on record to stop it. Described again, it reads the copy and is newer than it, so it
leaves the list and the pass ends.
"""

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
        """One page of HEIF stills walked: the ones whose description was read from one tile, and
        the last id walked, empty once the walk is over.

        A description is timed in milliseconds and a copy in seconds, so the copy's time is
        brought to milliseconds before the two are compared. A copy swept from the cache and made
        again is newer than every description, and the file is described once more: the same
        picture, read again from the same whole copy.
        """
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
        """Whether any HEIF still's description was read from one tile: what a start asks. A walk
        of the HEIF stills alone, which are few, stopping at the first page that holds one."""
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


#: The look again, for the start that asks whether it is owed.
WHOLE_PICTURE: Part[WholePicture] = Part("semantic_whole_picture")
