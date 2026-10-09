# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which files are waiting for a face scan: counted, paged for a sweep, and said as the Build's
term, all under the tuning set now."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from sift.kernel.access import AssetFilter, Viewer, Where
from sift.kernel.access.repository import MAX_PAGE_SIZE
from sift.kernel.content import Lack, VerdictProduct
from sift.kernel.sampling import face_frames
from sift.slices.faces.service_base import FaceServiceBase

#: Files measured to estimate an average one's cost: one page, the most the access layer answers.
_WORK_SAMPLE = MAX_PAGE_SIZE


class SweepMixin(FaceServiceBase):
    """Counting and paging the files still waiting for a scan."""

    async def under_an_earlier_floor(
        self, *, after: str = "", limit: int = MAX_PAGE_SIZE
    ) -> list[str]:
        """The files a lower size floor can change: what the floor pass looks at again."""
        configured = await self.configuration()
        return await self._store.under_an_earlier_floor(
            configured.bar.min_pixels, after=after, limit=limit
        )

    async def floor_pass_owed(self) -> bool:
        """Whether any file waits for the floor pass: what a start asks. One read of one row."""
        return bool(await self.under_an_earlier_floor(limit=1))

    async def read_from_a_tile(
        self, *, after: str = "", limit: int = MAX_PAGE_SIZE
    ) -> tuple[list[str], str]:
        """One page of HEIF stills walked: those whose faces were read from one tile (scanned
        before the whole-picture copy), and the last id walked, empty once the walk is over."""
        page = await self._content.heif_stills(after=after, limit=limit)
        if not page:
            return [], ""
        scanned = await self._store.scanned_at_of([asset_id for asset_id, _ in page])
        tiles = [
            asset_id
            for asset_id, copied_at in page
            if asset_id in scanned and (copied_at is None or scanned[asset_id] < copied_at * 1000)
        ]
        return tiles, page[-1][0]

    async def tile_pass_owed(self) -> bool:
        """Whether any HEIF still's faces were read from one tile: what a start asks."""
        after = ""
        while True:
            tiles, after = await self.read_from_a_tile(after=after)
            if tiles:
                return True
            if not after:
                return False

    async def work_left(self, viewer: Viewer) -> tuple[int, int]:
        """How many files are still to be scanned, and how many moments that is.

        Moments are what a pass spends its time on, so they make the estimate worth reading.
        """
        ids, waiting = await self._store.waiting_asset_ids(_WORK_SAMPLE)
        if waiting == 0 or not ids:
            return waiting, 0
        page = await self._repository.visible_assets(
            viewer,
            limit=len(ids),
            asset_filter=AssetFilter(where=Where("assets", tuple(sorted(set(ids))))),
        )
        durations = [item.asset.duration_ms or 0 for item in page.items]
        if not durations:
            return waiting, 0
        configured = await self.configuration()
        sampled = sum(len(face_frames(ms, density=configured.density)) for ms in durations)
        return waiting, round(sampled * waiting / len(durations))

    async def needs_scanning_page(
        self, viewer: Viewer, *, offset: int, limit: int, force: bool = False
    ) -> tuple[list[str], int, int]:
        """One page of the library, less what was looked at under the current tuning.

        Through the access layer, so the sweep covers what its admin may see. Returns the ids to
        scan, the library's total, and how many rows the page walked, which the caller steps by
        since the access layer caps a page.
        """
        if not await self.enabled():
            return [], 0, 0
        page = await self._repository.visible_assets(viewer, limit=limit, offset=offset)
        walked = len(page.items)
        if force:
            return [item.asset.id for item in page.items], page.total, walked
        configured = await self.configuration()
        settled = await self._store.settled_ids(
            configured.digest, shape=configured.shape, density=configured.density
        )
        # And the files this feature said it cannot look at, or every sweep would retry them.
        settled |= await self._content.verdicted_among(
            VerdictProduct.FACES, [item.asset.id for item in page.items]
        )
        waiting = [item.asset.id for item in page.items if item.asset.id not in settled]
        return waiting, page.total, walked

    async def needs_scanning_among(self, asset_ids: Sequence[str]) -> set[str]:
        """Which of these files a pass would look at under the tuning set; none while off."""
        if not await self.enabled():
            return set()
        configured = await self.configuration()
        return await self._store.unsettled_among(
            asset_ids, configured.digest, shape=configured.shape, density=configured.density
        )

    async def backlog(self) -> tuple[int, int]:
        """How many read files want a look: never looked at, and looked at under other rules."""
        if not await self.enabled():
            return 0, 0
        configured = await self.configuration()
        whole = replace(
            self._store.lack(configured.digest, shape=configured.shape, density=configured.density),
            product=VerdictProduct.FACES.value,
        )
        counted = await self._content.count_lacking([self._store.never_scanned(), whole])
        never, wanting = counted.each
        return never, wanting - never

    async def unread(self) -> int:
        """How many files not read yet will want a look; zero while off."""
        return await self._unread.get() if await self.enabled() else 0

    async def lack(self) -> Lack | None:
        """What a pass would look at, as one term of the Build's count; None while off."""
        if not await self.enabled():
            return None
        configured = await self.configuration()
        return self._store.lack(
            configured.digest, shape=configured.shape, density=configured.density
        )
