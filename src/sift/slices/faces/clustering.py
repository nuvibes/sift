# SPDX-License-Identifier: AGPL-3.0-or-later
"""Grouping faces that look alike, without being told who anybody is.

This is what turns a backlog into a manageable one. Matching against people you have already
described only ever finds people you have already described; everybody else stays an anonymous
face, and a library accumulates thousands of them. Grouping them the other way round (pile up
the faces that resemble each other, then ask *who is this* once per pile) turns forty sightings
of a stranger into a single question.

**The threshold is deliberately set to split too much rather than too little**, and that is the
whole of the tuning. At the level used here, roughly seven pairs in ten that really are the same
person end up in the same pile, so one person often arrives as two or three piles, while the rate
at which two *different* people are joined is under two in a hundred thousand. That trade is the
right way round and not close: two piles of one person is a merge away from being right, and one
pile of two people quietly attributes somebody's files to a stranger.

Groups are joined by their **average** likeness rather than their closest pair. One unusually
similar pair (two faces at the same angle in the same light) would otherwise pull two piles
together on the strength of a single coincidence, and that is exactly the failure this is tuned to
avoid.

## How it is arranged, and why

The obvious way to write this is to compare every pair of groups, merge the closest, and repeat.
The cost of that is roughly cubic in the number of faces: a few hundred faces take tens of seconds
of arithmetic, and at a few thousand it does not finish.

Two observations make it cheap without changing a single answer.

**A merge needs an edge.** Two groups join when the *average* likeness across them clears the bar.
An average never exceeds the largest thing in it, so a group pair that clears the bar must contain
at least one face pair that clears it too. Faces with no such pair between them can therefore never
end up together, however many merges happen in between, so the whole problem splits into islands
of faces linked by at least one close pair, and each island is grouped on its own. At a threshold
chosen to split, the islands are small and most faces are an island of one.

**A merged group's likeness is arithmetic, not a recount.** When two groups join, their average
likeness to a third is the weighted average of what the two already had. Nothing has to be
compared again, and because that value always lands between the two it replaced, no pair anywhere
can become *more* alike than it was, which is what makes it safe to remember each group's closest
neighbour and only revisit the ones the merge actually touched.

The result is the same groups, in the same order, with the same members: this is a faster route to
the previous answer and not a different answer. A test compares the two implementations directly.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

import numpy as np

from sift.kernel.log import get_logger
from sift.slices.faces import tuning
from sift.slices.faces.models import Vector

log = get_logger(__name__)


#: How many likeness values one block of the pair search holds, which is what bounds its memory.
#:
#: The search never builds the whole square. It takes a band of faces at a time and compares that
#: band against everything, so the largest array alive at once is this many floats (about 16 MB),
#: whatever the size of the library. Comparing 50,000 faces in one array would want 10 GB.
_BLOCK_VALUES = 4_000_000

#: An island of linked faces past which the grouping is worth a line in the log.
#:
#: Islands are small by construction: the threshold is set to split, so at this bar two different
#: people are joined about twice in a hundred thousand pairs. One large island therefore does not
#: mean a large library: it means something is matching far more than it should, and the usual
#: cause is a bad crop that resembles everything. Grouping one costs its size squared, so it is
#: worth being able to see in the log that it happened.
_LARGE_ISLAND = 4_000


def pairs_above(vectors: Sequence[Vector], *, floor: float) -> Iterator[tuple[int, int]]:
    """Every pair of descriptions at least this alike, as positions into `vectors`.

    **This is the swap point.** Answering "which of these are close to each other" by comparing
    everything with everything is right at the sizes Sift sees and wrong at some larger size, and
    an approximate index is the usual answer when that day comes. Everything above depends on this
    shape (pairs of positions, low index first) and on nothing else, so replacing what is
    behind it is a bounded job rather than a rewrite.

    An index is not used here: it answers "what is near this", and the expensive half of grouping needs the likeness of pairs
    that are *not* near, to work out what a merged group's average becomes. An index cannot answer
    that, so one would accelerate the cheap half and leave the rest where it is.

    Blocked rather than done in one array: the full square of a large library does not fit in
    memory, and it does not need to.
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
    """The faces linked by at least one close pair, gathered into islands.

    Every face is in exactly one, including the ones linked to nothing: an island of one is the
    ordinary case here and not an edge case. Returned smallest-position first, and each island's
    members in ascending order, so what comes out of the grouping is arranged the way it would have
    been had the whole library been done in one pass.
    """
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

    Positions are into `likeness`, which is square and symmetric. The closest pair is found from a
    remembered nearest neighbour per group rather than by comparing everything again: a merge can
    only ever *lower* a likeness, so the only groups whose closest neighbour can have changed are
    the ones that were pointing at the pair being merged.

    Ties go to the lowest pair of positions, which is what the straightforward version did by
    scanning in order, and keeping it means the two agree face for face rather than merely group
    for group.
    """
    count = len(likeness)
    groups: list[list[int]] = [[index] for index in range(count)]
    if count < 2:
        return groups

    # Held wider than the likenesses that came in. Averaging a group's likenesses repeatedly
    # accumulates error, and the two closest candidates at any moment can be a hair apart, so the
    # arithmetic that decides which of them wins is done with room to spare rather than at the
    # precision the descriptions happen to be stored in.
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
        # Ordered rather than tested. Whichever of the two the search happened to land on, the pair
        # is the same pair, and every step below reads the lower position as the one that survives,
        # so it is put that way round here instead of being checked for later.
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
    """Group descriptions by how alike they are, joining the closest pair until none are close
    enough.

    Returns the positions of the members of each group, so a caller can carry whatever it likes
    alongside the numbers.

    `most` caps how many groups come out, which is how the same function serves two very different
    callers: grouping a person's own reference faces wants a handful of groups whatever the
    threshold says, while piling up unknown faces wants as many as the threshold produces.

    The cap is why there are two routes through here. Without one, groups only ever join when they
    clear the bar, so the work splits into islands of linked faces and each is grouped alone. A cap
    forces joins *below* the bar to get the count down, and those can reach across islands, so
    that route compares everything. It is only ever handed one person's own references, which is
    tens of faces rather than a library of them.
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
    """Which existing pile a new face belongs to, if any.

    What keeps grouping cheap once a library has been through it once. Re-grouping everything from
    scratch every time a file is scanned would be quadratic in the size of the library; a new face
    is instead compared against the piles that already exist, and starts its own when it matches
    none of them.
    """
    if not centroids:
        return None
    matrix = np.asarray(centroids, dtype=np.float32)
    scores = matrix @ np.asarray(vector, dtype=np.float32)
    best = int(scores.argmax())
    return best if float(scores[best]) >= join_above else None


