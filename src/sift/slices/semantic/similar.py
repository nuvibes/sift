# SPDX-License-Identifier: AGPL-3.0-or-later
"""Finding the files that look like one, by meaning where indexed, else by near fingerprints."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from sift.kernel.access import Viewer
from sift.kernel.access.ranked import FINGERPRINTS_OF_VIEWER, ranked_among
from sift.kernel.content import perceptual
from sift.kernel.content.duplicates import DuplicateReads, Fingerprint
from sift.kernel.db import Database
from sift.kernel.log import get_logger

log = get_logger(__name__)

CANDIDATES = 200


class Tier(StrEnum):
    """Which of the two answered, so a screen can say so rather than implying they are the same."""

    LOOKS = "looks"
    MATCHES = "matches"


@dataclass(frozen=True, slots=True)
class Similar:
    """What was found, and which tier found it."""

    tier: Tier
    #: Ordering only: the read that follows decides who may see any of them.
    neighbours: tuple[tuple[str, float], ...]


class SimilarFinder:
    """Answers "what else looks like this", by whichever tier can."""

    def __init__(self, reads: DuplicateReads, database: Database | None) -> None:
        self._reads = reads
        self._database = database

    async def perceptual(
        self, asset_id: str, *, limit: int = CANDIDATES, asker: Viewer | None = None
    ) -> Similar:
        """The cheap tier: the visible files whose stored fingerprints sit nearest this one's."""
        within = await self._within(asker)
        if within is False:
            return Similar(tier=Tier.MATCHES, neighbours=())
        fingerprints = await self._reads.fingerprints(within=within)
        found = await asyncio.to_thread(_nearest, fingerprints, asset_id, limit, None)
        return Similar(tier=Tier.MATCHES, neighbours=found)

    async def _within(
        self, asker: Viewer | None
    ) -> tuple[str, Mapping[str, object]] | Literal[False] | None:
        """The files `asker` may see as a statement; None ranks every file, False ranks none."""
        if asker is None:
            return None
        if self._database is None:
            return False
        binds = await ranked_among(self._database, asker)
        return None if binds is None else (FINGERPRINTS_OF_VIEWER, binds)


def _nearest(
    fingerprints: Sequence[Fingerprint],
    asset_id: str,
    limit: int,
    among: frozenset[str] | None,
) -> tuple[tuple[str, float], ...]:
    """The files nearest this one among `among`, closest first and at most `limit`."""
    mine = next((one for one in fingerprints if one.asset_id == asset_id), None)
    if mine is None:
        return ()
    found: list[tuple[str, float]] = []
    for other in fingerprints:
        if other.asset_id == asset_id or (among is not None and other.asset_id not in among):
            continue
        apart = _apart(mine, other)
        if apart is None or apart > perceptual.NEAR_SHARE:
            continue
        found.append((other.asset_id, apart))
    found.sort(key=lambda pair: (pair[1], pair[0]))
    return tuple(found[:limit])


def _apart(mine: object, other: object) -> float | None:
    """How far apart two fingerprints are as a share of their bits, or None if not comparable."""
    shares = []
    for field in ("videohash", "phash"):
        first = getattr(mine, field, None)
        second = getattr(other, field, None)
        if not first or not second:
            continue
        share = perceptual.apart(first, second)
        if share is not None:
            shares.append(share)
    return min(shares, default=None)
