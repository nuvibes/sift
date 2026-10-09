# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a person decides about the faces they picked: naming, refusing, setting aside, moving
and teaching."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
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
from sift.slices.faces.store_records import Scan

log = get_logger(__name__)

#: Faces agreed with per turn at the writer: one turn for them all would hold every other write.
_AGREED_PER_WRITE = 200


@dataclass(frozen=True, slots=True)
class RunAnswered:
    """What one press over a person's faces did, and the receipt (`decision_id`) that undoes it."""

    changed: int
    offered: int = 0
    decision_id: str = ""


@dataclass(slots=True)
class Taught:
    """What one press taught Sift, gathered while `confirm` runs, so its Undo need not guess.

    `references` the rows the press created, `starters` those in use when it began, and
    `named_before` who each face was named as before.
    """

    references: dict[str, list[str]] = field(default_factory=dict)
    starters: dict[str, list[str]] = field(default_factory=dict)
    named_before: dict[str, tuple[str, str | None, float | None]] = field(default_factory=dict)


class DecisionsMixin(PicturesMixin):
    """Answering for faces somebody picked."""

    async def _taught_payload(
        self, person_id: str, taught: Taught | None, track_ids: Collection[str]
    ) -> dict[str, Any]:
        """What a receipt says its press taught; an empty `references` means it created none."""
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

        By the ids the receipt kept; an older receipt without them removes by the face's pictures.
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
        """The rest of an Undo of a naming or an agreement, once its faces are back. How many."""
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
        """Agree that an appearance is somebody, and file it as one of their references.

        The reference is stamped with the model of the pass that found the face, never the one
        loaded now, which is what keeps incomparable galleries apart. `viewer` records it as theirs.
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
        # What this press teaches is gathered where there is a receipt to write it into.
        gathered = taught if taught is not None else (Taught() if viewer is not None else None)
        if gathered is not None and person_id not in gathered.starters:
            gathered.starters[person_id] = await self._store.starters_in_use(person_id)
        # Remembered against the face's description, so a rescan puts the decision back.
        await self._store.remember_confirmation(track_id, person_id)
        await self._file_confirmed(track_id, person_id, track, scan, gathered)
        if settle:
            await self._settle(track.asset_id)
        if viewer is not None:
            await self._record_confirmed(viewer, person_id, [was], gathered)

    async def _file_confirmed(
        self,
        track_id: str,
        person_id: str,
        track: StoredTrack,
        scan: Scan,
        gathered: Taught | None,
    ) -> None:
        """`confirm`'s filing: the appearance's faces as her references, noted in `gathered`."""
        # Every frame of the appearance up to a cap, so one video cannot be most of her references.
        faces = sorted(await self._store.faces_of(track_id), key=lambda one: -one.quality)
        # A face turned past the bar's angle takes the name and is never filed (`asked_only`).
        line = (await self.configuration()).bar.min_frontality
        filed = 0
        for face in faces:
            if filed >= tuning.REFERENCES_PER_APPEARANCE:
                break
            if face.turned(line):
                continue
            # Filed whatever its quality, capped per appearance so one video cannot dominate her.
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
                # Which appearance and file filed it: crop bytes change on a rescan.
                track_id=track_id,
                asset_id=track.asset_id,
            )
            if reference_id is None:
                continue
            filed += 1
            if gathered is not None:
                gathered.references.setdefault(track_id, []).append(reference_id)
        if gathered is not None:
            gathered.references.setdefault(track_id, [])

    async def reject(self, track_id: str, person_id: str) -> None:
        """Say an appearance is not somebody, remembered so the next scan does not name it again."""
        await self._require_enabled()
        await self._store.forget_confirmation(track_id, person_id)
        # And the reference photos this face gave them, or a mistake goes on being recognized.
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
        """Agree that several appearances are the same person. Returns how many were written."""
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
        """`confirm_many`, with the receipt its Undo takes back where the press wrote one."""
        await self._require_enabled()
        written = 0
        touched: list[str] = []
        gathered = taught if taught is not None else (Taught() if viewer is not None else None)
        before = await self._store.tracks(track_ids)
        for track_id in dict.fromkeys(track_ids):
            track = before.get(track_id)
            if track is None or track.person_id is not None:
                continue
            # Settled once for the batch below: per face is a turn at the writer each.
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
        """Write a per-user receipt for the faces somebody named or confirmed, and how to undo."""
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
                # Rung on the receipt's own commit, so a re-read tab finds it.
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
        """Name these faces and OFFER the rest of their groups as the same person.

        Returns `(named, offered)`. The rest are only suggested, with no confidence, since nobody
        looked at them. The piles are read before confirming empties them.
        """
        await self._require_enabled()
        piles = {
            track.pile_id
            for track in (await self._store.tracks(track_ids)).values()
            if track.pile_id is not None
        }

        named = await self.confirm_many(track_ids, person_id, taught=taught)
        if not named:
            # Nothing was named, so there is nothing to extend.
            return 0, 0

        offered = len(await self._offer_their_groups(piles, person_id))
        log.info("faces.named_with_group", named=named, offered=offered, piles=len(piles))
        return named, offered

    async def _offer_their_groups(self, piles: set[str], person_id: str) -> list[str]:
        """Offer the rest of these groups as the same person. Returns the faces offered.

        Each is asked as the group's question (`AskedBy.GROUP`): a re-match may not just name it.
        """
        # A face somebody already refused as her is not asked about her again.
        refused = await self._store.refused_tracks(sorted(piles), person_id)
        waiting: dict[str, str] = {}
        for pile_id in sorted(piles):
            for track in await self._store.pile_tracks(pile_id):
                if track.id not in refused:
                    waiting[track.id] = track.asset_id
        # One turn at the writer for the whole offer; a face named meanwhile keeps that name.
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

        A face with no proposal is skipped: a dragged selection picks up settled faces.
        """
        await self._require_enabled()
        found = await self._store.tracks(track_ids)
        agreeing = [
            track
            for track in (found.get(track_id) for track_id in dict.fromkeys(track_ids))
            if track is not None
            and track.person_id is not None
            and track.attribution is not Attribution.CONFIRMED
        ]
        landed: list[StoredTrack] = []
        # A turn at the writer per batch, never per face.
        for start in range(0, len(agreeing), _AGREED_PER_WRITE):
            landed += await self._confirm_together(
                agreeing[start : start + _AGREED_PER_WRITE], taught=taught
            )
        await self._settle_all([track.asset_id for track in landed])
        return len(landed)

    async def _confirm_together(
        self, tracks: Sequence[StoredTrack], *, taught: Taught | None
    ) -> list[StoredTrack]:
        """`confirm` for a batch of proposed faces, read first and written in one turn.

        A face answered between the read and the write keeps that answer. The faces it landed.
        """
        chosen = await self._references_to_file(tracks)
        if taught is not None:
            for person_id in {str(track.person_id) for track in tracks}:
                if person_id not in taught.starters:
                    taught.starters[person_id] = await self._store.starters_in_use(person_id)
        pictures: list[tuple[Path, bytes]] = []
        async with self._store.database.write() as connection:
            landed = await self._store.restate_on(
                connection,
                [
                    Ruling(
                        track_id=track.id,
                        was_person=track.person_id,
                        was=track.attribution,
                        person_id=track.person_id,
                        attribution=Attribution.CONFIRMED,
                        confidence=1.0,
                    )
                    for track in tracks
                ],
            )
            done = [track for track in tracks if track.id in landed]
            await self._store.remember_confirmations_on(connection, [one.id for one in done])
            for track in done:
                made: list[str] = []
                for filing in chosen.get(track.id, []):
                    reference_id = await self._store.add_reference_on(
                        connection,
                        str(track.person_id),
                        pictures=pictures,
                        origin=Origin.CONFIRMED,
                        track_id=track.id,
                        asset_id=track.asset_id,
                        **filing,
                    )
                    if reference_id is not None:
                        made.append(reference_id)
                if taught is not None:
                    taught.references.setdefault(track.id, []).extend(made)
            if done:
                announce(EVERY_ADMIN, About.LIBRARY)
        await self._store.write_pictures(pictures)
        return done

    async def _references_to_file(
        self, tracks: Sequence[StoredTrack]
    ) -> dict[str, list[dict[str, Any]]]:
        """Each face's pictures to file, read together before any write: its clearest unturned
        faces still on disk, up to what one appearance files."""
        faces = await self._store.faces_of_many([track.id for track in tracks])
        line = (await self.configuration()).bar.min_frontality
        recognizers: dict[str, str] = {}
        for asset_id in {track.asset_id for track in tracks}:
            scan = await self._store.scan_of(asset_id)
            if scan is not None:
                recognizers[asset_id] = scan.recognizer
        waiting = {
            track.id: [
                face
                for face in sorted(faces.get(track.id, []), key=lambda one: -one.quality)
                if not face.turned(line)
            ]
            for track in tracks
            if track.asset_id in recognizers
        }
        recognizer_of = {
            track.id: recognizers[track.asset_id] for track in tracks if track.id in waiting
        }
        chosen: dict[str, list[dict[str, Any]]] = {}
        while waiting:
            asked = {track_id: rest.pop(0) for track_id, rest in waiting.items() if rest}
            crops = await asyncio.gather(
                *(self._store.picture_bytes(face.crop_path) for face in asked.values())
            )
            for (track_id, face), crop in zip(asked.items(), crops, strict=True):
                if crop is None:
                    continue
                pixels = face.box.long_side if face.pixels is None else face.pixels
                chosen.setdefault(track_id, []).append(
                    {
                        "vector": face.vector,
                        "quality": face.quality,
                        "crop": crop,
                        "recognizer": recognizer_of[track_id],
                        "pixels": pixels,
                    }
                )
                if len(chosen[track_id]) >= tuning.REFERENCES_PER_APPEARANCE:
                    del waiting[track_id]
            waiting = {track_id: rest for track_id, rest in waiting.items() if rest}
        return chosen

    async def set_aside(self, track_ids: Sequence[str]) -> str | None:
        """Set some faces aside as a pile of their own, leaving the rest of their pile."""
        await self._require_enabled()
        await self._unname_first(track_ids)
        # A hand-made grouping goes first: setting aside is the later decision.
        await self._store.forget_grouping(track_ids)
        moved = await self._store.set_aside(track_ids)
        if moved is not None:
            # Written before the tidy-up: the decision outlives the rows (`_set_aside_again`).
            await self._store.remember_ignored(moved)
            await self._store.drop_empty_piles()
        return moved

    async def _unname_first(self, track_ids: Sequence[str]) -> int:
        """Take the name off any of these faces before moving or setting it aside."""
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
        """Merge or split: move faces into a group, or into a new one when `pile_id` is None.

        Written down as descriptions so a rescan keeps it; an emptied pile is tidied away.
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
        """Set a pile aside, reversibly, and return the receipt; `None` when no such pile."""
        await self._require_enabled()
        changed = await self._store.set_pile_status(pile_id, PileStatus.IGNORED)
        if not changed:
            return None
        await self._store.remember_ignored(pile_id)
        return await self._record_pile(viewer, pile_id, ignored=True)

    async def _record_pile(self, viewer: Viewer | None, pile_id: str, *, ignored: bool) -> str:
        """Write down what setting a pile aside, or bringing one back, did."""
        if self._recorder is None or viewer is None:
            return ""
        tracks = await self._store.pile_tracks(pile_id)
        faces = len(tracks)
        many = "1 face" if faces == 1 else f"{faces} faces"
        subjects: list[Subject] = [Subject(kind="pile", id=pile_id)]
        subjects += [Subject(kind="asset", id=one.asset_id) for one in tracks]
        # "Discarded", the word on the button and the tab.
        title = f"A group of {many} discarded" if ignored else f"A group of {many} restored"
        detail = (
            f"{many} from that group will not be offered again. Nothing was deleted and no file "
            "was touched."
            if ignored
            else f"{many} are back among the groups waiting for a name."
        )
        async with self._store.database.write() as connection:
            # Rung on the receipt's own commit, so a re-read tab finds it.
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
        """Bring an ignored pile back, forgetting its descriptions so a rescan does not undo it."""
        await self._require_enabled()
        changed = await self._store.set_pile_status(pile_id, PileStatus.OPEN)
        if not changed:
            return False
        await self._store.forget_ignored(pile_id)
        return True

    async def teach(self, track_ids: Sequence[str], person_id: str) -> int:
        """Make faces already named as this person into references, as `confirm` does. How many."""
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
        """Take back what `teach` filed for these faces, unless named again since. How many."""
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
