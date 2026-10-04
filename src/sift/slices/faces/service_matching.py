# SPDX-License-Identifier: AGPL-3.0-or-later
"""Matching faces against the people Sift knows, and the receipts for what it decided on its own.

A match above a person's attach line names a face without asking, so every such decision is
recorded with a way to take it back.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from sift.kernel.access.sentences import FACES_MATCHED, faces_of_person, matched_sentence
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, announce, announce_now
from sift.kernel.ledger import Object as LedgerObject
from sift.kernel.log import get_logger
from sift.kernel.vocabulary import VIA_FACES, Subject
from sift.kernel.workbench import payload_held
from sift.slices.faces import matching, tuning
from sift.slices.faces.models import AskedBy, Attribution
from sift.slices.faces.receipts import IDENTIFIED_QUEUE, STOPPED_ASKING
from sift.slices.faces.service_base import Configured
from sift.slices.faces.service_learning import LearningMixin, _matched_payload
from sift.slices.faces.store import Ruling, clearest
from sift.slices.faces.store_records import Asked

log = get_logger(__name__)

#: How far apart two scores of one face may be and still be the same score: a re-score writes only
#: a number that moved, and a difference below this is the arithmetic's rounding, not news.
_SAME_SCORE = 1e-6


def _how_sure(pairs: Sequence[tuple[str, str, float]]) -> str:
    """How sure a run was of what it attached, as the tail of the sentence on its receipt.

    ON THE RECEIPT, written once: the appearances move afterwards, so a range read off
    `face_tracks` later would drift from the run it describes. Whole percentages, the ends joined
    by words rather than a dash. One figure when every face scored the same; never empty.
    """
    low = round(min(confidence for _track, _asset, confidence in pairs) * 100)
    high = round(max(confidence for _track, _asset, confidence in pairs) * 100)
    if low == high:
        return f", {low}% sure"
    return f", between {low}% and {high}% sure"


def _pictures(count: int) -> str:
    """Her reference pictures, counted, for the receipt's second line: "12 reference pictures"."""
    return "1 reference picture" if count == 1 else f"{count:,} reference pictures"


def _undo_asks(asked: int, total: int) -> str:
    """What a receipt for names Sift added on its own says its Undo does, in one sentence.

    Undo asks about every face instead (`FaceService.unmatch`), so the sentence says where each goes
    and, for the faces that had been questions until the run, that they are going back there, not
    "unnamed", which would be true only until the next re-match named them again.
    """
    them = "it" if total == 1 else "them"
    if asked >= total:
        waited = "it waited" if total == 1 else "they waited"
        return f"Until now {waited} under Needs your input, and Undo puts {them} back there."
    if asked == 0:
        return f"Undo takes the name off and asks you about {them} under Needs your input."
    were = "1 of them waited" if asked == 1 else f"{asked:,} of them waited"
    return (
        f"Until now {were} under Needs your input. Undo takes the name off every one and asks "
        "you about them there."
    )


def _attach_bar(
    person_id: str,
    gallery: matching.Gallery,
    counts: Mapping[str, int],
    setting: float,
) -> float:
    """How sure a match to this person has to be before Sift names the face without asking.

    `tuning.bar_for` over her own references at the install's setting, but `ALWAYS_ASK` for
    somebody known by STARTER pictures alone (`Gallery.starters_only`, linked on a name). A face
    turned past the quality bar's angle (`quality.asked_only`) meets the same bar as any other: the
    bar is a likeness to her pictures, and a turned face that clears it is as much her as a
    square-on one. One function for the three places a match is judged.
    """
    if person_id in gallery.starters_only:
        return tuning.bar_for(counts.get(person_id, 0), bar=tuning.ALWAYS_ASK)
    return tuning.bar_for(counts.get(person_id, 0), bar=setting)


def names_groups(confirmed: Mapping[str, int], gallery: matching.Gallery) -> frozenset[str]:
    """Who a re-match may name a group's question for: somebody with at least
    `tuning.GROUP_NAMING_REFERENCES` faces people confirmed, never somebody known by starters
    alone. Everybody else's group questions stay questions however they score."""
    return frozenset(
        person_id
        for person_id, faces in confirmed.items()
        if faces >= tuning.GROUP_NAMING_REFERENCES and person_id not in gallery.starters_only
    )


