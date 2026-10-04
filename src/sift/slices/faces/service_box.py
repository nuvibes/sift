# SPDX-License-Identifier: AGPL-3.0-or-later
"""A stash-box's answer raises one question: is the one face in this file the person it put there?

**A box's answer is a decision Sift took**, the way a folder filed is: a confirmed match puts the
people the box names on the file (`asset_people.source` is `stash_box`), and nobody pressed
anything to put them there. So the face in such a file is OFFERED as theirs, under Needs your
input, and never named on it: a question is not a name, and only Confirmed or Recognized by
Sift puts somebody on a face. A Yes teaches Sift what they look like (the confirmation files a
reference, as every Yes does); a No is kept, and the face is never asked about them again.

**Only where the arithmetic has nothing to say.** A person Sift has a picture of to compare has had
the face compared already: a face it did not put to them is a disagreement, and the Disagreements
tab is where that is answered. Somebody with no picture at all (a person a stash-box made, before
anybody has confirmed a face of theirs) has no other way to be offered one. The read that finds
these faces is the store's (`_BOX_QUESTIONS`), and the two reads split the same files between them.

**Asked wherever the two facts come to meet**, because either may come first: when a box files
people (the enrichment's write asks for the pass, which settles and then runs), when a file with a
box's people on it is scanned for faces (`ScanningMixin.scan`), and once at a start for whatever
is owed (`wiring/catch_up.py`). Each asks only what is not yet asked, so it can run any number of
times.

Each run is written into History per person, Sift as the one asking: "Sift asked whether the face
in <file> is <person>, because <box> says they are in it". Final, like the reconcile's record: a
question is taken back by answering it.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from sift.kernel.access import Viewer
from sift.kernel.access.history_boxes import boxes_that_named
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, announce, announce_now
from sift.kernel.log import get_logger
from sift.kernel.vocabulary import VIA_FACES, Subject
from sift.kernel.workbench import DOER, Named, Piece, Preview, Recorded, Worded
from sift.slices.faces.models import AskedBy, Attribution
from sift.slices.faces.receipts import BOX_QUESTIONS_QUEUE
from sift.slices.faces.service_base import FaceServiceBase
from sift.slices.faces.store import Ruling

log = get_logger(__name__)

#: How many faces one read of the library hands the pass. Each page asked leaves the read, so the
#: pass goes round until a read comes back empty; the bound is on one transaction, not the work.
BOX_QUESTIONS_PAGE = 500

#: What every record of these questions says under its line.
BOX_QUESTIONS_DETAIL = (
    "Each waits under Needs your input. Sift never names a face without you: "
    "a Yes teaches Sift what they look like, and a No is kept."
)

#: The `via` an enrichment reports for a stash-box, which is what names the box behind a filing.
_BY_A_BOX = "stash"


def _asked(faces: int, boxes: Sequence[str]) -> str:
    """The stored title, for a reader that cannot word the record (`BoxQuestionRecords.worded`)."""
    by = " and ".join(boxes) or "a stash-box"
    if faces == 1:
        return f"Sift asked about the face in a file {by} filed people under"
    return f"Sift asked about the faces in {faces:,} files {by} filed people under"


class BoxQuestionsMixin(FaceServiceBase):
    """Asking about the one face in a file a stash-box put somebody on."""

    async def box_questions_owed(self) -> bool:
        """Whether any such face waits to be asked about. One bounded read: what a start asks
        before it queues the pass, so a library with nothing owed queues nothing."""
        if not await self.enabled():
            return False
        configured = await self.configuration()
        return bool(await self._store.box_questions(configured.recognizer, most=1))

    async def ask_for_the_boxes(
        self, asset_ids: Sequence[str] | None = None, *, boxes: set[str] | None = None
    ) -> int:
        """Ask about every face a stash-box's answer is a claim about, in these files or in all of
        them. How many faces were asked about; the boxes that filed them are added to `boxes`,
        where given, so the job's note can name them.

        Settles the files it asked about (their status counts a question) and tells every admin
        once, through the record's own write. Nothing where the feature is off.
        """
        if not await self.enabled():
            return 0
        asked = 0
        while True:
            found = await self._store.box_questions(
                (await self.configuration()).recognizer,
                most=BOX_QUESTIONS_PAGE,
                asset_ids=asset_ids,
            )
            landed = await self._ask_for_the_boxes(found)
            await self._settle_all(sorted({asset_id for _face, asset_id, _who in landed}))
            named = await self._record_box_questions(landed)
            if boxes is not None:
                boxes |= named
            asked += len(landed)
            # A page every face of which was answered between the read and the write asks
            # nothing and would be read again for ever; a short page is the last.
            if not landed or len(found) < BOX_QUESTIONS_PAGE:
                break
        if asked:
            log.info("faces.box.asked", faces=asked)
        return asked

    async def _ask_for_the_boxes(
        self, found: Sequence[tuple[str, str, str]]
    ) -> list[tuple[str, str, str]]:
        """Put each face to its person as the box's question, in one transaction, each only while
        it is still nobody's (`Store.restate`'s guard). The ones that landed."""
        landed = await self._store.restate(
            [
                Ruling(
                    track_id=face,
                    was_person=None,
                    was=None,
                    person_id=who,
                    attribution=Attribution.SUGGESTED,
                    confidence=None,
                    asked_by=AskedBy.BOX,
                )
                for face, _asset_id, who in found
            ]
        )
        return [one for one in found if one[0] in landed]

    async def _box_that_filed(self, asset_id: str, person_id: str) -> str | None:
        """The stash-box that put this person on this file, by name, as the filing names it
        (`box_id`); for a filing that names none, the box whose applied answer on this file is the
        newest: the same read the file's own "Enriched by" marks come from."""
        named = await boxes_that_named(self._store.database, person_id, [asset_id])
        if named:
            return named[0]
        return next(
            (
                one.name
                for one in await self._repository.enriched_by(asset_id)
                if one.via == _BY_A_BOX and one.name
            ),
            None,
        )

    async def _record_box_questions(self, asked: Sequence[tuple[str, str, str]]) -> set[str]:
        """One record per person of what was asked about them, with the files as subjects, so it is
        on their History and on each file's. The box's name is kept, since which box said so is a
        fact of this moment. The boxes named, by name."""
        if not asked:
            return set()
        by_person: dict[str, list[tuple[str, str]]] = {}
        for face, asset_id, who in asked:
            by_person.setdefault(who, []).append((face, asset_id))
        boxes = {
            (asset_id, who): await self._box_that_filed(asset_id, who)
            for asset_id, who in dict.fromkeys((asset_id, who) for _face, asset_id, who in asked)
        }
        said = {box for box in boxes.values() if box}
        if self._recorder is None:
            announce_now(EVERY_ADMIN, About.LIBRARY)
            return said
        async with self._store.database.write() as connection:
            # Rung on the record's own commit: Needs your input and the person's page read again
            # with the questions and their line both there.
            announce(EVERY_ADMIN, About.LIBRARY)
            for person_id, faces in sorted(by_person.items()):
                named = sorted(
                    {box for _face, asset_id in faces if (box := boxes[(asset_id, person_id)])}
                )
                files = list(dict.fromkeys(asset_id for _face, asset_id in faces))
                await self._recorder.record_on(
                    connection,
                    queue=BOX_QUESTIONS_QUEUE,
                    user_id=None,
                    via=VIA_FACES,
                    title=_asked(len(faces), named),
                    detail=BOX_QUESTIONS_DETAIL,
                    payload=json.dumps(
                        {
                            "person_id": person_id,
                            "faces": [[face, asset_id] for face, asset_id in faces],
                            "boxes": named,
                        }
                    ),
                    subjects=[
                        Subject(kind="person", id=person_id),
                        *(Subject(kind="asset", id=asset_id) for asset_id in files),
                    ],
                )
        return said


