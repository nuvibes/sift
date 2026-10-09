# SPDX-License-Identifier: AGPL-3.0-or-later
"""The faces as the workbench sees them: one page of five tabs, each a tier of one list.

The receipts keep their old queue names (`ignored`, `identified`) as reversers, since a decision on
disk is found again by the name it was written under. Every decision stays in the service.
"""

from __future__ import annotations

import json
from typing import Any

from sift.kernel.access import Viewer
from sift.kernel.access.sentences import (
    FACES_CONFIRMED,
    FACES_MATCHED,
    FACES_WAITING,
    faces_of_person,
)
from sift.kernel.jobs import JobQueue
from sift.kernel.workbench import (
    FACE,
    Band,
    Preview,
    Recorded,
    Reversal,
    Summary,
    Worded,
)
from sift.slices.faces.jobs import ask_for_rematching
from sift.slices.faces.models import Attribution, ToCheckKind, ToCheckShow
from sift.slices.faces.receipts import DISAGREEMENTS_QUEUE
from sift.slices.faces.service import (
    AGREED_WITH_MATCHES,
    AGREED_WITH_PROPOSALS,
    FACE_QUEUE,
    IDENTIFIED_QUEUE,
    NAMED_GROUPS,
    REFUSED_FACES,
    REFUSED_GROUPS,
    STOPPED_ASKING,
    FaceService,
    Sighting,
    ToCheckView,
)
from sift.slices.faces.service_disagreements import filed_rows
from sift.slices.faces.worded import identified_said, ignored_said

#: The people Sift is proposing, first in the group, so the board's Faces card opens its page.
SUGGESTIONS = "faces"

#: The names a pass filed that the face in the file disagrees with.
DISAGREEMENTS = DISAGREEMENTS_QUEUE

#: The groups of look-alike faces nobody has named yet.
TO_NAME = "faces-to-name"

#: The groups somebody discarded, a record. `ignored` is a receipt's name, and the stored word.
SET_ASIDE = "discarded-faces"

#: The people Sift has faces for; also half of `_person_href`'s address.
PEOPLE_KNOWN = "known-people"

#: Where a decision about a set-aside group is recorded: a reverser now, not a queue.
IGNORED = FACE_QUEUE

#: Where a decision a match pass took on its own is recorded; also a reverser.
IDENTIFIED = IDENTIFIED_QUEUE

#: The page the two share: the record beside the work.
FACES_GROUP = "faces"

#: How many items a card puts on screen before it says "and N more".
PREVIEW = 24


def _pile_href(pile_id: str) -> str:
    """Where one group of faces lives, whatever its status: the group, never the face."""
    return f"/organize/{TO_NAME}/{pile_id}"


def _person_href(person_id: str, *, show: str = FACES_WAITING) -> str:
    """Where one person's faces are looked at; `sentences.faces_of_person` spells the address."""
    return faces_of_person(person_id, show)


class _FaceQueue:
    """What everything here shares: it is all absent unless recognition is switched on."""

    #: Declared because the shared code reads it; each queue sets its own.
    name: str

    def __init__(self, service: FaceService) -> None:
        self._service = service

    async def available(self) -> bool:
        """Whether recognizing faces is switched on at all: off is not a count of zero."""
        return await self._service.enabled()

    async def pictures_of(self, viewer: Viewer, payload: str) -> tuple[Preview, ...]:
        """The faces the decision was taken on, from the group it named."""
        try:
            recorded: Any = json.loads(payload)
            pile_id = str(recorded["pile_id"])
        except (ValueError, KeyError, TypeError):
            return ()
        found = await self._service.pile(viewer, pile_id, limit=PREVIEW, offset=0)
        if found is None:
            return ()
        group, _total = found
        where = _pile_href(pile_id)
        return tuple(
            Preview(kind=FACE, id=face.track_id, href=where)
            for face in group.faces[:PREVIEW]
            if not face.locked
        )