def nearest_piles(
    vectors: Sequence[Vector], centroids: Sequence[Vector], *, join_above: float = tuning.PILE_JOIN
) -> list[int | None]:
    """`nearest_pile` for a batch of new faces at once: one multiply rather than one per face.

    What an incremental grouping does with everything a batch of scans found. A library's worth
    of piles against a batch of faces is a small block, and the answer per face is the same one
    `nearest_pile` gives: a test says so.
    """
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
    """Which of the piles that existed each rebuilt pile is, by its middle: current -> previous.

    A full rebuild that minted a fresh identity for every pile would lose a screen's pile at every
    grouping pass: the same faces, under a name nothing on the screen still held. The middles of
    the old piles and the new are compared, and the closest pairs above the bar
    are paired off, best first, each side used once. A rebuilt pile that resembles no old one is
    new; an old pile nothing resembles is gone. Positions rather than ids, like everything here,
    so the caller carries whatever it likes alongside.
    """
    if not previous or not current:
        return {}
    scores = np.asarray(current, dtype=np.float32) @ np.asarray(previous, dtype=np.float32).T
    order = np.argsort(scores, axis=None)[::-1]
    carried: dict[int, int] = {}
    used: set[int] = set()
    # A full greedy matching always fills `min(len(previous), len(current))` places before the
    # scores run out, so the loop leaves by one of the two breaks and never by exhaustion.
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
    """Whether a pile is worth putting in front of somebody.

    Everything. See `MIN_PILE_SIZE`: withholding the piles of one would hide most of what a scan
    found, on the one screen whose job is to show what could not be placed.
    """
    return size >= tuning.MIN_PILE_SIZE
