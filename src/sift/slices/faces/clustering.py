# SPDX-License-Identifier: AGPL-3.0-or-later
"""Grouping faces that look alike, without being told who anybody is.

Set to split rather than merge: two piles of one person is a merge away from right, one pile of
two people files somebody under a stranger. Groups join on average likeness. Islands of faces
linked by a close pair are grouped apart, and a merged group's likeness is a weighted average,
which gives the plain version's answer far faster.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

import numpy as np

from sift.kernel.log import get_logger
from sift.slices.faces import tuning
from sift.slices.faces.models import Vector

log = get_logger(__name__)


#: Likeness values one block of the pair search holds, which bounds its memory (about 16 MB).
_BLOCK_VALUES = 4_000_000

#: An island this large means something matches too much, usually a bad crop, so it is logged.
_LARGE_ISLAND = 4_000


def pairs_above(vectors: Sequence[Vector], *, floor: float) -> Iterator[tuple[int, int]]:
    """Every pair of descriptions at least this alike, as positions into `vectors`, low first.

    The swap point for an approximate index, should the library outgrow comparing everything.
    """
    if not vectors:
        return
    matrix = np.asarray(vectors, dtype=np.float32)
    count = len(matrix)
    band = max(1, _BLOCK_VALUES // max(count, 1))
    for start in range(0, count, band):
        stop = min(start + band, count)
        block = matrix[start:stop] @ matrix.T
        for offset, first in enumerate(range(start, stop)):
            ahead = block[offset, first + 1 :]
            for second in np.nonzero(ahead >= floor)[0]:
                yield first, first + 1 + int(second)


def _islands(count: int, pairs: Iterator[tuple[int, int]]) -> list[list[int]]:
    """The faces linked by at least one close pair, as islands in ascending order."""
    home = list(range(count))

    def root(index: int) -> int:
        while home[index] != index:
            home[index] = home[home[index]]
            index = home[index]
        return index

    for first, second in pairs:
        left, right = root(first), root(second)
        if left != right:
            home[max(left, right)] = min(left, right)

    gathered: dict[int, list[int]] = {}
    for index in range(count):
        gathered.setdefault(root(index), []).append(index)
    return [gathered[key] for key in sorted(gathered)]


def _merge(likeness: np.ndarray, *, join_above: float, most: int | None) -> list[list[int]]:
    """Join the closest pair of groups until none are close enough, over one block of likenesses.

    A merge only lowers a likeness, so only groups pointing at the merged pair look again; ties go
    to the lowest positions, as the plain version's did.
    """
    count = len(likeness)
    groups: list[list[int]] = [[index] for index in range(count)]
    if count < 2:
        return groups

    # Held wider than what came in: repeated averaging drifts, and candidates can be a hair apart.
    scores = np.array(likeness, dtype=np.float64, copy=True)
    np.fill_diagonal(scores, -np.inf)
    sizes = np.ones(count, dtype=np.float64)
    alive = np.ones(count, dtype=bool)
    living = count

    closest = np.argmax(scores, axis=1)
    strength = scores[np.arange(count), closest]

    def look_again(at: int) -> None:
        closest[at] = int(np.argmax(scores[at]))
        strength[at] = scores[at, closest[at]]

    while living > 1:
        ranked = np.where(alive, strength, -np.inf)
        first = int(np.argmax(ranked))
        best = float(ranked[first])
        # The lower position is the one that survives.
        found = int(closest[first])
        first, second = min(first, found), max(first, found)

        if best < join_above and (most is None or living <= most):
            break

        kept, gone = sizes[first], sizes[second]
        blended = (kept * scores[first] + gone * scores[second]) / (kept + gone)
        scores[first] = blended
        scores[:, first] = blended
        scores[first, first] = -np.inf
        scores[first, second] = -np.inf
        scores[second, first] = -np.inf

        groups[first] = groups[first] + groups[second]
        sizes[first] = kept + gone
        alive[second] = False
        living -= 1
        scores[second, :] = -np.inf
        scores[:, second] = -np.inf

        look_again(first)
        for other in np.nonzero(alive)[0]:
            index = int(other)
            if index != first and closest[index] in (first, second):
                look_again(index)

    return [groups[index] for index in range(count) if alive[index]]


def agglomerate(
    vectors: Sequence[Vector], *, join_above: float, most: int | None = None
) -> list[list[int]]:
    """Group descriptions by likeness, returning each group's member positions.

    `most` caps the count by joining below the bar, which can cross islands, so that route compares
    everything; it is only given one person's references.
    """
    if not vectors:
        return []
    matrix = np.asarray(vectors, dtype=np.float32)

    if most is not None:
        return _merge(matrix @ matrix.T, join_above=join_above, most=most)

    groups: list[list[int]] = []
    for island in _islands(len(matrix), pairs_above(vectors, floor=join_above)):
        if len(island) == 1:
            groups.append(list(island))
            continue
        if len(island) >= _LARGE_ISLAND:
            log.info("faces.cluster.large_island", faces=len(island))
        members = np.asarray(island, dtype=np.intp)
        block = matrix[members]
        for found in _merge(block @ block.T, join_above=join_above, most=None):
            groups.append([island[position] for position in found])

    groups.sort(key=min)
    return groups


def pile_up(vectors: Sequence[Vector], *, join_above: float = tuning.PILE_JOIN) -> list[list[int]]:
    """Group unidentified faces into piles, splitting rather than merging when unsure."""
    return agglomerate(vectors, join_above=join_above)


def nearest_pile(
    vector: Vector, centroids: Sequence[Vector], *, join_above: float = tuning.PILE_JOIN
) -> int | None:
    """Which existing pile a new face belongs to, if any."""
    if not centroids:
        return None
    matrix = np.asarray(centroids, dtype=np.float32)
    scores = matrix @ np.asarray(vector, dtype=np.float32)
    best = int(scores.argmax())
    return best if float(scores[best]) >= join_above else None


def nearest_piles(
    vectors: Sequence[Vector], centroids: Sequence[Vector], *, join_above: float = tuning.PILE_JOIN
) -> list[int | None]:
    """`nearest_pile` for a batch of new faces in one multiply."""
    if not vectors:
        return []
    if not centroids:
        return [None] * len(vectors)
    scores = np.asarray(vectors, dtype=np.float32) @ np.asarray(centroids, dtype=np.float32).T
    best = scores.argmax(axis=1)
    return [
        int(index) if float(scores[row, index]) >= join_above else None
        for row, index in enumerate(best)
    ]


def carry_identities(
    previous: Sequence[Vector], current: Sequence[Vector], *, join_above: float = tuning.PILE_JOIN
) -> dict[int, int]:
    """Which old pile each rebuilt pile is, by its middle: current -> previous, best pairs first,
    each side used once, so a rebuild keeps the screen's piles."""
    if not previous or not current:
        return {}
    scores = np.asarray(current, dtype=np.float32) @ np.asarray(previous, dtype=np.float32).T
    order = np.argsort(scores, axis=None)[::-1]
    carried: dict[int, int] = {}
    used: set[int] = set()
    # A greedy matching fills every place first, so the loop leaves by a break.
    for flat in order:  # pragma: no branch (exits by break, see above)
        row, column = divmod(int(flat), len(previous))
        if float(scores[row, column]) < join_above:
            break
        if row in carried or column in used:
            continue
        carried[row] = column
        used.add(column)
        if len(carried) == min(len(previous), len(current)):
            break
    return carried


def worth_showing(size: int) -> bool:
    """Whether a pile is worth putting in front of somebody: every one (`MIN_PILE_SIZE`)."""
    return size >= tuning.MIN_PILE_SIZE
