# SPDX-License-Identifier: AGPL-3.0-or-later
"""The People Sift can recognize wall and one person's faces: cards counted from the stored
figures, and each person's decided faces in the screen's order."""

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
    """How the people Sift knows are ordered: those with anything outstanding first, then by
    name, case-folded, the nameless card last in its half."""
    _person_id, faces = entry
    outstanding = any(
        face.attribution in (Attribution.SUGGESTED, Attribution.MATCHED) for face in faces
    )
    return _attention_key(outstanding=outstanding, name=faces[0].person_name if faces else None)


def _attention_key(*, outstanding: bool, name: str | None) -> tuple[int, int, str]:
    """`_attention_first` as the two facts it reads, so stored counts sort by the same rule."""
    return (0 if outstanding else 1, 0 if name else 1, (name or "").casefold())


_OUTSTANDING = (Attribution.SUGGESTED, Attribution.MATCHED)


@dataclass(frozen=True, slots=True)
class _KnownCard:
    """One wall card as the stored counts give it; `members` is one person, or all withheld."""

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

    `waiting`, `matched` and `confirmed` count apart; `surest` belongs to the matched alone. A
    withheld person has no id and no name.
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
    """One page of a person's decided faces, with each tab's count over all of them."""

    items: list[Sighting]
    total: int
    waiting: int
    matched: int
    confirmed: int


class IdentifiedMixin(VisibilityMixin):
    """Reading who Sift has identified, person by person."""

    async def appearances_in(self, viewer: Viewer, asset_id: str) -> list[Sighting]:
        """Who is in one file, with the moments to seek to; a hidden person's face is unnamed,
        never dropped, so the count does not depend on who is looking."""
        tracks = await self._store.tracks_of(asset_id)
        # One question for every name on the file, not one per face.
        names = await self._names_of(viewer, tracks)
        # And one for the piles.
        piles = await self._store.pile_statuses(
            [track.pile_id for track in tracks if track.pile_id and track.person_id is None]
        )
        moments = await self._store.picture_moments([track.id for track in tracks])
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
        """What Sift has decided, one card per person: outstanding first, then by name.

        `attribution`, `starters` and `who` narrow before paging; withheld people share one
        nameless card, which any narrowing drops. `marked=False` skips reading reference marks.
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
                    # None, not zero, where nothing matched: zero is a confidence.
                    surest=surest.get(card.person_id or "") if matched else None,
                    faces=faces.get(card.person_id, []),
                )
            )
        return out, len(cards)

    async def people_called(self, words: str) -> frozenset[str] | None:
        """Who a tab's search box narrows to, or None for no words (`Store.people_called`)."""
        wanted = words.strip()
        return None if not wanted else await self._store.people_called(wanted)

    async def known_people_preview(self, viewer: Viewer, *, most: int) -> tuple[list[str], int]:
        """The board's card: the first face of each of the first `most` people, and their count."""
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
        """One person's decided faces for the opened card, scoped again, narrowed by `attribution`
        before paging, with all three counts."""
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
        """One person's decided faces, scoped and narrowed, in the screen's order: the one list the
        page and its lookups read. A withheld person comes back with no id and is dropped."""
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
                # Her own page keeps her name on a locked face: it says nothing new.
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
        """Where one appearance sits on this person's screen, narrowed as the page is, or None."""
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
        """How many cards are People known from starters alone, and how many are not, under any
        search; the nameless card is in neither."""
        cards = await self._known_cards(viewer, attribution=attribution, who=who)
        apart = await self._starters_only()
        only = sum(1 for card in cards if card.person_id in apart)
        rest = sum(
            1 for card in cards if card.person_id is not None and card.person_id not in apart
        )
        return only, rest

    async def _starters_only(self) -> frozenset[str]:
        """Who Sift knows from starter pictures alone: the gallery's own answer."""
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

        Read off the stored per-user counts in one indexed read; withheld people share one nameless
        card, which `attribution` and `starters` drop.
        """
        bands = await self._store.face_bands(viewer.id, revealed=self._reveals_existence(viewer))
        # A person is named only where a face of theirs is on a file that is not locked.
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
        """The first `each` faces each card draws, reading on where its newest are all hidden."""
        wanted = [member for card in cards for member in card.members]
        heads = await self._store.attributed_heads(wanted, attribution=attribution, each=each)
        by_member: dict[str, list[StoredTrack]] = {}
        for track in heads:
            by_member.setdefault(str(track.person_id), []).append(track)
        shown = await self._shown_of(viewer, [track.asset_id for track in heads])
        chosen: dict[str | None, list[StoredTrack]] = {}
        for card in cards:
            # Newest decision first across every member.
            members = set(card.members)
            tracks = [track for track in heads if track.person_id in members]
            picked = [track for track in tracks if track.asset_id in shown][:each]
            everything_read = all(len(by_member.get(member, [])) < each for member in card.members)
            if len(picked) < min(each, card.size) and not everything_read:
                picked, shown = await self._read_on(
                    viewer, card, attribution=attribution, each=each, shown=shown
                )
            chosen[card.person_id] = picked
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
        """`_surest` for a page of cards with matched faces, in one read where it can."""
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
        """Where one person's card sits on the wall, narrowed as the wall is, or None; the
        nameless card is never found."""
        cards = await self._known_cards(viewer, attribution=attribution, starters=starters, who=who)
        for index, card in enumerate(cards):
            if card.person_id is not None and card.person_id == person_id:
                return index
        return None
