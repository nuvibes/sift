# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fingerprinting the music in a file, and the four questions the Build asks about it.

One read per file, whole, once. What makes that affordable on a library served from another
machine is not the reading: it is WHEN the reading happens: a file that arrived through a
download, a drop, a paste or an upload was on the local disk for a moment first, and its
fingerprint was taken there (`kernel/landing.py`). This claims that waiting answer by the file's
identity before it considers opening anything, so the same bytes are never read twice.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence

from sift.kernel import chromaprint
from sift.kernel.config import Settings
from sift.kernel.content import Lack
from sift.kernel.jobs import JobContext
from sift.kernel.log import get_logger
from sift.kernel.media import MissingAsset, NoReadableCopy, resolve_decodable
from sift.kernel.wiring import Part
from sift.slices.music.matching import Match, as_array, is_candidate, keys_of, pair
from sift.slices.music.store import Candidate, Kept, MusicStore, Pair

log = get_logger(__name__)

#: How long a fingerprint taken at staging waits for a file that never arrived, in seconds.
#:
#: Thirty days, and swept before a catch-up run rather than on a clock: that is the one moment
#: anybody reads the table, so a sweep there costs a statement instead of a timer. A row is a
#: handful of kilobytes, so waiting a month to drop one costs nothing anybody would notice.
PENDING_KEEP_SECONDS = 30 * 24 * 60 * 60

#: Whether this is wanted, asked of one file so a folder can answer differently. The import
#: policy's own answer, handed in by the composition root: a feature never imports another.
Allowed = Callable[[str, str | None], Awaitable[bool]]

#: The payload key that makes a fingerprint job CLAIM ONLY: take a fingerprint waiting under the
#: file's identity if there is one, and otherwise return without opening the file. What the scan's
#: follow-on sends (`sift/wiring/imports.py`, `media_jobs.probe`), because a scan finding a file is
#: nobody asking for its music to be read. A press, the Build and the card send no such key and
#: read.
CLAIM_ONLY = "claim_only"


#: What happens once a file's pairs are settled: the file, and every pair it is now in. Handed in
#: by the composition root (the song names spreading through the group live in another part of
#: this slice, and their receipts in the workbench), so the pairing never has to know what reads
#: its answer. Called once per settled file with a fingerprint, with no pairs as well as with some:
#: a file with no partner is still a file whose pairing is done.
PairsSettled = Callable[[str, Sequence[Pair]], Awaitable[None]]


async def _nothing(asset_id: str, pairs: Sequence[Pair]) -> None:
    """The default: nothing reads the pairs yet."""


