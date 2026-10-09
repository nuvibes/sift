# SPDX-License-Identifier: AGPL-3.0-or-later
"""The review list under Faces: disagreements, proposals and groups, ranked and paged as one."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass

from sift.kernel.access import Viewer
from sift.kernel.access.repository import MAX_PAGE_SIZE
from sift.kernel.log import get_logger
from sift.slices.faces import tuning
from sift.slices.faces.models import PileStatus, ToCheckKind, ToCheckShow
from sift.slices.faces.search import kept, searched_page
from sift.slices.faces.service_may_be import MayBeMixin, ToCheckView
from sift.slices.faces.service_visibility import Sighting
from sift.slices.faces.store import FiledFace

log = get_logger(__name__)

_WINDOWS_AT_MOST = 4


@dataclass(frozen=True, slots=True)
class GroupView:
    """A pile of faces as this viewer may see it; `size` is the visible count, not the stored."""

    id: str
    status: PileStatus
    size: int
    faces: tuple[Sighting, ...] | list[Sighting]


@dataclass(frozen=True, slots=True)
class FiledInReach:
    """The disagreements one viewer may be told about, before any is drawn."""

    rows: list[FiledFace]
    shown: dict[str, bool]
    names: dict[str, str]


def _tiers(kind: ToCheckKind | Collection[ToCheckKind] | None) -> frozenset[ToCheckKind] | None:
    """The tiers a read asks for, or None for all; a `StrEnum` is checked first: it is a string."""
    if kind is None:
        return None
    if isinstance(kind, ToCheckKind):
        return frozenset({kind})
    return frozenset(kind) or None


def _searched_tiers(
    kind: ToCheckKind | Collection[ToCheckKind] | None, who: frozenset[str] | None
) -> frozenset[ToCheckKind] | None:
    """`_tiers` under a search: a group names nobody, so a searched list reads no group tier."""
    wanted = _tiers(kind)
    if who is None:
        return wanted
    return (frozenset(ToCheckKind) if wanted is None else wanted) - {ToCheckKind.GROUP}


def _as_group(group: GroupView) -> ToCheckView:
    """One pile as an item of the review list."""
    return ToCheckView(
        kind=ToCheckKind.GROUP,
        id=group.id,
        size=group.size,
        faces=group.faces,
        status=group.status,
    )


class ReviewMixin(MayBeMixin):
    """Reading the review list, and the groups waiting for a name."""

    async def piles(
        self,
        viewer: Viewer,
        status: PileStatus,
        *,
        limit: int = 50,
        page_size: int | None = None,
        offset: int = 0,
        floor: int = 1,
        ceiling: int = 0,
    ) -> tuple[list[GroupView], int]:
        """One page of unidentified groups as this viewer may see them, and their total.

        Scoped before paging, so the page and the total describe one set.
        """
        wanted = page_size or MAX_PAGE_SIZE
        out: list[GroupView] = []
        start, total, unshown = max(0, offset), 0, 0
        # A pile showing no face leaves a gap filled from the next window, a few reads at most.
        for read in range(_WINDOWS_AT_MOST):
            window, counted = await self._repository.waiting_piles(
                viewer,
                status.value,
                limit=wanted - len(out),
                offset=start,
                floor=floor,
                ceiling=ceiling,
            )
            total = counted if read == 0 else total
            drawn = await self._drawn_piles(viewer, status, window, limit=limit)
            out.extend(drawn)
            start += len(window)
            unshown += len(window) - len(drawn)
            if len(drawn) == len(window) or not window:
                break
        if unshown:
            log.warning("faces.piles_unshown", piles=unshown, offset=offset)
        return out, total

    async def _drawn_piles(
        self, viewer: Viewer, status: PileStatus, window: Sequence[tuple[str, int]], *, limit: int
    ) -> list[GroupView]:
        """One window's piles as this viewer may see them; a pile showing no face is left out."""
        held = await self._store.tracks_in_piles([pile_id for pile_id, _visible in window])
        shown = await self._shown_of(
            viewer, [track.asset_id for tracks in held.values() for track in tracks]
        )

        moments = await self._store.picture_moments(
            [
                track.id
                for tracks in held.values()
                for track in [one for one in tracks if one.asset_id in shown][:limit]
            ]
        )
        out: list[GroupView] = []
        for pile_id, _visible in window:
            faces = [track for track in held.get(pile_id, []) if track.asset_id in shown]
            if not faces:
                continue
            out.append(
                GroupView(
                    id=pile_id,
                    status=status,
                    size=len(faces),
                    faces=[
                        await self._sighting(
                            viewer, track, moments=moments, locked=shown[track.asset_id]
                        )
                        for track in faces[:limit]
                    ],
                )
            )
        return out

    async def position_of_pile(
        self, viewer: Viewer, status: PileStatus, pile_id: str
    ) -> int | None:
        """Where a pile sits on the groups wall, or None whether hidden or absent."""
        return await self._repository.waiting_pile_position(viewer, status.value, pile_id)

    async def pile(
        self,
        viewer: Viewer,
        pile_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[GroupView, int] | None:
        """One pile with its faces a page at a time, or None if absent or wholly hidden."""
        await self._require_enabled()
        row = await self._store.pile_of(pile_id)
        if row is None:
            return None
        tracks = await self._store.pile_tracks(pile_id)
        shown = await self._shown_of(viewer, [track.asset_id for track in tracks])
        faces = [track for track in tracks if track.asset_id in shown]
        if not faces:
            return None
        window = faces[max(0, offset) : max(0, offset) + limit]
        moments = await self._store.picture_moments([track.id for track in window])
        view = GroupView(
            id=pile_id,
            status=PileStatus(str(row["status"])),
            size=len(faces),
            faces=[
                await self._sighting(viewer, track, moments=moments, locked=shown[track.asset_id])
                for track in window
            ],
        )
        return view, len(faces)

    async def pile_track_ids(self, viewer: Viewer, pile_id: str) -> list[str] | None:
        """Every face in one pile this viewer may see, by id, or None as `pile` answers."""
        await self._require_enabled()
        if await self._store.pile_of(pile_id) is None:
            return None
        tracks = await self._store.pile_tracks(pile_id)
        shown = await self._shown_of(viewer, [track.asset_id for track in tracks])
        ids = [track.id for track in tracks if track.asset_id in shown]
        return ids or None

    async def position_of_face(self, viewer: Viewer, pile_id: str, track_id: str) -> int | None:
        """Where one face sits in a pile's own page, or None as `pile` answers."""
        await self._require_enabled()
        if await self._store.pile_of(pile_id) is None:
            return None
        tracks = await self._store.pile_tracks(pile_id)
        shown = await self._shown_of(viewer, [track.asset_id for track in tracks])
        faces = [track for track in tracks if track.asset_id in shown]
        for index, track in enumerate(faces):
            if track.id == track_id:
                return index
        return None

    async def look_alikes(
        self,
        viewer: Viewer,
        *,
        limit: int = 24,
        offset: int = 0,
        faces_per_card: int = 12,
        who: frozenset[str] | None = None,
    ) -> tuple[list[tuple[str, str, int, float | None, list[Sighting]]], int]:
        """Who Sift proposed somebody for, one row per person, surest person first.

        A withheld person is absent rather than nameless: a card asks to agree to a name.
        """
        if not await self.enabled():
            return [], 0
        tracks = await self._store.proposed(most=tuning.SUGGESTIONS_AT_MOST)
        shown = await self._shown_of(viewer, [track.asset_id for track in tracks])
        names = await self._names_of(viewer, tracks)
        moments = await self._store.picture_moments(
            [track.id for track in tracks if track.asset_id in shown]
        )
        gathered: dict[str, list[Sighting]] = {}
        for track in tracks:
            if track.asset_id not in shown:
                continue
            sighting = await self._sighting(
                viewer, track, moments=moments, names=names, locked=shown[track.asset_id]
            )
            # A withheld person comes back with no id, which drops the card.
            if sighting.person_id is None or sighting.person_name is None:
                continue
            gathered.setdefault(sighting.person_id, []).append(sighting)

        # `proposed` ordered the faces surest first, so nothing is sorted again here.
        rows = [
            (person_id, str(faces[0].person_name), len(faces), faces[0].confidence, faces)
            for person_id, faces in gathered.items()
            if kept(who, person_id)
        ]
        begin = max(0, offset)
        return [
            (person_id, name, size, best, faces[:faces_per_card])
            for person_id, name, size, best, faces in rows[begin : begin + limit]
        ], len(rows)

    async def filed_faces_that_do_not_match(
        self, viewer: Viewer, *, limit: int, offset: int, who: frozenset[str] | None = None
    ) -> tuple[list[ToCheckView], int]:
        """A page of files named for a person their one face does not match."""
        reach = await self._filed_in_reach(viewer)
        rows, total = searched_page(
            reach.rows, who, lambda one: one.person_id, offset=offset, limit=limit
        )
        return await self._as_disagreements(viewer, reach, rows), total

    async def _filed_in_reach(self, viewer: Viewer) -> FiledInReach:
        """Every disagreement this viewer may be told about, in the store's order, undrawn."""
        if not await self.enabled():
            return FiledInReach(rows=[], shown={}, names={})
        configured = await self.configuration()
        filed = await self._store.filed_but_unrecognised(
            configured.recognizer, most=tuning.FILED_FACES_AT_MOST
        )
        if not filed:
            return FiledInReach(rows=[], shown={}, names={})
        shown = await self._shown_of(viewer, [one.track.asset_id for one in filed])
        named = await self._repository.visible_people(
            viewer, sorted({one.person_id for one in filed})
        )
        return FiledInReach(
            rows=[one for one in filed if one.track.asset_id in shown and one.person_id in named],
            shown=shown,
            names={person_id: person.name for person_id, person in named.items()},
        )

    async def _as_disagreements(
        self, viewer: Viewer, reach: FiledInReach, rows: Sequence[FiledFace]
    ) -> list[ToCheckView]:
        """Some of the rows `_filed_in_reach` answered, drawn as the tab's rows."""
        moments = await self._store.picture_moments([one.track.id for one in rows])
        names = await self._names_of(viewer, [one.track for one in rows])
        drawn: list[ToCheckView] = []
        for one in rows:
            sighting = await self._sighting(
                viewer,
                one.track,
                moments=moments,
                names=names,
                locked=reach.shown[one.track.asset_id],
            )
            drawn.append(
                ToCheckView(
                    kind=ToCheckKind.MISMATCH,
                    id=one.track.asset_id,
                    size=1,
                    faces=[sighting],
                    person_name=reach.names[one.person_id],
                    person_id=one.person_id,
                    source=one.source,
                )
            )
        return drawn

    async def to_check(
        self,
        viewer: Viewer,
        *,
        show: ToCheckShow = ToCheckShow.WAITING,
        kind: ToCheckKind | Collection[ToCheckKind] | None = None,
        limit: int = 24,
        offset: int = 0,
        faces_per_card: int = 12,
        who: frozenset[str] | None = None,
    ) -> tuple[list[ToCheckView], int, int]:
        """What is left to check, tiers paged as one list: `(page, total, small groups)`."""
        if not await self.enabled():
            return [], 0, 0
        begin = max(0, offset)
        if show is not ToCheckShow.WAITING:
            return await self._held_back(
                viewer, show, limit=limit, offset=begin, faces_per_card=faces_per_card, who=who
            )

        # A tier not asked for is not read and its total stays zero, so the offsets still hold.
        wanted = _searched_tiers(kind, who)
        page: list[ToCheckView] = []
        mismatch_total = 0
        people_total = 0
        may_be_total = 0
        group_total = 0
        small_groups = 0
        if wanted is None or ToCheckKind.MISMATCH in wanted:
            mismatched, mismatch_total = await self.filed_faces_that_do_not_match(
                viewer, limit=limit, offset=begin, who=who
            )
            page.extend(mismatched)

        # A page filled by the tier above still reads the next one: the total adds every tier.
        room = max(0, limit - len(page))
        if wanted is None or ToCheckKind.PERSON in wanted:
            rows, people_total = await self.look_alikes(
                viewer,
                limit=max(1, room),
                offset=max(0, begin - mismatch_total),
                faces_per_card=faces_per_card,
                who=who,
            )
            if room > 0:
                page.extend(
                    ToCheckView(
                        kind=ToCheckKind.PERSON,
                        id=person_id,
                        size=size,
                        faces=faces,
                        person_name=name,
                        best=best,
                    )
                    for person_id, name, size, best, faces in rows[:room]
                )
        room = max(0, limit - len(page))
        if wanted is None or ToCheckKind.MAY_BE in wanted:
            cards, may_be_total = await self.groups_that_may_be(
                viewer,
                limit=max(1, room),
                offset=max(0, begin - mismatch_total - people_total),
                faces_per_group=min(faces_per_card, tuning.FACES_PER_GROUP),
                who=who,
            )
            if room > 0:
                page.extend(cards[:room])
        room = max(0, limit - len(page))
        if wanted is None or ToCheckKind.GROUP in wanted:
            groups, group_total, small_groups = await self._group_tier(
                viewer,
                room=room,
                offset=max(0, begin - mismatch_total - people_total - may_be_total),
                faces_per_card=faces_per_card,
            )
            page.extend(groups)
        return page, mismatch_total + people_total + may_be_total + group_total, small_groups

    async def _group_tier(
        self, viewer: Viewer, *, room: int, offset: int, faces_per_card: int
    ) -> tuple[list[ToCheckView], int, int]:
        """`to_check`'s groups tier: the rows that fit, its total, the groups under the floor."""
        groups, group_total = await self.piles(
            viewer,
            PileStatus.OPEN,
            limit=faces_per_card,
            page_size=max(1, room),
            offset=offset,
            floor=tuning.STRANGER_FLOOR,
        )
        # The count under the floor is the groups tab's alone.
        small_groups = await self._repository.waiting_pile_count(
            viewer, PileStatus.OPEN.value, ceiling=tuning.STRANGER_FLOOR - 1
        )
        return [_as_group(group) for group in groups[:room]], group_total, small_groups

    async def _held_back(
        self,
        viewer: Viewer,
        show: ToCheckShow,
        *,
        limit: int,
        offset: int,
        faces_per_card: int,
        who: frozenset[str] | None,
    ) -> tuple[list[ToCheckView], int, int]:
        """`to_check` for the small or set-aside groups; a search finds none of them."""
        if who is not None:
            return [], 0, 0
        small = show is ToCheckShow.SMALL
        groups, total = await self.piles(
            viewer,
            PileStatus.OPEN if small else PileStatus.IGNORED,
            limit=faces_per_card,
            page_size=limit,
            offset=offset,
            # The list's own bound turned round, so the foot line and what it opens agree.
            ceiling=tuning.STRANGER_FLOOR - 1 if small else 0,
        )
        return [_as_group(group) for group in groups], total, total if small else 0

    async def _held_back_position(
        self, viewer: Viewer, item_id: str, show: ToCheckShow, *, who: frozenset[str] | None
    ) -> int | None:
        """`position_in_to_check` on the lists `_held_back` reads, with the same bounds."""
        if who is not None:
            return None
        small = show is ToCheckShow.SMALL
        return await self._repository.waiting_pile_position(
            viewer,
            (PileStatus.OPEN if small else PileStatus.IGNORED).value,
            item_id,
            ceiling=tuning.STRANGER_FLOOR - 1 if small else 0,
        )

    async def position_in_to_check(
        self,
        viewer: Viewer,
        item_id: str,
        *,
        show: ToCheckShow = ToCheckShow.WAITING,
        kind: ToCheckKind | Collection[ToCheckKind] | None = None,
        who: frozenset[str] | None = None,
    ) -> int | None:
        """Where one row sits in `to_check`'s list, or None.

        Each tier is ranked by reading it whole, so the order is the page's own. None for a row
        absent, answered, hidden or under the floor alike.
        """
        if not await self.enabled():
            return None
        if show is not ToCheckShow.WAITING:
            return await self._held_back_position(viewer, item_id, show, who=who)
        wanted = _searched_tiers(kind, who)
        if wanted == frozenset({ToCheckKind.GROUP}):
            return await self._repository.waiting_pile_position(
                viewer, PileStatus.OPEN.value, item_id, floor=tuning.STRANGER_FLOOR
            )
        found, above = await self._position_above_groups(viewer, item_id, wanted, who=who)
        if found is not None:
            return found
        if wanted is not None and ToCheckKind.GROUP not in wanted:
            return None
        at = await self._repository.waiting_pile_position(
            viewer, PileStatus.OPEN.value, item_id, floor=tuning.STRANGER_FLOOR
        )
        return None if at is None else above + at

    async def _position_above_groups(
        self,
        viewer: Viewer,
        item_id: str,
        wanted: frozenset[ToCheckKind] | None,
        *,
        who: frozenset[str] | None,
    ) -> tuple[int | None, int]:
        """`position_in_to_check` above the groups tier: the position or None, and rows above."""
        # A PERSON and a MAY_BE card share the person's id; the first tier holding it wins.
        above = 0
        if wanted is None or ToCheckKind.MISMATCH in wanted:
            mismatched, mismatch_total = await self.filed_faces_that_do_not_match(
                viewer, limit=tuning.FILED_FACES_AT_MOST, offset=0, who=who
            )
            # The first row naming the file: a file filed under two people is two rows.
            for index, row in enumerate(mismatched):
                if row.id == item_id:
                    return index, above
            above += mismatch_total
        if wanted is None or ToCheckKind.PERSON in wanted:
            proposed, people_total = await self.look_alikes(
                viewer, limit=tuning.SUGGESTIONS_AT_MOST, offset=0, faces_per_card=1, who=who
            )
            for index, (person_id, *_rest) in enumerate(proposed):
                if person_id == item_id:
                    return above + index, above
            above += people_total
        if wanted is None or ToCheckKind.MAY_BE in wanted:
            cards, may_be_total = await self.groups_that_may_be(
                viewer, limit=tuning.SUGGESTIONS_AT_MOST, offset=0, faces_per_group=0, who=who
            )
            for index, card in enumerate(cards):
                if card.id == item_id:
                    return above + index, above
            above += may_be_total
        return None, above
