# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fingerprint the music in a file, claiming one taken at staging first so no bytes are read
twice."""

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

#: Swept before a catch-up run, the one moment anybody reads the table.
PENDING_KEEP_SECONDS = 30 * 24 * 60 * 60

#: The import policy's answer, handed in: a feature never imports another.
Allowed = Callable[[str, str | None], Awaitable[bool]]

#: Claim a staged fingerprint or return unopened: a scan finding a file is not a request.
CLAIM_ONLY = "claim_only"


#: Called once per settled file, with or without pairs; handed in by the composition root.
PairsSettled = Callable[[str, Sequence[Pair]], Awaitable[None]]


async def _nothing(asset_id: str, pairs: Sequence[Pair]) -> None:
    """The default: nothing reads the pairs yet."""


class MusicService:
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
        """Files lacking a fingerprint or pairing, as a Generate term; None while switched off."""
        if not await self._allowed(self._job_type, None):
            return None
        return self._store.lack()

    async def waiting_for(self, user_id: str) -> int:
        """How many files this user can see still want a fingerprint; zero while switched off."""
        if not await self._allowed(self._job_type, None):
            return 0
        return await self._store.waiting_for(user_id)

    async def any_waiting(self) -> bool:
        if not await self._allowed(self._job_type, None):
            return False
        return await self._store.any_waiting()

    async def lacking_among(self, asset_ids: Sequence[str]) -> set[str]:
        """Which of these files still owe a fingerprint or pairing, where the switch wants one."""
        if not asset_ids or not await self._allowed(self._job_type, None):
            return set()
        return await self._store.lacking_among(asset_ids)

    async def same_music_of(self, user_id: str, asset_id: str, *, reveal: bool) -> list[str]:
        """The files sharing a song with this one that this user may see, closest first."""
        return await self._store.same_music_of(user_id, asset_id, reveal=reveal)

    async def before_run(self) -> None:
        """Drop staging fingerprints nothing ever claimed."""
        await self._store.forget_pending_before(int(time.time()) - PENDING_KEEP_SECONDS)

    async def fingerprint(self, context: JobContext, *, settings: Settings) -> None:
        """Give one file its fingerprint (kept, claimed, empty or read) and then pair it."""
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
            # An unreachable file is a moment, not an answer, so the next pass tries again.
            log.info("music.unreachable", asset_id=asset_id)
            return
        await self._keep(
            asset_id, await chromaprint.read_whole_track(source.path, settings=settings)
        )

    async def _keep(self, asset_id: str, fingerprint: chromaprint.Fingerprint) -> None:
        await self._store.keep(asset_id, as_kept(fingerprint))
        await self._pair(asset_id)

    async def _pair(self, asset_id: str) -> None:
        """Pair one file: keys first, then verify, replace pairs, tell the hook, and stamp last."""
        try:
            fingerprint = await self._store.fingerprint_of(asset_id)
            if fingerprint is None:
                # The file went, and its row with it.
                return
            keys = keys_of(fingerprint.values)
            await self._store.index_keys(asset_id, keys)
            found = await self._verified(
                asset_id, fingerprint, await self._store.candidates(asset_id, keys)
            )
            await self._store.write_pairs(asset_id, found)
            if not fingerprint.empty:
                # Before the stamp, so a failed hook is retried by the next run.
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
    """This file against each candidate, both directions."""
    mine = as_array(values)
    return {other_id: pair(mine, as_array(theirs)) for other_id, theirs in others.items()}


def as_kept(fingerprint: chromaprint.Fingerprint) -> Kept:
    return Kept(
        algorithm=fingerprint.algorithm,
        tool=fingerprint.tool,
        duration_ms=fingerprint.duration_ms,
        offset_ms=fingerprint.offset_ms,
        fingerprint=fingerprint.blob,
        computed_at=int(time.time()),
    )


SERVICE: Part[MusicService] = Part("music_service")
