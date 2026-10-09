# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sift learning from its own surest names of somebody well known, and the receipt a re-match's
names are written down with, which the learning's receipt shares so one Undo takes back either."""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping, Sequence

from sift.kernel.access import Viewer
from sift.kernel.access.sentences import FACES_MATCHED, faces_of_person
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, announce
from sift.kernel.ledger import Object as LedgerObject
from sift.kernel.log import get_logger
from sift.kernel.vocabulary import VIA_FACES, Subject
from sift.kernel.workbench import DOER, Named, Piece, Preview, Recorded, Worded
from sift.slices.faces import tuning
from sift.slices.faces.models import Attribution, Vector
from sift.slices.faces.receipts import IDENTIFIED_QUEUE
from sift.slices.faces.service_base import Configured
from sift.slices.faces.service_grouping import GroupingMixin

log = get_logger(__name__)

#: Sift's own picks of somebody taken out of her references past the cap (`settle_picks`). Final:
#: the faces keep their name, and the next learning run files picks again within the cap.
PICKS_RETIRED_QUEUE = "picks-retired"


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
        await self.settle_picks()
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

    async def settle_picks(self) -> int:
        """Take Sift's own picks of anybody past the cap out of her references, the oldest first,
        each person's with her History line in the same write. How many went."""
        gone = 0
        for person_id, over in await self._store.picks_over_cap():
            name = await self._store.person_name(person_id) or "them"
            async with self._store.database.write() as connection:
                taken = await self._store.retire_picks_on(connection, person_id, over)
                if taken and self._recorder is not None:
                    many = "1 face" if len(taken) == 1 else f"{len(taken):,} faces"
                    await self._recorder.record_on(
                        connection,
                        queue=PICKS_RETIRED_QUEUE,
                        user_id=None,
                        via=VIA_FACES,
                        title=f"Sift took {many} it recognized as {name} out of their reference "
                        "pictures",
                        detail="Sift keeps no more of its own picks of a person than the faces "
                        "you confirmed of them. The faces keep the name; no file is touched.",
                        payload=json.dumps({"person_id": person_id, "references": list(taken)}),
                        subjects=[Subject(kind="person", id=person_id)],
                    )
                if taken:
                    announce(EVERY_ADMIN, About.LIBRARY)
            gone += len(taken)
            log.info("faces.picks_retired", person_id=person_id, faces=len(taken))
        return gone


class RetiredPickRecords:
    """Picks taken out past the cap, in History. Final: see `PICKS_RETIRED_QUEUE`."""

    name = PICKS_RETIRED_QUEUE
    reversible = False

    async def pictures_of(self, viewer: Viewer, payload: str) -> tuple[Preview, ...]:
        """Nothing: the pictures went with the references."""
        return ()

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        """Nothing to put back. See the class."""
        return False

    def worded(self, recorded: Recorded) -> Worded | None:
        """This line, worded when shown, the person named. See `kernel.workbench.Recorded`."""
        held = recorded.held()
        person, references = held.get("person_id"), held.get("references")
        if not isinstance(person, str) or not isinstance(references, list) or not references:
            return None
        many = "1 face" if len(references) == 1 else f"{len(references):,} faces"
        more: tuple[Piece, ...] = (recorded.detail,) if recorded.detail else ()
        return Worded(
            said=(
                DOER,
                f" took {many} it recognized as ",
                Named(kind="person", id=person),
                " out of their reference pictures",
            ),
            more=more,
        )
