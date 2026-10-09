# SPDX-License-Identifier: AGPL-3.0-or-later
"""The "these groups may be her" cards: unnamed groups that look like a person or sit in her
folder, and the Yes and the No that answer for a whole group."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sift.kernel.access import Viewer
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, announce
from sift.kernel.content import TreeReads
from sift.kernel.log import get_logger
from sift.kernel.vocabulary import FACE_SAID_NO, FACE_SAID_YES, RECEIPT_FACES, Subject
from sift.slices.faces import matching, tuning
from sift.slices.faces.models import Attribution, PileStatus, ToCheckKind, Vector
from sift.slices.faces.receipts import IDENTIFIED_QUEUE, NAMED_GROUPS, REFUSED_GROUPS
from sift.slices.faces.search import searched_page
from sift.slices.faces.service_base import _face_answered
from sift.slices.faces.service_decisions import DecisionsMixin, RunAnswered, Taught
from sift.slices.faces.service_grouping import GroupingMixin
from sift.slices.faces.service_visibility import Sighting, VisibilityMixin
from sift.slices.faces.store import Ruling, StoredTrack

if TYPE_CHECKING:  # numpy is only needed for a signature
    pass

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ToCheckView:
    """One press's worth of work on the review list; `kind` says which card to draw.

    `size` is what the press settles as this viewer may see it; `person_id` is set for a MISMATCH
    alone, whose `id` is a file.
    """

    kind: ToCheckKind
    id: str
    size: int
    faces: tuple[Sighting, ...] | list[Sighting]
    person_name: str | None = None
    best: float | None = None
    status: PileStatus | None = None
    person_id: str | None = None
    source: str | None = None
    """Which pass filed that name, for a MISMATCH alone: the word `asset_people.source` carries.
    "Filed from a folder name" and "filed by a stash-box" are different amounts of evidence, and
    the row says which, for whoever decides which way to answer."""
    groups: tuple[GroupMayBe, ...] = ()
    """The groups a MAY_BE card asks about, closest first. See `GroupMayBe`. Empty on every other
    kind, whose question is about faces or a file rather than about several groups together."""


#: Why a group may be somebody: the group as a whole comes close to their pictures.
LIKENESS_REASON = "likeness"
#: The person is known by a stash-box's starter pictures alone: weaker, so the boxes are named.
STARTER_REASON = "stash-box"


@dataclass(frozen=True, slots=True)
class GroupReason:
    """One reason a group may be somebody, as one viewer may be told it; `kind` picks the fields."""

    kind: str
    folder_id: str | None = None
    folder_name: str | None = None
    in_folder: int | None = None
    group_files: int | None = None
    box_names: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GroupMayBe:
    """One unnamed group on a "these groups may be her" card.

    `likeness` is on the group scale (`tuning.GROUP_ASK`), None where it could not be measured;
    `ticked` is whether the card starts with it chosen.
    """

    pile_id: str
    size: int
    likeness: float | None
    ticked: bool
    faces: tuple[Sighting, ...] | list[Sighting]
    reasons: tuple[GroupReason, ...]


@dataclass(slots=True)
class _MayBe:
    """A group that may be one person, while the tier is being worked out. See `groups_that_may_be`."""

    pile_id: str
    likeness: float | None
    reasons: list[GroupReason]
    faces: list[StoredTrack]


def _closeness(
    gallery: matching.Gallery, middles: Sequence[Vector]
) -> tuple[tuple[str, ...], list[list[float]]]:
    """How close each group's middle comes to each person: `(people, grid)`, one row per middle,
    the best of each person's gallery rows, in one matrix product."""
    # Imported here: the face service names numpy only for signatures, and `matching` has it loaded.
    import numpy as np

    rows = matching.rows_by_person(gallery)
    people = tuple(rows)
    if not middles or not people:
        return people, [[] for _middle in middles]
    scores = np.asarray(middles, dtype=np.float32) @ gallery.vectors.T
    best = np.stack([scores[:, rows[person]].max(axis=1) for person in people], axis=1)
    return people, best.tolist()