class MusicService:
    """What the product does, and what it answers about itself."""

    def __init__(
        self,
        *,
        store: MusicStore,
        allowed: Allowed,
        job_type: str,
        on_pairs_settled: PairsSettled = _nothing,
    ) -> None:
        self._store = store
        self._allowed = allowed
        self._job_type = job_type
        self._on_pairs_settled = on_pairs_settled

    async def lack(self) -> Lack | None:
        """Files with audio and no fingerprint, or with one not yet paired, as one term of the
        Generate count (see `LACKS_AUDIO_FINGERPRINT`). None while the switch is off: nothing is
        going to be read, so nothing is lacking."""
        if not await self._allowed(self._job_type, None):
            return None
        return self._store.lack()

    async def waiting_for(self, user_id: str) -> int:
        """How many files this user can see still want a fingerprint. Nought while the switch
        is off, for the reason `lack` gives: nothing is going to be read."""
        if not await self._allowed(self._job_type, None):
            return 0
        return await self._store.waiting_for(user_id)

    async def any_waiting(self) -> bool:
        """Whether any file in the library still wants one, where the switch wants them."""
        if not await self._allowed(self._job_type, None):
            return False
        return await self._store.any_waiting()

    async def lacking_among(self, asset_ids: Sequence[str]) -> set[str]:
        """Which of these files still owe a fingerprint, or its pairing, where the switch wants one.

        The same set the Build's count names (`MusicStore.lack`), asked of a page: a file read with
        a sound track, a copy there to read and no row yet (the waiting table the kernel keeps),
        or a row not yet paired. Answered from this feature's own tables alone; the waiting table is
        where the file's own row was read.
        """
        if not asset_ids or not await self._allowed(self._job_type, None):
            return set()
        return await self._store.lacking_among(asset_ids)

    async def same_music_of(self, user_id: str, asset_id: str, *, reveal: bool) -> list[str]:
        """The files sharing a song with this one that this user may see, closest first. See
        `MusicStore.same_music_of`."""
        return await self._store.same_music_of(user_id, asset_id, reveal=reveal)

    async def before_run(self) -> None:
        """Housekeeping before a catch-up: drop staging fingerprints nothing ever claimed."""
        await self._store.forget_pending_before(int(time.time()) - PENDING_KEEP_SECONDS)

    async def fingerprint(self, context: JobContext, *, settings: Settings) -> None:
        """Give one file its audio fingerprint. The unit the Build and an arriving file share.

        Five ways this ends, and each of them writes a row or deliberately writes nothing:

        * **The file already has one.** Nothing to read; a rerun of the pass costs a point read.
          If its pairing is not done (a fingerprint kept before pairing existed, or one whose
          pairing failed part-way), it is paired now, from the row, without opening the file.
        * **A fingerprint is waiting under this file's identity**, taken while its bytes were on
          the local disk. Claimed: one statement, no decode, no read of the library at all.
        * **Probing says there is no audio.** An empty row, with no launch: `acodec` is NULL for
          a photograph, for a GIF and for a silent video, and ffmpeg refuses `-map 0:a:0` on all
          three anyway. The row is what stops the file being offered again for ever.
        * **The job only claims** (`CLAIM_ONLY` in its payload: the scan's follow-on) and there
          was nothing to claim. It returns without opening the file and logs `music.left_waiting`:
          a scan finding a file is not somebody asking for its music, and the file waits for a
          press or the card like the rest of the library.
        * **Otherwise the file is read**, whole, once, in its storage's lane and at background
          priority. A file ffmpeg cannot read audio from gets the empty row and a log line.

        A file that cannot be REACHED (gone, or on a drive that is not plugged in) is left
        alone rather than written down, so the next pass tries again once the drive is back. The
        same rule the picture fingerprints follow, and for the same reason.

        **And every path that leaves the file with a fingerprint goes on to pair it** (`_pair`):
        the keys, the candidates, the verification, the pairs, and then whatever reads them. In the
        same job, because it is one unit of work on one file, and after the fingerprint is
        written, so nothing that goes wrong in the pairing can cost the read.
        """
        asset_id = str(context.payload.get("asset_id") or "")
        if not asset_id:
            raise ValueError("an audio fingerprint needs the id of its file")
        if not await self._allowed(self._job_type, asset_id):
            return
        if await self._store.has(asset_id):
            if not await self._store.indexed(asset_id):
                await self._pair(asset_id)
            return

        asset = await context.content.get(asset_id)
        if asset is None:
            log.info("music.gone", asset_id=asset_id)
            return
        if await self._store.claim(asset_id, asset.identity):
            log.info("music.claimed_from_staging", asset_id=asset_id)
            await self._pair(asset_id)
            return

        if asset.acodec is None:
            await self._keep(asset_id, chromaprint.empty(await chromaprint.tool_version(settings)))
            return
        if context.payload.get(CLAIM_ONLY):
            log.info("music.left_waiting", asset_id=asset_id)
            return

        try:
            source = await resolve_decodable(context.content, asset_id, settings=settings)
        except (MissingAsset, NoReadableCopy):
            # Nothing is written. See the docstring: an unreachable file is a moment, not an answer.
            log.info("music.unreachable", asset_id=asset_id)
            return
        await self._keep(
            asset_id, await chromaprint.read_whole_track(source.path, settings=settings)
        )

    async def _keep(self, asset_id: str, fingerprint: chromaprint.Fingerprint) -> None:
        await self._store.keep(asset_id, as_kept(fingerprint))
        await self._pair(asset_id)

    async def _pair(self, asset_id: str) -> None:
        """Find which files share a song with this one, write the pairs, and say so.

        Read back from the row rather than handed the values, so a fingerprint just read, one
        claimed from staging and one kept long ago are paired by one path. The order is the
        design (see `slices/music/matching.py` for the rule):

        1. **The keys are written first**, on their own, so of two files paired at the same moment
           whichever searches second finds the other.
        2. **The candidates**: files over the key floor, from the index, then the length-relative
           half of the floor (`matching.is_candidate`).
        3. **The verification**, on a thread: numpy arithmetic, milliseconds a pair, but still
           arithmetic that has no business on the event loop.
        4. **This file's pairs are replaced** with the ones that held.
        5. **What reads the pairs is told** (`on_pairs_settled`), once, with all of them.
        6. **The stamp** (`indexed_scheme`) last. Anything that fails before it leaves the file
           unpaired, and the next run (a press, the Build) pairs it again from the row. So a
           failure here is logged and swallowed: the fingerprint is already written, and it is the
           expensive half.

        An EMPTY fingerprint has no keys and no pairs; it is stamped and nothing is told.
        """
        try:
            fingerprint = await self._store.fingerprint_of(asset_id)
            if fingerprint is None:
                # Gone between the write and this read: the file went, and its row with it.
                return
            keys = keys_of(fingerprint.values)
            await self._store.index_keys(asset_id, keys)
            found = await self._verified(
                asset_id, fingerprint, await self._store.candidates(asset_id, keys)
            )
            await self._store.write_pairs(asset_id, found)
            if not fingerprint.empty:
                # Before the stamp on purpose: a fault in the names' hook (a spread, a lookup's
                # start) leaves the file unsettled, so the next run pairs it again and calls the
                # hook again. That re-compare is the hook's one retry path, and it costs a few
                # milliseconds per candidate; a hook whose failure was swallowed here would never
                # be asked again, and the name it owed would be lost for good.
                await self._on_pairs_settled(asset_id, found)
            await self._store.settle(asset_id)
        except Exception:
            log.exception("music.pairing_failed", asset_id=asset_id)

    async def _verified(
        self,
        asset_id: str,
        fingerprint: chromaprint.Fingerprint,
        candidates: Sequence[Candidate],
    ) -> list[Pair]:
        """The candidates that really share a song with this file, as pairs."""
        others: dict[str, tuple[int, ...]] = {}
        for one in candidates:
            if not is_candidate(one.shared, fingerprint.duration_ms, one.duration_ms):
                continue
            other = await self._store.fingerprint_of(one.asset_id)
            if other is not None and not other.empty:
                others[one.asset_id] = other.values
        if not others:
            return []
        matches = await asyncio.to_thread(_compare_all, fingerprint.values, others)
        now = int(time.time())
        return [
            Pair.of(asset_id, other_id, match, now)
            for other_id, match in matches.items()
            if match.matches
        ]


def _compare_all(values: Sequence[int], others: Mapping[str, Sequence[int]]) -> dict[str, Match]:
    """This file against each candidate, both directions. On a thread: see `_pair`."""
    mine = as_array(values)
    return {other_id: pair(mine, as_array(theirs)) for other_id, theirs in others.items()}


def as_kept(fingerprint: chromaprint.Fingerprint) -> Kept:
    """One fingerprint in the shape the table takes it, stamped with the moment it was made."""
    return Kept(
        algorithm=fingerprint.algorithm,
        tool=fingerprint.tool,
        duration_ms=fingerprint.duration_ms,
        offset_ms=fingerprint.offset_ms,
        fingerprint=fingerprint.blob,
        computed_at=int(time.time()),
    )


#: The service, on the application.
SERVICE: Part[MusicService] = Part("music_service")
