# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a person decides about the faces they picked: naming, refusing, setting aside, moving
between groups, and teaching Sift from faces another feature named.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from sift.kernel.access import Viewer
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, announce
from sift.kernel.ledger import Actor
from sift.kernel.log import get_logger
from sift.kernel.vocabulary import FACE_SAID_YES, RECEIPT_FACES, Subject
from sift.slices.faces import tuning
from sift.slices.faces.models import AskedBy, Attribution, Origin, PileStatus
from sift.slices.faces.receipts import (
    AGREED_WITH_MATCHES,
    AGREED_WITH_PROPOSALS,
    FACE_QUEUE,
    IDENTIFIED_QUEUE,
    NAMED_GROUPS,
)
from sift.slices.faces.service_pictures import PicturesMixin
from sift.slices.faces.store import Ruling, StoredTrack

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RunAnswered:
    """What one press over a person's run of faces did, and the receipt that takes it back.

    `decision_id` is the receipt the press wrote: empty where nothing changed, so nothing was
    written, or where no recorder is wired. It is handed back so the surface that pressed can offer
    Undo immediately: it reads it off the reply, and a bulk yes or no over two thousand
    faces is exactly the press somebody wants to take back within seconds rather than find in
    History later.
    """

    changed: int
    offered: int = 0
    decision_id: str = ""


@dataclass(slots=True)
class Taught:
    """What one press taught Sift about somebody, gathered while `confirm` runs, for its receipt.

    An Undo works from what the decision wrote down and never from the state it finds, and two
    of the things confirming does leave nothing on the face to find them by afterwards:

    - `references`: per face, the reference rows the press CREATED. A picture the person already
      held is claimed, not filed, and it is the earlier decision's; removing by the picture instead
      takes that one too (`Store.remove_references_by_id`).
    - `starters`: per person, the starters in use when the press began. The first reference of her
      own retires them in its transaction, so the ones still in use afterwards are subtracted to
      say which this press retired.
    - `named_before`: per face, who it was named as before the press took that name off to give
      it this one (a Yes on a disagreement), with the state and confidence, so the Undo names it
      that person again (`unreject`).
    """

    references: dict[str, list[str]] = field(default_factory=dict)
    starters: dict[str, list[str]] = field(default_factory=dict)
    named_before: dict[str, tuple[str, str | None, float | None]] = field(default_factory=dict)


