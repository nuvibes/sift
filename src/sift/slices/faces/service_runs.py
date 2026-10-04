# SPDX-License-Identifier: AGPL-3.0-or-later
"""The answers over one person's whole run of faces: agreeing with or refusing every proposal or
every match that stands for her, and taking such an answer back, each with the receipt for it.
"""

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
    """One person's faces of one kind, cut down to the ones a press named, or all of them.

    The press names faces (a page, or a pick) and the SERVER decides which of those are still
    standing: an id is acted on only if it is in the set just read for this person and this state.
    So an id answered since the page was drawn, an id of somebody else's face, and an id that never
    existed are one case (not part of this press), and none of them can widen it.

    `None` is the whole tab. An empty collection is NOT: it narrows to nothing, which is what a
    press naming no faces means. The route refuses that request before it gets here (see
    `RunWrite`), and this keeps the two meanings apart underneath it too.
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

        **Both halves of naming a face by hand, and neither is `name_with_their_group` itself.**
        That call cannot be reused here and the reason is worth stating, because it is the obvious
        shape and it is wrong: `confirm_many` underneath it skips every face that ALREADY carries a
        person (deliberately, so naming a pile never overwrites a decision that has been made),
        and a proposal carries one by definition. Handed a page of proposals it writes nothing at
        all and answers zero, which reads exactly like a permission refusing them.

        So the two halves are called by name. `accept_suggestions` is the act for agreeing with
        what Sift proposed: each face confirmed as the person already on its row, which is the
        only place that answer can come from. `_offer_their_groups` is the other half, shared with
        naming by hand rather than copied: a face that sits in a group of look-alikes carries that
        group's claim with it, so the faces it was grouped with become proposals of their own,
        exactly as they do when somebody names one face in the player. Confirming one at a time
        would drop that silently, on the surface built to clear the backlog fastest.

        The piles are read BEFORE anything is confirmed, for the reason naming by hand reads them
        first: confirming empties a pile and an empty pile is dropped, so a pile read afterwards is
        one that may no longer exist, and the faces this is for are the ones left behind in it.

        **Asked of the PERSON rather than of the card's own page**, and that is not a second way of
        reading the same thing. The card is ranked by confidence and pages, so a press can arrive
        from any row of it: answering from the page it was drawn on would confirm whatever
        happened to be on the first page instead of what the button says. `_sightings_for` is the
        one read that narrows to one person, and it is what that person's own screen reads too, so
        the two surfaces cannot come to disagree about what is standing.

        Only what this user may act on: `touchable_faces` is asked again rather than trusted
        from the gather, so a proposal on a file that moved into the vault between the card being
        drawn and the button being pressed is skipped rather than confirmed.
        """
        await self._require_enabled()
        if not await self.may_see_person(viewer, person_id):
            return RunAnswered(changed=0)
        theirs = _narrowed(
            await self._sightings_for(viewer, person_id, attribution=Attribution.SUGGESTED), only
        )
        actionable = await self.touchable_faces(viewer, [face.track_id for face in theirs])
        if not actionable.allowed:
            return RunAnswered(changed=0)
        named = list(actionable.allowed)
        # How sure Sift was of each, read BEFORE anything is written, for the reason
        # `confirm_matches` reads it: confirming writes certainty over the comparison's own number,
        # so the receipt is the only place it could come back from.
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
            # Nothing was agreed to, so nothing was decided, so there is nothing to extend:
            # the same guard naming by hand makes, and for the same reason: offering a person to
            # twenty faces off the back of a call that wrote nothing is a decision nobody took.
            return RunAnswered(changed=0)
        # Offered BEFORE the receipt is written, so the receipt can name the faces it offered: the
        # rest of each group now asked about as her. Its undo sends them back to nobody, which is
        # where each was before the press (`IdentifiedRecords.reverse`).
        offered = await self._offer_their_groups(piles, person_id)
        # A receipt: naming a face again undoes one face and says nothing about a press that
        # settles two thousand, and every other press here writes one.
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

    async def reject_look_alikes(
        self, viewer: Viewer, person_id: str, *, only: Collection[str] | None = None
    ) -> RunAnswered:
        """Refuse every proposal standing for one person: how many were written, and the receipt.

        **The mirror of `confirm_look_alikes` above, and it is deliberately the same shape.** The
        card asks one question (do these faces look like her), and both answers are one press
        where it is asked. The row that opens the faces one at a time stays beside it for somebody
        who wants to look rather than already knows.

        Asked of the PERSON rather than of the card's page, for the reason agreeing is: the card is
        ranked and pages, so answering from the page it was drawn on would refuse whatever happened
        to be on the first page rather than what the button says.

        `reject` one face at a time underneath, which is the same door the review screen presses.
        One loop rather than a statement over the set, because rejecting is four acts (forgetting
        an earlier confirmation, withdrawing the pictures that face taught, writing the refusal, and
        settling the file it is on), and a second path through them is a second place for any of
        the four to be missed.

        It writes a receipt, as agreeing does: naming a face again is an undo for one face, which
        is no undo at all for a press that refuses two thousand of them.

        A person this user may not be told about answers "nothing changed" rather than refusing,
        exactly as agreeing does, so the route cannot be used to ask whether somebody exists.
        """
        return await self._refuse_run(viewer, person_id, Attribution.SUGGESTED, only)

    async def reject_matches(
        self, viewer: Viewer, person_id: str, *, only: Collection[str] | None = None
    ) -> RunAnswered:
        """Refuse every match Sift made for one person: how many were written, and the receipt.

        **The no beside the card's yes**: a card on People Sift can recognize offers a yes to what Sift
        decided on its own, and the opposite in the same press. The refusal has to reach the same
        set the yes confirms, or the two rows on one control answer two different questions.

        Not the same act as taking a re-match back (`unmatch`), and the difference is recorded
        there: an undo says "do not decide this for me", while this says "this is not them" and
        teaches the next pass. Somebody pressing No on a card is making the second claim.
        """
        return await self._refuse_run(viewer, person_id, Attribution.MATCHED, only)

    async def _refuse_run(
        self,
        viewer: Viewer,
        person_id: str,
        attribution: Attribution,
        only: Collection[str] | None = None,
    ) -> RunAnswered:
        """Refuse every face of one kind standing for one person, with a way back.

        One body for the two bulk refusals, because they differ in exactly one value (which state
        the faces were in), and two copies of four steps is two places for one of the four to be
        missed. It is the same argument `reject` makes for being one door.

        Asked of the PERSON rather than of the card's page, for the reason agreeing is: the card is
        ranked and pages, so answering from the page it was drawn on would refuse whatever happened
        to be on the first page rather than what the button says.

        `reject` one face at a time underneath, which is the same door the review screen presses.
        One loop rather than a statement over the set, because rejecting is four acts (forgetting
        an earlier confirmation, withdrawing the pictures that face taught, writing the refusal, and
        settling the file it is on), and a second path through them is a second place for any of
        the four to be missed.
        """
        await self._require_enabled()
        if not await self.may_see_person(viewer, person_id):
            return RunAnswered(changed=0)
        theirs = _narrowed(
            await self._sightings_for(viewer, person_id, attribution=attribution), only
        )
        # Asked again rather than trusted from the gather, as agreeing does: a proposal on a file
        # that moved into the vault between the card being drawn and the button being pressed is
        # skipped rather than refused.
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
        """Write down that somebody refused a run of faces, with a way to take it back.

        Under the same name the agreements are written under, because it is the same question
        (what has been decided about who this is), and a pile of its own for one button would be a
        screen nobody asked for. The payload's `act` is what tells the four apart.

        The state each face was in is written PER FACE rather than once for the run. The run is
        narrowed to one state today, so one word would be true; it would stop being true the first
        time a screen offers a No over a mixture, and a payload is read back months later by code
        that cannot ask what the press meant.
        """
        if self._recorder is None:
            return ""
        name = await self.name_of(viewer, person_id)
        if name is None:  # pragma: no cover (the person was resolved a moment ago)
            return ""
        one = len(refusing) == 1
        many = "1 face" if one else f"{len(refusing)} faces"
        # The state each face was in, in the state's own words (the three on every faces screen).
        was = (
            "waiting under Needs your input"
            if attribution is Attribution.SUGGESTED
            else "Recognized by Sift"
        )
        # The verb and the pronouns agree with the count: one face is the commonest No there is.
        # `worded.identified_said` words older receipts.
        subjects: list[Subject] = [Subject(kind="person", id=person_id)]
        subjects += [
            Subject(kind="asset", id=asset_id)
            for asset_id in dict.fromkeys(face.asset_id for face in refusing)
        ]
        async with self._store.database.write() as connection:
            # Rung on the receipt's own commit, so a tab that re-read on the press's earlier bell
            # reads again with this receipt in it, never one receipt short.
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

        **The deliberate exception to the rule `confirm_many` keeps**, and it is worth stating
        outright rather than leaving somebody to find it: that call skips every face that already
        carries a person, so that naming a pile can never overwrite a decision already made. A
        match IS a face carrying a person (it is the decision Sift took on its own), and this is
        the one press whose whole meaning is "those were right". Nothing else about who may be
        confirmed changes: the faces are this person's, on files this user may act on, and one
        somebody has already agreed to is left exactly where it is.

        Whatever the confidence. The bar Sift attaches at is a guess about where somebody would
        have agreed anyway; this is that person saying where they agree, and a face at 0.61 is no
        more theirs to keep than a face at 0.99.

        `accept_suggestions` does the writing, which is the same path the board's Confirm all goes
        through and the reason there is no loop here: each face is confirmed as the person already
        on its row, one settle per file afterwards, and a face becomes a reference by exactly one
        route. What this adds is the narrowing (to one person, to their MATCHED faces) and the
        receipt, because a press that files hundreds of reference pictures owes a way back.

        The faces are chosen BEFORE anything is written and the list is what the receipt carries.
        An undo that read the state afterwards could not tell a face this press confirmed from one
        somebody had agreed to last week, and taking back the second is not this decision's to do.

        Asked of the person rather than of a page, exactly as agreeing with every proposal is: what
        the button says is "these matches are right", and answering from whichever page the press
        came from would confirm the first fifty of them.

        The reference count is read on either side and the DIFFERENCE is reported. It is not the
        same as the number of faces: one appearance files one picture at most, a picture already
        held is refused by its own identity, and a crop that cannot be read files none. So counting
        the presses and calling them pictures would be a number nobody measured.
        """
        await self._require_enabled()
        if not await self.may_see_person(viewer, person_id):
            return 0, 0
        theirs = _narrowed(
            await self._sightings_for(viewer, person_id, attribution=Attribution.MATCHED), only
        )
        actionable = await self.touchable_faces(viewer, [face.track_id for face in theirs])
        # How sure Sift was of each, kept because confirming overwrites it: a confirmation is
        # certain by definition, so the number the match produced is gone the moment it is written
        # and the receipt is the only place it could come back from.
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
            # The files those faces are on, off the appearances already read rather than asked for
            # again: every sighting carries the file it was found in, and a second statement over
            # the same rows would be a second answer to a question already answered.
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

        `offered` is the rest of their groups, asked about as the same person because of the
        agreement; written down so the undo can take those questions back as well. `taught` is
        what the agreement filed, gave and retired (`Taught`), so the undo takes back that and no
        more.

        Under the same name the matches themselves were recorded under, because it is the same
        question (what has been decided about who this is), and a fifth pile on the board for
        one button would be a screen nobody asked for. What tells them apart is the ACT written
        into the payload: a match Sift made comes off entirely, an agreement with one goes back to
        being a match, and an agreement with a PROPOSAL goes back to being a proposal, because
        that is where each of the three was before the press. See `IdentifiedQueue.reverse`.

        The user is named here where a scan's record names nobody: a scan is not a person and
        saying it was one would put a name against a decision nobody made, and this is the opposite
        case: somebody pressed a button.
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
            # Rung on the receipt's own commit, so a tab that re-read on the press's earlier bell
            # reads again with this receipt in it, never one receipt short.
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
        """Put agreed-with faces back to what they were. Returns how many went back.

        `back_to` is where they came FROM, which the receipt's own act says: a run of matches
        somebody agreed with goes back to being matched, and a run of proposals goes back to being
        a proposal. It defaults to matched because an older receipt carries no other word and that
        was its only act, so an old payload keeps its old meaning.

        **Never detached**, and that is the whole difference from `unmatch` next door. What is being
        taken back is the agreement, not the match: Sift still says those appearances are this
        person, exactly as it did before the button was pressed, and a face that fell all the way
        to nobody would make an undo of "I agree" into a decision that Sift was wrong.

        So each goes back to where it was, carrying the confidence the receipt kept for it: the
        comparison's own number, which confirming overwrote with certainty. Missing from an older
        receipt it comes back as nothing, which is what the column already says for an offer made
        from a group rather than from a match.

        The reference pictures it filed are removed, because leaving them would make the undo a
        half-measure: the whole cost of agreeing is that those faces become what the person is
        recognized by from then on. `made` is the rows the press created, per face, as its receipt
        kept them, and those are the ones removed (`_take_references_back`): a picture the person
        had filed by hand before is the same row as this face's and stays.
        `remove_references_from_track` finds that hand-filed row too, so it is only the fallback
        for a receipt that kept no ids.

        The confirmation is forgotten as well. Left behind, the next scan of the file would put the
        agreement back on its own: the trap `reject` records, met again here.

        From what the decision wrote down and never from the state: a face somebody has since moved
        to somebody else belongs to that decision, and one already back to matched is not this
        undo's to write over.
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

        The mirror of `unconfirm_matches` above and deliberately the same shape: the faces the
        RECEIPT names, each back to the state the receipt recorded for it, carrying the confidence
        the comparison gave it.

        The refusal is forgotten as well, and that is the half that is easy to miss: left behind,
        the name would go back on and the next pass would take it straight off again: the trap
        `reject` records, met here from the other side.

        Nothing is re-filed as a reference. Only a confirmation files pictures, and none of these
        faces was confirmed: a refused proposal goes back to being a proposal, so it teaches Sift
        nothing until somebody agrees with it.

        From what the decision wrote down and never from the state: a face somebody has since named
        as anybody at all carries a decision that is newer than this one, and taking it back is not
        this undo's to do.
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