#: The reasons a likeness measures; any other reason (her folder) the number does not.
_MEASURED_REASONS = frozenset({LIKENESS_REASON, STARTER_REASON})


def _ticked(group: _MayBe) -> bool:
    """Whether a group starts chosen: close enough by `tuning.GROUP_TICK`, or proposed for a
    reason the likeness does not measure. Starters are held to the line too."""
    if any(reason.kind not in _MEASURED_REASONS for reason in group.reasons):
        return True
    return group.likeness is not None and group.likeness >= tuning.GROUP_TICK


def _closest_first(group: _MayBe) -> tuple[float, int, str]:
    """A card's order for its groups: closest, unmeasured last, then larger, then the id."""
    return (
        -(group.likeness if group.likeness is not None else -2.0),
        -len(group.faces),
        group.pile_id,
    )


def _faces_counted(count: int) -> str:
    """Say "1 face" or "N faces", for a receipt's sentence."""
    return "1 face" if count == 1 else f"{count:,} faces"


@dataclass(frozen=True, slots=True)
class GroupProposalView:
    """One "this group may be this person", as one viewer may be told it. See `proposals_for`."""

    person_id: str
    person_name: str
    #: Why: `folder` is the only reason so far (`evidence.FOLDER_REASON`).
    reason: str
    folder_id: str
    folder_name: str
    #: The group's files inside that folder, as this viewer may see them.
    in_folder: int
    #: Every file the group's unnamed faces are in, as this viewer may see them.
    group_files: int


def _person_of(card: tuple[str, str, list[_MayBe]]) -> str:
    """The person a may-be card asks about."""
    return card[0]


