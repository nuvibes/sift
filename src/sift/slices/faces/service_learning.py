# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sift learning from its own surest names of somebody well known, and the receipt a re-match's
names are written down with, which the learning's receipt shares so one Undo takes back either."""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping, Sequence

from sift.kernel.access.sentences import FACES_MATCHED, faces_of_person
from sift.kernel.ledger import Object as LedgerObject
from sift.kernel.log import get_logger
from sift.kernel.vocabulary import VIA_FACES, Subject
from sift.slices.faces import tuning
from sift.slices.faces.models import Attribution, Vector
from sift.slices.faces.receipts import IDENTIFIED_QUEUE
from sift.slices.faces.service_base import Configured
from sift.slices.faces.service_grouping import GroupingMixin

log = get_logger(__name__)


def _learned_words(
    person_id: str, name: str, pairs: Sequence[tuple[str, str, float]], confirmed: int
) -> tuple[str, dict[str, str], str]:
    """The receipt for faces Sift named that it learned from: title, link and the line under it.
    One per person per run."""
    one = len(pairs) == 1
    many = "1 face" if one else f"{len(pairs):,} faces"
    title = f"Sift added {many} it recognized as {name} to their reference pictures"
    link = {
        "kind": "faces",
        "id": person_id,
        "words": many,
        "href": faces_of_person(person_id, FACES_MATCHED),
    }
    them = "it" if one else "them"
    detail = (
        f"{'It was' if one else 'Each was'} recognized at "
        f"{round(tuning.LEARNING_CONFIDENCE * 100)}% or more, and "
        f"{f'{confirmed:,} faces' if confirmed != 1 else '1 face'} of {name} "
        f"{'was' if confirmed == 1 else 'were'} confirmed by people. Undo takes the name off "
        f"{'it' if one else 'every one'}, takes {them} out of the reference pictures and asks "
        f"you about {them} under Needs your input. No file is touched and nothing is deleted."
    )
    return title, link, detail


def _matched_payload(
    person_id: str,
    pairs: Sequence[tuple[str, str, float]],
    *,
    known_by: int,
    rested_on: list[str],
    link: dict[str, str] | None,
    asked_here: Mapping[str, float | None] | None,
) -> str:
    """A match receipt's payload.

    `asked_here` is None on a scan, whose faces were nobody's a moment earlier; on a re-match each
    face's state BEFORE the run goes with it, which faces Undo puts back under Needs your input
    and at what confidence.
    """
    return json.dumps(
        {
            "person_id": person_id,
            "track_ids": [track for track, _asset, _sure in pairs],
            "reference_pictures": known_by,
            "rested_on": rested_on,
            **({"link": link} if link else {}),
            **(
                {}
                if asked_here is None
                else {
                    "attribution": {
                        track: (Attribution.SUGGESTED.value if track in asked_here else None)
                        for track, _asset, _sure in pairs
                    },
                    "confidence": {track: asked_here.get(track) for track, _asset, _sure in pairs},
                }
            ),
        }
    )


class LearningMixin(GroupingMixin):
    """Sift's surest names of somebody, filed as references, with an Undo for each run."""

    async def learn_from_recognitions(
        self, confirmed: Mapping[str, int], configured: Configured
    ) -> int:
        """Sift files its surest names of somebody well known as references. How many were filed.

        For each person with at least `tuning.LEARNING_REFERENCES` faces people confirmed, every
        face Sift named as her (`MATCHED`, never a question) at `tuning.LEARNING_CONFIDENCE` or
        above becomes a reference (`Origin.RECOGNIZED`), one per file (`recognitions_to_learn`), so
        her description follows the library rather than the few faces somebody pressed. Each run's
        are written down per person with an Undo that takes the names off, and the references go
        with the names (`unmatch`). The clearest face of each that is not turned past the quality
        bar's angle, for the reason a confirmation files none of those.
        """
        learned = 0
        line = configured.bar.min_frontality
        for person_id, faces in sorted(confirmed.items()):
            if faces < tuning.LEARNING_REFERENCES:
                continue
            candidates = await self._store.recognitions_to_learn(
                person_id, recognizer=configured.recognizer, at_least=tuning.LEARNING_CONFIDENCE
            )
            if not candidates:
                continue
            found = await self._store.faces_of_many([track for track, _asset, _sure in candidates])
            rows: list[tuple[str, str, Vector, float, bytes, int | None]] = []
            for track_id, asset_id, _sure in candidates:
                for face in sorted(found.get(track_id, []), key=lambda one: -one.quality):
                    if face.turned(line):
                        continue
                    crop = await self._store.picture_bytes(face.crop_path)
                    if crop is None:
                        continue
                    pixels = face.box.long_side if face.pixels is None else face.pixels
                    rows.append((track_id, asset_id, face.vector, face.quality, crop, pixels))
                    break
            made = await self._store.add_recognized_references(
                person_id, rows, recognizer=configured.recognizer
            )
            if not made:
                continue
            filed = [one for one in candidates if one[0] in made]
            await self._record_learned(person_id, filed, confirmed=faces, made=set(made.values()))
            learned += len(made)
            log.info("faces.references_learned", person_id=person_id, faces=len(made))
        return learned

    async def _record_learned(
        self,
        person_id: str,
        pairs: Sequence[tuple[str, str, float]],
        *,
        confirmed: int,
        made: Collection[str],
    ) -> None:
        """Write down the faces one run filed as her references, with the Undo of their names.

        A re-match's receipt in shape (`_matched_payload`, no act), so its Undo is `unmatch`'s: every
        face asked about instead, and its reference taken out with the name it rested on. The
        pictures it rested on are her own before this run's were added.
        """
        if self._recorder is None:
            return
        name = await self._store.person_name(person_id)
        if name is None:  # pragma: no cover (the person was read a moment ago to learn from)
            return
        title, link, detail = _learned_words(person_id, name, pairs, confirmed)
        subjects: list[Subject] = [Subject(kind="person", id=person_id)]
        subjects += [
            Subject(kind="asset", id=asset_id)
            for asset_id in dict.fromkeys(asset_id for _track, asset_id, _sure in pairs)
        ]
        async with self._store.database.write() as connection:
            rested_on = [
                one for one in await self._store.own_reference_ids(person_id) if one not in made
            ]
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
                    known_by=len(rested_on),
                    rested_on=rested_on,
                    link=link,
                    asked_here={},
                ),
                subjects=subjects,
                verb="linked",
                object=LedgerObject(kind="person", id=person_id, name=name),
            )