class SuggestionsQueue(_FaceQueue):
    """The people Sift is proposing, surest first, and the tab the Faces page opens on."""

    name = SUGGESTIONS
    title = "Faces to confirm"
    band = Band.DECISION
    group = FACES_GROUP
    #: The whole page's name: the queue heading the group names the card.
    group_title = "Faces"
    purpose = "Confirm who is in your files, and name the faces Sift doesn't know."
    reversible = True

    async def survey(self, viewer: Viewer) -> Summary:
        items, total, _small = await self._service.to_check(
            # Her standing questions and the groups that may be her, counted as one.
            viewer,
            kind=(ToCheckKind.PERSON, ToCheckKind.MAY_BE),
            limit=PREVIEW,
        )
        return Summary(
            name=SUGGESTIONS,
            title=self.title,
            # The board's Faces card counts every pending tab of the group.
            verb="questions about faces",
            verb_one="question about faces",
            decision=(
                "Confirm faces that match people you've already named, or open a group to review "
                "each face."
            ),
            icon="person",
            count=total,
            advice=(
                "Work from the top. The first is the match Sift is surest of, and each answer "
                "helps Sift recognize that person."
            ),
            preview=tuple(
                shown
                for item in items[:PREVIEW]
                if (shown := _picture(item, _suggestion_href(item))) is not None
            ),
        )

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        """Nothing. An agreement's receipt is written under `IDENTIFIED`, which takes it back."""
        return False


def _drawn(item: ToCheckView) -> Sighting | None:
    """The face a card draws for this row: the first not behind a shut vault, or None."""
    return next((face for face in item.faces if not face.locked), None)


def _picture(item: ToCheckView, href: str | None) -> Preview | None:
    """The row's picture on the board, leading to `href`, or None when it has none to draw."""
    face = _drawn(item)
    return None if face is None else Preview(kind=FACE, id=face.track_id, href=href)


def _suggestion_href(item: ToCheckView) -> str:
    """Where a crop on the Needs your input strip leads: her screen, or a may-be card's group."""
    if item.kind is ToCheckKind.MAY_BE and item.groups:
        return _pile_href(item.groups[0].pile_id)
    return _person_href(item.id)


class DisagreementsQueue(_FaceQueue):
    """The names a pass filed that the face in the file disagrees with: work done wrongly."""

    name = DISAGREEMENTS
    title = "Disagreements"
    band = Band.DECISION
    group = FACES_GROUP
    group_title = None
    purpose = "Files whose face and name disagree, for you to choose which is right."
    #: A No over her files is written under this queue's name and taken back here.
    reversible = True

    async def survey(self, viewer: Viewer) -> Summary:
        items, total, _small = await self._service.to_check(
            viewer, kind=ToCheckKind.MISMATCH, limit=PREVIEW
        )
        return Summary(
            name=DISAGREEMENTS,
            title=self.title,
            verb="files where the face and the name disagree",
            verb_one="file where the face and the name disagree",
            decision=(
                "Each file already has a name, and its face is named as someone else. Choose "
                "which is right."
            ),
            icon="person_alert",
            count=total,
            advice=(
                "Each of these names is already in use in your library. Correcting one fixes the "
                "name everywhere."
            ),
            preview=tuple(
                shown
                for item in items[:PREVIEW]
                if (shown := _picture(item, _file_href(item.id))) is not None
            ),
        )

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        """Put her back on the files a No took her off, each row as it was. True if any moved."""
        read = filed_rows(payload)
        if read is None:
            return False
        person_id, rows = read
        return await self._service.put_filed_back(person_id, rows) > 0


class ToNameQueue(_FaceQueue):
    """The groups of look-alike faces nobody has named, largest first, above the stranger floor."""

    name = TO_NAME
    title = "Unnamed faces"
    band = Band.DECISION
    group = FACES_GROUP
    group_title = None
    purpose = "Groups of look-alike faces that Sift can't name yet."
    reversible = True

    async def survey(self, viewer: Viewer) -> Summary:
        items, total, _small = await self._service.to_check(
            viewer, kind=ToCheckKind.GROUP, limit=PREVIEW
        )
        return Summary(
            name=TO_NAME,
            title=self.title,
            verb="groups of faces to name",
            verb_one="group of faces to name",
            decision=("Name a group of look-alike faces. One answer names every face in it."),
            icon="group",
            count=total,
            advice=(
                "The largest group is first. Naming it teaches Sift the most, and smaller groups "
                "of the same face join it on their own."
            ),
            preview=tuple(
                shown
                for item in items[:PREVIEW]
                if (shown := _picture(item, _pile_href(item.id))) is not None
            ),
        )

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        """Put a group back among the ones waiting."""
        recorded: Any = json.loads(payload)
        return await self._service.restore(str(recorded["pile_id"]))


