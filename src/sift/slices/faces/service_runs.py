# SPDX-License-Identifier: AGPL-3.0-or-later
"""The answers over one person's whole run of faces: agreeing with or refusing every proposal or
match standing for her, and taking such an answer back, each with its receipt."""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping, Sequence

from sift.kernel.access import Viewer
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, announce
from sift.kernel.log import get_logger
from sift.kernel.vocabulary import FACE_SAID_NO, FACE_SAID_YES, RECEIPT_FACES, Subject
from sift.slices.faces.models import Attribution
from sift.slices.faces.receipts import (
    AGREED_WITH_MATCHES,
    AGREED_WITH_PROPOSALS,
    IDENTIFIED_QUEUE,
    REFUSED_FACES,
)
from sift.slices.faces.service_base import _face_answered
from sift.slices.faces.service_decisions import DecisionsMixin, RunAnswered, Taught
from sift.slices.faces.service_identified import IdentifiedMixin
from sift.slices.faces.service_visibility import Sighting

log = get_logger(__name__)


def _narrowed(theirs: list[Sighting], only: Collection[str] | None) -> list[Sighting]:
    """One person's faces of one kind, narrowed to the ones a press named, or all for `None`.

    The server decides which named ids still stand, so no id can widen the press.
    """
    if only is None:
        return theirs
    named = set(only)
    return [face for face in theirs if face.track_id in named]


