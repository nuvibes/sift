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
    """The same settings at the depth asked for this one file; the density follows."""
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
        # Why a file has nobody in it: nobody there, or people too far away.
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

        # Moments to read, not cut short, nothing back: a verdict, and the earlier faces stay.
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
        # After the settle, only what is new: a face found again stays reached by its receipt.
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
        # A file the previous model described is measured again first, so descriptions compare.
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
        # Named for the log a lost device leaves (`runner.IN_FLIGHT`).
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
        """Store what a pass found: `(track ids, the faces before, the faces carried across)`."""
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
        # Before attribution: a decision is not overwritten by a guess.
        decided = await self._set_aside_again(asset_id, track_ids)
        decided |= await self._name_again(asset_id, track_ids)
        # A "no" leaves the face in matching; `_attribute` passes that person over.
        await self._reject_again(asset_id, track_ids)
        matched = await self._attribute(
            [one for one in track_ids if one not in decided], configured, asset_id=asset_id
        )
        # A confirmation unpaired by description is put back by person where unambiguous.
        carried |= await self._confirm_by_name(
            asset_id, before, carried, decided, track_ids, matched
        )
        # Then by the name Sift had given it, now the arithmetic has spoken (`_carry_by_name`).
        carried |= await self._carry_by_name(asset_id, before, carried, track_ids)
        # A face whose name somebody took back is asked about again, never named (`unmatch`).
        await self._still_asked(carried, matched)
        # After attribution: a hand-made pile holds only unnamed faces.
        await self._group_again(asset_id, [one for one in track_ids if one not in decided])
        # The box's question for a lone face (`service_box`), before the settle counts it.
        boxed = await self._ask_for_the_boxes(
            await self._store.box_questions(
                configured.recognizer, most=BOX_QUESTIONS_PAGE, asset_ids=[asset_id]
            )
        )
        return matched, boxed

    async def _carry_across(
        self, asset_id: str, before: Sequence[Standing], track_ids: Sequence[str]
    ) -> dict[str, Standing]:
        """Pair the appearances a rescan just found with the ones it replaced, by description
        (`_already_decided`), written down for an Undo. New id to old."""
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
        """Put back a confirmation the description could not pair, only where exactly one
        confirmed face of that person went unpaired and exactly one new face carries her."""
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
        """Pair what the description could not, by the name that Sift had put on the face. New id to old.

        Sift's own names and Undo questions alone move this way, one old face to one new; people's
        decisions are put back by description alone.
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
        """Keep a face whose name was taken back a question (`AskedBy.UNDONE`) after a rescan."""
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

        One rule for every decision: descriptions agreeing closely within one file; first match
        wins, each appearance claimed once. `remembered` pairs a payload with its description.
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
        """Put back the names a person gave this file, as theirs rather than as Sift's."""
        remembered = await self._store.confirmations_for(asset_id)
        if not remembered or not track_ids:
            return set()

        named: set[str] = set()
        for track_id, person_id in await self._already_decided(track_ids, remembered):
            await self._store.attribute(
                track_id, person_id, confidence=1.0, attribution=Attribution.CONFIRMED
            )
            # And the pictures that confirmation filed follow the name to the new appearance (v24).
            claimed = await self._store.claim_references_in(person_id, asset_id, track_id)
            if claimed:
                log.info("faces.reference.reclaimed", asset_id=asset_id, pictures=claimed)
            named.add(track_id)
        if named:
            log.info("faces.scan.named_again", asset_id=asset_id, faces=len(named))
        return named

    async def _reject_again(self, asset_id: str, track_ids: Sequence[str]) -> int:
        """Put back every "no" said about a face in this file. Returns how many went back.

        Asked per person, since one face can be refused as several people.
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
                # Every one: a pass's new appearances carry no refusal yet.
                restored += await self._store.reject_again(track_id, person_id, created_at)
        if restored:
            log.info("faces.scan.refused_again", asset_id=asset_id, faces=restored)
        return restored

    async def _set_aside_again(self, asset_id: str, track_ids: Sequence[str]) -> set[str]:
        """Put back the set-asides a rescan just deleted, by description. Returns those tracks."""
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
        """Put back the grouping somebody did by hand, which a rescan deleted; named faces stay."""
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
        """Where a pass over this file should carry on from, or None to start afresh.

        Only under the same tuning. A press carries on only from a pass the time limit cut short;
        a pass nobody pressed carries on from any stored position.
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
        """For each appearance a resumed pass found, the stored one it continues, by description,
        or None."""
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
                # One continuation per stored appearance, or two alike people fold into one.
                taken.add(match[1])
                joins.append(match[1])
        return joins

    async def _without_removed(
        self, asset_id: str, appearances: Sequence[Appearance], recognizer: str
    ) -> list[Appearance]:
        """Drop anything this file already had removed, matched by likeness, not position.

        An appearance goes only when all its faces were removed.
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
        """Take these faces away for good, keeping their description so they do not come back."""
        await self._require_enabled()
        assets = await self._store.assets_of(track_ids)
        removed = await self._store.remove_faces(track_ids)
        for asset_id in assets:
            await self._settle(asset_id)
        await self._store.drop_empty_piles()
        return removed