@dataclass
class _Rematched:
    """What one re-match decided, gathered as it goes.

    `attached` is who it named on its own (MATCHED only) with each face's file and confidence,
    the number the receipt states; `from_groups` the same for the questions a GROUP asked that it
    named, which have a receipt of their own; `was_asked` the questions it answered, at the
    confidence each had been put at, and `stopped` the ones it stopped asking, per person: what an
    Undo puts back.
    """

    attached: dict[str, list[tuple[str, str, float]]] = field(default_factory=dict)
    from_groups: dict[str, list[tuple[str, str, float]]] = field(default_factory=dict)
    was_asked: dict[str, float | None] = field(default_factory=dict)
    stopped: dict[str, list[tuple[str, str, float | None]]] = field(default_factory=dict)
    touched: set[str] = field(default_factory=set)
    changed: int = 0
    rescored: int = 0
    offers_kept: int = 0

    def questions_landed(
        self, rulings: Sequence[Ruling], questions: Mapping[str, Asked], landed: Collection[str]
    ) -> None:
        """Step 1's rulings that landed, counted in."""
        for ruling in rulings:
            if ruling.track_id not in landed:
                # Answered, or moved, between the read and the write: the newer decision stands.
                continue
            question = questions[ruling.track_id]
            if ruling.attribution is Attribution.MATCHED and ruling.confidence is not None:
                self.was_asked[ruling.track_id] = question.confidence
                named = self.from_groups if question.asked_by is AskedBy.GROUP else self.attached
                named.setdefault(question.person_id, []).append(
                    (question.track_id, question.asset_id, ruling.confidence)
                )
                self.touched.add(question.asset_id)
            elif ruling.attribution is None:
                self.stopped.setdefault(question.person_id, []).append(
                    (question.track_id, question.asset_id, question.confidence)
                )
                self.touched.add(question.asset_id)
            else:
                self.rescored += 1
                if question.asked_by is AskedBy.GROUP:
                    self.offers_kept += 1
        self.changed = len(self.was_asked) + sum(len(faces) for faces in self.stopped.values())


def _judge_questions(
    questions: Iterable[Asked],
    *,
    ignored: Collection[str],
    gallery: matching.Gallery,
    rejections: Mapping[str, set[str]],
    counts: Mapping[str, int],
    named_from_groups: Collection[str],
    configured: Configured,
) -> list[Ruling]:
    """Step 1 of a re-match: each standing question judged again by the bar a face arriving
    today meets, so a change of either line re-judges it.

    What a question may become, and nothing more:
      * RECOGNIZED BY SIFT: one the ARITHMETIC asked (`AskedBy.MATCH`), or one a GROUP asked about
        somebody in `named_from_groups` (`names_groups`), only when the closest person is still the
        one proposed and the match clears her bar. Never moved to somebody else.
      * GONE from Needs your input: any question now under the line for asking; step 2 then
        compares it with everybody.
      * RE-SCORED and kept: everything else: a question an Undo or a stash-box put, and a group's
        question about somebody known from fewer confirmed faces, which only an answer settles.
    Each ruling carries the earlier state for the receipt, and who asked stays on the face
    (`schema._ADD_ASKED_BY`), the stored word deciding rather than whether a face has a score yet.
    """
    rows = matching.rows_by_person(gallery)
    rulings: list[Ruling] = []
    for question in questions:
        if question.track_id in ignored:
            continue
        match = matching.best_match(
            question.vector,
            gallery,
            rejected=rejections.get(question.track_id, ()),
            floor=configured.suggest_above,
        )
        closest = match is not None and match.person_id == question.person_id
        own = (
            match.confidence
            if match is not None and closest
            else matching.likeness(question.vector, gallery, rows.get(question.person_id))
        )
        if own is None:
            # The person proposed has no pictures this model described: no evidence is not
            # evidence against, so it stands.
            continue
        now: Attribution | None = Attribution.SUGGESTED
        if own < configured.suggest_above:
            now = None
        elif (
            closest
            and match is not None
            and (
                question.asked_by is AskedBy.MATCH
                or (question.asked_by is AskedBy.GROUP and question.person_id in named_from_groups)
            )
        ):
            now = matching.verdict(
                match,
                attach_above=_attach_bar(match.person_id, gallery, counts, configured.attach_above),
            )
        if (
            now is Attribution.SUGGESTED
            and question.confidence is not None
            and abs(own - question.confidence) < _SAME_SCORE
        ):
            continue
        rulings.append(
            Ruling(
                track_id=question.track_id,
                was_person=question.person_id,
                was=Attribution.SUGGESTED,
                person_id=question.person_id if now is not None else None,
                attribution=now,
                confidence=own if now is not None else None,
                # Said again on a re-score, which pins the word on a question from before it was
                # written down.
                asked_by=question.asked_by,
            )
        )
    return rulings