class SetAsideQueue(_FaceQueue):
    """The groups somebody put aside: a record, as a tab rather than a filter behind a control."""

    name = SET_ASIDE
    title = "Discarded"
    band = Band.RECORD
    group = FACES_GROUP
    group_title = None
    purpose = None
    reversible = True

    async def survey(self, viewer: Viewer) -> Summary:
        items, total, _small = await self._service.to_check(
            viewer, show=ToCheckShow.IGNORED, limit=PREVIEW
        )
        return Summary(
            name=SET_ASIDE,
            title=self.title,
            verb="groups you discarded",
            verb_one="group you discarded",
            decision="Groups you discarded. Sift doesn't ask about them again. Restore one at any time.",
            # The Discard glyph, as on the verbs (`DISCARD_ICON` in the client).
            icon="remove",
            count=total,
            preview=tuple(
                shown
                for item in items[:PREVIEW]
                if (shown := _picture(item, _pile_href(item.id))) is not None
            ),
        )

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        """Put a group back among the ones waiting."""
        recorded: Any = json.loads(payload)
        return await self._service.restore(str(recorded["pile_id"]))


def _file_href(asset_id: str) -> str:
    """Where one file lives, for a crop on a mismatched row: the row is about the file."""
    return f"/asset/{asset_id}"


class PeopleKnownQueue(_FaceQueue):
    """The people Sift has faces for, by person and most recent first: a record, not a question."""

    name = PEOPLE_KNOWN
    title = "People Sift can recognize"
    band = Band.RECORD
    group = FACES_GROUP
    group_title = None
    purpose = None
    reversible = True

    async def survey(self, viewer: Viewer) -> Summary:
        # The wall's own order and count, one face per card.
        firsts, total = await self._service.known_people_preview(viewer, most=PREVIEW)
        return Summary(
            name=PEOPLE_KNOWN,
            title=self.title,
            verb="people",
            verb_one="person",
            decision="Who Sift has faces for, what you confirmed, and what needs your input.",
            icon="supervised_user_circle",
            count=total,
            preview=tuple(Preview(kind=FACE, id=track_id) for track_id in firsts),
        )

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        """Nothing. The receipts belong to `IdentifiedRecords`, which is registered beside this."""
        return False


class IgnoredRecords(_FaceQueue):
    """How a group set aside is put back: a reverser with no card, whose receipts keep its name."""

    #: A record with no card still decides under the faces card.
    group = FACES_GROUP

    name = IGNORED
    reversible = True

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        recorded: Any = json.loads(payload)
        return await self._service.restore(str(recorded["pile_id"]))

    def worded(self, recorded: Recorded) -> Worded | None:
        """This decision's line, worded when shown. See `worded.py` beside this file."""
        return ignored_said(recorded)


async def _as_found_now(service: FaceService, recorded: dict[str, Any]) -> dict[str, Any]:
    """A receipt's payload with every face it names by the id that face goes by now.

    The `cover` is not read through: a cover keeps naming the face it was cut from.
    """
    named = [str(one) for one in recorded.get("track_ids") or []]
    offered = recorded.get("offered")
    offered_ids = [str(one) for one in offered] if isinstance(offered, list) else []
    maps = [
        key
        for key in ("confidence", "attribution", "references", "named_before")
        if isinstance(recorded.get(key), dict)
    ]
    keys = [str(one) for key in maps for one in recorded[key]]
    live = await service.live_ids([*named, *offered_ids, *keys])
    out = dict(recorded)
    out["track_ids"] = [live.get(one, one) for one in named]
    if isinstance(offered, list):
        out["offered"] = [live.get(one, one) for one in offered_ids]
    for key in maps:
        out[key] = {live.get(str(one), str(one)): value for one, value in recorded[key].items()}
    return out


