# SPDX-License-Identifier: AGPL-3.0-or-later
"""The review list under Faces: the disagreements, the proposals, the groups that may be somebody
and the groups waiting for a name, ranked together and paged as one list.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass

from sift.kernel.access import Viewer
from sift.kernel.access.repository import MAX_PAGE_SIZE
from sift.slices.faces import tuning
from sift.slices.faces.models import PileStatus, ToCheckKind, ToCheckShow
from sift.slices.faces.search import kept, searched_page
from sift.slices.faces.service_may_be import MayBeMixin, ToCheckView
from sift.slices.faces.service_visibility import Sighting
from sift.slices.faces.store import FiledFace


@dataclass(frozen=True, slots=True)
class GroupView:
    """A pile of faces that resemble each other, counted as this viewer may see it.

    `size` is the visible count, not the stored one. The two differ whenever a viewer may see some
    of a pile's files and not others, and reporting the stored number would say how many faces are
    being kept back.
    """

    id: str
    status: PileStatus
    size: int
    faces: tuple[Sighting, ...] | list[Sighting]


@dataclass(frozen=True, slots=True)
class FiledInReach:
    """The disagreements one viewer may be told about, before any of them is drawn.

    `rows` in the store's order; `shown` is each file's vault state as `_shown_of` answered it
    (True where the face is behind a shut vault), and `names` each person as this viewer may read
    her name.
    """

    rows: list[FiledFace]
    shown: dict[str, bool]
    names: dict[str, str]


def _tiers(kind: ToCheckKind | Collection[ToCheckKind] | None) -> frozenset[ToCheckKind] | None:
    """The tiers of the review list a read asks for: one, several, or None for the whole list.

    Several because one tab can draw more than one tier: Needs your input holds a person's standing
    questions and the groups that may be her, which are two questions about the same person. A
    tier is still read here or not at all. See `FaceService.to_check` for why a narrowing is
    never a filter over a page. Checked as a single word FIRST: a `StrEnum` is itself a string, so
    iterating one would be iterating its letters.
    """
    if kind is None:
        return None
    if isinstance(kind, ToCheckKind):
        return frozenset({kind})
    return frozenset(kind) or None


def _searched_tiers(
    kind: ToCheckKind | Collection[ToCheckKind] | None, who: frozenset[str] | None
) -> frozenset[ToCheckKind] | None:
    """`_tiers` under a tab's search (`who`): a search keeps the rows naming one of the People
    it found, and a group of faces names nobody, so a searched list reads no group tier."""
    wanted = _tiers(kind)
    if who is None:
        return wanted
    return (frozenset(ToCheckKind) if wanted is None else wanted) - {ToCheckKind.GROUP}


def _as_group(group: GroupView) -> ToCheckView:
    """One pile as an item of the review list. The pile's own numbers, none of them recomputed."""
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
        """One page of the groups of unidentified faces, and how many there are in total.

        `floor` is the smallest group worth listing and `ceiling` the largest, in faces this viewer
        may see: one and no ceiling, which is every group, unless a caller says otherwise. See
        `tuning.STRANGER_FLOOR` for why a caller would, and `Repository.waiting_piles` for why the
        pair is decided in the statement rather than here: the page, the order and the total are one
        set or the pager describes a screen nobody is looking at.

        A pile is faces from files scattered all over the library, so its size is the one number
        here that has to be recounted per viewer: the stored `size` is what clustering found, and
        a viewer who may see three of those forty faces must be told three. A pile they may see
        none of is absent entirely: a row saying "17 faces" over a screen that can show none of
        them is a count of what is being kept back.

        The recount goes through the resolver once for the whole page rather than once per pile,
        which is what the `assets` filter is for.

        The total is what this viewer may see, not the stored count: the pile-and-face pairs come
        back in one read, the resolver answers for every file in one membership question, and what
        is left is counted here: two reads and one resolver call, however many piles there are. A
        stored total would not move when the vault opened or shut, and a count that includes what
        is being kept back is the same disclosure a facet count would be.

        ## Scoped first, then paged

        A page taken in SQL over every pile of this status, with the ones this viewer may see
        nothing of dropped afterwards, would walk one population while the total described
        another: short pages, Next skipping or repeating piles, Last landing on an empty screen.
        Not only for a guest: a pile whose faces have all been named is still a row in
        `face_piles`, so it would happen to an admin with the vault wide open.

        Ordered by how many faces this viewer may see, largest first, which is also the honest order
        rather than merely the convenient one. Ordering by the stored size would rank a pile whose
        faces are mostly concealed above one they can see all of, and the difference between where a
        pile sits and the number printed on it is a measure of what is being kept back, the same
        disclosure, arrived at from a third direction. The id breaks ties so a page is stable.
        """
        window, total = await self._repository.waiting_piles(
            viewer,
            status.value,
            # No page size means the whole wall, which the resolver bounds at one page rather than
            # letting a caller ask for a library: the statement reads one page, never every group
            # there is.
            limit=page_size or MAX_PAGE_SIZE,
            offset=offset,
            floor=floor,
            ceiling=ceiling,
        )

        # Two reads for the whole page, not two per pile: a store read and a resolver call per pile
        # grow linearly with the page, to most of a second for a page of forty-eight.
        held = await self._store.tracks_in_piles([pile_id for pile_id, _visible in window])
        shown = await self._shown_of(
            viewer, [track.asset_id for tracks in held.values() for track in tracks]
        )

        # When each face's picture was taken, for every face this page will draw, in one read.
        moments = await self._store.picture_moments(
            [
                track.id
                for tracks in held.values()
                for track in [one for one in tracks if one.asset_id in shown][:limit]
            ]
        )
        out: list[GroupView] = []
        for pile_id, _visible in window:
            tracks = held.get(pile_id, [])
            faces = [track for track in tracks if track.asset_id in shown]
            if not faces:
                # Named out from under us between the two reads. Skipped rather than drawn empty,
                # and the total is a beat stale for one request either way.
                continue
            out.append(
                GroupView(
                    id=pile_id,
                    # Every pile in this call was read under one status, so it is the one asked for
                    # rather than a column to re-read per row.
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
        return out, total

    async def position_of_pile(
        self, viewer: Viewer, status: PileStatus, pile_id: str
    ) -> int | None:
        """How far down the groups wall a pile sits, counting from zero, or None.

        None for a pile that is not there and None for one this viewer may see nothing of, which is
        the same answer the pile's own page gives, so asking where something is cannot become a
        way of asking whether it is there.
        """
        return await self._repository.waiting_pile_position(viewer, status.value, pile_id)

    async def pile(
        self,
        viewer: Viewer,
        pile_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[GroupView, int] | None:
        """One pile with every face in it, a page at a time, or None if there is no such pile.

        The list screen shows a handful per pile because it is drawing hundreds of them; this shows
        one pile and therefore shows all of it, which is what makes it possible to answer for part
        of a pile rather than the whole thing.

        Scoped the same way the list is: a face in a file this user may not see is absent, and a
        pile they may see none of is a 404 rather than an empty page, because an empty page for a
        pile that exists is itself an answer about what is being kept back.

        The total counts what this viewer may see, not what the pile holds, so the paging and the
        page agree. That costs one extra pass over the pile's faces and is worth it here: a listing
        of hundreds of piles cannot afford it, one pile can.
        """
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
        """Every unattributed face in one pile this viewer may see, by id, in the pile's order.

        Scoped exactly as `pile` scopes it, and None for a pile that is not there or that this
        viewer may see nothing of: the same answer that screen gives.
        """
        await self._require_enabled()
        if await self._store.pile_of(pile_id) is None:
            return None
        tracks = await self._store.pile_tracks(pile_id)
        shown = await self._shown_of(viewer, [track.asset_id for track in tracks])
        ids = [track.id for track in tracks if track.asset_id in shown]
        return ids or None

    async def position_of_face(self, viewer: Viewer, pile_id: str, track_id: str) -> int | None:
        """Where one face sits inside a pile's own page, counting from zero, or None.

        Scoped exactly as `pile` scopes it, and None for a pile this viewer may see nothing of:
        the same answer that screen gives, so this cannot be used to ask whether a pile is there.
        """
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
        """Who Sift has PROPOSED somebody for, gathered by person and surest person first.

        Each row is `(person_id, name, how many, the best confidence, a few of the faces)`.

        **The half of recognition that would otherwise have nowhere to be seen.** A match above the
        attach line puts a name on a file and shows up in the record of what Sift decided; one below
        it waits, and on the person's own page alone, which nobody opens unless they already
        suspect something is on it, proposals accumulate by the thousand unseen.

        **Gathered by person, and ranked by how sure the arithmetic is.** One card per face says
        nothing about who is on it, as on the Identified wall; and the order has to be the
        confidence, because the only question the card asks is which of
        these is worth looking at first. A person's own best proposal is what ranks them, rather
        than how many they have: a hundred distant resemblances are not more worth opening than one
        near-certain match.

        **Everybody this user may not be told about is absent entirely, rather than gathered
        under a nameless card.** The Identified wall keeps a nameless card because it is a record of
        what happened and something did happen; this is WORK, and a card offering to confirm a
        person whose name is withheld is a decision nobody can take: pressing it would be agreeing
        to a name the screen may not print.

        Not capped at any number of cards or of faces: `limit` pages it. What is bounded is the read
        underneath, at `SUGGESTIONS_AT_MOST`, for the reason every read here is bounded.
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
            # A withheld person comes back with no id at all, which is what drops their card.
            # See the docstring. The name is checked too because a card is a sentence with a name
            # in it, and there is nothing to put in one without it.
            if sighting.person_id is None or sighting.person_name is None:
                continue
            gathered.setdefault(sighting.person_id, []).append(sighting)

        # `proposed` already ordered the faces surest first, and a dict keeps what it was given in
        # order, so the first face of a person's list IS their best, and the people are already
        # in the order their best arrived. Nothing is sorted here, because a second ordering is a
        # second answer to one question.
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
        """Files whose one face is named as another than the person a pass filed them under, a page.

        **The question every other read in this feature asks backwards.** Everything else starts
        at a face and looks for a name. This starts at the name already on the file (put there by
        a folder, a filename, a stash-box) and asks whether the face in the file agrees with it.
        Nothing in the library ever did that, so a folder spelled with somebody else's name filed
        every file in it under her and no pass ever disagreed out loud.

        The condition is `FaceStore.filed_but_unrecognised`, and the reason it needs no arithmetic
        is written there. What is added here is who may be told: the file has to be one this viewer
        may be shown and the person one they may be told about, and a row failing either is absent
        rather than nameless. A card here is a sentence with a name in it and two answers about
        that person: there is nothing to draw without the name and nothing to press without the
        id, so a withheld person leaves no row at all. That is the rule the look-alike cards follow
        and it is the same reason.

        Re-read every time, which is what makes it current without a scheduler: an import, a face
        scan, a name filed and a reference added all change one of the columns underneath, so the
        next read of this list is the next answer.
        """
        reach = await self._filed_in_reach(viewer)
        rows, total = searched_page(
            reach.rows, who, lambda one: one.person_id, offset=offset, limit=limit
        )
        return await self._as_disagreements(viewer, reach, rows), total

    async def _filed_in_reach(self, viewer: Viewer) -> FiledInReach:
        """Every disagreement this viewer may be told about, in the store's order, unbuilt.

        The one place the rule about who may be told lives, for the tab, the survey and the rows
        gathered by person alike (`DisagreementsMixin`): the file has to be one this viewer may be
        shown and the person one they may be told about, and a row failing either is absent rather
        than nameless. Nothing is drawn here: a sighting is built only for the rows a page shows.
        """
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
        drawn: list[ToCheckView] = []
        for one in rows:
            sighting = await self._sighting(
                viewer, one.track, moments=moments, locked=reach.shown[one.track.asset_id]
            )
            drawn.append(
                ToCheckView(
                    kind=ToCheckKind.MISMATCH,
                    # The FILE, because that is what the row is about and what opening it opens.
                    # The person rides along in `person_id`. See `ToCheckView`.
                    id=one.track.asset_id,
                    # One, always: a file with two faces is not asked about at all, which is the
                    # whole of what makes the claim safe to make. Said rather than counted so the
                    # card's own sentence has a number that means the same thing on every kind.
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
        """What is left to check, as one list. Returns `(page, total, small groups)`.

        **One list rather than three walls, because the thing being chosen between is how much one
        press settles.** Three separate screens could not say which held the answer worth giving
        first. Ordered together, they can:
        a person's proposals lead, because agreeing to one settles every face standing for them and
        every one of those becomes a reference that improves the next pass, and the groups follow
        largest first, which is the order that makes a large group's name fold the smaller groups of
        the same face into it without anybody being asked.

        **The floor is what makes the list readable and it is not a filter over the drawing.** Most
        of a swept library is groups of one or two strangers (somebody who walked past a camera
        once, a frame the detector read twice), and listed among the real questions they bury them.
        Groups under `tuning.STRANGER_FLOOR` are not listed and not counted; how many there are is
        the third number here, said in one line at the foot of the list and opened by pressing it
        (`ToCheckShow.SMALL`). Nothing is hidden and nothing is deleted: they are people in the
        library and they are there for a reason.

        **And one tier above both: the names a pass filed that the face in the file disagrees
        with.** They lead, for the reason written at the read below: the rest of
        this list is work that has not been done, and one of those is work that was done wrongly
        and is live in the library. See `filed_faces_that_do_not_match`.

        **Paged across the three sources by one offset**, which is what makes it one list rather
        than three lists on one screen: each tier comes in order, so a page that starts past one
        starts that far into the next. The alternative (a pager each) is three populations
        under one count.

        `ignored` is the same list narrowed to what was set aside. It holds no proposals: agreeing
        or refusing is done about a person, and nobody set a person aside.

        **`kind` reads one of the three tiers, which is what the Faces tabs are** (Suggestions,
        Disagreements, Faces to name), because one list of thousands of rows cannot say which part
        of it somebody has reached. It is a narrowing here rather than a filter over
        a page for the reason every narrowing in this file is: filtering after paging asks for the
        wrong rows, and the count and the contents then describe different sets. The order, the
        floor and the paging rule stay in this one function, so a tab is a tier of the same list
        rather than a second list that could come to disagree with it.
        """
        if not await self.enabled():
            return [], 0, 0
        begin = max(0, offset)
        if show is not ToCheckShow.WAITING:
            return await self._held_back(
                viewer, show, limit=limit, offset=begin, faces_per_card=faces_per_card, who=who
            )

        # **First, and ahead of every question.** Everything else on this list is a question that
        # has not been answered yet; one of these is an answer already IN the library and wrong:
        # somebody's name sits on a file whose only face is not hers, and it is on her page, in her
        # count and in every search for her until somebody says otherwise. One press settles one
        # file, which is the least of anything here, and correcting a wrong answer still comes
        # before giving a missing one.
        #
        # A tier the caller did not ask for is not read at all, and its total stays zero, which is
        # what makes the offset arithmetic below come out right for one tab as well as for all
        # three: asked for the people alone, nothing is subtracted, so the offset is the offset into
        # the people.
        wanted = _searched_tiers(kind, who)
        page: list[ToCheckView] = []
        mismatch_total = 0
        people_total = 0
        may_be_total = 0
        group_total = 0
        if wanted is None or ToCheckKind.MISMATCH in wanted:
            mismatched, mismatch_total = await self.filed_faces_that_do_not_match(
                viewer, limit=limit, offset=begin, who=who
            )
            page.extend(mismatched)

        # What is left of the page once those have had it, and the same rule at each tier below:
        # a page filled by the tier above STILL asks the next one, because the TOTAL is every
        # source added up and a page that skipped the read would have to guess at the rest of it.
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
        # The groups that may be somebody, after the person's own questions: both ask whether faces
        # are somebody Sift knows, so Needs your input draws both (see `ToCheckKind.MAY_BE`).
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
            groups, group_total = await self.piles(
                viewer,
                PileStatus.OPEN,
                limit=faces_per_card,
                page_size=max(1, room),
                offset=max(0, begin - mismatch_total - people_total - may_be_total),
                floor=tuning.STRANGER_FLOOR,
            )
            if room > 0:
                page.extend(_as_group(group) for group in groups[:room])
        _under, small_groups = await self._repository.waiting_piles(
            viewer,
            PileStatus.OPEN.value,
            limit=1,
            offset=0,
            ceiling=tuning.STRANGER_FLOOR - 1,
        )
        return page, mismatch_total + people_total + may_be_total + group_total, small_groups

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
        """`to_check` for the groups the floor holds back or what was set aside: groups alone,
        which name nobody, so a search (`who`) finds none of them."""
        if who is not None:
            return [], 0, 0
        small = show is ToCheckShow.SMALL
        groups, total = await self.piles(
            viewer,
            PileStatus.OPEN if small else PileStatus.IGNORED,
            limit=faces_per_card,
            page_size=limit,
            offset=offset,
            # The groups under the floor, and only those: the same read as the list above with
            # the bound turned round, so the line at the foot and what it opens can never
            # describe different sets.
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
        """Where one row sits in `to_check`'s list, counting from zero, or None.

        The position read behind the list's `from`: an id resolved to an offset in the SAME list
        `to_check` would page for these same `show` and `kind`, so the page it opens is the page
        that row is on. What the id names follows the tier, because each tier's row is keyed on a
        different thing: a GROUP's id for a group, a PERSON's id for a person's proposals (the
        card is the person), and the FILE's id for a disagreement (the row is the file: see
        `ToCheckView`).

        **Each tier is ranked by READING IT, never by a second ordering written here.** A group is
        found by the store with the bounds `to_check` itself applies (the stranger floor on the
        list, the floor turned round into a ceiling for the small groups, no bound at all for what
        was set aside), because a position taken in one of those lists means nothing in another.
        A person and a disagreement have no statement of their own to ask: their order is made in
        Python, by `look_alikes` (a person ranked by their surest proposal, in the order `proposed`
        hands the faces over) and by `filed_faces_that_do_not_match` (the files in track order,
        scoped to what this viewer may be shown and told about). So this reads those two functions
        whole (every row, one face a card) and takes the index. Each already reads its whole
        bounded set to answer for any one page (the scoping is decided per row, so a page cannot be
        cut in SQL), which makes the position the same work as serving one page of that tab, and
        the order it finds is the order the page is served in by construction.

        On the whole list (`kind` None) the tiers come in `to_check`'s order (disagreements, then
        proposals, then groups), so the totals of the tiers above are added: the same arithmetic
        `to_check` subtracts to find where a page starts in the tier below.

        Every tier, not only groups: the anchor is the first row of the page somebody was on, not
        the row they answered, so it reopens a page that still holds the work. Without it the
        Suggestions and Disagreements tabs would come back at the top after every look at a person.
        No second ordering is written for a person: the rank is taken from `look_alikes` itself.

        None for a row that is not there, that has been answered or set aside since, that this
        viewer may see nothing of, or that sits on the other side of the floor: one answer for
        all of them, so asking where a row is cannot become a way of asking whether it exists.
        The route then serves the top, which is the ordinary case on a list that empties as it
        is answered.
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
        # The tiers in `to_check`'s order, each read whole and ranked by where the id sits in it.
        # A tier this tab does not draw is not read, and adds nothing: the rule `to_check` keeps.
        # The limits are the reads' own bounds, so "whole" is every row either can return.
        #
        # A PERSON and a MAY_BE card are both keyed on the person, so on a tab drawing both an id
        # resolves to the first of them (her questions), which is the page that was being read
        # whenever she has any: the two tiers come in that order.
        above = 0
        if wanted is None or ToCheckKind.MISMATCH in wanted:
            mismatched, mismatch_total = await self.filed_faces_that_do_not_match(
                viewer, limit=tuning.FILED_FACES_AT_MOST, offset=0, who=who
            )
            # The FIRST row naming the file: a file filed under two people is two rows, and the
            # page that holds the first of them is the one that was being read.
            for index, row in enumerate(mismatched):
                if row.id == item_id:
                    return index
            above += mismatch_total
        if wanted is None or ToCheckKind.PERSON in wanted:
            proposed, people_total = await self.look_alikes(
                viewer, limit=tuning.SUGGESTIONS_AT_MOST, offset=0, faces_per_card=1, who=who
            )
            for index, (person_id, *_rest) in enumerate(proposed):
                if person_id == item_id:
                    return above + index
            above += people_total
        if wanted is None or ToCheckKind.MAY_BE in wanted:
            # Every card, no faces: the order is the cards', and a face read here is read for
            # nothing.
            cards, may_be_total = await self.groups_that_may_be(
                viewer, limit=tuning.SUGGESTIONS_AT_MOST, offset=0, faces_per_group=0, who=who
            )
            for index, card in enumerate(cards):
                if card.id == item_id:
                    return above + index
            above += may_be_total
        if wanted is not None and ToCheckKind.GROUP not in wanted:
            return None
        at = await self._repository.waiting_pile_position(
            viewer, PileStatus.OPEN.value, item_id, floor=tuning.STRANGER_FLOOR
        )
        return None if at is None else above + at
