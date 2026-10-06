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

from sift.kernel.access import Viewer
from sift.kernel.changes import About, mark_of
from sift.kernel.log import get_logger
from sift.kernel.memo import MarkedMemo
from sift.slices.semantic.service import SemanticService
from sift.slices.semantic.store import index_writes

log = get_logger(__name__)

#: How many neighbours to ask for: more than a page, as the other filters run after.
CANDIDATES = 200

#: How many recent questions are remembered for each asker, vectors and rankings alike.
REMEMBERED = 16

#: How many askers' questions are remembered at once.
ASKERS = 16


def _settled() -> str | None:
    """The mark the Settings stand at (the switch and the model), or None while nothing listens."""
    return mark_of((About.SETTINGS,))


def _index_mark() -> str | None:
    """What a ranking rests on: Settings and the index's own writes, never another's change."""
    settled = _settled()
    return None if settled is None else f"{settled}:{index_writes()}"


class _NothingToRankBy(Exception):
    """The words could not be described, so there is nothing to rank by and nothing to keep."""


class _Kept:
    """One asker's recent questions: the words' meaning under a model, the closest under a mark."""

    __slots__ = ("ranked", "vectors")

    def __init__(self) -> None:
        self.vectors: OrderedDict[tuple[str, str | None], list[float]] = OrderedDict()
        self.ranked: MarkedMemo[tuple[tuple[str, float], ...]] = MarkedMemo(kept=REMEMBERED)


class SemanticSearch:
    """The search surface's view of the index: two questions, both answerable or not."""

    def __init__(self, service: SemanticService) -> None:
        self._service = service
        # Per asker, so a quick answer never tells one User what another searched for.
        self._kept: OrderedDict[str, _Kept] = OrderedDict()

    async def neighbours(
        self, text: str, *, limit: int = CANDIDATES, asker: Viewer | None
    ) -> tuple[tuple[str, float], ...] | None:
        """The files `asker` may see that look like what these words describe, closest first.

        Kept for `asker` alone until the mark or what they may see moves; with no asker every
        file is ranked and nothing is kept."""
        kept = self._kept_for(asker)
        try:
            if asker is None or kept is None:
                return await self._rank(text, limit, None, None)
            scope = (asker.role, asker.show_hidden, asker.cache_stamp)
            return await kept.ranked.get(
                (text, limit, scope), _index_mark(), lambda: self._rank(text, limit, kept, asker)
            )
        except _NothingToRankBy:
            return None

    def _kept_for(self, asker: Viewer | None) -> _Kept | None:
        if asker is None:
            return None
        kept = self._kept.get(asker.id)
        if kept is None:
            kept = self._kept[asker.id] = _Kept()
            while len(self._kept) > ASKERS:
                self._kept.popitem(last=False)
        else:
            self._kept.move_to_end(asker.id)
        return kept

    async def _rank(
        self, text: str, limit: int, kept: _Kept | None, asker: Viewer | None
    ) -> tuple[tuple[str, float], ...]:
        vector = await self._vector(text, kept)
        found = await self._service.nearest(vector, limit=limit, asker=asker)
        return tuple((neighbour.asset_id, neighbour.distance) for neighbour in found)

    async def _vector(self, text: str, kept: _Kept | None) -> list[float]:
        # Under Settings' mark: another model's numbers for the same words compare as noise.
        words = (text, _settled())
        if kept is not None:
            held = kept.vectors.get(words)
            if held is not None:
                kept.vectors.move_to_end(words)
                return held
        vector = await self._service.describe_query(text)
        if vector is None:
            # Not kept: the model may not be ready yet, and the next ask should find out.
            raise _NothingToRankBy
        if kept is not None:
            kept.vectors[words] = vector
            while len(kept.vectors) > REMEMBERED:
                kept.vectors.popitem(last=False)
        return vector

    async def can_answer(self) -> bool:
        """Whether asking this install about meaning can produce anything, at all, right now.

        For a caller with thousands of files to ask about rather than one. `like_asset` answers
        None for a file the index has not described AND for an install that cannot use the index,
        and one file's None cannot tell those apart, so a pass would have no way to find out that
        every answer it was about to ask for would be None except by asking for all of them.

        False where the machine cannot hold the index or the model in use has described nothing.
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
        self, asset_id: str, *, limit: int = CANDIDATES, asker: Viewer | None
    ) -> tuple[tuple[str, float], ...]:
        """Files similar to this one, closest first, by whichever way can answer, ranked among
        what `asker` may see.

        The strip under a file asks the service the same question (`SemanticService.similar_to`),
        so a wall filtered by `like:` holds what that strip draws, and more of it.
        """
        return (await self._service.similar_to(asset_id, limit=limit, asker=asker)).neighbours

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
