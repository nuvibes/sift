# SPDX-License-Identifier: AGPL-3.0-or-later
"""Scanning one file for faces.

A rescan replaces a file's appearances, so a scan also puts back every decision already made about
the same faces before it attributes anything.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import replace
from typing import Any

from sift.kernel.log import get_logger
from sift.kernel.media import NoReadableCopy, Source, resolve_decodable
from sift.slices.faces import crop as cropping
from sift.slices.faces import recognize, tuning, weights
from sift.slices.faces.frames import Reader
from sift.slices.faces.models import Appearance, AskedBy, Attribution, Depth, ScanStatus, Vector
from sift.slices.faces.pipeline import Outcome, Pipeline
from sift.slices.faces.recognize import Recognizer
from sift.slices.faces.runner import IN_FLIGHT
from sift.slices.faces.service_base import Configured
from sift.slices.faces.service_box import BOX_QUESTIONS_PAGE, BoxQuestionsMixin
from sift.slices.faces.service_matching import MatchingMixin
from sift.slices.faces.service_weights import WeightsMixin
from sift.slices.faces.store import PassRecord, Ruling, Standing, clearest

log = get_logger(__name__)


def _named_before(old: Standing | None, person_id: str) -> bool:
    """Whether the face a rescan found carried this very name, given by Sift, before it."""
    return old is not None and old.person_id == person_id and old.attribution is Attribution.MATCHED


def _with_depth(configured: Configured, depth: Depth) -> Configured:
    """The same settings, looked at as hard as this one file was asked for.

    Only the depth changes. The sampling stays as the setting stated it and the density follows,
    so asking for a deep look at a file on an install already set to deep is the same thing twice
    rather than three times as much again, and asking for a fast look on that install gets the
    install's ordinary pass back.
    """
    return replace(configured, depth=depth)


def _pass_record(
    configured: Configured, outcome: Outcome, recognizer: str, *, found: bool
) -> PassRecord:
    """What a pass over one file records about itself, from the settings and what it saw."""
    return PassRecord(
        status=ScanStatus.NONE_IDENTIFIED if found else ScanStatus.NO_FACES,
        depth=configured.depth.value,
        coverage=outcome.coverage,
        frames_sampled=outcome.frames_examined,
        detector=weights.pairing(configured.family)[0].revision,
        recognizer=recognizer,
        settings_digest=configured.digest,
        settings_shape=configured.shape,
        settings_density=configured.density,
        # Cleared once the plan has run out, so a finished file carries no half-finished position.
        reached_ms=None if outcome.coverage >= 1.0 else outcome.reached_ms,
        # Why a file with nobody in it has nobody in it, for its History: nobody there, or
        # people too far away, told apart by reason.
        refused_small=outcome.refused_small,
        refused_closer=outcome.refused_closer,
        refused_largest=outcome.refused_largest,
        refused_blurred=outcome.refused_blurred,
        refused_turned=outcome.refused_turned,
        refused_edge=outcome.refused_edge,
        # WHY it stopped, which `reached_ms` cannot say: the time limit is the only early stop.
        cut_short=outcome.stopped_early,
    )


class ScanningMixin(WeightsMixin, MatchingMixin, BoxQuestionsMixin):
    """Scanning a file, and putting back what was decided about its faces."""

    async def scan(
        self,
        asset_id: str,
        *,
        depth: Depth | None = None,
        run: str | None = None,
        again: bool = False,
    ) -> ScanStatus:
        """Find, describe and attribute every face in one file. `run` names the sweep whose tuning
        it runs under (`Configured.pinned`); `again` is a press to look again (`_resume_point`)."""
        await self._require_enabled()
        configured = await self.configuration()
        if run is not None:
            configured = await self._as_the_run_started(configured, run)
        if depth is not None:
            configured = _with_depth(configured, depth)

        try:
            source = await resolve_decodable(self._content, asset_id, settings=self._settings)
        except NoReadableCopy:
            # No copy just now (a drive unplugged): transient, cleared by the next scan to see it.
            await self._give_up(
                asset_id,
                code="no_copy",
                reason="No copy of this file could be read.",
                transient=True,
            )
            return await self._settle(asset_id)

        resume_from = await self._resume_point(asset_id, configured, again=again)
        outcome, recognizer = await self._read_faces(asset_id, configured, source, resume_from)

        # Moments to look at, not cut short, nothing back: the file cannot be looked at, a verdict
        # rather than "no faces", and what an earlier pass found stays.
        if (
            outcome.frames_attempted > 0
            and outcome.frames_examined == 0
            and not outcome.stopped_early
        ):
            await self._give_up(
                asset_id,
                code="no_frame_decoded",
                reason="No moment of this file could be decoded, so nothing in it could be looked at.",
                transient=False,
            )
            return await self._settle(asset_id)

        appearances = await self._without_removed(
            asset_id, outcome.appearances, recognizer.revision
        )
        pictures = [
            await cropping.encode([face.chip for face in appearance.faces], self._settings)
            for appearance in appearances
        ]
        record = _pass_record(configured, outcome, recognizer.revision, found=bool(appearances))
        track_ids, before, carried = await self._keep_the_pass(
            asset_id, appearances, pictures, record, resume_from=resume_from
        )
        matched, boxed = await self._decide_the_faces(
            asset_id, configured, track_ids, before, carried
        )
        status = await self._settle(asset_id)
        # AFTER THE SETTLE, and ONLY WHAT IS NEW: a face found again with the name it carried is
        # still reached by the receipt that named it (`face_successors`).
        new = {
            person_id: [
                (track_id, asset_id, sure)
                for track_id, sure in pairs
                if not _named_before(carried.get(track_id), person_id)
            ]
            for person_id, pairs in matched.items()
        }
        await self._record_matches(
            {person_id: pairs for person_id, pairs in new.items() if pairs}, during_a_scan=True
        )
        await self._record_box_questions(boxed)
        return status

    async def _read_faces(
        self, asset_id: str, configured: Configured, source: Source, resume_from: int | None
    ) -> tuple[Outcome, Recognizer]:
        """Run the pipeline over one file, from `resume_from` when a pass carries on."""
        asset = source.asset
        detector, recognizer = await self._models(configured)
        # A file the previous model described is measured again FIRST: everything a rescan puts
        # back is found by comparing descriptions, which must be this model's.
        previous = await self._store.scan_of(asset_id)
        if previous is not None and previous.recognizer != recognizer.revision:
            await self._remeasure_file(asset_id, recognizer)
        pipeline = Pipeline(
            Reader(self._settings),
            detector,
            recognizer,
            bar=configured.bar,
            density=configured.density,
            budget_seconds=configured.budget_seconds,
        )
        # Named for the log a lost device leaves (`runner.IN_FLIGHT`), and only while it reads.
        reading = IN_FLIGHT.set(f"file {asset_id}")
        try:
            outcome = await pipeline.run(
                source.path,
                media_type=asset.media_type,
                width=asset.width or 0,
                height=asset.height or 0,
                duration_ms=asset.duration_ms,
                after_ms=resume_from,
            )
        finally:
            IN_FLIGHT.reset(reading)
        return outcome, recognizer

    async def _keep_the_pass(
        self,
        asset_id: str,
        appearances: Sequence[Appearance],
        pictures: Sequence[Sequence[bytes]],
        record: PassRecord,
        *,
        resume_from: int | None,
    ) -> tuple[list[str], Sequence[Standing], dict[str, Standing]]:
        """Store what a pass found: `(track ids, the faces before, the faces carried across)`.

        A whole pass replaces the file's faces and pairs each found again with the one it was
        (`_carry_across`); a pass carrying on keeps every appearance and carries nothing.
        """
        carried: dict[str, Standing] = {}
        before: Sequence[Standing] = ()
        if resume_from is None:
            before = await self._store.standing_of(asset_id)
            track_ids = await self._store.replace_pass(asset_id, appearances, pictures, record)
            carried = await self._carry_across(asset_id, before, track_ids)
        else:
            joins = await self._rejoin(asset_id, appearances)
            track_ids = await self._store.extend_pass(
                asset_id, appearances, pictures, joins, record
            )
        return track_ids, before, carried

    async def _decide_the_faces(
        self,
        asset_id: str,
        configured: Configured,
        track_ids: list[str],
        before: Sequence[Standing],
        carried: dict[str, Standing],
    ) -> tuple[dict[str, list[tuple[str, float]]], list[tuple[str, str, str]]]:
        """Put back what was decided about a file's faces, then attribute the rest, in the order
        each step needs. Grows `carried`; answers what was attached and the box questions asked."""
        # Before attribution: a face somebody answered for is not a question, and a guess must not
        # overwrite a decision.
        decided = await self._set_aside_again(asset_id, track_ids)
        decided |= await self._name_again(asset_id, track_ids)
        # A "no" does not take the face out of matching: refused as one person, it may be
        # another, so `_attribute` reads the refusal and passes that person over.
        await self._reject_again(asset_id, track_ids)
        matched = await self._attribute(
            [one for one in track_ids if one not in decided], configured, asset_id=asset_id
        )
        # A confirmation the description could not pair is put back by the person where that is
        # unambiguous (`_confirm_by_name`), first, so the face it takes is nobody else's to pair.
        carried |= await self._confirm_by_name(
            asset_id, before, carried, decided, track_ids, matched
        )
        # Then by the name Sift had given it, now the arithmetic has spoken (`_carry_by_name`).
        carried |= await self._carry_by_name(asset_id, before, carried, track_ids)
        # A face whose name somebody took back is asked about again, never named (`unmatch`).
        await self._still_asked(carried, matched)
        # After attribution: a hand-made pile holds faces nobody has named, so a face just
        # recognized has left it.
        await self._group_again(asset_id, [one for one in track_ids if one not in decided])
        # The one face of a file a stash-box put somebody on, with nobody to compare it with: the
        # box's question (`service_box`), after the arithmetic and before the settle counts it.
        boxed = await self._ask_for_the_boxes(
            await self._store.box_questions(
                configured.recognizer, most=BOX_QUESTIONS_PAGE, asset_ids=[asset_id]
            )
        )
        return matched, boxed

    async def _carry_across(
        self, asset_id: str, before: Sequence[Standing], track_ids: Sequence[str]
    ) -> dict[str, Standing]:
        """Pair the appearances a rescan just found with the ones it replaced. New id to old.

        By description within the file, through the rule every remembered decision is put back by
        (`_already_decided`), and written down (`Store.write_successors`) so an Undo naming the old
        face reaches the new one. The old standing comes back too: what the scan reads to keep a
        taken-back name a question (`_still_asked`) and to leave a name it already carried
        unrecorded.
        """
        if not before or not track_ids:
            return {}
        pairs = await self._already_decided(track_ids, [(one, one.vector) for one in before])
        await self._store.write_successors(asset_id, [(old.track_id, new) for new, old in pairs])
        return {new: old for new, old in pairs}

    async def _confirm_by_name(
        self,
        asset_id: str,
        before: Sequence[Standing],
        carried: Mapping[str, Standing],
        decided: Collection[str],
        track_ids: Sequence[str],
        matched: dict[str, list[tuple[str, float]]],
    ) -> dict[str, Standing]:
        """Put back a confirmation the description could not pair, ONLY where nothing else fits.

        `_name_again` pairs a person's confirmation with the face found again by description, at
        `tuning.ALREADY_DECIDED`. A pass over a video at another depth describes the same face too
        differently, the confirmation is not put back, and the name returns as Sift's own guess,
        with a fresh "Sift recognized X here" for a face somebody had confirmed.

        A confirmation is somebody's decision, so it moves by the person only when there is exactly
        one place for it: ONE confirmed face of that person in this file that found no pair, and
        ONE face found again that no pair claimed and that the arithmetic put on that same person.
        Two of either and nothing is moved: which face was confirmed is then a guess, and a guess
        is what a confirmation exists to replace. Then it is done as `_name_again` does it: named as
        theirs, the file's pictures of them handed to the new face, and no receipt for the match.
        """
        paired = {old.track_id for old in carried.values()}
        lost: dict[str, list[Standing]] = {}
        for old in before:
            if (
                old.track_id not in paired
                and old.person_id is not None
                and old.attribution is Attribution.CONFIRMED
            ):
                lost.setdefault(old.person_id, []).append(old)
        if not lost:
            return {}
        fresh = [one for one in track_ids if one not in carried and one not in decided]
        now = await self._store.tracks(fresh)
        found: dict[str, list[str]] = {}
        for track in now.values():
            if track.person_id in lost and track.attribution in (
                Attribution.MATCHED,
                Attribution.SUGGESTED,
            ):
                found.setdefault(track.person_id, []).append(track.id)
        out: dict[str, Standing] = {}
        for person_id, olds in lost.items():
            news = found.get(person_id, [])
            if len(olds) != 1 or len(news) != 1:
                continue
            (track_id,) = news
            await self._store.attribute(
                track_id, person_id, confidence=1.0, attribution=Attribution.CONFIRMED
            )
            claimed = await self._store.claim_references_in(person_id, asset_id, track_id)
            if claimed:
                log.info("faces.reference.reclaimed", asset_id=asset_id, pictures=claimed)
            matched[person_id] = [
                pair for pair in matched.get(person_id, []) if pair[0] != track_id
            ]
            out[track_id] = olds[0]
        if out:
            log.info("faces.scan.confirmed_again_by_person", asset_id=asset_id, faces=len(out))
            await self._store.write_successors(
                asset_id, [(old.track_id, new) for new, old in out.items()]
            )
        return out

    async def _carry_by_name(
        self,
        asset_id: str,
        before: Sequence[Standing],
        carried: Mapping[str, Standing],
        track_ids: Sequence[str],
    ) -> dict[str, Standing]:
        """Pair what the description could not, by the name Sift had put on the face. New id to old.

        `_carry_across` pairs by description at `tuning.ALREADY_DECIDED`, which is strict because it
        also puts back what PEOPLE decided. A pass over a video looks at its own moments (another
        depth, another budget, a pass cut short), so the clearest picture of the same face differs
        from the last pass's and the pair is missed, and a rescan would write "Sift recognized X
        here" again for the same person in the same file. A name an Undo took back is lost the same
        way: the face found again meets no `UNDONE` question and the arithmetic names it. Both are
        reproduced in `tests/test_service_review.py`.

        So a face that was Sift's own name for somebody, or a question an Undo left about somebody
        (`AskedBy.UNDONE`), and found no pair, is the face in this file that the arithmetic now puts
        that same person on (a name first, then a question), one old face to one new. Sift's own
        words and the Undo's alone are carried this way. A confirmation, a No and a discarded pile
        are still put back by description alone, because pairing those wrongly would move a
        decision somebody made onto a face they never saw.
        """
        paired = {old.track_id for old in carried.values()}
        waiting = [
            old
            for old in before
            if old.track_id not in paired
            and old.person_id is not None
            and (
                old.attribution is Attribution.MATCHED
                or (old.attribution is Attribution.SUGGESTED and old.asked_by is AskedBy.UNDONE)
            )
        ]
        fresh = [one for one in track_ids if one not in carried]
        if not waiting or not fresh:
            return {}
        now = await self._store.tracks(fresh)
        ranked = sorted(
            (track for track in now.values() if track.person_id is not None),
            key=lambda track: track.attribution is not Attribution.MATCHED,
        )
        out: dict[str, Standing] = {}
        for track in ranked:
            if track.attribution not in (Attribution.MATCHED, Attribution.SUGGESTED):
                continue
            old = next((one for one in waiting if one.person_id == track.person_id), None)
            if old is None:
                continue
            waiting.remove(old)
            out[track.id] = old
        await self._store.write_successors(
            asset_id, [(old.track_id, new) for new, old in out.items()]
        )
        return out

    async def _still_asked(
        self, carried: Mapping[str, Standing], matched: dict[str, list[tuple[str, float]]]
    ) -> None:
        """Keep a face somebody took a name back from a question, across the rescan that found it.

        `unmatch` marks such a face `AskedBy.UNDONE` and a re-match never names it; but a rescan
        replaces the appearance, and the scan's own arithmetic would then meet a face with nothing
        on it and name it again. Each one the arithmetic just named, or asked about, as the same person
        goes back to being asked as `UNDONE`, and leaves `matched`, so no receipt says it was named.
        """
        held = {
            new: old.person_id
            for new, old in carried.items()
            if old.asked_by is AskedBy.UNDONE and old.person_id is not None
        }
        if not held:
            return
        now = await self._store.tracks(list(held))
        rulings = [
            Ruling(
                track_id=track.id,
                was_person=track.person_id,
                was=track.attribution,
                person_id=track.person_id,
                attribution=Attribution.SUGGESTED,
                confidence=track.confidence,
                asked_by=AskedBy.UNDONE,
            )
            for track in now.values()
            if track.person_id == held[track.id]
            and track.attribution in (Attribution.MATCHED, Attribution.SUGGESTED)
        ]
        landed = await self._store.restate(rulings)
        for person_id, pairs in matched.items():
            matched[person_id] = [pair for pair in pairs if pair[0] not in landed]

    async def _already_decided(
        self, track_ids: Sequence[str], remembered: Sequence[tuple[Any, Vector]]
    ) -> list[tuple[str, Any]]:
        """Pair freshly found appearances with decisions already made about the same faces.

        The one place the rule lives, because it is one rule serving three decisions: a face is the
        same face as one somebody already answered for when their descriptions agree closely, within
        the one file. Written per decision it would be the same loop three times and the same
        threshold three times, which is how two of them would end up tuned differently by accident.

        `remembered` is (whatever the decision carries, the description it was made on). The payload
        is opaque here (a pile for a face set aside, a person for a face named) because matching
        does not care which decision it is putting back, and a version that did would be a switch
        added to every future one.

        First match wins and each appearance is claimed once. Two remembered decisions about faces
        that close together are the same face twice; applying both would put one appearance in two
        places.
        """
        out: list[tuple[str, Any]] = []
        for track_id in track_ids:
            faces = await self._store.faces_of(track_id)
            if not faces:
                continue
            best = clearest(faces)
            for payload, vector in remembered:
                if recognize.similarity(best.vector, vector) >= tuning.ALREADY_DECIDED:
                    out.append((track_id, payload))
                    break
        return out

    async def _name_again(self, asset_id: str, track_ids: Sequence[str]) -> set[str]:
        """Put back the names a person gave this file, as theirs rather than as Sift's.

        The reference photo a confirmation files already brings the NAME back on its own, so nothing
        is lost, but it comes back as something Sift worked out, and anything landing between the
        two confidence thresholds comes back as a question the person had already answered. That
        is the whole of what this restores: the authorship, and with it the state that says no
        further decision is wanted.
        """
        remembered = await self._store.confirmations_for(asset_id)
        if not remembered or not track_ids:
            return set()

        named: set[str] = set()
        for track_id, person_id in await self._already_decided(track_ids, remembered):
            await self._store.attribute(
                track_id, person_id, confidence=1.0, attribution=Attribution.CONFIRMED
            )
            # And the pictures that confirmation filed follow the name (v24). The scan that just ran
            # deleted this file's appearances and cut fresh crops, so the references those
            # confirmations filed name an appearance that is gone; the only reader is a mark on a
            # screen, so nothing else would report it. The file has not moved, so the file is what
            # hands them over.
            claimed = await self._store.claim_references_in(person_id, asset_id, track_id)
            if claimed:
                log.info("faces.reference.reclaimed", asset_id=asset_id, pictures=claimed)
            named.add(track_id)
        if named:
            log.info("faces.scan.named_again", asset_id=asset_id, faces=len(named))
        return named

    async def _reject_again(self, asset_id: str, track_ids: Sequence[str]) -> int:
        """Put back every "no" said about a face in this file. Returns how many went back.

        The fifth decision of the shape `_name_again` restores, by the same rule and the same
        threshold (`_already_decided`). A rescan deletes every appearance a file had, and a refusal
        is keyed by the appearance, so without this every "not her" on a file would be wiped the
        next time it was looked at and the question would come straight back.

        **Asked once per person, which is the one way refusals differ from the other four.**
        `_already_decided` gives each appearance to ONE remembered decision, which is right for a
        name or a pile: a face is in one place. Refusals add up: one face can be "not her" and
        "not him" both, and two remembered faces of the same person in a file (once before a
        rescan, once after) can each have been refused as somebody different. Asked across every
        refusal together, the first match would win and the others would be lost. Asked per person,
        the rule is unchanged and every refusal of that person finds its face.
        """
        remembered = await self._store.rejections_for(asset_id)
        if not remembered or not track_ids:
            return 0

        by_person: dict[str, list[tuple[int, Vector]]] = {}
        for person_id, created_at, vector in remembered:
            by_person.setdefault(person_id, []).append((created_at, vector))

        restored = 0
        for person_id, kept in by_person.items():
            for track_id, created_at in await self._already_decided(track_ids, kept):
                # Counted by what the store wrote, which here is every one: the appearances a pass
                # hands this were made by that pass, so none carries a refusal yet.
                restored += await self._store.reject_again(track_id, person_id, created_at)
        if restored:
            log.info("faces.scan.refused_again", asset_id=asset_id, faces=restored)
        return restored

    async def _set_aside_again(self, asset_id: str, track_ids: Sequence[str]) -> set[str]:
        """Put back the decisions a rescan just deleted. Returns the tracks it set aside.

        A rescan deletes every track a file has. Recorded only as the pile it sat in, a face set
        aside would come back among the open piles while the pile survived as a row claiming faces
        it no longer held, and "what you ignored stays ignored" would be true about regrouping and
        not about rescanning.

        Recognized by description rather than by anything about the row, at the same distance a
        removal uses, and within the one file. It is the same question in both cases: is this the
        same particular thing I already answered for.
        """
        remembered = await self._store.ignored_for(asset_id)
        if not remembered or not track_ids:
            return set()

        by_pile: dict[str, tuple[list[str], bytes]] = {}
        claimed: set[str] = set()
        pairs = [((pile_id, centroid), vector) for pile_id, vector, centroid in remembered]
        for track_id, (pile_id, centroid) in await self._already_decided(track_ids, pairs):
            members, _ = by_pile.setdefault(pile_id, ([], centroid))
            members.append(track_id)
            claimed.add(track_id)

        for pile_id, (members, centroid) in by_pile.items():
            await self._store.set_aside_again(pile_id, members, centroid)
        if claimed:
            log.info("faces.scan.set_aside_again", asset_id=asset_id, faces=len(claimed))
        return claimed

    async def _group_again(self, asset_id: str, track_ids: Sequence[str]) -> set[str]:
        """Put back the grouping somebody did by hand, which a rescan just deleted.

        The fourth thing of this shape. A rescan deletes every track a file had, so a pile built by
        merging two groups or splitting one is left holding nothing while the faces it held come
        back among whatever the clustering makes of them: a decision undone quietly, at a moment
        nobody is watching.

        Faces that have since been recognized are left where they are. A hand-made pile is a claim
        about strangers, and somebody who now has a name is no longer one of them.
        """
        remembered = await self._store.grouped_for(asset_id)
        if not remembered or not track_ids:
            return set()

        unnamed = [
            track_id
            for track_id in track_ids
            if (track := await self._store.track(track_id)) is not None and track.person_id is None
        ]
        if not unnamed:
            return set()

        by_pile: dict[str, tuple[list[str], bytes]] = {}
        claimed: set[str] = set()
        pairs = [((pile_id, centroid), vector) for pile_id, vector, centroid in remembered]
        for track_id, (pile_id, centroid) in await self._already_decided(unnamed, pairs):
            members, _ = by_pile.setdefault(pile_id, ([], centroid))
            members.append(track_id)
            claimed.add(track_id)

        for pile_id, (members, centroid) in by_pile.items():
            await self._store.group_again(pile_id, members, centroid)
        if claimed:
            log.info("faces.scan.grouped_again", asset_id=asset_id, faces=len(claimed))
        return claimed

    async def _resume_point(
        self, asset_id: str, configured: Configured, *, again: bool = False
    ) -> int | None:
        """Where a pass over this file should carry on from, or None to start it afresh.

        Only ever a pass under the **same tuning**. A different quality bar or density means the
        moments would not be the same moments and the faces already stored were measured against a
        different rule, so there is nothing there to carry on from, so that file is scanned again
        from the beginning, which is what changing a setting is supposed to mean.

        **A press carries on only from a pass the time limit cut short.** Coverage below one and a
        stored position do not mean that: moments that will not decode leave both behind on a pass
        that read everything it could, and carrying on from there would read one frame and keep the
        old appearances: the press would not look again. Pressed on a finished pass (or on a row
        older than the record of why it stopped,
        where looking again is what the press means) it starts over; pressed on a pass the limit
        stopped, it reads on, which is the only way a file longer than the limit is ever read
        through by pressing. A pass nobody pressed (a sweep, an arriving file) carries on from
        any stored position, as before: a pass that could not decode its tail gets one more try
        at it, and that try settles the file (`Outcome.coverage`).
        """
        previous = await self._store.scan_of(asset_id)
        if previous is None or previous.settings_digest != configured.digest:
            return None
        if previous.coverage >= 1.0 or previous.reached_ms is None:
            return None
        if again and previous.cut_short is not True:
            return None
        return previous.reached_ms

    async def _rejoin(self, asset_id: str, appearances: Sequence[Appearance]) -> list[str | None]:
        """For each appearance a resumed pass found, the stored one it is a continuation of.

        Somebody on screen either side of the point the last pass stopped is one appearance, not
        two, and the only thing that survives the gap is the description: position does not, and
        the two halves were read in different passes so nothing links them in time.

        Matched at the same likeness two runs within a single pass are joined at, because it is
        the same question: is this the same face carrying on. None means nobody stored looks like
        it, so it becomes an appearance of its own.
        """
        if not appearances:
            return []
        stored = await self._store.appearance_vectors(asset_id)
        if not stored:
            return [None] * len(appearances)

        taken: set[str] = set()
        joins: list[str | None] = []
        for appearance in appearances:
            best = max(appearance.faces, key=lambda face: face.quality.score)
            match: tuple[float, str] | None = None
            for track_id, vector in stored:
                if track_id in taken:
                    continue
                likeness = recognize.similarity(best.vector, vector)
                if likeness >= tuning.TRACKLET_MERGE and (match is None or likeness > match[0]):
                    match = (likeness, track_id)
            if match is None:
                joins.append(None)
            else:
                # One stored appearance takes at most one continuation. Without this, two people
                # who are alike enough to clear the bar both fold into whichever track scored
                # highest, and the file loses one of them.
                taken.add(match[1])
                joins.append(match[1])
        return joins

    async def _without_removed(
        self, asset_id: str, appearances: Sequence[Appearance], recognizer: str
    ) -> list[Appearance]:
        """Drop anything this file already had removed from it, without asking again.

        A removal has to survive the scan that follows it or it is not a removal at all: a face
        lives in a row a rescan replaces wholesale, so the same hand, logo or unusable crop would
        come back every time and be answered every time.

        Matched by likeness rather than by position, because position does not survive. A scan at a
        different depth samples different moments, so the same thing in the same file is found at a
        different timestamp in a slightly different box, and the description is the only part of
        it that stays put.

        An appearance every one of whose faces was removed goes entirely. One that keeps some of
        them keeps those: an appearance is a face continuing across frames, and a few bad frames of
        a real person are not the same thing as a detection that was never a face.
        """
        removed = await self._store.removals_for(asset_id, recognizer)
        if not removed:
            return list(appearances)

        kept: list[Appearance] = []
        for appearance in appearances:
            faces = tuple(
                face
                for face in appearance.faces
                if not any(
                    recognize.similarity(face.vector, gone) >= tuning.ALREADY_DECIDED
                    for gone in removed
                )
            )
            if faces:
                kept.append(replace(appearance, faces=faces))
        dropped = sum(len(one.faces) for one in appearances) - sum(len(one.faces) for one in kept)
        if dropped:
            log.info("faces.scan.removed_again", asset_id=asset_id, faces=dropped)
        return kept

    async def remove_faces(self, track_ids: Sequence[str]) -> int:
        """Take these faces away for good, and stop them coming back.

        For a detection that was never a face (a hand, a logo, a pattern in a curtain) and for
        a real face whose crop is worthless. Both are the same answer as far as Sift is concerned:
        there is nothing here worth recognizing, and it should stop being asked about.

        There is no undo. What is kept is the description, so the next scan of that file recognizes
        the thing rather than raising it again, and what its quality measured, so where the bar sits
        can be argued from evidence rather than from memory.
        """
        await self._require_enabled()
        assets = await self._store.assets_of(track_ids)
        removed = await self._store.remove_faces(track_ids)
        for asset_id in assets:
            await self._settle(asset_id)
        await self._store.drop_empty_piles()
        return removed