class DecisionsMixin(PicturesMixin):
    """Answering for faces somebody picked."""

    async def _taught_payload(
        self, person_id: str, taught: Taught | None, track_ids: Collection[str]
    ) -> dict[str, Any]:
        """The part of a receipt that says what its press taught, for `IdentifiedRecords.reverse`.

        `references` is written even when empty: an empty map says the press created none, which is
        a different answer from a receipt with no map at all (one written before the map existed,
        whose Undo removes by the pictures). Nothing at all where nothing was gathered.
        """
        if taught is None:  # pragma: no cover (every receipt writer is handed one)
            return {}
        out: dict[str, Any] = {
            "references": {
                track_id: list(made)
                for track_id, made in taught.references.items()
                if track_id in track_ids
            }
        }
        named = {
            track_id: list(was)
            for track_id, was in taught.named_before.items()
            if track_id in track_ids
        }
        if named:
            out["named_before"] = named
        before = taught.starters.get(person_id)
        if before:
            still = set(await self._store.starters_in_use(person_id))
            retired = [one for one in before if one not in still]
            if retired:
                out["starters"] = retired
        return out

    async def _take_references_back(
        self, person_id: str, track_id: str, made: Mapping[str, Sequence[str]] | None
    ) -> int:
        """Remove the reference rows one face's confirmation filed, for an Undo. How many.

        By the ids the receipt kept where it kept them (`Taught.references`), so a picture the
        person had filed by hand before the press stays. A receipt from before the ids were kept
        names none, and its Undo removes by the face's pictures as it always did, which takes a
        matching picture filed by hand as well: the most an older receipt can say.
        """
        if made is None:
            return await self._store.remove_references_from_track(person_id, track_id)
        return await self._store.remove_references_by_id(person_id, made.get(track_id, ()))

    async def take_back_what_it_taught(
        self,
        viewer: Viewer,
        person_id: str,
        *,
        cover: str | None,
        starters: Sequence[str],
        proposals: Sequence[str],
    ) -> int:
        """The rest of an Undo of a naming or an agreement, once its faces are back. How many.

        Each from what the receipt recorded and only while it still stands as the press left it:
        the cover the press gave, while it is still that face (`Store.take_back_a_cover`); the
        starters its first own reference retired, while she has no picture of her own again
        (`Store.bring_back_starters`); and a folder's proposal its Yes accepted, back to asking
        (`Store.reopen_accepted_proposal`).
        """
        moved = 0
        if cover is not None and await self._store.take_back_a_cover(
            person_id, cover, actor=Actor.user(viewer.id)
        ):
            moved += 1
        if starters:
            back = await self._store.bring_back_starters(person_id, starters)
            if back:
                log.info("faces.starter.back_in_use", person_id=person_id, pictures=back)
            moved += back
        for pile_id in proposals:
            moved += int(await self._store.reopen_accepted_proposal(pile_id, person_id))
        return moved

    async def own_reference_ids(self, person_id: str) -> list[str]:
        """Which pictures of her own Sift knows somebody by. See `Store.own_reference_ids`."""
        return await self._store.own_reference_ids(person_id)

    async def confirm(
        self,
        track_id: str,
        person_id: str,
        *,
        settle: bool = True,
        viewer: Viewer | None = None,
        taught: Taught | None = None,
    ) -> None:
        """Agree that an appearance is somebody, and let it improve future matching.

        A confirmed face becomes one of that person's references, which is what makes matching
        improve from this library's own pictures: its resolutions, its lighting, its cameras.
        There is no second quality check here and there should not be: every face a pass kept had
        already cleared the bar before it was ever described, with one exception, which is the one
        judgement the filing below keeps. A face turned past the bar's angle is kept so somebody
        can name it (`quality.asked_only`); it takes the name and is never filed.

        The measured quality goes with it rather than a flat "a person said so". It is what an
        exported pack carries about that face, and a column that is the same number on every row
        tells whoever reads it nothing.

        The risk is real and is managed rather than ignored: one wrong confirmation would quietly
        degrade that person from then on, so references stay visible and removable, and the
        odd-one-out check runs over them as well as over imports.

        **Which model the new reference is stamped with is read from the pass that found the face,
        never from whichever model happens to be loaded now.** The numbers being stored were
        produced by that one, months ago perhaps, and the stamp is the only thing that says whether
        they can be compared with anything else. Stamping the current model would relabel an old
        measurement as a new one, silently, and in a way nothing downstream could detect, because
        the whole purpose of the column is to be what tells two incomparable galleries apart.

        It is also what makes confirming cost nothing: no model is loaded here, so a decision about
        a face already on the disk works on an install whose models are not to hand.

        `viewer` is the person who pressed, where one did: the confirmation is then recorded for
        them (`_record_confirmed`), so it counts as theirs and has a way back.
        """
        await self._require_enabled()
        track = await self._store.track(track_id)
        if track is None:
            return
        was = track
        scan = await self._store.scan_of(track.asset_id)
        if scan is None:  # pragma: no cover (a track exists only where a pass recorded one)
            return
        await self._store.attribute(
            track_id, person_id, confidence=1.0, attribution=Attribution.CONFIRMED
        )
        # No cover from naming a face: a cover nobody chose is the whole first picture filed under
        # the person, which the filing this name leads to gives them where they have none
        # (`kernel/access/default_covers.py`). Never a face cut out of one.
        # What this press teaches is gathered where there is a receipt to write it into: the one
        # a caller is building (`taught`), or this confirmation's own when somebody pressed it.
        gathered = taught if taught is not None else (Taught() if viewer is not None else None)
        if gathered is not None and person_id not in gathered.starters:
            gathered.starters[person_id] = await self._store.starters_in_use(person_id)
        # And written down against the face's own description, so the next scan of this file puts
        # the decision back as a decision rather than leaving the reference photo to re-derive the
        # name as something Sift worked out. See `_name_again`.
        await self._store.remember_confirmation(track_id, person_id)
        # Every frame of the appearance, up to what one appearance may contribute. Poor crops and
        # near-copies are filed too: see the note on the loop below for the trade. The cap is not
        # about judgement: a person on screen through a four-minute video is tracked as many short
        # runs and merged back into ONE appearance carrying a frame from each, so without it one
        # press would file dozens of references, most of somebody's, all from one video (one
        # outfit, one light, one camera), and every reference is blended into a single
        # description, so what Sift held would describe her in that video rather than her.
        faces = sorted(await self._store.faces_of(track_id), key=lambda one: -one.quality)
        # A face turned past the bar's angle takes the name and is never filed: it is the one
        # judgement this loop keeps. A reference is what every other face is compared with, and a
        # library's turned faces often carry a second face in their square
        # (`quality.asked_only`), which a blended description would then partly be of.
        line = (await self.configuration()).bar.min_frontality
        filed = 0
        for face in faces:
            if filed >= tuning.REFERENCES_PER_APPEARANCE:
                break
            if face.turned(line):
                continue
            # A confirmation files the face, as a trade: filtering is the person's job before
            # assigning a face group. Declining silently here (a crop below the quality floor, or a
            # near-copy of one already held) would leave a press doing less than it appears to;
            # filing them spends some matching accuracy (a poor crop pulls an averaged description
            # toward a blur, and a near-copy costs a comparison on every match) for certainty.
            #
            # What still holds: the per-appearance cap above, because one person tracked through a
            # four-minute video is one appearance carrying a frame from each of its runs, and filing
            # all of them would put most of somebody's references in one outfit under one light.
            # And `UNIQUE(person_id, crop_digest)` in the schema, which refuses the same picture
            # twice: that is identity, not judgement, and nothing is lost by keeping it.
            #
            # `teachable` still says which crops are poor, and a face that became a reference is
            # marked, so what this rule does is visible on the screen either way.
            #
            # Read here rather than earlier: this one goes to the disk, and a picture read for a
            # face about to be skipped is a file opened for nothing.
            crop = await self._store.picture_bytes(face.crop_path)
            if crop is None:
                continue
            reference_id = await self._store.add_reference(
                person_id,
                vector=face.vector,
                quality=face.quality,
                crop=crop,
                origin=Origin.CONFIRMED,
                recognizer=scan.recognizer,
                pixels=face.box.long_side if face.pixels is None else face.pixels,
                # Which appearance filed it, and which file that is in (v24), not derived from
                # the bytes of the picture, an identity a rescan destroys because fresh crops
                # re-encode differently. See `reference_tracks`.
                track_id=track_id,
                asset_id=track.asset_id,
            )
            if reference_id is None:
                continue
            filed += 1
            # The rows this press CREATED, by id: a picture already held comes back as None above
            # and is the earlier decision's (`Taught.references`).
            if gathered is not None:
                gathered.references.setdefault(track_id, []).append(reference_id)
        if gathered is not None:
            # Named even where nothing was filed, so the receipt says "none" rather than nothing.
            gathered.references.setdefault(track_id, [])
        if settle:
            await self._settle(track.asset_id)
        if viewer is not None:
            await self._record_confirmed(viewer, person_id, [was], gathered)

    async def reject(self, track_id: str, person_id: str) -> None:
        """Say an appearance is not somebody. Remembered, so it is not offered again.

        Forgets an earlier confirmation of the same face, and that is not tidying up: it is what
        stops the name coming back at the next scan of the file. Left behind, taking a name off
        would work until the file was scanned again and then quietly undo itself, which is the trap
        restoring an ignored pile has and the reason both are cleared here rather than only flipped.
        """
        await self._require_enabled()
        await self._store.forget_confirmation(track_id, person_id)
        # And the reference photos this face gave them. Confirming files every picture of an
        # appearance; leaving them behind means a face agreed to by mistake goes on being one of the
        # things that person is recognized by, forever, with nothing to say so.
        taken = await self._store.remove_references_from_track(person_id, track_id)
        if taken:
            log.info("faces.reference.withdrawn", person_id=person_id, pictures=taken)
        await self._store.reject(track_id, person_id)
        track = await self._store.track(track_id)
        if track is not None:
            await self._settle(track.asset_id)

    async def confirm_many(
        self,
        track_ids: Sequence[str],
        person_id: str,
        *,
        viewer: Viewer | None = None,
        taught: Taught | None = None,
    ) -> int:
        """Agree that several appearances are the same person. Returns how many were written.

        One call rather than one per face, because the screen this serves is a whole pile in one go
        and forty separate requests is forty round trips writing to the same person's gallery, each
        waiting on the last for the write lock. Each is still confirmed individually underneath, so
        there is one path by which a face becomes a reference and not two.

        A pile with nothing unclaimed left in it is dropped afterwards: it is a question that has
        been answered, and leaving the row means a count that never comes down.
        """
        return (
            await self.confirm_answered(track_ids, person_id, viewer=viewer, taught=taught)
        ).changed

    async def confirm_answered(
        self,
        track_ids: Sequence[str],
        person_id: str,
        *,
        viewer: Viewer | None = None,
        taught: Taught | None = None,
    ) -> RunAnswered:
        """`confirm_many`, answered with the receipt its Undo takes back.

        The receipt where the press wrote exactly one, which is a press over faces that were all
        nobody's, or all proposed, or all matched; several (a mix) or none carry no receipt, so a
        toast never offers an Undo that would put back only part of what was done.
        """
        await self._require_enabled()
        written = 0
        touched: list[str] = []
        gathered = taught if taught is not None else (Taught() if viewer is not None else None)
        before = await self._store.tracks(track_ids)
        for track_id in dict.fromkeys(track_ids):
            track = before.get(track_id)
            if track is None or track.person_id is not None:
                continue
            # Settled once for the whole batch below, not once per face: settled per face, a group
            # across twenty-four files would be settled twenty-four times, each a turn at the writer
            # and a ring of the change bus.
            await self.confirm(track_id, person_id, settle=False, taught=gathered)
            touched.append(track.asset_id)
            written += 1
        await self._settle_all(touched)
        if written:
            await self._store.drop_empty_piles()
        receipts: list[str] = []
        if viewer is not None:
            receipts = await self._record_confirmed(
                viewer,
                person_id,
                [
                    before[one]
                    for one in dict.fromkeys(track_ids)
                    if one in before and before[one].person_id is None
                ],
                gathered,
            )
        return RunAnswered(changed=written, decision_id=receipts[0] if len(receipts) == 1 else "")

    async def _record_confirmed(
        self,
        viewer: Viewer,
        person_id: str,
        faces: Sequence[StoredTrack],
        taught: Taught | None = None,
    ) -> list[str]:
        """Write down, for the person who pressed, the faces they named or confirmed as someone.

        **A PER-USER RECORD, as every other face decision has** (`_record_named_groups`,
        `_record_agreement`): without it, naming or confirming a face on the popout would record
        nothing a User's Insights or History could count, with no way back. Under the queue the
        other face decisions use, with the User and the track ids, and with the act that says how
        each face is put back (`IdentifiedQueue.reverse`): a face that was nobody's goes back to
        nobody (the act a Yes on a group writes); one Sift had proposed, or matched, as this person
        goes back to being a proposal, or a match, with the number the comparison gave it.

        `taught` is what the press filed, gave and retired (`Taught`), written into each receipt
        for the faces that receipt names; the starters it retired go on each, because the Undo
        puts them back only once she has no picture of her own left.
        """
        if self._recorder is None or not faces:
            return []
        found = await self._repository.visible_person(viewer, person_id)
        if found is None:  # pragma: no cover (the route resolved the person a moment ago)
            return []
        by_act: dict[str, list[StoredTrack]] = {}
        for face in faces:
            if face.person_id == person_id and face.attribution is Attribution.SUGGESTED:
                act = AGREED_WITH_PROPOSALS
            elif face.person_id == person_id and face.attribution is Attribution.MATCHED:
                act = AGREED_WITH_MATCHES
            elif face.person_id == person_id:
                continue
            else:
                act = NAMED_GROUPS
            by_act.setdefault(act, []).append(face)
        receipts: list[str] = []
        for act, run in by_act.items():
            track_ids = [one.id for one in run]
            answered = await self._faces_answered(track_ids, person_id, FACE_SAID_YES)
            learned = await self._taught_payload(person_id, taught, track_ids)
            one = len(run) == 1
            counted = "1 face" if one else f"{len(run)} faces"
            if act == NAMED_GROUPS:
                title = f"You named {counted} as {found.name}"
                back = "takes the name off " + ("it" if one else "them")
            else:
                title = f"You agreed with {counted} for {found.name}"
                back = (
                    "leaves "
                    + ("it" if one else "them")
                    + (
                        " waiting under Needs your input, as before"
                        if act == AGREED_WITH_PROPOSALS
                        else " Recognized by Sift, as before"
                    )
                )
            subjects: list[Subject] = [Subject(kind="person", id=person_id)]
            subjects += [
                Subject(kind="asset", id=asset_id)
                for asset_id in dict.fromkeys(face.asset_id for face in run)
            ]
            async with self._store.database.write() as connection:
                # Rung on the receipt's own commit, so a tab that re-read on the press's earlier bell
                # reads again with this receipt in it, never one receipt short.
                announce(EVERY_ADMIN, About.LIBRARY)
                receipt = await self._recorder.record_on(
                    connection,
                    queue=IDENTIFIED_QUEUE,
                    user_id=viewer.id,
                    title=title,
                    detail=(
                        f"Sift learns from {'it' if one else 'them'}. Taking this back {back}, "
                        "and removes the pictures. No file is touched and nothing is deleted."
                    ),
                    payload=json.dumps(
                        {
                            "act": act,
                            "person_id": person_id,
                            "track_ids": track_ids,
                            "offered": [],
                            "confidence": {one.id: one.confidence for one in run},
                            RECEIPT_FACES: answered,
                            **learned,
                        }
                    ),
                    subjects=subjects,
                )
            receipts.append(receipt)
        return receipts

    async def name_with_their_group(
        self, track_ids: Sequence[str], person_id: str, *, taught: Taught | None = None
    ) -> tuple[int, int]:
        """Name these faces, and OFFER the rest of their groups as the same person.

        Returns `(named, offered)`. `taught` gathers what the naming filed, for a caller that
        writes the receipt (`Taught`).

        **A group is already a claim that these faces are one person.** The clustering makes it at
        import, it deliberately over-splits so a group is high-precision rather than complete, and
        naming one face out of it while leaving nineteen strangers behind ignores work that has
        already been done. Somebody who names a face in the player has answered the question the
        whole group was asking.

        The two halves are deliberately NOT the same strength, and this is the whole design:

        - The faces that were NAMED are `CONFIRMED`. Somebody looked at that crop and said who it
          was, which is the only evidence in this feature that comes from a person, and it is what
          makes a face eligible to become a reference.
        - The rest of their group is `SUGGESTED`. Nobody has looked at those, and the grouping is
          arithmetic: good arithmetic, but the same kind that produces every other suggestion in
          Sift. So they land on the person's own wall under "waiting for you", where each is agreed
          to or has the name taken off, exactly as a match below the attach line does.

        Silently confirming the other nineteen would be the tempting version and it is wrong twice
        over: it would put unreviewed faces into the gallery a person is RECOGNIZED by, so one bad
        group would quietly degrade them from then on, and it would present arithmetic as a decision
        somebody made, which is precisely the distinction `Attribution` exists to keep.

        **No confidence is stored on the offered ones, and that is honest rather than an omission.**
        A confidence here is a match score; this did not come from a match, it came from these
        faces having been grouped with one that was named. A number invented for the column would be
        read by the screens as a measurement.

        The piles are read BEFORE anything is confirmed. Confirming empties a pile and the empty
        pile is then dropped, so a pile read afterwards is a pile that may no longer exist, and
        the faces this is for would be the ones left behind.
        """
        await self._require_enabled()
        # One read for all of them rather than one per face, for the reason the offer below is one
        # write: a press is worth one place in each queue, however many faces it names.
        piles = {
            track.pile_id
            for track in (await self._store.tracks(track_ids)).values()
            if track.pile_id is not None
        }

        named = await self.confirm_many(track_ids, person_id, taught=taught)
        if not named:
            # Nothing was named, so nothing was decided, so there is nothing to extend. Offering the
            # group off the back of a call that wrote nothing would attach a person to twenty faces
            # on the strength of a press that did nothing.
            return 0, 0

        offered = len(await self._offer_their_groups(piles, person_id))
        log.info("faces.named_with_group", named=named, offered=offered, piles=len(piles))
        return named, offered

    async def _offer_their_groups(self, piles: set[str], person_id: str) -> list[str]:
        """Offer the rest of these groups as the same person. Returns the faces that were offered.

        The faces rather than a count, because agreeing with proposals writes them into its
        receipt: an undo that did not know which faces it offered would leave every one of them
        asked about as her after the agreement itself was taken back.

        The second half of naming a face, kept in one place because it is now the second half of
        TWO acts: naming a face by hand, and agreeing with a proposal from the board. Written
        twice they would be two answers to one question, and the one that drifts is the one nobody
        is looking at.

        `pile_tracks` answers with the faces in the pile that NOBODY is attached to, which is the
        filter this needs and the reason there is no second one here: the faces just settled carry
        a person by now and are excluded by the same clause, as is anything a match had already
        claimed. A guard repeating that here could never fire.

        **Each is asked as the GROUP's question (`AskedBy.GROUP`), and that word decides what a
        re-match may do with it.** A re-match scores it (the number is its first) and names it only
        for somebody known from enough confirmed faces, where it clears her line
        (`service_matching.names_groups`); for anybody else it stays a question however high it
        scores. Without the word, the score the re-match writes is the only thing that told an
        offer apart, and it stops telling it apart the moment it is written (see
        `schema._ADD_ASKED_BY`).
        """
        # A face somebody already refused as her is not asked about her again: the No stands.
        refused = await self._store.refused_tracks(sorted(piles), person_id)
        waiting: dict[str, str] = {}
        for pile_id in sorted(piles):
            for track in await self._store.pile_tracks(pile_id):
                if track.id not in refused:
                    waiting[track.id] = track.asset_id
        # One turn at the writer for the whole offer, through `Store.restate`. A write per face
        # would take a place in the single writer's queue per face: over a minute for a group of
        # hundreds while other work is writing, against half a second in one transaction.
        # Guarded as every restate is: a face somebody named between the read and the write keeps
        # that name, and is neither offered nor counted.
        landed = await self._store.restate(
            [
                Ruling(
                    track_id=track_id,
                    was_person=None,
                    was=None,
                    person_id=person_id,
                    attribution=Attribution.SUGGESTED,
                    confidence=None,
                    asked_by=AskedBy.GROUP,
                )
                for track_id in waiting
            ]
        )
        await self._settle_all(sorted({waiting[track_id] for track_id in landed}))
        return sorted(landed)

    async def accept_suggestions(
        self, track_ids: Sequence[str], *, taught: Taught | None = None
    ) -> int:
        """Agree with what Sift proposed for these faces. Returns how many were agreed to.

        `taught` gathers what the agreement filed, for a caller that writes the receipt.

        The other half of a suggestion: a face Sift has proposed a person for can be refused, and
        this is how it is accepted.

        Each face is confirmed as **the person already proposed for it**, which is what makes this
        one call rather than one per person: a page of suggestions is a page about several different
        people, and asking the caller to say who is asking it to repeat what is already on the row.

        A face nobody proposed anybody for is skipped rather than refused. The screen offers this
        over a selection, and a selection made by dragging across a page will pick up faces that
        were already settled: agreeing with a decision that has already been made is not an error
        worth failing the whole call for.
        """
        await self._require_enabled()
        agreed = 0
        touched: list[str] = []
        found = await self._store.tracks(track_ids)
        for track_id in dict.fromkeys(track_ids):
            track = found.get(track_id)
            if track is None or track.person_id is None:
                continue
            if track.attribution is Attribution.CONFIRMED:
                continue
            await self.confirm(track_id, track.person_id, settle=False, taught=taught)
            touched.append(track.asset_id)
            agreed += 1
        await self._settle_all(touched)
        return agreed

    async def set_aside(self, track_ids: Sequence[str]) -> str | None:
        """Set some faces aside, leaving the rest of their pile where it is.

        The grouping deliberately over-splits and will sometimes put a stranger in a pile, so
        all-or-nothing is the wrong shape for the decision. The chosen faces become a pile of their
        own under Discarded: listed, reversible, and kept out of every future regrouping, exactly as
        a whole pile set aside is.
        """
        await self._require_enabled()
        await self._unname_first(track_ids)
        # Any hand-made grouping of these faces goes first. Two memories both claiming a face would
        # fight at the next scan of the file, and setting aside is the later decision.
        await self._store.forget_grouping(track_ids)
        moved = await self._store.set_aside(track_ids)
        if moved is not None:
            # Written down before the tidy-up, because the decision has to outlive the rows it was
            # made against. See `_set_aside_again`.
            await self._store.remember_ignored(moved)
            await self._store.drop_empty_piles()
        return moved

    async def _unname_first(self, track_ids: Sequence[str]) -> int:
        """Take the name off any of these faces that has one, before moving or setting it aside.

        Setting aside and moving are decisions about faces nobody has placed. Done to a face that
        carries a name they leave it in two states at the same time: still listed under that person,
        and also sitting in a group of strangers. So the name comes off first, which also withdraws
        the reference photos it gave, and records the refusal so the next scan does not put it back.

        Removing does not go through here. It takes the face away entirely, and a face that is gone
        is not in Identified either.
        """
        unnamed = 0
        for track_id in track_ids:
            track = await self._store.track(track_id)
            if track is None or track.person_id is None:
                continue
            await self.reject(track_id, track.person_id)
            unnamed += 1
        if unnamed:
            log.info("faces.unnamed_before_moving", faces=unnamed)
        return unnamed

    async def move_faces(self, track_ids: Sequence[str], pile_id: str | None) -> str | None:
        """Merge or split, which are one operation with two destinations.

        Moving faces into an existing group merges them into it; moving them into a new one splits
        them out. The grouping deliberately over-splits, so the same stranger arriving as two piles
        is the ordinary case rather than a fault, and the only other answer would be to name them,
        which creates a Person to hold a decision that is not about a name at
        all, and which merges by collision if two real people happen to share one.

        Written down as descriptions, because a rescan deletes the rows this sits on. See
        `_group_again`. The old memory for these faces goes first: without that, moving a face out
        of a pile it was moved into would work until the next scan of the file and then quietly
        undo itself.

        A pile emptied by the move is tidied away, so splitting everything out of a group leaves
        one group rather than one full and one empty.
        """
        await self._require_enabled()
        if not track_ids:
            return None
        await self._unname_first(track_ids)
        await self._store.forget_grouping(track_ids)
        moved = await self._store.move_tracks(track_ids, pile_id)
        if moved is None:
            return None
        await self._store.remember_grouping(moved)
        await self._store.drop_empty_piles()
        log.info("faces.moved", pile_id=moved, faces=len(track_ids), into_new=pile_id is None)
        return moved

    async def ignore(self, pile_id: str, viewer: Viewer | None = None) -> str | None:
        """Set a pile aside. Still listed, and reversible: not a trapdoor.

        What the pile holds is written down as descriptions as well as flipped, so the decision
        survives the next scan of those files. Without that it survives regrouping alone.

        Returns the record it wrote, so the screen that made the decision can offer to take it back
        without going looking for it. Empty when there is nowhere to write one: a pass has no
        viewer and makes no decisions. `None` when there was no such pile.
        """
        await self._require_enabled()
        changed = await self._store.set_pile_status(pile_id, PileStatus.IGNORED)
        if not changed:
            return None
        await self._store.remember_ignored(pile_id)
        return await self._record_pile(viewer, pile_id, ignored=True)

    async def _record_pile(self, viewer: Viewer | None, pile_id: str, *, ignored: bool) -> str:
        """Write down what setting a pile aside, or bringing one back, did.

        Here rather than in the route, so a receipt cannot be forgotten by whoever adds the next way
        of setting one aside.
        """
        if self._recorder is None or viewer is None:
            return ""
        # How big the group was, so the record says what was affected rather than only that
        # something was. "A group set aside" is a true sentence that answers none of the questions
        # somebody opens this list to ask.
        tracks = await self._store.pile_tracks(pile_id)
        faces = len(tracks)
        many = "1 face" if faces == 1 else f"{faces} faces"
        # The pile, and every file those faces are on. Both free (the tracks were already read
        # for the count above, and each one carries the file it was found in), and both worth
        # having: the pile is what the decision was taken ON, and the files are where somebody will
        # be standing when they wonder why a face stopped being offered.
        subjects: list[Subject] = [Subject(kind="pile", id=pile_id)]
        subjects += [Subject(kind="asset", id=one.asset_id) for one in tracks]
        # "Discarded", the word on the button and the tab. An older receipt keeps the title it was
        # written with.
        title = f"A group of {many} discarded" if ignored else f"A group of {many} restored"
        detail = (
            f"{many} from that group will not be offered again. Nothing was deleted and no file "
            "was touched."
            if ignored
            else f"{many} are back among the groups waiting for a name."
        )
        async with self._store.database.write() as connection:
            # Rung on the receipt's own commit, so a tab that re-read on the press's earlier bell
            # reads again with this receipt in it, never one receipt short.
            announce(EVERY_ADMIN, About.LIBRARY)
            return await self._recorder.record_on(
                connection,
                queue=FACE_QUEUE,
                user_id=viewer.id,
                title=title,
                detail=detail,
                payload=json.dumps({"pile_id": pile_id}),
                subjects=subjects,
            )

    async def restore(self, pile_id: str) -> bool:
        """Bring an ignored pile back, with its faces.

        Forgets the descriptions too. Leaving them would mean the pile came back and then set
        itself aside again at the next scan of those files: the person did the one thing the
        screen offers and it came undone later, which is worse than not remembering at all.
        """
        await self._require_enabled()
        changed = await self._store.set_pile_status(pile_id, PileStatus.OPEN)
        if not changed:
            return False
        await self._store.forget_ignored(pile_id)
        return True

    async def teach(self, track_ids: Sequence[str], person_id: str) -> int:
        """Make faces ALREADY named as somebody into what a confirmation makes them. Returns how many.

        The second half of naming a group from a folder answer (`evidence.FaceEvidence.teach`):
        the name went on inside that answer's transaction, and this does the rest through `confirm`
        (the one path by which a face becomes a reference) for each face, then settles the files
        once for the batch, the way `confirm_many` does.

        **Only a face still carrying THIS person is taught.** `confirm_many` skips a face that
        carries anybody, because it is for faces being named; these were named a moment ago, so the
        test turns round: a face that no longer carries the person (an undo got there first, or
        somebody renamed it) is not this decision's to teach from.

        Zero on an install with recognition switched off: nothing is taught and nothing is refused,
        because the answer that named the faces has already landed.
        """
        if not track_ids or not await self.enabled():
            return 0
        held = await self._store.tracks(list(dict.fromkeys(track_ids)))
        touched: list[str] = []
        for track_id in dict.fromkeys(track_ids):
            track = held.get(track_id)
            if track is None or track.person_id != person_id:
                continue
            await self.confirm(track_id, person_id, settle=False)
            touched.append(track.asset_id)
        await self._settle_all(touched)
        return len(touched)

    async def unteach(self, track_ids: Sequence[str], person_id: str) -> int:
        """Take back what `teach` filed for these faces. Returns how many faces lost something.

        Run after an undo has put the faces back to unnamed: their remembered decision and the
        references their pictures gave the person go, and the files are settled so the person
        comes off any file only those faces put them on.

        **A face carrying the person AGAIN is left alone.** Somebody named it since, and the
        references it holds now are that later decision's.

        Each face by the id it goes by now (`Store.live_ids`): one a rescan found again keeps its
        remembered name and its pictures, and an Undo naming the old id would leave both behind.
        """
        if not track_ids or not await self.enabled():
            return 0
        live = await self._store.live_ids(track_ids)
        track_ids = [live[one] for one in dict.fromkeys(track_ids)]
        held = await self._store.tracks(list(dict.fromkeys(track_ids)))
        touched: list[str] = []
        taken = 0
        for track_id in dict.fromkeys(track_ids):
            track = held.get(track_id)
            if track is None or track.person_id == person_id:
                continue
            forgot = await self._store.forget_confirmation(track_id, person_id)
            removed = await self._store.remove_references_from_track(person_id, track_id)
            if forgot or removed:
                taken += 1
            touched.append(track.asset_id)
        await self._settle_all(touched)
        return taken