class BoxQuestionRecords:
    """The record of a box's questions in History, worded, and the answer that it is final.

    A reverser with no card, like the reconcile's (`jobs.AskedOnlyRecords`): History draws the
    record as final rather than offering an Undo. A question is taken back by answering it, and
    one withdrawn by hand would be asked again by the next pass over the same files.
    """

    name = BOX_QUESTIONS_QUEUE
    #: Final. See the class.
    reversible = False

    async def pictures_of(self, viewer: Viewer, payload: str) -> tuple[Preview, ...]:
        """Nothing: the faces are on Needs your input, and the files are the record's subjects."""
        return ()

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        """Nothing to put back. See the class."""
        return False

    def worded(self, recorded: Recorded) -> Worded | None:
        """ "Sift asked whether the face in <file> is <person>, because <box> says they are in it",
        or the faces in N files. See `kernel.workbench.Recorded`."""
        held = recorded.held()
        person_id, faces, boxes = held.get("person_id"), held.get("faces"), held.get("boxes")
        if not isinstance(person_id, str) or not isinstance(faces, list) or not faces:
            return None
        files = list(
            dict.fromkeys(str(one[1]) for one in faces if isinstance(one, list) and len(one) == 2)
        )
        if not files:
            return None
        by = (
            " and ".join(boxes)
            if isinstance(boxes, list) and boxes and all(isinstance(one, str) for one in boxes)
            else "a stash-box"
        )
        who = Named(kind="person", id=person_id)
        if len(files) == 1:
            said: tuple[Piece, ...] = (
                DOER,
                " asked whether the face in ",
                Named(kind="asset", id=files[0]),
                " is ",
                who,
                f", because {by} says they are in it",
            )
        else:
            said = (
                DOER,
                f" asked whether the faces in {len(files):,} files are ",
                who,
                f", because {by} says they are in those files",
            )
        return Worded(said=said, more=(recorded.detail,) if recorded.detail else ())