class MayBeMixin(DecisionsMixin, GroupingMixin, VisibilityMixin):
    """Offering unnamed groups to the person they look like, and answering for them."""

    async def groups_that_may_be(
        self,
        viewer: Viewer,
        *,
        limit: int,
        offset: int,
        faces_per_group: int = tuning.FACES_PER_GROUP,
        who: frozenset[str] | None = None,
    ) -> tuple[list[ToCheckView], int]:
        """One card per person listing the unnamed groups that may be them, closest first.

        A page of cards and their total. A group's middle is compared with the person, since its
        faces may each fall short; always asked, never attached; offered to its closest person not
        refused, beside any folder proposal. A person or group this viewer may not see is absent.
        """
        if not await self.enabled():
            return [], 0
        cards, shown = await self._may_be_cards(viewer)
        window, total = searched_page(cards, who, _person_of, offset=offset, limit=max(0, limit))
        drawing = [
            track
            for _person_id, _name, groups in window
            for one in groups[: tuning.GROUPS_PER_CARD]
            for track in one.faces[: max(0, faces_per_group)]
        ]
        moments = await self._store.picture_moments([track.id for track in drawing])
        views: list[ToCheckView] = []
        for person_id, name, groups in window:
            drawn: list[GroupMayBe] = []
            for one in groups[: tuning.GROUPS_PER_CARD]:
                faces = [
                    await self._sighting(
                        viewer, track, moments=moments, locked=shown.get(track.asset_id, False)
                    )
                    for track in one.faces[: max(0, faces_per_group)]
                ]
                drawn.append(
                    GroupMayBe(
                        pile_id=one.pile_id,
                        size=len(one.faces),
                        likeness=one.likeness,
                        ticked=_ticked(one),
                        faces=faces,
                        reasons=tuple(one.reasons),
                    )
                )
            views.append(
                ToCheckView(
                    kind=ToCheckKind.MAY_BE,
                    # The person, as a PERSON card's is; `kind` tells the two apart.
                    id=person_id,
                    size=sum(group.size for group in drawn),
                    # One face of each group, for a reader that draws a card by its first faces.
                    faces=[group.faces[0] for group in drawn if group.faces],
                    person_name=name,
                    best=next((one.likeness for one in groups if one.likeness is not None), None),
                    groups=tuple(drawn),
                )
            )
        return views, total

    async def _may_be_cards(
        self, viewer: Viewer, *, only: str | None = None
    ) -> tuple[list[tuple[str, str, list[_MayBe]]], dict[str, bool]]:
        """Every may-be card this viewer may be shown, in order, and `_shown_of`'s answer for
        its faces. `only` narrows to one person, as a press re-reads it."""
        configured = await self.configuration()
        gallery = await self._gallery_for(configured.groups, configured.recognizer)
        middles = (
            await self._store.open_pile_middles(
                configured.recognizer, at_least=tuning.GROUP_AT_LEAST
            )
            if len(gallery)
            else []
        )
        people, grid = await asyncio.to_thread(
            _closeness, gallery, [middle for _pile_id, middle, _unnamed in middles]
        )
        row_of = {pile_id: row for row, (pile_id, _middle, _unnamed) in enumerate(middles)}
        column_of = {person_id: column for column, person_id in enumerate(people)}

        def likeness(pile_id: str, person_id: str) -> float | None:
            row, column = row_of.get(pile_id), column_of.get(person_id)
            return None if row is None or column is None else float(grid[row][column])

        near = _near_people(people, grid, row_of)
        proposed = await self.proposals_for(viewer, await self._store.proposed_piles())
        asked = sorted(set(near) | set(proposed))
        held = await self._store.tracks_in_piles(asked)
        refused = await self._store.refusals_in_piles(asked)

        def answered_no(pile_id: str, person_id: str) -> bool:
            faces = len(held.get(pile_id, []))
            refusals = refused.get((pile_id, person_id), 0)
            return faces == 0 or refusals > faces * tuning.GROUP_REFUSED_SHARE

        reason_of = await self._likeness_reasons(near, gallery.starters_only)
        found = _gathered(near, proposed, likeness, answered_no, reason_of)
        if only is not None:
            found = {only: found[only]} if only in found else {}
        names = await self._names_for(viewer, list(found))
        shown = await self._shown_of(
            viewer,
            [
                track.asset_id
                for groups in found.values()
                for pile_id in groups
                for track in held.get(pile_id, [])
            ],
        )
        cards = _cards_of(found, names, held, shown)
        return cards, shown

    async def _likeness_reasons(
        self, near: Mapping[str, Sequence[str]], starters_only: frozenset[str]
    ) -> dict[str, GroupReason]:
        """Why each person a group is near may be it, the starters' boxes read once for all."""
        everyone = {person_id for candidates in near.values() for person_id in candidates}
        boxes = await self._store.starter_boxes(sorted(everyone & starters_only))
        return {
            person_id: GroupReason(kind=STARTER_REASON, box_names=boxes.get(person_id, ()))
            if person_id in starters_only
            else GroupReason(kind=LIKENESS_REASON)
            for person_id in everyone
        }

    async def _groups_standing(self, viewer: Viewer, person_id: str) -> dict[str, _MayBe]:
        """The groups one person's card offers now, by id: what an answer may act on."""
        cards, _shown = await self._may_be_cards(viewer, only=person_id)
        return {one.pile_id: one for _id, _name, groups in cards for one in groups}

    async def confirm_groups(
        self, viewer: Viewer, person_id: str, pile_ids: Sequence[str], track_ids: Sequence[str]
    ) -> RunAnswered:
        """Yes, these groups are her: the faces shown are confirmed, the rest are offered.

        Only faces in a group the card still offers are confirmed; a folder proposal answered here
        is accepted. One receipt, undone by `unname_groups`.
        """
        await self._require_enabled()
        if not await self.may_see_person(viewer, person_id):
            return RunAnswered(changed=0)
        standing = await self._groups_standing(viewer, person_id)
        chosen = [pile_id for pile_id in dict.fromkeys(pile_ids) if pile_id in standing]
        in_groups = {track.id for pile_id in chosen for track in standing[pile_id].faces}
        seen = [track_id for track_id in dict.fromkeys(track_ids) if track_id in in_groups]
        actionable = await self.touchable_faces(viewer, seen)
        naming = list(actionable.allowed)
        if not naming:
            return RunAnswered(changed=0)
        # Proposals are settled inside, before the receipt, so its Undo can reopen them.
        return await self._name_groups_recorded(
            viewer,
            person_id,
            naming,
            sorted({pile_id for pile_id in chosen if standing[pile_id].faces}),
            groups=len(chosen),
            proposals=chosen,
        )

    async def name_groups(
        self, viewer: Viewer, track_ids: Sequence[str], person_id: str
    ) -> RunAnswered:
        """Name these faces and offer the rest of their groups, with a group Yes's receipt."""
        await self._require_enabled()
        piles = sorted(
            {
                track.pile_id
                for track in (await self._store.tracks(track_ids)).values()
                if track.pile_id is not None
            }
        )
        if not piles:
            named, offered = await self.name_with_their_group(track_ids, person_id)
            return RunAnswered(changed=named, offered=offered)
        return await self._name_groups_recorded(
            viewer, person_id, list(track_ids), piles, groups=len(piles)
        )

    async def _name_groups_recorded(
        self,
        viewer: Viewer,
        person_id: str,
        naming: Sequence[str],
        piles: Sequence[str],
        *,
        groups: int,
        proposals: Sequence[str] = (),
    ) -> RunAnswered:
        """Both halves of naming groups and their one receipt; `proposals` settle as accepted."""
        # Read before any write, so the receipt can say which faces this press offered.
        before = await self._store.tracks_in_piles(list(piles))
        every = [track.id for tracks in before.values() for track in tracks]
        pictures = await self._store.reference_count(person_id)
        taught = Taught()
        named, _offered = await self.name_with_their_group(naming, person_id, taught=taught)
        if not named:
            return RunAnswered(changed=0)
        after = await self._store.tracks([*naming, *every])
        naming_set = set(naming)
        confirmed = [
            track_id
            for track_id in naming
            if (now := after.get(track_id)) is not None
            and now.person_id == person_id
            and now.attribution is Attribution.CONFIRMED
        ]
        offered = [
            track_id
            for track_id in every
            if track_id not in naming_set
            and (now := after.get(track_id)) is not None
            and now.person_id == person_id
            and now.attribution is Attribution.SUGGESTED
        ]
        # Only a proposal still asking moves (`settle_pile_proposal`).
        accepted = [
            pile_id
            for pile_id in (proposals if confirmed else ())
            if await self._store.settle_pile_proposal(pile_id, person_id, state="accepted")
        ]
        receipt = await self._record_named_groups(
            viewer,
            person_id,
            confirmed=confirmed,
            offered=offered,
            groups=groups,
            pictures=await self._store.reference_count(person_id) - pictures,
            assets=[after[track_id].asset_id for track_id in [*confirmed, *offered]],
            taught=taught,
            accepted=accepted,
        )
        log.info(
            "faces.groups_named",
            person_id=person_id,
            groups=groups,
            confirmed=len(confirmed),
            offered=len(offered),
        )
        return RunAnswered(changed=len(confirmed), offered=len(offered), decision_id=receipt)

    async def refuse_groups(
        self, viewer: Viewer, person_id: str, pile_ids: Sequence[str]
    ) -> RunAnswered:
        """No, these groups are not her: every face of theirs this user may act on is refused.

        Refused face by face through `reject`, so the answer outlasts a regrouping; a folder
        proposal is settled as refused. One receipt, undone by `unrefuse_groups`.
        """
        await self._require_enabled()
        if not await self.may_see_person(viewer, person_id):
            return RunAnswered(changed=0)
        standing = await self._groups_standing(viewer, person_id)
        chosen = [pile_id for pile_id in dict.fromkeys(pile_ids) if pile_id in standing]
        faces = [track for pile_id in chosen for track in standing[pile_id].faces]
        actionable = await self.touchable_faces(viewer, [track.id for track in faces])
        allowed = set(actionable.allowed)
        refusing = [track for track in faces if track.id in allowed]
        for track in refusing:
            await self.reject(track.id, person_id)
        if not refusing:
            return RunAnswered(changed=0)
        for pile_id in chosen:
            await self._store.settle_pile_proposal(pile_id, person_id, state="refused")
        receipt = await self._record_refused_groups(viewer, person_id, refusing, groups=len(chosen))
        log.info(
            "faces.groups_refused", person_id=person_id, groups=len(chosen), refused=len(refusing)
        )
        return RunAnswered(changed=len(refusing), decision_id=receipt)

    async def unname_groups(
        self,
        person_id: str,
        confirmed: Sequence[str],
        offered: Sequence[str],
        *,
        made: Mapping[str, Sequence[str]] | None = None,
    ) -> int:
        """Take back a Yes on a may-be card. Returns how many faces came back.

        Only faces still as the press left them go back to nobody, with what they filed; the loose
        faces are then grouped again.
        """
        await self._require_enabled()
        took = set(confirmed)
        rulings = [
            Ruling(
                track_id=track_id,
                was_person=person_id,
                was=Attribution.CONFIRMED if track_id in took else Attribution.SUGGESTED,
                person_id=None,
                attribution=None,
                confidence=None,
            )
            for track_id in dict.fromkeys([*confirmed, *offered])
        ]
        found = await self._store.tracks([ruling.track_id for ruling in rulings])
        landed = await self._store.restate(rulings)
        for track_id in confirmed:
            if track_id in landed:
                await self._store.forget_confirmation(track_id, person_id)
                await self._take_references_back(person_id, track_id, made)
        await self._settle_all(sorted({found[one].asset_id for one in landed if one in found}))
        if landed:
            await self.regroup(full=False)
        log.info("faces.groups_unnamed", person_id=person_id, faces=len(landed))
        return len(landed)

    async def unrefuse_groups(self, person_id: str, track_ids: Sequence[str]) -> int:
        """Take back a No on a may-be card, reopening any folder proposal it settled. How many."""
        await self._require_enabled()
        found = await self._store.tracks(list(dict.fromkeys(track_ids)))
        back: list[str] = []
        piles: set[str] = set()
        for track_id in dict.fromkeys(track_ids):
            track = found.get(track_id)
            if track is None or track.person_id is not None:
                continue
            await self._store.forget_rejection(track_id, person_id)
            back.append(track.asset_id)
            if track.pile_id is not None:
                piles.add(track.pile_id)
        for pile_id in sorted(piles):
            await self._store.settle_pile_proposal(pile_id, person_id, state="pending")
        await self._settle_all(sorted(set(back)))
        log.info("faces.groups_unrefused", person_id=person_id, faces=len(back))
        return len(back)

    async def _record_named_groups(
        self,
        viewer: Viewer,
        person_id: str,
        *,
        confirmed: Sequence[str],
        offered: Sequence[str],
        groups: int,
        pictures: int,
        assets: Sequence[str],
        taught: Taught | None = None,
        accepted: Sequence[str] = (),
    ) -> str:
        """The receipt for a Yes on a may-be card, with what it taught and proposals accepted."""
        if self._recorder is None or not confirmed:
            return ""
        name = await self.name_of(viewer, person_id)
        if name is None:  # pragma: no cover (the person was resolved a moment ago)
            return ""
        grouped = "1 group" if groups == 1 else f"{groups} groups"
        answered = await self._faces_answered(confirmed, person_id, FACE_SAID_YES)
        learned = await self._taught_payload(person_id, taught, set(confirmed))
        waiting = (
            ""
            if not offered
            else (
                f" The other {_faces_counted(len(offered))} in "
                f"{'it' if groups == 1 else 'them'} are waiting under Needs your input."
            )
        )
        subjects: list[Subject] = [Subject(kind="person", id=person_id)]
        subjects += [Subject(kind="asset", id=asset_id) for asset_id in dict.fromkeys(assets)]
        async with self._store.database.write() as connection:
            # Rung on the receipt's own commit, so a re-read tab finds it.
            announce(EVERY_ADMIN, About.LIBRARY)
            return await self._recorder.record_on(
                connection,
                queue=IDENTIFIED_QUEUE,
                user_id=viewer.id,
                title=f"You said {grouped} {'is' if groups == 1 else 'are'} {name}",
                detail=(
                    f"{_faces_counted(len(confirmed))} "
                    f"{'is' if len(confirmed) == 1 else 'are'} now Confirmed, and Sift "
                    f"learned from {_faces_counted(pictures)} of them.{waiting} Taking this back "
                    "takes the "
                    "name off all of them and removes the pictures. No file is touched and nothing "
                    "is deleted."
                ),
                payload=json.dumps(
                    {
                        "act": NAMED_GROUPS,
                        "person_id": person_id,
                        "track_ids": list(confirmed),
                        "offered": list(offered),
                        # The faces this press confirmed; the offered ones were only asked.
                        RECEIPT_FACES: answered,
                        **learned,
                        **({"proposals": list(accepted)} if accepted else {}),
                    }
                ),
                subjects=subjects,
            )

    async def _record_refused_groups(
        self, viewer: Viewer, person_id: str, refusing: Sequence[StoredTrack], *, groups: int
    ) -> str:
        """The receipt for a No on a "these groups may be her" card. See `unrefuse_groups`."""
        if self._recorder is None:
            return ""
        name = await self.name_of(viewer, person_id)
        if name is None:  # pragma: no cover (the person was resolved a moment ago)
            return ""
        grouped = "1 group" if groups == 1 else f"{groups} groups"
        subjects: list[Subject] = [Subject(kind="person", id=person_id)]
        subjects += [
            Subject(kind="asset", id=asset_id)
            for asset_id in dict.fromkeys(track.asset_id for track in refusing)
        ]
        async with self._store.database.write() as connection:
            # Rung on the receipt's own commit, so a re-read tab finds it.
            announce(EVERY_ADMIN, About.LIBRARY)
            return await self._recorder.record_on(
                connection,
                queue=IDENTIFIED_QUEUE,
                user_id=viewer.id,
                title=f"You said {grouped} {'is' if groups == 1 else 'are'} not {name}",
                detail=(
                    f"Sift will not offer the {_faces_counted(len(refusing))} in "
                    f"{'it' if groups == 1 else 'them'} as {name} again. Taking this back lets "
                    "Sift ask again. No file is touched and nothing is deleted."
                ),
                payload=json.dumps(
                    {
                        "act": REFUSED_GROUPS,
                        "person_id": person_id,
                        "track_ids": [track.id for track in refusing],
                        RECEIPT_FACES: [
                            _face_answered(person_id, track.asset_id, FACE_SAID_NO)
                            for track in refusing
                        ],
                    }
                ),
                subjects=subjects,
            )

    async def proposals_for(
        self, viewer: Viewer, pile_ids: Sequence[str]
    ) -> dict[str, list[GroupProposalView]]:
        """What is proposed about these groups, as this viewer may be told it, keyed by group.

        Counted again as this viewer may see; a proposal whose person, folder or files they may
        not see is left out.
        """
        if not pile_ids or not await self.enabled():
            return {}
        stored = await self._store.pile_proposals(pile_ids)
        if not stored:
            return {}
        people = await self._repository.visible_people(
            viewer, sorted({one.person_id for one in stored})
        )
        folders = await self._repository.visible_folders_of(
            viewer, sorted({one.folder_id for one in stored})
        )
        tree = TreeReads(self._store.database)
        under: dict[str, set[str]] = {}
        found: dict[str, list[GroupProposalView]] = {}
        for one in stored:
            person = people.get(one.person_id)
            folder = folders.get(one.folder_id)
            if person is None or folder is None:
                continue
            files = await self._store.unnamed_files_of_pile(one.pile_id)
            shown = set(await self._shown_of(viewer, files)) if files else set()
            if one.folder_id not in under:
                under[one.folder_id] = set(await tree.assets_under(one.folder_id))
            in_folder = len(shown & under[one.folder_id])
            if not in_folder:
                continue
            found.setdefault(one.pile_id, []).append(
                GroupProposalView(
                    person_id=one.person_id,
                    person_name=person.name,
                    reason=one.reason,
                    folder_id=one.folder_id,
                    folder_name=folder.name,
                    in_folder=in_folder,
                    group_files=len(shown),
                )
            )
        return found


