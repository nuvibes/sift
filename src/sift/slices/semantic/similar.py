# SPDX-License-Identifier: AGPL-3.0-or-later
"""Finding the files that look like one you are already looking at.

Two ways of answering, cheapest first, and which one answered is reported rather than hidden.

**The cheap one needs no model and no index.** Every file already carries a short fingerprint of
what it looks like, computed when it was imported, and comparing two of them is arithmetic. It is
coarse (it finds another copy, a re-encode, a crop), and it works on every install, including
one that has never turned search by meaning on.

**The good one needs the index to have reached this file.** It compares what a model made of the
picture, so it finds things that merely *resemble* each other rather than things that are nearly
the same bytes. A file the background pass has not got to yet has no such numbers, and the honest
answer there is the cheap tier rather than an empty screen.

**Nothing here knows how a fingerprint is made.** Not its width, not how many bits differ before
two files are called alike, not which of them is video and which is a still. All of that belongs to
the content layer, is being changed there, and is read through the one interface it offers, so a
better fingerprint improves this without this file being touched.

**The cheap tier answers only what nearly matches.** It is what "Similar to this" shows with Smart
Search off, under a sentence saying these are files whose frames nearly match, so a file further
apart than the content layer's own edge (`perceptual.NEAR_SHARE`) is no answer. Ranked by the share
of bits apart rather than the count: a count of 14 bits is two photographs on a 64-bit frame and
one video on thirty frames laid end to end, and ranking the two counts together puts a video's
own source (its cover the same, bit for bit) below photographs 14 to 18 bits away.
"""

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

#: How many files to rank. Generous against any page the app shows.
CANDIDATES = 200


class Tier(StrEnum):
    """Which of the two answered, so a screen can say so rather than implying they are the same.

    They are not interchangeable, and somebody comparing two files' results deserves to know which
    question was answered. LOOKS is "a model thinks these resemble each other"; MATCHES is "their
    perceptual hashes nearly match" (a video's videohash, a picture's pHash). The second is a
    comparison and not a promise of a copy: two different files can hash close.
    """

    LOOKS = "looks"
    MATCHES = "matches"


@dataclass(frozen=True, slots=True)
class Similar:
    """What was found, and which tier found it."""

    tier: Tier
    #: Files and how far each sat, closest first. Ordering only: the read that follows decides
    #: who may see any of them.
    neighbours: tuple[tuple[str, float], ...]


class SimilarFinder:
    """Answers "what else looks like this", by whichever tier can."""

    def __init__(self, reads: DuplicateReads, database: Database | None) -> None:
        self._reads = reads
        # Without it an asker's files cannot be read, so an asker is ranked nothing.
        self._database = database

    async def perceptual(
        self, asset_id: str, *, limit: int = CANDIDATES, asker: Viewer | None = None
    ) -> Similar:
        """The cheap tier: the files `asker` may see whose stored fingerprints sit nearest this
        one's; every file for a pass with no asker.

        Reads every fingerprint and compares in memory, the read duplicate-finding makes: linear
        in the library, the cost of an answer that needs no index.

        A file with no fingerprint, or one whose fingerprint cannot be compared with this one,
        which happens when the two were computed differently, contributes nothing rather than a
        made-up distance, and neither does one further apart than the content layer's edge. The
        comparison itself belongs to the content layer; this only sorts what it says.
        """
        within = await self._within(asker)
        if within is False:
            return Similar(tier=Tier.MATCHES, neighbours=())
        fingerprints = await self._reads.fingerprints(within=within)
        # On a thread: one comparison per fingerprinted file the asker may see.
        found = await asyncio.to_thread(_nearest, fingerprints, asset_id, limit, None)
        return Similar(tier=Tier.MATCHES, neighbours=found)

    async def _within(
        self, asker: Viewer | None
    ) -> tuple[str, Mapping[str, object]] | Literal[False] | None:
        """The files `asker` may see as a statement to read within, None where every file may be
        ranked, False where nothing may."""
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
    """The files nearest this one among `among` (every file for None), closest first and at most
    `limit`, or none when this file has no fingerprint among them."""
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
    """How far apart two files' fingerprints are, as a share of their bits, or None when they
    cannot be compared.

    Asks each kind the content layer offers and takes the closest that answers: a video whose
    cover is the same picture as a photograph's, or as another video's, nearly matches it there
    whatever its other frames say. What a share means in each kind is the content layer's business
    (`perceptual.apart`): this asks and does not interpret.
    """
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