def _matched_words(
    person_id: str, name: str, pairs: Sequence[tuple[str, str, float]], *, during_a_scan: bool
) -> tuple[str, str, dict[str, str] | None]:
    """A match receipt's count, title and link: `(many, title, link)`.

    The link is only on a re-match, whose number is the one thing on the line somebody can act
    on; a scan's names the person, who has a page. Its address is written by `sentences.py`, the
    one place that spells one, and stored: which faces this was is knowable now and at no other
    moment. "recognized ... as" is the state's own word (Recognized by Sift).
    """
    one = len(pairs) == 1
    if during_a_scan:
        many = "1 face" if one else f"{len(pairs)} faces"
        return many, matched_sentence(name, [sure for _track, _asset, sure in pairs]), None
    many = "1 more face" if one else f"{len(pairs)} more faces"
    title = f"Sift recognized {many} as {name}{_how_sure(pairs)}"
    link = {
        "kind": "faces",
        "id": person_id,
        "words": many,
        "href": faces_of_person(person_id, FACES_MATCHED),
    }
    return many, title, link


def _from_groups_words(
    person_id: str, name: str, pairs: Sequence[tuple[str, str, float]]
) -> tuple[str, str, dict[str, str] | None]:
    """The count, title and link of the receipt for a group's questions a re-match named: one
    line per person per run, linked to the faces as a re-match's own receipt is."""
    many = "1 face" if len(pairs) == 1 else f"{len(pairs):,} faces"
    title = f"Sift named {many} as {name} from the groups that looked like them"
    link = {
        "kind": "faces",
        "id": person_id,
        "words": many,
        "href": faces_of_person(person_id, FACES_MATCHED),
    }
    return many, title, link


def _from_groups_detail(many: str, name: str, faces: int, known_by: int) -> str:
    """That receipt's second line: why these faces were asked, and what its Undo does."""
    one = faces == 1
    return (
        f"{many} {'was' if one else 'were'} asked about when somebody named "
        f"{'its group' if one else 'their groups'} as {name}, and "
        f"{'it' if one else 'each'} looked enough like {name} to name without asking, compared "
        f"with {_pictures(known_by)}. {_undo_asks(faces, faces)} No file is touched and nothing "
        "is deleted."
    )


def _matched_detail(many: str, name: str, faces: int, known_by: int, asked: int) -> str:
    """A match receipt's second line, the verb agreeing with the count."""
    one = faces == 1
    return (
        f"{many} {'was' if one else 'were'} attached by comparing "
        f"{'it' if one else 'them'} with {_pictures(known_by)} of {name}, "
        "without being asked. "
        f"{_undo_asks(asked, faces)} No file is touched and "
        "nothing is deleted."
    )


