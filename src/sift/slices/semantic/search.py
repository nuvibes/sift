# SPDX-License-Identifier: AGPL-3.0-or-later
"""Answering "which files look like this" for a surface that must not know how.

The search box and the vector index are different features, and a feature may not import another.
So this is the shape the search surface depends on, filled in here, and handed over when the
application is assembled.

**What crosses the boundary is an answer, never a question.** A list of files and how far each sat
from what was asked, closest first. The read that decides who may see what then orders by that
list, without ever knowing there is a vector index, which is what keeps a pre-1.0 dependency
swappable and keeps the permission rules in one place.

**None means "not right now", and it is an ordinary answer.** Switched off, models not obtained, a
machine that cannot hold the index at all: in every one of those the search box falls back to the
ordinary order rather than showing somebody an error about a feature they may not know exists. An
empty list means something different (the question was asked and nothing came near) and the
difference matters, because one is a fallback and the other is a result.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Sequence

from sift.kernel.changes import current_mark
from sift.kernel.log import get_logger
from sift.kernel.memo import MarkedMemo
from sift.slices.semantic.service import SemanticService

log = get_logger(__name__)

#: How many neighbours to ask the index for, however small the page is.
#:
#: The lookup runs before any permission rule does, and several of what it returns will be filtered
#: out afterwards: a file this user may not see, a file in a shut vault. Asking for exactly one
#: page therefore hands back less than a page. Two hundred is generous against any page the app
#: offers and is still a single pass over the index, which is the cheap half of this feature.
CANDIDATES = 200

#: How many recent questions are remembered, vectors and rankings alike.
REMEMBERED = 32


class _NothingToRankBy(Exception):
    """The words could not be described, so there is nothing to rank by and nothing to keep."""


class SemanticSearch:
    """The search surface's view of the index: two questions, both answerable or not."""

    def __init__(self, service: SemanticService) -> None:
        self._service = service
        # What the words mean is a property of the model alone, so a text's vector is kept as
        # long as this process lives, bounded. What is CLOSEST to it is a property of the index,
        # so the ranking is kept under the change bus's mark and thrown away when anything moves.
        # The grid asks the same question several times per search (the first page, its
        # top-up, the next page with a deeper reach), and each would otherwise pay the model and
        # the whole-index scan again.
        self._vectors: OrderedDict[str, list[float]] = OrderedDict()
        self._ranked: MarkedMemo[tuple[tuple[str, float], ...]] = MarkedMemo(kept=REMEMBERED)

    async def neighbours(
        self, text: str, *, limit: int = CANDIDATES
    ) -> tuple[tuple[str, float], ...] | None:
        """Files that look like what these words describe, closest first."""
        try:
            return await self._ranked.get(
                (text, limit), current_mark(), lambda: self._rank(text, limit)
            )
        except _NothingToRankBy:
            return None

    async def _rank(self, text: str, limit: int) -> tuple[tuple[str, float], ...]:
        vector = await self._vector(text)
        found = await self._service.nearest(vector, limit=limit)
        return tuple((neighbour.asset_id, neighbour.distance) for neighbour in found)

    async def _vector(self, text: str) -> list[float]:
        held = self._vectors.get(text)
        if held is not None:
            self._vectors.move_to_end(text)
            return held
        vector = await self._service.describe_query(text)
        if vector is None:
            # Nothing is kept: the model may simply not be ready yet, and the next asker should
            # get to find out rather than inherit this one's answer.
            raise _NothingToRankBy
        self._vectors[text] = vector
        while len(self._vectors) > REMEMBERED:
            self._vectors.popitem(last=False)
        return vector

    async def can_answer(self) -> bool:
        """Whether asking this install about meaning can produce anything, at all, right now.

        For a caller with thousands of files to ask about rather than one. `like_asset` answers
        None for a file the index has not described AND for an install that cannot use the index,
        and one file's None cannot tell those apart, so a pass would have no way to find out that
        every answer it was about to ask for would be None except by asking for all of them.

        Two conditions and they are different failures. The machine may not be able to hold the
        index at all, which no amount of describing will change; or it can, and nothing has been
        described by the model in use: a new library, the feature switched on this minute, or a
        model changed under an index full of the previous one's numbers. Both mean the same thing
        to a caller: there is nothing here to compare against.
        """
        readiness = await self._service.readiness()
        if not readiness.supported:
            return False
        return await self._service.describes_anything()

    async def describe_many(self, asset_ids: Sequence[str]) -> dict[str, list[float]] | None:
        """What each of these files looks like, for a caller comparing them among themselves.

        None where this install cannot use the index, as `like_asset` answers; a file the model in
        use has not described is simply absent. See `SemanticSeam.describe_many`.
        """
        readiness = await self._service.readiness()
        if not readiness.supported:
            return None
        return await self._service.describes_many(asset_ids)

    async def lookalikes(
        self, asset_id: str, *, limit: int = CANDIDATES
    ) -> tuple[tuple[str, float], ...]:
        """Files similar to this one, closest first, by whichever way can answer.

        The strip under a file asks the service the same question (`SemanticService.similar_to`),
        so a wall filtered by `like:` holds what that strip draws, and more of it.
        """
        return (await self._service.similar_to(asset_id, limit=limit)).neighbours

    async def like_asset(
        self, asset_id: str, *, limit: int = CANDIDATES
    ) -> tuple[tuple[str, float], ...] | None:
        """Files that look like this one, closest first, and never including itself.

        Answers None when this install cannot use the index at all, and also when this particular
        file has not been described yet, which is not a failure but a fact about how far the
        background pass has got. The caller falls back to the cheaper way of answering the same
        question, which needs no model and no index.
        """
        readiness = await self._service.readiness()
        if not readiness.supported:
            return None
        vector = await self._service.describes(asset_id)
        if not vector:
            return None
        found = await self._service.nearest(vector, limit=limit)
        # Every file is nearest to itself, and "this file is like this file" is not an answer.
        return tuple(
            (neighbour.asset_id, neighbour.distance)
            for neighbour in found
            if neighbour.asset_id != asset_id
        )
