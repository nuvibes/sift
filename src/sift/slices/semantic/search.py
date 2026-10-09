# SPDX-License-Identifier: AGPL-3.0-or-later
"""Answering "which files look like this" for a search surface that must not know how."""

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

#: More than a page, as the other filters run after.
CANDIDATES = 200

REMEMBERED = 16

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
        """The files `asker` may see that look like what these words describe, closest first."""
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
        """Whether asking this install about meaning can produce anything at all right now."""
        readiness = await self._service.readiness()
        if not readiness.supported:
            return False
        return await self._service.describes_anything()

    async def describe_many(self, asset_ids: Sequence[str]) -> dict[str, list[float]] | None:
        """What each of these files looks like; None where the index cannot be used."""
        readiness = await self._service.readiness()
        if not readiness.supported:
            return None
        return await self._service.describes_many(asset_ids)

    async def lookalikes(
        self, asset_id: str, *, limit: int = CANDIDATES, asker: Viewer | None
    ) -> tuple[tuple[str, float], ...]:
        """Files similar to this one, closest first, ranked among what `asker` may see."""
        return (await self._service.similar_to(asset_id, limit=limit, asker=asker)).neighbours

    async def like_asset(
        self, asset_id: str, *, limit: int = CANDIDATES
    ) -> tuple[tuple[str, float], ...] | None:
        """Files that look like this one, closest first, never itself; None to fall back."""
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
