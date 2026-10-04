# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which files are waiting for a face scan: counted, paged for a sweep, and said as the Build's
term, all under the tuning that is set now.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from sift.kernel.access import AssetFilter, Viewer, Where
from sift.kernel.access.repository import MAX_PAGE_SIZE
from sift.kernel.content import Lack, VerdictProduct
from sift.kernel.sampling import face_frames
from sift.slices.faces.service_base import FaceServiceBase

#: How many of the files waiting to be scanned are measured to work out what an average one costs.
#:
#: One page exactly, because that is the most the access layer answers about in one ask: request
#: more and everything past the cap comes back looking like a file this user may not see. Far
#: more than an average needs in any case: the queue on a large library IS the library, and reading
#: a hundred thousand rows to refine a number that gets rounded to the nearest ten minutes is work
#: spent on precision nobody can use.
_WORK_SAMPLE = MAX_PAGE_SIZE


class SweepMixin(FaceServiceBase):
    """Counting and paging the files still waiting for a scan."""

    async def under_an_earlier_floor(
        self, *, after: str = "", limit: int = MAX_PAGE_SIZE
    ) -> list[str]:
        """The files a lower size floor can change: a face their scan refused for size would be
        accepted today. What the floor pass looks at again, and nothing else. See
        `Store.under_an_earlier_floor`."""
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
        """One page of HEIF stills walked: the ones whose faces were read from one tile, and the
        last id walked, empty once the walk is over.

        A HEIF photograph is a grid of tiles, and before the HEIF door every pass read the first
        tile as the picture. The door writes a copy of the whole picture, and a face scan reads
        that copy, so a scan OLDER than the copy, or of a file with no copy yet, read a tile. A
        scan taken again reads the copy and is newer than it, so the file leaves the list and the
        pass ends. A copy swept from the cache and made again is newer than every scan, and the
        file is looked at once more: a picture's faces read again from the same picture.
        """
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
        """Whether any HEIF still's faces were read from one tile: what a start asks. A walk of
        the HEIF stills alone, which are few."""
        after = ""
        while True:
            tiles, after = await self.read_from_a_tile(after=after)
            if tiles:
                return True
            if not after:
                return False

    async def work_left(self, viewer: Viewer) -> tuple[int, int]:
        """How many files are still to be scanned, and how many moments that is.

        The second number is what makes an estimate of the time left worth reading. Files are not
        interchangeable (one is a photograph and the next is two hours), so a rate in files per
        minute describes the stretch of library it was measured over and not the stretch ahead of
        it. Moments are what a pass actually spends its time on: one seek, one decode, one look. A
        hundred of them costs about the same whatever they were cut from.

        Nothing is opened to work this out. Every file was probed when it was imported, so the
        durations are already stored, and how many moments a duration comes to is the sampler's
        arithmetic.

        The durations come back through the access layer like everything else about an asset, so a
        file this user may not see contributes nothing, including to a number as innocuous as
        how much work is left. The sample is one page exactly, which is the largest a single ask can
        answer without being silently cut short.
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
        """One page of the library, minus what has already been looked at under the current tuning.

        Through the access layer rather than against the assets table. A slice reading that table
        directly writes a second copy of the scoping rule, and this one runs as a background job
        where nobody would notice it disagreeing with the first, which is exactly the case the
        rule about it exists for. It also means the sweep covers what an admin who asked for it
        may see, rather than quietly reaching past them.

        **A file scanned under different settings counts as needing another look.** Changing the
        depth, the quality bar or the model family changes what a pass would find, and the tuning
        each result was produced under is stored beside it precisely so this can be told. Without
        that comparison every file is scanned once and never again, and turning the depth up does
        nothing to anything already in the library.

        The comparison is symmetrical, which is worth saying out loud: turning the depth back *down*
        also makes everything stale, and the next sweep will redo it at the shallower setting. That
        is the honest reading of "scan under the settings I have now", and the alternative (only
        ever redoing a file when the new settings look harder) means a setting that is supposed to
        control the whole library silently applies to part of it.

        `force` ignores all of that and offers everything. It is for the cases the comparison cannot
        see: crops that came out badly, a model swapped underneath, or scans that failed so often
        they were given up on.

        Paged by offset, and that is safe here for a reason worth stating: scanning a file writes a
        row in the face tables and changes nothing about the asset list, so the page under the
        offset does not shift while the sweep walks it.

        Returns the ids worth scanning, how many the library holds, and **how many rows this page
        actually walked past**, which is not the same as the limit that was asked for, and the
        difference is why this third number exists.

        The access layer caps a page. A caller that asks for 500 and is given 200 has no way to tell
        from the first two numbers, so a sweep advancing its offset by what it requested would step
        over everything between the cap and the request, silently. The walked count is what the
        caller must step by, and it is reported rather than inferred so that the cap can change
        without anything here having to know it did.
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
        # And the files this feature has said it cannot look at: offered again, they would be
        # scanned again, and verdicted again, on every sweep.
        settled |= await self._content.verdicted_among(
            VerdictProduct.FACES, [item.asset.id for item in page.items]
        )
        waiting = [item.asset.id for item in page.items if item.asset.id not in settled]
        return waiting, page.total, walked

    async def needs_scanning_among(self, asset_ids: Sequence[str]) -> set[str]:
        """Which of these files a pass would look at under the tuning that is set.

        Empty while the feature is off: nothing is going to be looked at, so nothing is lacking.
        What the Build asks, a page at a time, to decide each file's products.
        """
        if not await self.enabled():
            return set()
        configured = await self.configuration()
        return await self._store.unsettled_among(
            asset_ids, configured.digest, shape=configured.shape, density=configured.density
        )

    async def backlog(self) -> tuple[int, int]:
        """How many files want looking at for faces, as the two reasons: never looked at, and
        looked at under an older rule or other settings. Zero and zero while the feature is off.

        What the Faces pane says beside the controls, so a backlog (which can be most of a
        library) is a sentence somebody reads rather than something found by asking why a plain
        face was not recognized.

        One statement over the library, counted by the content store from this feature's own two
        conditions (never scanned, and the whole rule `lack` states, of which the first is a
        part), so the two numbers add up to what `lack` counts. That is the Faces row of the Build sheet on
        Importing, where the press that does the work is, except that the sheet leaves out files
        only in folders that refuse face scans and this does not: this says what has and has not
        been looked at, which is true whatever a folder asks for next.
        """
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

    async def lack(self) -> Lack | None:
        """What a pass would look at under the tuning that is set, as one term of the Build's
        count. None while the feature is off: nothing is going to be looked at, so nothing is
        lacking: the same answer `needs_scanning_among` gives, as a condition."""
        if not await self.enabled():
            return None
        configured = await self.configuration()
        return self._store.lack(
            configured.digest, shape=configured.shape, density=configured.density
        )
