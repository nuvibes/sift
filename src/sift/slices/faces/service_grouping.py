# SPDX-License-Identifier: AGPL-3.0-or-later
"""Grouping the faces nobody has named into piles of look-alikes."""

from __future__ import annotations

import asyncio

from sift.kernel.log import get_logger
from sift.slices.faces import clustering, tracking
from sift.slices.faces.models import Vector
from sift.slices.faces.service_base import Configured, FaceServiceBase

log = get_logger(__name__)


class GroupingMixin(FaceServiceBase):
    """Building, and topping up, the piles of unnamed faces."""

    async def regroup(self, *, full: bool = False) -> int:
        """Pile up every appearance nobody has been attached to, leaving set-aside and hand-built
        piles alone.

        Incremental by default, so piles keep their identity; `full` re-clusters from scratch
        and keeps each rebuilt pile's nearest old identity (`carry_identities`).
        """
        await self._require_enabled()
        configured = await self.configuration()
        if not full:
            return await self._group_the_new(configured)
        # And the faces turned past the bar's angle, which are never grouped.
        kept = (
            await self._store.ignored_track_ids()
            | await self._store.by_hand_track_ids()
            | await self._store.turned_away(configured.bar.min_frontality)
        )
        pending = [
            (track_id, vector)
            for track_id, _, vector in await self._store.unattributed(configured.recognizer)
            if track_id not in kept
        ]
        if not pending:
            await self._store.replace_piles([])
            return 0

        vectors = [vector for _, vector in pending]
        previous = await self._store.open_piles_for_grouping()
        # Off the loop: from scratch this grows with the square of the pool.
        groups = await asyncio.to_thread(clustering.pile_up, vectors)
        made: list[tuple[Vector, list[str]]] = []
        for members in groups:
            # Every size shows now (`MIN_PILE_SIZE` is one).
            if not clustering.worth_showing(len(members)):  # pragma: no cover (floor is one)
                continue
            middle = tracking.centroid([vectors[index] for index in members])
            made.append((middle, [pending[index][0] for index in members]))
        carried = clustering.carry_identities(
            [middle for _, middle in previous], [middle for middle, _ in made]
        )
        await self._store.replace_piles(
            made, kept={position: previous[at][0] for position, at in carried.items()}
        )
        log.info("faces.regroup", piles=len(made), faces=len(pending), kept=len(carried))
        return len(made)

    async def _group_the_new(self, configured: Configured) -> int:
        """The incremental grouping: place the faces in no pile, and touch nothing else."""
        piles = await self._store.open_piles_for_grouping()
        # A turned face is in no group: one name for a group would name it too.
        turned = await self._store.turned_away(configured.bar.min_frontality)
        loose = [
            one for one in await self._store.unpiled(configured.recognizer) if one[0] not in turned
        ]
        if not loose:
            await self._store.drop_empty_piles()
            return 0
        vectors = [vector for _, _, vector in loose]
        centroids = [middle for _, middle in piles]
        placed = await asyncio.to_thread(clustering.nearest_piles, vectors, centroids)
        joined: dict[str, list[str]] = {}
        left: list[int] = []
        for position, at in enumerate(placed):
            if at is None:
                left.append(position)
            else:
                joined.setdefault(piles[at][0], []).append(loose[position][0])
        await self._store.add_to_piles(joined)
        made: list[str] = []
        if left:
            groups = await asyncio.to_thread(clustering.pile_up, [vectors[index] for index in left])
            # A group too small to show is not written, and its faces wait for the next pass.
            made = await self._store.add_piles(
                [
                    (
                        tracking.centroid([vectors[left[index]] for index in group]),
                        [loose[left[index]][0] for index in group],
                    )
                    for group in groups
                    if clustering.worth_showing(len(group))
                ]
            )
        await self._store.drop_empty_piles()
        log.info(
            "faces.regroup.incremental",
            placed=len(loose) - len(left),
            joined=len(joined),
            made=len(made),
        )
        return len(joined) + len(made)
