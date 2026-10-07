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
        """Pile up every appearance nobody has been attached to.

        Piles somebody set aside are left exactly as they are, and their faces are kept out of the
        grouping, or every re-group would resurrect what was ignored.

        Piles somebody built by merging or splitting get the same treatment, for the same reason
        one step over: re-clustering their faces would scatter a decision a person made back into
        whatever the arithmetic thinks, which is precisely what they overrode.

        **Two ways, and the ordinary one is the cheap one.** Re-clustering every unnamed face from
        scratch after every batch compares every pair, grows with the square of the library (about
        five seconds a job at ten thousand faces), and gives every pile a fresh identity, so a
        screen showing one loses it. Incrementally, the faces in no pile are compared against the
        piles that exist and join the nearest one
        that clears the bar; the rest are grouped among themselves into new piles. Existing piles
        keep their identity and everything in them. A batch against the piles is one small
        multiply.

        `full` groups every unnamed face from scratch, and is for the moments the piles
        themselves are in question: the end of a sweep, every description having been measured
        again by a different model, and the press of the button. Even then a rebuilt pile keeps
        the identity of the old pile whose middle it is nearest. See `carry_identities`.
        """
        await self._require_enabled()
        configured = await self.configuration()
        if not full:
            return await self._group_the_new(configured)
        # And the faces turned past the bar's angle, which wait on their own files to be named
        # or asked about: see `_group_the_new`.
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
        # Off the event loop, because Sift is one process with one loop and arithmetic that holds it
        # freezes every screen, every video and the live job feed for as long as it runs.
        #
        # `pile_up` takes the uncapped route through the clustering, which only joins groups that
        # clear the bar and splits into islands: a hundred milliseconds at a couple of thousand
        # faces, but about five seconds at ten thousand, growing with the square of the pool. That
        # is not nothing on a loop serving video. It runs from scratch only when asked to; the
        # ordinary pass is `_group_the_new`. See `GALLERY_SETTLE_SECONDS`.
        groups = await asyncio.to_thread(clustering.pile_up, vectors)
        made: list[tuple[Vector, list[str]]] = []
        for members in groups:
            # Every group has a member and every size shows now (`MIN_PILE_SIZE` is one); the
            # question is kept for the day a floor comes back.
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
        """The incremental grouping: place the faces in no pile, and touch nothing else.

        Returns how many piles were joined or made. A face joins the nearest existing pile
        that clears the bar; the faces that join none are grouped among themselves, which is a
        small clustering rather than a library-wide one. Piles emptied by naming are tidied
        away, as the full pass does.
        """
        piles = await self._store.open_piles_for_grouping()
        # **A face turned past the bar's angle is in no group.** It is kept to be named on its file
        # and asked about by a match, never to decide anything alone, and a group is a way of
        # deciding many together: one name given to a group names every face in it. A turned
        # face's nearest face of SOMEBODY ELSE clears the bar for joining about twice as often as a
        # square-on face's does, so it would also carry strangers in.
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
            # The same floor the full pass applies: a group too small to be worth showing is not
            # written, and its faces are offered to the next pass again.
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