class RunsMixin(DecisionsMixin, IdentifiedMixin):
    """Answering for a person's run of faces in one press."""

    async def confirm_look_alikes(
        self, viewer: Viewer, person_id: str, *, only: Collection[str] | None = None
    ) -> RunAnswered:
        """Agree with every proposal standing for one person: how many were named, how many more
        were offered, and the receipt.

        `accept_suggestions` names them, as `confirm_many` skips faces carrying a person, then
        `_offer_their_groups`; piles are read first. Asked of the person, never the card's page,
        and only faces this user may still act on.
        """
        await self._require_enabled()
        theirs = await self._standing_to_agree(viewer, person_id, only)
        actionable = await self.touchable_faces(viewer, [face.track_id for face in theirs])
        if not actionable.allowed:
            return RunAnswered(changed=0)
        named = list(actionable.allowed)
        # How sure Sift was of each, read before confirming writes certainty over it.
        agreeing = [face for face in theirs if face.track_id in actionable.allowed]
        sure = {face.track_id: face.confidence for face in agreeing}
        piles = {
            track.pile_id
            for track in (await self._store.tracks(named)).values()
            if track.pile_id is not None
        }
        before = await self._store.reference_count(person_id)
        taught = Taught()
        agreed = await self.accept_suggestions(named, taught=taught)
        if not agreed:
            # Nothing was agreed to, so there is nothing to extend.
            return RunAnswered(changed=0)
        # Offered before the receipt, so it can name the faces offered.
        offered = await self._offer_their_groups(piles, person_id)
        receipt = await self._record_agreement(
            viewer,
            person_id,
            sure,
            assets=[face.asset_id for face in agreeing],
            pictures=await self._store.reference_count(person_id) - before,
            act=AGREED_WITH_PROPOSALS,
            offered=offered,
            taught=taught,
        )
        log.info(
            "faces.look_alikes_agreed", person_id=person_id, named=agreed, offered=len(offered)
        )
        return RunAnswered(changed=agreed, offered=len(offered), decision_id=receipt)

    async def look_alikes_to_agree(
        self, viewer: Viewer, person_id: str, *, only: Collection[str] | None = None
    ) -> list[str]:
        """The faces `confirm_look_alikes` would agree to for this viewer now, by id, so later
        work keeps the press's reach."""
        await self._require_enabled()
        theirs = await self._standing_to_agree(viewer, person_id, only)
        touchable = await self.touchable_faces(viewer, [face.track_id for face in theirs])
        return list(touchable.allowed)

    async def _standing_to_agree(
        self, viewer: Viewer, person_id: str, only: Collection[str] | None
    ) -> list[Sighting]:
        """The proposals standing for her that a press names, none for a person withheld."""
        if not await self.may_see_person(viewer, person_id):
            return []
        return _narrowed(
            await self._sightings_for(viewer, person_id, attribution=Attribution.SUGGESTED), only
        )

    async def reject_look_alikes(
        self, viewer: Viewer, person_id: str, *, only: Collection[str] | None = None
    ) -> RunAnswered:
        """Refuse every proposal standing for one person: how many were written, and the receipt.

        Asked of the person, through `reject`; a withheld person answers "nothing changed".
        """
        return await self._refuse_run(viewer, person_id, Attribution.SUGGESTED, only)

    async def reject_matches(
        self, viewer: Viewer, person_id: str, *, only: Collection[str] | None = None
    ) -> RunAnswered:
        """Refuse every match Sift made for one person: how many were written, and the receipt."""
        return await self._refuse_run(viewer, person_id, Attribution.MATCHED, only)

    async def _refuse_run(
        self,
        viewer: Viewer,
        person_id: str,
        attribution: Attribution,
        only: Collection[str] | None = None,
    ) -> RunAnswered:
        """Refuse every face of one kind standing for one person, through `reject`."""
        await self._require_enabled()
        if not await self.may_see_person(viewer, person_id):
            return RunAnswered(changed=0)
        theirs = _narrowed(
            await self._sightings_for(viewer, person_id, attribution=attribution), only
        )
        # Asked again: a file that moved into the vault since is skipped.
        actionable = await self.touchable_faces(viewer, [face.track_id for face in theirs])
        refusing = [face for face in theirs if face.track_id in actionable.allowed]
        refused = 0
        for face in refusing:
            await self.reject(face.track_id, person_id)
            refused += 1
        receipt = ""
        if refused:
            receipt = await self._record_refusal(viewer, person_id, refusing, attribution)
            log.info(
                "faces.run_refused",
                person_id=person_id,
                refused=refused,
                attribution=attribution.value,
            )
        return RunAnswered(changed=refused, decision_id=receipt)

    async def _record_refusal(
        self,
        viewer: Viewer,
        person_id: str,
        refusing: Sequence[Sighting],
        attribution: Attribution,
    ) -> str:
        """Write down that somebody refused a run of faces, each face's state per face."""
        if self._recorder is None:
            return ""
        name = await self.name_of(viewer, person_id)
        if name is None:  # pragma: no cover (the person was resolved a moment ago)
            return ""
        one = len(refusing) == 1
        many = "1 face" if one else f"{len(refusing)} faces"
        was = (
            "waiting under Needs your input"
            if attribution is Attribution.SUGGESTED
            else "Recognized by Sift"
        )
        # The verb and pronouns agree with the count (`worded.identified_said` words older ones).
        subjects: list[Subject] = [Subject(kind="person", id=person_id)]
        subjects += [
            Subject(kind="asset", id=asset_id)
            for asset_id in dict.fromkeys(face.asset_id for face in refusing)
        ]
        async with self._store.database.write() as connection:
            # Rung on the receipt's own commit, so a re-read tab finds it.
            announce(EVERY_ADMIN, About.LIBRARY)
            return await self._recorder.record_on(
                connection,
                queue=IDENTIFIED_QUEUE,
                user_id=viewer.id,
                title=f"You said {many} {'is' if one else 'are'} not {name}",
                detail=(
                    f"{'It was' if one else 'They were'} {was}. Taking this back puts the name on "
                    f"{'it' if one else 'them'} again, as it was, and lets Sift offer it there in "
                    "future. No file is touched and nothing is deleted."
                ),
                payload=json.dumps(
                    {
                        "act": REFUSED_FACES,
                        "person_id": person_id,
                        RECEIPT_FACES: [
                            _face_answered(person_id, face.asset_id, FACE_SAID_NO)
                            for face in refusing
                        ],
                        "track_ids": [face.track_id for face in refusing],
                        "confidence": {face.track_id: face.confidence for face in refusing},
                        "attribution": {
                            face.track_id: (
                                face.attribution.value if face.attribution is not None else None
                            )
                            for face in refusing
                        },
                    }
                ),
                subjects=subjects,
            )

    async def confirm_matches(
        self, viewer: Viewer, person_id: str, *, only: Collection[str] | None = None
    ) -> tuple[int, int]:
        """Agree with every match Sift made for one person. Returns `(confirmed, references)`.

        The one press that confirms faces already carrying a person, whatever their confidence.
        Faces are chosen before writing, for the receipt; references are counted as a difference.
        """
        await self._require_enabled()
        if not await self.may_see_person(viewer, person_id):
            return 0, 0
        theirs = _narrowed(
            await self._sightings_for(viewer, person_id, attribution=Attribution.MATCHED), only
        )
        actionable = await self.touchable_faces(viewer, [face.track_id for face in theirs])
        # How sure Sift was of each, kept because confirming overwrites it.
        agreeing = [face for face in theirs if face.track_id in actionable.allowed]
        sure = {face.track_id: face.confidence for face in agreeing}
        if not sure:
            return 0, 0
        before = await self._store.reference_count(person_id)
        taught = Taught()
        agreed = await self.accept_suggestions(list(sure), taught=taught)
        if not agreed:  # pragma: no cover (every face here was read as this person's a moment ago)
            return 0, 0
        pictures = await self._store.reference_count(person_id) - before
        await self._record_agreement(
            viewer,
            person_id,
            sure,
            # The files, off the appearances already read.
            assets=[face.asset_id for face in agreeing],
            pictures=pictures,
            taught=taught,
        )
        log.info("faces.matches_agreed", person_id=person_id, confirmed=agreed, references=pictures)
        return agreed, pictures

    async def _record_agreement(
        self,
        viewer: Viewer,
        person_id: str,
        sure: Mapping[str, float | None],
        *,
        assets: Sequence[str],
        pictures: int,
        act: str = AGREED_WITH_MATCHES,
        offered: Sequence[str] = (),
        taught: Taught | None = None,
    ) -> str:
        """Write down that somebody agreed with a run of faces, with a way to take it back.

        The payload's act says where each face goes back to; `offered` and `taught` let the undo
        take back exactly what the press did.
        """
        if self._recorder is None:
            return ""
        name = await self.name_of(viewer, person_id)
        if name is None:  # pragma: no cover (the person was resolved a moment ago)
            return ""
        one = len(sure) == 1
        proposals = act == AGREED_WITH_PROPOSALS
        if proposals:
            many = "1 face" if one else f"{len(sure)} faces"
            back = "waiting under Needs your input, as they were"
        else:
            many = "1 match" if one else f"{len(sure)} matches"
            back = "Recognized by Sift, as they were"
        pictured = "1 face" if pictures == 1 else f"{pictures} faces"
        answered = await self._faces_answered(list(sure), person_id, FACE_SAID_YES)
        learned = await self._taught_payload(person_id, taught, set(sure))
        subjects: list[Subject] = [Subject(kind="person", id=person_id)]
        subjects += [Subject(kind="asset", id=asset_id) for asset_id in dict.fromkeys(assets)]
        async with self._store.database.write() as connection:
            # Rung on the receipt's own commit, so a re-read tab finds it.
            announce(EVERY_ADMIN, About.LIBRARY)
            return await self._recorder.record_on(
                connection,
                queue=IDENTIFIED_QUEUE,
                user_id=viewer.id,
                title=f"You agreed with {many} for {name}",
                detail=(
                    f"Sift learned from {pictured} of theirs. Taking this back leaves "
                    f"{'that appearance' if one else 'those appearances'} {back}, and "
                    "removes the pictures. No file is touched and nothing is deleted."
                ),
                payload=json.dumps(
                    {
                        "act": act,
                        "person_id": person_id,
                        "track_ids": list(sure),
                        "confidence": dict(sure),
                        "offered": list(offered),
                        RECEIPT_FACES: answered,
                        **learned,
                    }
                ),
                subjects=subjects,
            )

    async def unconfirm_matches(
        self,
        person_id: str,
        confidence: Mapping[str, float | None],
        *,
        back_to: Attribution = Attribution.MATCHED,
        made: Mapping[str, Sequence[str]] | None = None,
    ) -> int:
        """Put agreed-with faces back to what they were (`back_to`). Returns how many went back.

        Never detached: they keep the person, with the confidence the receipt kept. The pictures
        filed (`made`) and the remembered confirmation go, from the receipt, never the state.
        """
        await self._require_enabled()
        taken: list[str] = []
        found = await self._store.tracks(list(confidence))
        for track_id in dict.fromkeys(confidence):
            track = found.get(track_id)
            if track is None or track.person_id != person_id:
                continue
            if track.attribution is not Attribution.CONFIRMED:
                continue
            gone = await self._take_references_back(person_id, track_id, made)
            await self._store.forget_confirmation(track_id, person_id)
            await self._store.attribute(
                track_id,
                person_id,
                confidence=confidence[track_id],
                attribution=back_to,
            )
            if gone:
                log.info("faces.reference.withdrawn", person_id=person_id, pictures=gone)
            taken.append(track.asset_id)
        await self._settle_all(taken)
        log.info("faces.agreement_undone", person_id=person_id, faces=len(taken))
        return len(taken)

    async def unreject(
        self,
        person_id: str,
        confidence: Mapping[str, float | None],
        attribution: Mapping[str, str | None],
    ) -> int:
        """Put a refused run of faces back where it was. Returns how many went back.

        Each to its recorded state and confidence; the refusal is forgotten, nothing re-filed.
        """
        await self._require_enabled()
        taken: list[str] = []
        found = await self._store.tracks(list(confidence))
        for track_id in dict.fromkeys(confidence):
            track = found.get(track_id)
            if track is None or track.person_id is not None:
                continue
            word = attribution.get(track_id)
            back = (
                Attribution.MATCHED if word == Attribution.MATCHED.value else Attribution.SUGGESTED
            )
            await self._store.forget_rejection(track_id, person_id)
            await self._store.attribute(
                track_id, person_id, confidence=confidence[track_id], attribution=back
            )
            taken.append(track.asset_id)
        await self._settle_all(taken)
        log.info("faces.refusal_undone", person_id=person_id, faces=len(taken))
        return len(taken)