def _near_people(
    people: Sequence[str], grid: list[list[float]], row_of: Mapping[str, int]
) -> dict[str, list[str]]:
    """The people over the ask line for each group, closest first."""
    near: dict[str, list[str]] = {}
    for pile_id, row in row_of.items():
        over = [
            (float(grid[row][column]), person_id)
            for column, person_id in enumerate(people)
            if grid[row][column] >= tuning.GROUP_ASK
        ]
        if over:
            near[pile_id] = [person_id for _score, person_id in sorted(over, reverse=True)]
    return near


def _gathered(
    near: Mapping[str, list[str]],
    proposed: Mapping[str, list[GroupProposalView]],
    likeness: Callable[[str, str], float | None],
    answered_no: Callable[[str, str], bool],
    reason_of: Mapping[str, GroupReason],
) -> dict[str, dict[str, _MayBe]]:
    """The groups each person may be, by likeness to the closest not refused and by proposal."""
    found: dict[str, dict[str, _MayBe]] = {}
    for pile_id, candidates in near.items():
        for person_id in candidates:
            if answered_no(pile_id, person_id):
                continue
            found.setdefault(person_id, {})[pile_id] = _MayBe(
                pile_id=pile_id,
                likeness=likeness(pile_id, person_id),
                reasons=[reason_of[person_id]],
                faces=[],
            )
            break
    for pile_id, proposals in proposed.items():
        for one in proposals:
            if answered_no(pile_id, one.person_id):
                continue
            entry = found.setdefault(one.person_id, {}).setdefault(
                pile_id,
                _MayBe(
                    pile_id=pile_id,
                    likeness=likeness(pile_id, one.person_id),
                    reasons=[],
                    faces=[],
                ),
            )
            entry.reasons.append(
                GroupReason(
                    kind=one.reason,
                    folder_id=one.folder_id,
                    folder_name=one.folder_name,
                    in_folder=one.in_folder,
                    group_files=one.group_files,
                )
            )
    return found


def _cards_of(
    found: Mapping[str, dict[str, _MayBe]],
    names: Mapping[str, str | None],
    held: Mapping[str, list[StoredTrack]],
    shown: Mapping[str, bool],
) -> list[tuple[str, str, list[_MayBe]]]:
    """The cards with a name and a face this viewer may see, groups and cards closest first."""
    cards: list[tuple[str, str, list[_MayBe]]] = []
    for person_id, groups in found.items():
        name = names.get(person_id)
        if name is None:
            continue
        kept: list[_MayBe] = []
        for entry in groups.values():
            entry.faces = [
                track for track in held.get(entry.pile_id, []) if track.asset_id in shown
            ]
            if entry.faces:
                kept.append(entry)
        if kept:
            kept.sort(key=_closest_first)
            cards.append((person_id, name, kept))
    cards.sort(key=lambda card: (_closest_first(card[2][0]), card[1].casefold(), card[0]))
    return cards