def _named_before(
    held: object,
) -> list[tuple[str, dict[str, float | None], dict[str, str | None]]]:
    """A receipt's `named_before` as `unreject` takes it, per person; nothing if unreadable."""
    if not isinstance(held, dict):
        return []
    by_person: dict[str, tuple[dict[str, float | None], dict[str, str | None]]] = {}
    for track_id, was in held.items():
        if not isinstance(was, list) or len(was) != 3 or not isinstance(was[0], str):
            continue
        person_id, state, sure = was
        sures, states = by_person.setdefault(person_id, ({}, {}))
        sures[str(track_id)] = float(sure) if isinstance(sure, (int, float)) else None
        states[str(track_id)] = str(state) if isinstance(state, str) else None
    return [(person_id, sures, states) for person_id, (sures, states) in sorted(by_person.items())]


class IdentifiedRecords(_FaceQueue):
    """How a match pass's own decision is taken back: a reverser with no card of its own."""

    #: A record with no card still decides under the faces card.
    group = FACES_GROUP

    name = IDENTIFIED
    reversible = True

    def __init__(self, service: FaceService, *, queue: JobQueue | None = None) -> None:
        super().__init__(service)
        #: Where a re-match is asked for after an Undo took pictures away; None where nothing runs.
        self._queue = queue

    async def pictures_of(self, viewer: Viewer, payload: str) -> tuple[Preview, ...]:
        """The faces a re-match attached, leading to the person's page."""
        try:
            recorded: Any = json.loads(payload)
            person_id = str(recorded["person_id"])
            track_ids = [str(one) for one in recorded["track_ids"]]
        except (ValueError, KeyError, TypeError):
            return ()
        # Where those faces are now: an agreement is confirmed, a refusal is on no tab of hers.
        act = recorded.get("act")
        if act in (REFUSED_FACES, STOPPED_ASKING, REFUSED_GROUPS):
            where = None
        elif act in (AGREED_WITH_MATCHES, AGREED_WITH_PROPOSALS, NAMED_GROUPS):
            where = _person_href(person_id, show=FACES_CONFIRMED)
        else:
            where = _person_href(person_id, show=FACES_MATCHED)
        # Scoped as every picture of a decision is.
        actionable = await self._service.touchable_faces(viewer, track_ids[:PREVIEW])
        return tuple(Preview(kind=FACE, id=track_id, href=where) for track_id in actionable.allowed)

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool | Reversal:
        """Take back one act recorded under this name, from the payload. True if anything moved.

        Sift's own matches come off and are asked about; an agreement goes back to a match.
        """
        try:
            recorded: Any = json.loads(payload)
            person_id = str(recorded["person_id"])
            track_ids = [str(one) for one in recorded["track_ids"]]
        except (ValueError, KeyError, TypeError):
            return False
        # Every face by the id it goes by now, so an Undo after a rescan still reaches it.
        recorded = await _as_found_now(self._service, recorded)
        track_ids = [str(one) for one in recorded["track_ids"]]
        pictures = set(await self._service.own_reference_ids(person_id))
        moved = await self._reverse_act(recorded, person_id, track_ids)
        # What the press did beyond its faces, after them (`take_back_what_it_taught`).
        cover = recorded.get("cover")
        starters = recorded.get("starters")
        proposals = recorded.get("proposals")
        if await self._service.take_back_what_it_taught(
            viewer,
            person_id,
            cover=cover if isinstance(cover, str) else None,
            starters=[str(one) for one in starters] if isinstance(starters, list) else [],
            proposals=[str(one) for one in proposals] if isinstance(proposals, list) else [],
        ):
            moved = True
        # Recognitions resting only on the pictures this Undo took away go too (`Reversal.along`).
        removed = pictures - set(await self._service.own_reference_ids(person_id))
        along, _faces = await self._service.take_back_recognitions(
            person_id, removed, since=receipt_id
        )
        # Where it took her last picture, the questions about her go back to nobody.
        offered = recorded.get("offered")
        own = (
            [*track_ids, *(str(one) for one in offered)] if isinstance(offered, list) else track_ids
        )
        if removed and await self._service.return_questions(person_id, leaving=own):
            moved = True
        # A gallery that lost pictures matches differently.
        if self._queue is not None and removed:
            await ask_for_rematching(self._queue)
        if along:
            return Reversal(put_back=1, of=1, along=tuple(along))
        return moved

    async def _reverse_act(
        self, recorded: dict[str, Any], person_id: str, track_ids: list[str]
    ) -> bool:
        """The faces of one receipt put back, by the act it records; `made` per confirmed face."""
        act = recorded.get("act")
        kept_ids = recorded.get("references")
        made: dict[str, list[str]] | None = (
            {
                str(one): [str(row) for row in rows]
                for one, rows in kept_ids.items()
                if isinstance(rows, list)
            }
            if isinstance(kept_ids, dict)
            else None
        )
        if act == REFUSED_FACES:
            kept = recorded.get("confidence")
            held: dict[str, float | None] = (
                {str(one): value for one, value in kept.items()}
                if isinstance(kept, dict) and kept
                else dict.fromkeys(track_ids)
            )
            states = recorded.get("attribution")
            was: dict[str, str | None] = (
                {str(one): (None if value is None else str(value)) for one, value in states.items()}
                if isinstance(states, dict)
                else {}
            )
            return await self._service.unreject(person_id, held, was) > 0
        if act in (AGREED_WITH_MATCHES, AGREED_WITH_PROPOSALS):
            return await self._reverse_agreement(recorded, act, person_id, track_ids, made)
        if act == NAMED_GROUPS:
            # A may-be Yes: back to nobody, and any name a disagreement's Yes took off goes back on.
            offered = recorded.get("offered")
            back = await self._service.unname_groups(
                person_id,
                track_ids,
                [str(one) for one in offered] if isinstance(offered, list) else [],
                made=made,
            )
            before = recorded.get("named_before")
            for other, sure, was in _named_before(before):
                back += await self._service.unreject(other, sure, was)
            return back > 0
        if act == REFUSED_GROUPS:
            return await self._service.unrefuse_groups(person_id, track_ids) > 0
        if act == STOPPED_ASKING:
            # Withdrawn questions are asked again at their old confidence, or none.
            kept = recorded.get("confidence")
            asked: dict[str, float | None] = (
                {str(one): value for one, value in kept.items()}
                if isinstance(kept, dict) and kept
                else dict.fromkeys(track_ids)
            )
            return await self._service.ask_again(person_id, asked) > 0
        # A run Sift matched on its own: a face that was a question goes back to being one.
        states = recorded.get("attribution")
        numbers = recorded.get("confidence")
        was_asked: dict[str, float | None] = (
            {
                str(one): (numbers.get(one) if isinstance(numbers, dict) else None)
                for one, value in states.items()
                if value == Attribution.SUGGESTED.value
            }
            if isinstance(states, dict)
            else {}
        )
        return await self._service.unmatch(track_ids, person_id, was_asked=was_asked) > 0

    async def _reverse_agreement(
        self,
        recorded: dict[str, Any],
        act: object,
        person_id: str,
        track_ids: list[str],
        made: dict[str, list[str]] | None,
    ) -> bool:
        """`_reverse_act` for an agreement with matches or proposals."""
        # The kept confidences, or none each: a missing map must still take faces back.
        agreed = recorded.get("confidence")
        sure: dict[str, float | None] = (
            {str(one): value for one, value in agreed.items()}
            if isinstance(agreed, dict) and agreed
            else dict.fromkeys(track_ids)
        )
        # Back to what the act says each face was.
        back = await self._service.unconfirm_matches(
            person_id,
            sure,
            back_to=(
                Attribution.SUGGESTED if act == AGREED_WITH_PROPOSALS else Attribution.MATCHED
            ),
            made=made,
        )
        # The rest of their groups, offered as her, go back to nobody (`unname_groups`).
        offered = recorded.get("offered")
        if isinstance(offered, list) and offered:
            back += await self._service.unname_groups(person_id, [], [str(one) for one in offered])
        return back > 0

    def worded(self, recorded: Recorded) -> Worded | None:
        """This decision's line, worded when shown. See `worded.py` beside this file."""
        return identified_said(recorded)
