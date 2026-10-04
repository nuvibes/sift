# SPDX-License-Identifier: AGPL-3.0-or-later
"""The rule that turns one creator's loose pictures into proposed shoots.

A shoot is a run of pictures from one sitting, which no catalog records; the meaning index says
how far apart two pictures look. Greedy and seeded in a fixed order, the cheap answer: one
neighbour lookup per shoot instead of comparing every pair, at the price that grouping depends on
the seed, so seeds go in a fixed order (the same library gives the same shoots) and a refused shoot
is remembered per PICTURE. No database or index is touched here: it takes a pool and a way to ask
for neighbours, which is what makes the distance measurable.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence

import numpy as np

#: How far apart two pictures may sit and still be one shoot: straight-line distance between unit
#: descriptions (zero is the same picture). Set by the largest group, not folder purity (which holds
#: well past it): above it groups run away into proposals nobody can check, below it too little is
#: found. A similarity rule, so every group is only a proposal.
DISTANCE = 0.30

#: No `LEAST` here: the floor is the Photo Sets' rule, declared once there and handed across by the
#: composition root (`sift/wiring/understanding.py`, `least=photo_sets.MIN_PICTURES`), so `shoots`
#: takes it with no default to drift back to.

#: The most pictures one proposal may hold: the distance cannot promise a ceiling (a creator shot
#: against one backdrop runs away at any threshold). A larger group is dropped, never trimmed to an
#: arbitrary sixty presented as the rule's finding.
MOST = 60

#: Neighbours one lookup asks for, nearest first, so a short answer loses only the farthest; well
#: above `MOST` because other creators', filed and video neighbours are filtered out afterwards.
REACH = 400

#: What one picture's neighbours look like: the file and how far it sat, closest first.
Neighbours = Callable[[str], Awaitable[tuple[tuple[str, float], ...] | None]]


async def among(
    pool: Sequence[str], vectors: Mapping[str, Sequence[float]], *, within: float = DISTANCE
) -> Neighbours:
    """Each picture's neighbours WITHIN THE POOL, worked out in memory from the pool's own numbers.

    A pool of a few thousand is one matrix in memory, so the index is not asked per picture. The
    answer matches the index's (a still's pooled description is its one frame; nearest first, seed
    left out) except that it is never cut short at `REACH`, and float64 against float32 moves a
    distance by far less than any threshold step. `within` stops where `shoots` would anyway. A
    picture with no numbers answers None. The matrix work runs on a thread.
    """
    ids = [asset_id for asset_id in pool if vectors.get(asset_id)]
    if not ids:
        return _nobody
    at = {asset_id: row for row, asset_id in enumerate(ids)}
    matrix, lengths = await asyncio.to_thread(_matrix_of, ids, vectors)

    def near(row: int) -> tuple[tuple[str, float], ...]:
        apart = np.sqrt(np.maximum(lengths + lengths[row] - 2.0 * (matrix @ matrix[row]), 0.0))
        close = np.flatnonzero(apart <= within)
        order = close[np.argsort(apart[close], kind="stable")]
        return tuple((ids[one], float(apart[one])) for one in order if one != row)

    async def neighbours(asset_id: str) -> tuple[tuple[str, float], ...] | None:
        row = at.get(asset_id)
        if row is None:
            return None
        return await asyncio.to_thread(near, row)

    return neighbours


def _matrix_of(
    ids: Sequence[str], vectors: Mapping[str, Sequence[float]]
) -> tuple[np.ndarray, np.ndarray]:
    """The pool's numbers as one matrix, a row per picture in `ids` order, and each row's squared
    length."""
    matrix = np.asarray([vectors[asset_id] for asset_id in ids], dtype=np.float64)
    return matrix, np.einsum("ij,ij->i", matrix, matrix)


async def _nobody(asset_id: str) -> tuple[tuple[str, float], ...] | None:
    return None


async def shoots(
    pool: Sequence[str],
    neighbours: Neighbours,
    *,
    distance: float = DISTANCE,
    least: int,
    most: int = MOST,
) -> list[list[str]]:
    """Group one creator's loose pictures into shoots, in the order the pool was given.

    The pool's order is the proposal's (the file-name order, how somebody numbered the sitting).
    `least` is the Photo Sets' floor handed in, `most` the ceiling. An undescribed picture (None)
    is left for a later run, never grouped on no evidence. A group grows from its seed only, one
    lookup per shoot, so two members may be further apart than `distance`; each is close to the
    seed, which is the sitting.
    """
    waiting = list(pool)
    taken: set[str] = set()
    found: list[list[str]] = []
    for seed in waiting:
        if seed in taken:
            continue
        near = await neighbours(seed)
        if near is None:
            continue
        # The seed is claimed before the group is judged, so a picture that leads nothing is asked
        # about once; members are claimed only on acceptance, so a later seed can still take them.
        taken.add(seed)
        group = [seed]
        inside = {one for one in waiting if one not in taken}
        for asset_id, apart in near:
            if apart > distance:
                # Nearest first, so the first one outside the distance ends the answer.
                break
            if asset_id in inside:
                group.append(asset_id)
                inside.discard(asset_id)
        if len(group) < least or len(group) > most:
            continue
        taken.update(group)
        found.append(group)
    return found
