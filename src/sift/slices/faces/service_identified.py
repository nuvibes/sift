# SPDX-License-Identifier: AGPL-3.0-or-later
"""The People Sift can recognize wall and one person's faces: cards counted from the stored figures, and
each person's decided faces in the order the screen draws them.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, replace

from sift.kernel.access import Viewer
from sift.slices.faces import tuning
from sift.slices.faces.models import Attribution, StartersShow
from sift.slices.faces.service_visibility import Sighting, VisibilityMixin
from sift.slices.faces.store import StoredTrack

#: A card's best percentage is read a page of its surest matched faces at a time.
_SUREST_STEP = 48


def _attention_first(entry: tuple[str | None, list[Sighting]]) -> tuple[int, int, str]:
    """How the people Sift knows are ordered: the ones who need an answer, then the rest, by name.

    A card needs an answer when anything on it is outstanding
    (a face Sift is proposing, or one it attributed on its own that nobody has agreed with yet),
    and both of those are questions somebody has still to answer, so they are one bucket rather
    than two. Everything else is settled: "Nothing needs your input", and Sift identifying them
    reliably, is a card to be reassured by rather than worked on, and it goes behind.

    Alphabetical inside each half, because a record is something somebody comes back to looking for
    a NAME, and an order that moves whenever a pass runs cannot be looked in twice. Case-folded so
    the order is the one a person would write down.

    The nameless card (everybody this user may not be told about, gathered under one) sorts
    last inside its half rather than first. It has no name to file under, and an empty string would
    put it at the top of the alphabet, which reads as a card that lost its label.
    """
    _person_id, faces = entry
    outstanding = any(
        face.attribution in (Attribution.SUGGESTED, Attribution.MATCHED) for face in faces
    )
    return _attention_key(outstanding=outstanding, name=faces[0].person_name if faces else None)


def _attention_key(*, outstanding: bool, name: str | None) -> tuple[int, int, str]:
    """`_attention_first` as the two facts it reads, so a card counted from the stored figures
    sorts by the same rule as one gathered from its faces: one rule, two ways to arrive at it."""
    return (0 if outstanding else 1, 0 if name else 1, (name or "").casefold())


#: The two kinds of face that are still a question: see `_attention_first`.
_OUTSTANDING = (Attribution.SUGGESTED, Attribution.MATCHED)


@dataclass(frozen=True, slots=True)
class _KnownCard:
    """One card of the People Sift can recognize wall as the stored counts give it, before any face.

    `members` is the stored people behind the card: one, or for the nameless card every person
    this viewer may not be told about. `counts` is by how each face was named, and a withheld
    person's faces are counted under None, the same way `_sighting` withholds the attribution
    with the name.
    """

    person_id: str | None
    name: str | None
    members: tuple[str, ...]
    counts: dict[Attribution | None, int]

    @property
    def size(self) -> int:
        return sum(self.counts.values())

    def key(self) -> tuple[int, int, str]:
        outstanding = any(self.counts.get(kind, 0) > 0 for kind in _OUTSTANDING)
        return _attention_key(outstanding=outstanding, name=self.name)


@dataclass(frozen=True, slots=True)
class IdentifiedView:
    """One person's card on the Identified wall, counted as this viewer may see it.

    Every number here is of faces this viewer may actually be shown, for the reason `GroupView`
    gives: a count taken off the stored rows would say how many are being kept back.

    The three states are counted apart rather than summed, because the question this screen
    answers is which of these Sift attached by itself, and a card saying only "28 faces" cannot
    answer it. `waiting` is a proposal nobody has answered, `matched` is Sift's
    own decision above the attach line, and `confirmed` is somebody having agreed.

    `surest` is the best confidence among the MATCHED faces, which is the only one of the three a
    percentage means anything for: a proposal's confidence belongs to the proposal, and a confirmed
    face was decided by a person rather than by arithmetic. None when nothing here was matched.

    A person this viewer may not be told about arrives with no id and no name, exactly as their
    faces do one at a time. See `_known_cards`.
    """

    person_id: str | None
    person_name: str | None
    size: int
    waiting: int
    matched: int
    confirmed: int
    surest: float | None
    faces: list[Sighting]


@dataclass(frozen=True, slots=True)
class AppearancesView:
    """One page of a person's decided faces, and how big each of the three tabs is.

    The counts are of the WHOLE of that person's faces as this viewer may see them, never of the
    page: they are what the tab row is drawn from, and a row of tabs counting one page would say a
    different number on every page turn.

    All three travel with the page because the screen asks one question ("what is there for this
    person"). A count that arrives only when its tab is pressed is worse than none, because until
    then the row says the other two states are empty.

    `total` stays what the pager needs: how many are in the narrowing THIS page is of, which is one
    of the three below, or their sum when nothing was narrowed.
    """

    items: list[Sighting]
    total: int
    waiting: int
    matched: int
    confirmed: int


class IdentifiedMixin(VisibilityMixin):
    """Reading who Sift has identified, person by person."""

    async def appearances_in(self, viewer: Viewer, asset_id: str) -> list[Sighting]:
        """Who is in one file, with the moments to seek to. The item detail's block.

        The caller has already settled that this viewer may see the file. What is left is the other
        half, and it is the half that is easy to miss: **an appearance must not name somebody this
        viewer cannot otherwise see.** Somebody they have hidden is absent from every list, count
        and search box in the application, and a face crop captioned with that name would put them
        back on screen through the one surface that had not been asked.

        So each name goes through the same question the People screen asks, and an appearance whose
        person fails it comes back unnamed rather than being dropped: the face IS in the file, the
        viewer can see the file, and hiding somebody conceals the person, not the pixels. Dropping
        the row instead would make the number of faces in a file depend on who is looking, which is
        its own disclosure.
        """
        tracks = await self._store.tracks_of(asset_id)
        # One question for every name on the file, not one per face. Asked per face, a picture with
        # eight faces on it would ask the People wall eight times, on the panel that opens every time somebody opens
        # anything. See `_names_of` for what a single name costs.
        names = await self._names_of(viewer, tracks)
        # And one for the piles, for the same reason and on the same panel. A file's faces sit in a
        # handful of piles at most, so this is one read whatever the file holds.
        piles = await self._store.pile_statuses(
            [track.pile_id for track in tracks if track.pile_id and track.person_id is None]
        )
        moments = await self._store.picture_moments([track.id for track in tracks])
        # Which of them are turned past the bar's angle, so the file can say so beside each.
        turned = await self._store.turned_of(
            [track.id for track in tracks], (await self.configuration()).bar.min_frontality
        )
        return [
            await self._sighting(
                viewer, track, moments=moments, names=names, piles=piles, turned=track.id in turned
            )
            for track in tracks
        ]

    async def identified_people(
        self,
        viewer: Viewer,
        *,
        limit: int = 24,
        offset: int = 0,
        faces_per_card: int = 12,
        attribution: Attribution | None = None,
        marked: bool = True,
        starters: StartersShow | None = None,
        who: frozenset[str] | None = None,
    ) -> tuple[list[IdentifiedView], int]:
        """What Sift has decided lately, gathered by person rather than listed face by face.

        One card per face would say nothing about who was on it: thirteen appearances of one person
        would read as thirteen separate answers, and a person with two faces waiting to be agreed
        to would look exactly like a person with none.

        **Ordered by whether anybody is being asked anything, then by name.** Not by recency: that
        is a fact about the pass rather than about the person, so a run of matches would reorder the
        whole wall and sink a person with faces standing under people with nothing left to answer.
        The wall is there to be worked down, so the people with something
        outstanding come first and the settled ones sit behind them, each half alphabetical: an
        order somebody can find a name in twice running. See `_attention_first`.

        `attribution` narrows to one of the three ways a face came to carry a name, and it narrows
        before the page is taken for the reason `identified_for` gives: filtering afterwards asks
        for the wrong rows, and the count and the contents then describe different sets. A person
        with none of the wanted kind is not a card at all, so the count is of people who have some.

        Faces this viewer may not see are gone before any of this, so a card's counts are counts of
        what they can actually look at. Everything Sift attributed to a person they may not be told
        about gathers under a single nameless card, exactly as those faces already appear namelessly
        one at a time: the alternative is one nameless card per hidden person, which is a count of
        how many hidden people are in the library. A narrowing drops that card, because withholding
        a person withholds how their faces came to carry the name as well.

        `marked=False` leaves every face's reference mark unread (`Sighting.is_reference` False) for
        a caller that draws no mark: the board's card, which draws one crop per person. The cards,
        their order and every count are the same either way; see `_known_cards`.

        `starters` sets apart the People Sift can recognize from starter pictures alone, before the page is
        taken, for the same reason `attribution` narrows there. See `StartersShow`.

        `who` keeps only these People (the tab's search box, through `people_called`), before the
        page is taken for the same reason again; the nameless card goes, as with every narrowing.
        """
        cards = await self._known_cards(viewer, attribution=attribution, starters=starters, who=who)
        page = cards[max(0, offset) : max(0, offset) + limit]
        names = {member: card.name for card in page for member in card.members}
        faces = await self._card_faces(
            viewer, page, attribution=attribution, each=faces_per_card, names=names, marked=marked
        )
        surest = await self._surest_of(viewer, page)
        out: list[IdentifiedView] = []
        for card in page:
            matched = card.counts.get(Attribution.MATCHED, 0)
            out.append(
                IdentifiedView(
                    person_id=card.person_id,
                    person_name=card.name,
                    size=card.size,
                    waiting=card.counts.get(Attribution.SUGGESTED, 0),
                    matched=matched,
                    confirmed=card.counts.get(Attribution.CONFIRMED, 0),
                    # The best of the matched, and None where nothing was matched. See
                    # `IdentifiedView.surest`. A face whose confidence was never recorded counts
                    # towards `matched` and contributes no number, which is why the default is
                    # None rather than zero: zero is a confidence, and an absent one is not.
                    surest=surest.get(card.person_id or "") if matched else None,
                    faces=faces.get(card.person_id, []),
                )
            )
        return out, len(cards)

    async def people_called(self, words: str) -> frozenset[str] | None:
        """Who a tab's search box narrows to: everybody whose name or an alias holds the words,
        or None for no words, which is no narrowing at all. See `Store.people_called`."""
        wanted = words.strip()
        return None if not wanted else await self._store.people_called(wanted)

    async def known_people_preview(self, viewer: Viewer, *, most: int) -> tuple[list[str], int]:
        """The board's card: the first face of each of the first `most` people, and how many there
        are. The wall's own order and count, with one face per card and nothing else assembled."""
        cards = await self._known_cards(viewer, attribution=None)
        page = cards[:most]
        names = {member: card.name for card in page for member in card.members}
        faces = await self._card_faces(
            viewer, page, attribution=None, each=1, names=names, marked=False
        )
        firsts = [faces[card.person_id][0].track_id for card in page if faces.get(card.person_id)]
        return firsts, len(cards)

    async def identified_for(
        self,
        viewer: Viewer,
        person_id: str,
        *,
        limit: int = 60,
        offset: int = 0,
        attribution: Attribution | None = None,
    ) -> AppearancesView:
        """One person's decided faces, for the card opened up.

        Same scoping as the wall it was opened from, asked again rather than carried across: a page
        that trusted what the previous one handed it would show faces a share had moved out of reach
        between one press and the next.

        `attribution` narrows to one of the three ways a face came to carry this name, and it
        narrows here rather than wherever the answer lands: filtering after paging asks for the
        wrong rows, so a person whose proposals all sit past the first page would show none under a
        heading counting twenty-four. The count and the contents must describe one set.

        One three-way value rather than booleans: the screen draws all three as tabs (awaiting
        review, agreed by you, matched by Sift), each a paged wall with its own count, and two
        booleans describing one three-way choice can be set to a state that means nothing.

        All three counts come back with the page, and they cost nothing: the whole of this person's
        scoped faces is assembled here whatever the narrowing is (`_sightings_for` reads the same
        statements either way), so counting the three states is a pass over a list already in hand.
        """
        everything = await self._sightings_for(viewer, person_id, attribution=None)
        counted = Counter(sighting.attribution for sighting in everything)
        theirs = [
            sighting
            for sighting in everything
            if attribution is None or sighting.attribution is attribution
        ]
        begin = max(0, offset)
        return AppearancesView(
            items=theirs[begin : begin + limit],
            total=len(theirs),
            waiting=counted[Attribution.SUGGESTED],
            matched=counted[Attribution.MATCHED],
            confirmed=counted[Attribution.CONFIRMED],
        )

    async def _sightings_for(
        self, viewer: Viewer, person_id: str, *, attribution: Attribution | None
    ) -> list[Sighting]:
        """One person's decided faces, scoped and narrowed, in the order the screen shows them.

        The one list that screen is built from. The page and the lookup that says where a face sits
        in it both read THIS rather than each building the list themselves: two assemblies of one
        list are two things that can stop agreeing, and the address would then carry a position in
        a list nobody is looking at.

        **Asked of the person, not of the library.** A library-wide slice filtered down to one
        person would make a person's own numbers move with how busy the rest of the library had
        been. `store.attributed_to` carries the reasoning.

        The person is still filtered again below, off `sighting.person_id` rather than off the stored
        row, and that is not a leftover: a person this viewer may not be told about comes back from
        `_sighting` with no id at all, so the check is what stops a page being served for somebody
        whose name is being withheld. It is not the narrowing.
        """
        tracks = await self._store.attributed_to(person_id, most=tuning.PERSON_FACES_AT_MOST)
        shown = await self._shown_of(viewer, [track.asset_id for track in tracks])
        names = await self._names_of(viewer, tracks)
        references = await self._store.reference_tracks([track.id for track in tracks])
        moments = await self._store.picture_moments(
            [track.id for track in tracks if track.asset_id in shown]
        )
        theirs = []
        for track in tracks:
            if track.asset_id not in shown:
                continue
            sighting = await self._sighting(
                viewer,
                track,
                moments=moments,
                names=names,
                references=references,
                locked=shown[track.asset_id],
                # Her own page is one of the two places a locked face keeps her name: the page is
                # about her, so the name says nothing the page has not.
                named_when_locked=True,
            )
            if sighting.person_id != person_id:
                continue
            if attribution is not None and sighting.attribution is not attribution:
                continue
            theirs.append(sighting)
        return theirs

    async def position_of_appearance(
        self,
        viewer: Viewer,
        person_id: str,
        track_id: str,
        *,
        attribution: Attribution | None = None,
    ) -> int | None:
        """Where one appearance sits on this person's screen, counting from zero, or None.

        Narrowed the same way the page is, because a position taken on one tab means nothing on
        another: the same face is the third row of the proposals and the ninetieth of everything.
        """
        theirs = await self._sightings_for(viewer, person_id, attribution=attribution)
        for index, sighting in enumerate(theirs):
            if sighting.track_id == track_id:
                return index
        return None

    async def starters_apart(
        self,
        viewer: Viewer,
        *,
        attribution: Attribution | None = None,
        who: frozenset[str] | None = None,
    ) -> tuple[int, int]:
        """How many cards of the wall are People known from starter pictures alone, and how many
        are not: the two counts the wall's own control carries, each the size of the wall that
        press opens, so under a search (`who`) they count what that search found. The nameless
        card is in neither, as every narrowing leaves it out."""
        cards = await self._known_cards(viewer, attribution=attribution, who=who)
        apart = await self._starters_only()
        only = sum(1 for card in cards if card.person_id in apart)
        rest = sum(
            1 for card in cards if card.person_id is not None and card.person_id not in apart
        )
        return only, rest

    async def _starters_only(self) -> frozenset[str]:
        """Who Sift knows from starter pictures alone: the gallery's own answer, the one the bar
        for asking rather than naming reads (`Gallery.starters_only`), so the wall and the
        matching cannot hold two ideas of who that is."""
        configured = await self.configuration()
        gallery = await self._gallery_for(configured.groups, configured.recognizer)
        return gallery.starters_only

    async def _known_cards(
        self,
        viewer: Viewer,
        *,
        attribution: Attribution | None,
        starters: StartersShow | None = None,
        who: frozenset[str] | None = None,
    ) -> list[_KnownCard]:
        """The People Sift can recognize wall, as cards with their counts and in the wall's order.

        Read off the stored per-user counts (`Store.face_bands`), so the wall's order, its size
        and every figure on a card are one indexed read and a question about names: nothing about
        any face is assembled to say how many there are, which would grow with every attributed face
        in the library.

        Everybody this viewer may not be told about gathers under a SINGLE nameless card, exactly
        as those faces appear namelessly one at a time: the alternative is one nameless card per
        hidden person, which is a count of how many hidden people there are. Their faces carry no
        attribution for this viewer (see `_sighting`), so a narrowing drops that card.

        `attribution` narrows to one of the three ways a face came to carry a name, before the
        order is taken, for the reason `identified_for` gives: a person with none of that kind is
        not a card at all, and the count is of people who have some.

        `starters` keeps only the People known from starter pictures alone, or leaves them out.
        Either way the nameless card goes: what Sift knows a withheld person from is withheld with
        her name.
        """
        bands = await self._store.face_bands(viewer.id, revealed=self._reveals_existence(viewer))
        # Who may be named, which is a stricter question than whose faces are counted. With "leave
        # a locked tile" on and the vault shut, a face on a locked file is counted and drawn as a
        # padlock, and a card naming somebody whose every face is locked says a hidden file exists
        # and she is in it. So a person is named only where a face of
        # theirs is on a file that is not locked; the rest gather under the nameless card, exactly
        # as a person withheld by name does. One more read of the same stored counts, and only in
        # the one mode where the two answers can differ.
        nameable = (
            bands
            if viewer.show_hidden or not self._reveals_existence(viewer)
            else await self._store.face_bands(viewer.id, revealed=False)
        )
        names = await self._names_for(viewer, [one for one in bands if one in nameable])
        named: list[_KnownCard] = []
        withheld: list[str] = []
        hidden_counts: dict[Attribution | None, int] = {}
        for person_id in sorted(bands):
            name = names.get(person_id)
            if name is None:
                withheld.append(person_id)
                hidden_counts[None] = hidden_counts.get(None, 0) + sum(bands[person_id].values())
                continue
            counts: dict[Attribution | None, int] = {}
            for band, count in bands[person_id].items():
                kind = Attribution(band) if band else None
                counts[kind] = counts.get(kind, 0) + count
            named.append(_KnownCard(person_id, name, (person_id,), counts))
        cards = named
        if withheld:
            cards = [*named, _KnownCard(None, None, tuple(withheld), hidden_counts)]
        if attribution is not None:
            cards = [
                replace(card, counts={attribution: card.counts[attribution]})
                for card in cards
                if card.counts.get(attribution, 0) > 0
            ]
        if starters is not None:
            apart = await self._starters_only()
            wanted = starters is StartersShow.ONLY
            cards = [
                card
                for card in cards
                if card.person_id is not None and (card.person_id in apart) is wanted
            ]
        if who is not None:
            cards = [card for card in cards if card.person_id is not None and card.person_id in who]
        return sorted(cards, key=_KnownCard.key)

    async def _card_faces(
        self,
        viewer: Viewer,
        cards: Sequence[_KnownCard],
        *,
        attribution: Attribution | None,
        each: int,
        names: dict[str, str | None],
        marked: bool,
    ) -> dict[str | None, list[Sighting]]:
        """The first `each` faces each card draws, in its own page's order.

        One read of every card's newest faces and one question about their files, which is the
        whole of it whenever those faces are on files this viewer may see. A card that comes up
        short (its newest faces all on files kept from this viewer) reads on down its own list a
        page at a time, so a card never draws fewer faces than it has because of whose they are.
        """
        wanted = [member for card in cards for member in card.members]
        heads = await self._store.attributed_heads(wanted, attribution=attribution, each=each)
        by_member: dict[str, list[StoredTrack]] = {}
        for track in heads:
            by_member.setdefault(str(track.person_id), []).append(track)
        shown = await self._shown_of(viewer, [track.asset_id for track in heads])
        chosen: dict[str | None, list[StoredTrack]] = {}
        for card in cards:
            # In the order the one statement read them, which is newest decision first across
            # every member, so the nameless card's faces interleave as one list, not per person.
            members = set(card.members)
            tracks = [track for track in heads if track.person_id in members]
            picked = [track for track in tracks if track.asset_id in shown][:each]
            everything_read = all(len(by_member.get(member, [])) < each for member in card.members)
            if len(picked) < min(each, card.size) and not everything_read:
                picked, shown = await self._read_on(
                    viewer, card, attribution=attribution, each=each, shown=shown
                )
            chosen[card.person_id] = picked
        # Every card's marks and moments in one read each, not one per card.
        every = [track.id for picked in chosen.values() for track in picked]
        references = await self._store.reference_tracks(every) if marked else set()
        moments = await self._store.picture_moments(every)
        return {
            person_id: [
                await self._sighting(
                    viewer,
                    track,
                    moments=moments,
                    names=names,
                    references=references,
                    locked=shown[track.asset_id],
                )
                for track in picked
            ]
            for person_id, picked in chosen.items()
        }

    async def _read_on(
        self,
        viewer: Viewer,
        card: _KnownCard,
        *,
        attribution: Attribution | None,
        each: int,
        shown: dict[str, bool],
    ) -> tuple[list[StoredTrack], dict[str, bool]]:
        """A card's faces read on past its newest few, a page at a time, until it has `each`."""
        picked: list[StoredTrack] = []
        seen = dict(shown)
        offset = 0
        step = max(each * 4, 48)
        while len(picked) < each:
            page = await self._store.attributed_page(
                card.members, attribution=attribution, limit=step, offset=offset
            )
            if not page:
                break
            seen.update(await self._shown_of(viewer, [track.asset_id for track in page]))
            picked.extend(track for track in page if track.asset_id in seen)
            offset += step
        return picked[:each], seen

    async def _surest_of(
        self, viewer: Viewer, cards: Sequence[_KnownCard]
    ) -> dict[str, float | None]:
        """`_surest` for a page of cards with matched faces: every card's first page in one read,
        read on only for a card whose first page is all on files this viewer may not see."""
        person_ids = [
            card.person_id
            for card in cards
            if card.person_id is not None and card.counts.get(Attribution.MATCHED, 0)
        ]
        heads = await self._store.surest_matched_heads(person_ids, each=_SUREST_STEP)
        shown = await self._shown_of(
            viewer, [track.asset_id for tracks in heads.values() for track in tracks]
        )
        found: dict[str, float | None] = {}
        for person_id in person_ids:
            tracks = heads.get(person_id, [])
            first = next((track for track in tracks if track.asset_id in shown), None)
            if first is not None:
                found[person_id] = first.confidence
            elif len(tracks) < _SUREST_STEP:
                found[person_id] = None
            else:
                found[person_id] = await self._surest(viewer, person_id)
        return found

    async def _surest(self, viewer: Viewer, person_id: str) -> float | None:
        """The best confidence among one person's matched faces that this viewer may see."""
        offset = 0
        step = _SUREST_STEP
        while True:
            page = await self._store.surest_matched(person_id, limit=step, offset=offset)
            if not page:
                return None
            shown = await self._shown_of(viewer, [track.asset_id for track in page])
            for track in page:
                if track.asset_id in shown:
                    return track.confidence
            offset += step

    async def position_of_identified(
        self,
        viewer: Viewer,
        person_id: str,
        *,
        attribution: Attribution | None = None,
        starters: StartersShow | None = None,
        who: frozenset[str] | None = None,
    ) -> int | None:
        """Where one person's card sits on the Identified wall, counting from zero, or None.

        Narrowed the same way the wall is, because a position taken on one narrowing means nothing
        on another: the same person is the second card of what Sift matched and the ninth of
        everything. The same rule `position_of_appearance` follows one screen down.

        The nameless card can never be found here, and that is deliberate rather than an oversight:
        it is keyed on None, no address can name it, and a card that exists in order to have no name
        is not one a link should be able to point at.
        """
        cards = await self._known_cards(viewer, attribution=attribution, starters=starters, who=who)
        for index, card in enumerate(cards):
            if card.person_id is not None and card.person_id == person_id:
                return index
        return None
