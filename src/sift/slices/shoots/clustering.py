# SPDX-License-Identifier: AGPL-3.0-or-later
"""Greedy grouping of one creator's loose pictures into shoots, seeded in a fixed order."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence

import numpy as np

#: Straight-line distance between unit descriptions, set by the size of the largest group rather
#: than folder purity, which holds well past it.
DISTANCE = 0.30

#: The floor is the Photo Sets' rule, handed in by the composition root with no default here.

#: The distance cannot promise a ceiling; a larger group is dropped, never trimmed.
MOST = 60

#: Well above `MOST`: other creators', filed and video neighbours are filtered out afterwards.
REACH = 400

#: What one picture's neighbours look like: the file and how far it sat, closest first.
Neighbours = Callable[[str], Awaitable[tuple[tuple[str, float], ...] | None]]


async def among(
    pool: Sequence[str], vectors: Mapping[str, Sequence[float]], *, within: float = DISTANCE
) -> Neighbours:
    """Each picture's neighbours within the pool, from one matrix in memory, not the index."""
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
    """The pool's numbers as one matrix in `ids` order, and each row's squared length."""
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
    """Group loose pictures into shoots in pool order, each grown from its seed alone."""
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
                break
            if asset_id in inside:
                group.append(asset_id)
                inside.discard(asset_id)
        if len(group) < least or len(group) > most:
            continue
        taken.update(group)
        found.append(group)
    return found