class MatchingMixin(LearningMixin):
    """Comparing faces with everybody, and recording and taking back what a pass attached."""

    async def _attribute(
        self, track_ids: Sequence[str], configured: Configured
    ) -> dict[str, list[tuple[str, float]]]:
        """Compare each new appearance against everybody, act on the answer, and say what it did.

        **What it ATTACHED is handed back, not recorded here**: the record waits for the settle
        still ahead in the scan. Keyed on the person, holding the appearance and how sure the
        comparison was; the file is the caller's.
        """
        attached: dict[str, list[tuple[str, float]]] = {}
        gallery = await self._gallery_for(configured.groups, configured.recognizer)
        if len(gallery) == 0:
            return attached
        rejections = await self._store.rejections()
        # How well Sift knows each person decides the bar (`tuning.bar_for`): once per file, over
        # the pictures THIS model measured, the ones the gallery was built from.
        counts = await self._store.reference_counts(recognizer=configured.recognizer)
        # Every appearance's faces in ONE read: all belong to the one file being scanned.
        faces_by_track = await self._store.faces_of_many(track_ids)
        for track_id in track_ids:
            faces = faces_by_track.get(track_id, [])
            if not faces:
                # An appearance whose faces have gone has nothing to compare.
                continue
            # The clearest view, chosen exactly as a later re-match chooses it.
            best = clearest(faces)
            match = matching.best_match(
                best.vector,
                gallery,
                rejected=rejections.get(track_id, ()),
                floor=configured.suggest_above,
            )
            if match is None:
                continue
            # The bar belongs to whoever the face landed on, so it is settled after the match and
            # never before it. A lower bar does not make a worse match win: the best match is still
            # the best match, and what moves is only whether Sift acts on it alone.
            outcome = matching.verdict(
                match,
                attach_above=_attach_bar(match.person_id, gallery, counts, configured.attach_above),
            )
            await self._store.attribute(
                track_id,
                match.person_id,
                confidence=match.confidence,
                attribution=outcome,
                # The arithmetic asked, when this is a question: a re-match may recognize it once
                # it clears the line. Written only on a question (`Store.attribute`).
                asked_by=AskedBy.MATCH,
            )
            # No cover from a match: a cover nobody chose is the whole first picture filed under
            # the person, which the filing this match leads to gives them where they have none
            # (`kernel/access/default_covers.py`). Never a face cut out of one.
            if outcome is Attribution.MATCHED:
                attached.setdefault(match.person_id, []).append((track_id, match.confidence))
        return attached

    async def rematch(self) -> int:
        """Compare every appearance nobody has been attached to against everybody, again, and
        every question standing, by the same arithmetic. Returns how many faces moved.

        Runs when references change and when either line changes. **No file is opened**: every
        face's description is stored, so this is arithmetic over a few megabytes. What it attaches
        on its own is recorded per person (`_record_matches`), and a question it stops asking
        likewise (`_record_stopped_asking`), each with a way to take it back.
        """
        await self._require_enabled()
        configured = await self.configuration()
        gallery = await self._gallery_for(configured.groups, configured.recognizer)
        if len(gallery) == 0:
            return 0

        rejections = await self._store.rejections()
        ignored = await self._store.ignored_track_ids()
        # The bar belongs to the person a match lands on, counted over this model's pictures, as
        # `_attribute` reads it; once for the whole pass.
        counts = await self._store.reference_counts(recognizer=configured.recognizer)
        # Who Sift knows from enough confirmed faces to name a group's question and to learn from
        # its own names, read once for the pass.
        confirmed = await self._store.confirmed_reference_counts(recognizer=configured.recognizer)
        tally = _Rematched()

        # 1. The questions standing, judged again.
        questions = {one.track_id: one for one in await self._store.asked(configured.recognizer)}
        rulings = _judge_questions(
            questions.values(),
            ignored=ignored,
            gallery=gallery,
            rejections=rejections,
            counts=counts,
            named_from_groups=names_groups(confirmed, gallery),
            configured=configured,
        )
        tally.questions_landed(rulings, questions, await self._store.restate(rulings))
        # 2. EVERY FACE NOBODY IS ON, including the questions step 1 stopped asking.
        await self._match_the_unnamed(
            configured,
            gallery,
            rejections=rejections,
            ignored=ignored,
            counts=counts,
            tally=tally,
        )

        await self._settle_all(sorted(tally.touched))
        # After the attributions are settled, never before: a record of a decision that then
        # failed to take effect is worse than none.
        await self._record_matches(tally.attached, was_asked=tally.was_asked)
        await self._record_matches(tally.from_groups, was_asked=tally.was_asked, from_groups=True)
        await self._record_stopped_asking(tally.stopped, line=configured.suggest_above)
        # 3. What Sift may learn from its own names, once they have landed and been recorded.
        learned = await self.learn_from_recognitions(confirmed, configured)
        if tally.attached or tally.from_groups or learned:
            # Every screen drawing those faces is told once for the pass: `_settle_all` speaks only
            # when a file's People change, so a face named on a file already carrying her would
            # tell nobody. `announce_now`, since each attribution was its own transaction and has
            # landed. Every admin: the screens then ask, per viewer.
            announce_now(EVERY_ADMIN, About.LIBRARY)
        log.info(
            "faces.rematch",
            attributed=tally.changed,
            files_touched=len(tally.touched),
            questions_recognized=len(tally.was_asked),
            group_questions_named=sum(len(faces) for faces in tally.from_groups.values()),
            questions_withdrawn=sum(len(faces) for faces in tally.stopped.values()),
            questions_rescored=tally.rescored,
            group_questions_scored=tally.offers_kept,
            references_learned=learned,
        )
        return tally.changed

    async def _match_the_unnamed(
        self,
        configured: Configured,
        gallery: matching.Gallery,
        *,
        rejections: Mapping[str, set[str]],
        ignored: Collection[str],
        counts: Mapping[str, int],
        tally: _Rematched,
    ) -> None:
        """Step 2 of a re-match: every face nobody is on compared with everybody, into `tally`."""
        fresh: dict[str, tuple[str, float]] = {}
        rulings: list[Ruling] = []
        for track_id, asset_id, vector in await self._store.unattributed(configured.recognizer):
            if track_id in ignored:
                continue
            match = matching.best_match(
                vector,
                gallery,
                rejected=rejections.get(track_id, ()),
                floor=configured.suggest_above,
            )
            if match is None:
                continue
            # After the match, by the same function `_attribute` asks: the bar is the person's.
            outcome = matching.verdict(
                match,
                attach_above=_attach_bar(match.person_id, gallery, counts, configured.attach_above),
            )
            fresh[track_id] = (asset_id, match.confidence)
            rulings.append(
                Ruling(
                    track_id=track_id,
                    was_person=None,
                    was=None,
                    person_id=match.person_id,
                    attribution=outcome,
                    confidence=match.confidence,
                    asked_by=AskedBy.MATCH,
                )
            )
        landed = await self._store.restate(rulings)
        for ruling in rulings:
            if ruling.track_id not in landed or ruling.person_id is None:
                continue
            asset_id, sure = fresh[ruling.track_id]
            if ruling.attribution is Attribution.MATCHED:
                tally.attached.setdefault(ruling.person_id, []).append(
                    (ruling.track_id, asset_id, sure)
                )
            tally.changed += 1
            tally.touched.add(asset_id)

    async def _record_matches(
        self,
        attached: Mapping[str, list[tuple[str, str, float]]],
        *,
        during_a_scan: bool = False,
        was_asked: Mapping[str, float | None] | None = None,
        from_groups: bool = False,
    ) -> None:
        """Write down what Sift attached on its own, so it can be taken back.

        `from_groups` is a re-match's names for questions a GROUP asked (`names_groups`): the same
        receipt and the same Undo, which asks about every face again, under its own words.

        `was_asked` names the faces a re-match attached that had been QUESTIONS, with the
        confidence each was put at: the receipt carries every face's earlier state, so Undo puts a
        question back under Needs your input rather than leaving it unnamed. No `cover`: a match
        gives nobody one (an older receipt's is read by `IdentifiedRecords.reverse`).

        **Two grains.** A re-match is one press over the library: one record per PERSON, the run
        being the record and the face the sentence (a file's history reads each match off
        `face_tracks`). A scan is a per-file pipeline: one record per person per FILE, the act,
        undoable a file at a time, as the filename and watermark passes record theirs.

        A scan's title is the sentence the file's own history writes for that match
        (`history.matched_sentence`), so the pane folds the two into one line. Every file touched
        and the person are subjects. `user_id` is None: a pass is not a user.
        """
        if self._recorder is None:
            return
        for person_id, pairs in sorted(attached.items()):
            name = await self._store.person_name(person_id)
            if name is None:  # pragma: no cover (the person was read a moment ago to match on)
                continue
            # WHAT THE MATCH WAS BASED ON, known at this moment only: how many of her own reference
            # pictures the faces were compared with.
            known_by = await self._store.reference_count(person_id)
            asked_here = {
                track: (was_asked or {})[track]
                for track, _asset, _sure in pairs
                if track in (was_asked or {})
            }
            if from_groups:
                many, title, link = _from_groups_words(person_id, name, pairs)
                detail = _from_groups_detail(many, name, len(pairs), known_by)
            else:
                many, title, link = _matched_words(
                    person_id, name, pairs, during_a_scan=during_a_scan
                )
                detail = _matched_detail(many, name, len(pairs), known_by, len(asked_here))
            subjects: list[Subject] = [Subject(kind="person", id=person_id)]
            subjects += [
                Subject(kind="asset", id=asset_id)
                for asset_id in dict.fromkeys(asset_id for _track, asset_id, _sure in pairs)
            ]
            async with self._store.database.write() as connection:
                # WHICH of her pictures, by id: an Undo that takes some of them away takes back a
                # run that rested on nothing else (`take_back_recognitions`).
                rested_on = await self._store.own_reference_ids(person_id)
                await self._recorder.record_on(
                    connection,
                    queue=IDENTIFIED_QUEUE,
                    user_id=None,
                    via=VIA_FACES,
                    title=title,
                    detail=detail,
                    payload=_matched_payload(
                        person_id,
                        pairs,
                        known_by=known_by,
                        rested_on=rested_on,
                        link=link,
                        asked_here=None if during_a_scan else asked_here,
                    ),
                    subjects=subjects,
                    # The ledger's own words: faces linked to one person, the object the person, so
                    # a file's pane folds this receipt and the line about the same match into one
                    # row without comparing their English.
                    verb="linked",
                    object=LedgerObject(kind="person", id=person_id, name=name),
                )

    async def live_ids(self, track_ids: Sequence[str]) -> dict[str, str]:
        """Each face by the id it goes by now; see `Store.live_ids`. What every Undo reads through."""
        return await self._store.live_ids(track_ids)

    async def unmatch(
        self,
        track_ids: Sequence[str],
        person_id: str,
        *,
        was_asked: Mapping[str, float | None] | None = None,
    ) -> int:
        """Take Sift's own matches back off these appearances. Returns how many came off.

        **Only the ones this decision actually made**, which is why the person is named as well as
        the faces: an appearance somebody has since AGREED to is `CONFIRMED` and stays, and one a
        later pass moved to somebody else belongs to that decision rather than to this one. Undo
        works from what the decision wrote down and never from the state it finds. See `Reverser`.

        **Every face goes under Needs your input, and stays there until somebody answers it.** A
        face that was a question before a re-match attached it (`was_asked`) goes back at the
        confidence it had been put at; one that was nobody's goes back at the confidence the match
        gave it. Each is asked as `AskedBy.UNDONE`, which a re-match scores and never recognizes:
        a face left unnamed, or put back as the kind of question it was, would be recognized again
        by the very next re-match at the same score, and the Undo would last until the next press.

        No rejection is remembered. Refusing a face says "this is not them" and teaches the next
        pass; taking a run back says "do not decide this for me", which is not the same claim, and
        writing a refusal here would quietly turn an undo into a judgement about every face in it.
        Asking instead is the claim itself: Sift may still wonder, and somebody decides.
        """
        await self._require_enabled()
        asked = was_asked or {}
        found = await self._store.tracks(track_ids)
        rulings: list[Ruling] = []
        for track_id in dict.fromkeys(track_ids):
            track = found.get(track_id)
            if track is None or track.person_id != person_id:
                continue
            # Only a face still carrying THIS match: the guard below writes nothing to a face that
            # is not `MATCHED` to this person any more, so one somebody has since agreed to stays.
            rulings.append(
                Ruling(
                    track_id=track_id,
                    was_person=person_id,
                    was=Attribution.MATCHED,
                    person_id=person_id,
                    attribution=Attribution.SUGGESTED,
                    confidence=asked.get(track_id, track.confidence),
                    asked_by=AskedBy.UNDONE,
                )
            )
        # Guarded as the pass that wrote them was: a face answered between the read above and this
        # write carries the newer decision, and the undo leaves it there.
        landed = await self._store.restate(rulings)
        taken = [found[one.track_id].asset_id for one in rulings if one.track_id in landed]
        # A face Sift learned from rests on its name, so the reference goes with it
        # (`learn_from_recognitions`).
        unlearned = await self._store.unlearn_recognitions(person_id, taken)
        await self._settle_all(taken)
        log.info("faces.unmatched", person_id=person_id, faces=len(taken), unlearned=unlearned)
        return len(taken)

    async def take_back_recognitions(
        self, person_id: str, removed: Collection[str], *, since: str
    ) -> tuple[list[str], int]:
        """Take back Sift's own matches of her that rested only on pictures an Undo just removed.

        Returns the receipts taken back, by id, and how many faces came off. What the Undo of a
        naming (or of an agreement) runs once its pictures are gone: a recognition is a comparison
        with her pictures, and one whose every picture went with the Undo rests on nothing that
        stands, so it goes with the Undo rather than waiting for its own. A run that also used a
        picture that stays is left exactly as it is.

        Which pictures a run rested on is on its receipt (`rested_on`). A receipt written before
        that was kept says only how many, and is taken back when she has no picture of her own
        left: whatever it rested on has gone. Only runs made at or after decision `since` are
        read, since a run before the naming cannot have rested on its pictures.
        """
        gone = set(removed)
        if not gone:
            return [], 0
        remaining: int | None = None
        taken: list[str] = []
        faces = 0
        for receipt_id, payload in await self._store.standing_recognitions(person_id, since):
            recorded = payload_held(payload)
            if recorded.get("act") is not None or not isinstance(recorded.get("track_ids"), list):
                continue
            rested = recorded.get("rested_on")
            if isinstance(rested, list):
                if not rested or not {str(one) for one in rested} <= gone:
                    continue
            else:
                if remaining is None:
                    remaining = await self._store.reference_count(person_id)
                if remaining > 0:
                    continue
            live = await self._store.live_ids([str(one) for one in recorded["track_ids"]])
            track_ids = [live.get(str(one), str(one)) for one in recorded["track_ids"]]
            states = recorded.get("attribution")
            numbers = recorded.get("confidence")
            was_asked = {
                live.get(str(one), str(one)): (
                    numbers.get(one) if isinstance(numbers, dict) else None
                )
                for one, value in (states.items() if isinstance(states, dict) else ())
                if value == Attribution.SUGGESTED.value
            }
            faces += await self.unfile_matches(track_ids, person_id, was_asked=was_asked)
            taken.append(receipt_id)
        if taken:
            log.info(
                "faces.recognitions_taken_back",
                person_id=person_id,
                runs=len(taken),
                faces=faces,
            )
        return taken, faces

    async def unfile_matches(
        self,
        track_ids: Sequence[str],
        person_id: str,
        *,
        was_asked: Mapping[str, float | None] | None = None,
    ) -> int:
        """Put Sift's matches of these faces back where each stood before the run. How many.

        Unlike `unmatch`, which leaves every face asked about so the next re-match cannot make it
        again, this is for a match whose pictures are gone: nothing is left to make it again with,
        so a face that was nobody's goes back to nobody and into a group, and one that was a
        question (`was_asked`) goes back to being asked at the confidence it had. Only a face still
        carrying this match moves: one somebody has since agreed to stays.
        """
        await self._require_enabled()
        asked = was_asked or {}
        found = await self._store.tracks(track_ids)
        rulings: list[Ruling] = []
        for track_id in dict.fromkeys(track_ids):
            track = found.get(track_id)
            if track is None or track.person_id != person_id:
                continue
            question = track_id in asked
            rulings.append(
                Ruling(
                    track_id=track_id,
                    was_person=person_id,
                    was=Attribution.MATCHED,
                    person_id=person_id if question else None,
                    attribution=Attribution.SUGGESTED if question else None,
                    confidence=asked.get(track_id) if question else None,
                )
            )
        landed = await self._store.restate(rulings)
        taken = [found[one.track_id].asset_id for one in rulings if one.track_id in landed]
        await self._store.unlearn_recognitions(person_id, taken)
        await self._settle_all(taken)
        if taken:
            # The faces back with nobody are in no group: placed now, as a Yes taken back places
            # the faces it offered (`unname_groups`).
            await self.regroup(full=False)
        return len(taken)

    async def return_questions(self, person_id: str, *, leaving: Collection[str] = ()) -> int:
        """Her questions that rest on nothing now, back to nobody. How many went.

        What the Undo of a naming runs once it has taken her pictures away (`IdentifiedRecords`):
        with no picture of her left, not her own and not a starter in use, a question about her
        is a comparison with nothing, and the rule that keeps a question standing ("no evidence is
        not evidence against", `rematch` step 1) would keep it for ever. So each of Sift's own
        questions goes back to nobody and into a group, and asks again on its own once she has a
        picture: the next re-match compares it with that picture like any other face.

        Sift's own questions alone (`Store.questions_sift_asked`): a group's are there because
        somebody named the group. A face somebody answered is no question: a Yes made it hers and
        a No took her off it. Guarded as every write here is, so a face answered between the read
        and the write keeps the answer. The questions that went are written down with an Undo that
        asks them again (`STOPPED_ASKING`), and that line is what History says about them.

        `leaving` is the faces of the decision being taken back: its Undo has just put each where
        it stood before the press, a question included, and that is the Undo's whole promise.
        """
        await self._require_enabled()
        if await self._store.pictures_in_use(person_id) > 0:
            return 0
        left = set(leaving)
        asked = [
            one for one in await self._store.questions_sift_asked(person_id) if one[0] not in left
        ]
        landed = await self._store.restate(
            [
                Ruling(
                    track_id=track_id,
                    was_person=person_id,
                    was=Attribution.SUGGESTED,
                    person_id=None,
                    attribution=None,
                    confidence=None,
                )
                for track_id, _asset, _sure in asked
            ]
        )
        went = [one for one in asked if one[0] in landed]
        if not went:
            return 0
        await self._settle_all([asset_id for _track, asset_id, _sure in went])
        await self.regroup(full=False)
        await self._record_questions_returned(person_id, went)
        log.info("faces.questions_returned", person_id=person_id, faces=len(went))
        return len(went)

    async def _record_questions_returned(
        self, person_id: str, went: Sequence[tuple[str, str, float | None]]
    ) -> None:
        """The line History says for the questions an Undo left resting on nothing.

        The stopped-asking receipt's shape (`_record_stopped_asking`) under its act, so its Undo
        asks each question again at the confidence it had been put at; its own words, because the
        reason is not a line that moved but a picture that went.
        """
        if self._recorder is None:
            return
        name = await self._store.person_name(person_id) or "this person"
        many = "1 question" if len(went) == 1 else f"{len(went):,} questions"
        subjects: list[Subject] = [Subject(kind="person", id=person_id)]
        subjects += [
            Subject(kind="asset", id=asset_id)
            for asset_id in dict.fromkeys(asset_id for _track, asset_id, _sure in went)
        ]
        async with self._store.database.write() as connection:
            # Inside the write that records it: the faces are back with nobody by now, and every
            # screen drawing her questions, the groups or History reads again once this lands.
            announce(EVERY_ADMIN, About.LIBRARY)
            await self._recorder.record_on(
                connection,
                queue=IDENTIFIED_QUEUE,
                user_id=None,
                via=VIA_FACES,
                title=(
                    f"The Undo left no picture of {name}, and {many} about them went back to nobody"
                ),
                detail=(
                    f"{'It is' if len(went) == 1 else 'They are'} asked about again once {name} "
                    f"has a picture. Undo puts {'it' if len(went) == 1 else 'them'} back under "
                    "Needs your input now. No file is touched and nothing is deleted."
                ),
                payload=json.dumps(
                    {
                        "act": STOPPED_ASKING,
                        "person_id": person_id,
                        "track_ids": [track for track, _asset, _sure in went],
                        "confidence": {track: sure for track, _asset, sure in went},
                        "attribution": {
                            track: Attribution.SUGGESTED.value for track, _asset, _sure in went
                        },
                    }
                ),
                subjects=subjects,
            )

    async def _record_stopped_asking(
        self, stopped: Mapping[str, list[tuple[str, str, float | None]]], *, line: float
    ) -> None:
        """Write down the questions a re-match stopped asking, one record per person, with Undo.

        The same grain as a re-match's attachments and for the same reason (see `_record_matches`):
        one pass, one record per person, every file named as a subject. The payload is the refusal
        receipt's shape (each face's earlier state and confidence) under its own act, because
        nobody refused anything: the arithmetic stopped asking, and Undo puts each question back.
        """
        if self._recorder is None:
            return
        for person_id, faces in sorted(stopped.items()):
            name = await self._store.person_name(person_id)
            if name is None:  # pragma: no cover (the person was read a moment ago to match on)
                continue
            one = len(faces) == 1
            many = "1 face" if one else f"{len(faces)} faces"
            subjects: list[Subject] = [Subject(kind="person", id=person_id)]
            subjects += [
                Subject(kind="asset", id=asset_id)
                for asset_id in dict.fromkeys(asset_id for _track, asset_id, _sure in faces)
            ]
            async with self._store.database.write() as connection:
                await self._recorder.record_on(
                    connection,
                    queue=IDENTIFIED_QUEUE,
                    user_id=None,
                    via=VIA_FACES,
                    title=f"Sift stopped asking whether {many} {'is' if one else 'are'} {name}",
                    detail=(
                        f"{'It' if one else 'They'} no longer look{'s' if one else ''} enough like "
                        f"{name} to ask about: under {round(line * 100)}%. Undo puts "
                        f"{'it' if one else 'them'} back under Needs your input. No file is "
                        "touched and nothing is deleted."
                    ),
                    payload=json.dumps(
                        {
                            "act": STOPPED_ASKING,
                            "person_id": person_id,
                            "track_ids": [track for track, _asset, _sure in faces],
                            "confidence": {track: sure for track, _asset, sure in faces},
                            "attribution": {
                                track: Attribution.SUGGESTED.value for track, _asset, _sure in faces
                            },
                        }
                    ),
                    subjects=subjects,
                    # "decided", the default, as the refusal receipt it mirrors writes: a question
                    # withdrawn was never a name on the file, so it neither linked nor unlinked one.
                )

    async def ask_again(self, person_id: str, confidence: Mapping[str, float | None]) -> int:
        """Put questions a re-match stopped asking back under Needs your input. Returns how many.

        Each carries the confidence it had been put at. From what the receipt wrote down and never
        from the state, like every undo here: a face somebody has named since, or a later pass put
        somebody on, carries a newer decision and is left alone, and so is one somebody has said is
        not this person, because putting the question back would ask what they already answered.
        """
        await self._require_enabled()
        refused = await self._store.rejections()
        rulings = [
            Ruling(
                track_id=track_id,
                was_person=None,
                was=None,
                person_id=person_id,
                attribution=Attribution.SUGGESTED,
                confidence=sure,
            )
            for track_id, sure in confidence.items()
            if person_id not in refused.get(track_id, ())
        ]
        landed = await self._store.restate(rulings)
        found = await self._store.tracks(sorted(landed))
        await self._settle_all([track.asset_id for track in found.values()])
        log.info("faces.questions_asked_again", person_id=person_id, faces=len(landed))
        return len(landed)
