# SPDX-License-Identifier: AGPL-3.0-or-later
"""Starter pictures: a stash-box's photos of somebody Sift has no reference for, checked and filed
so that Sift may ask about her, and never name her on their strength alone.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from sift.kernel.access.sentences import people as people_counted
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, announce
from sift.kernel.log import get_logger
from sift.kernel.seams import BoxPicturesSeam
from sift.kernel.vocabulary import VIA_FACES, Subject
from sift.slices.faces import crop as cropping
from sift.slices.faces.models import Origin, Vector
from sift.slices.faces.receipts import STARTERS_QUEUE
from sift.slices.faces.references import (
    Auditor,
    PersonReport,
    mark_near_duplicates,
    mark_odd_ones_out,
)
from sift.slices.faces.runner import IN_FLIGHT
from sift.slices.faces.service_weights import WeightsMixin

if TYPE_CHECKING:  # numpy is only needed for a signature
    import numpy as np

log = get_logger(__name__)

#: How many of a box's pictures of one person are looked at, at most. Five because the pictures
#: come largest first, a box rarely holds more than a handful of any one performer, and the
#: odd-one-out check (`mark_odd_ones_out`) needs four to say anything at all.
STARTERS_PER_PERSON = 5


class StartersMixin(WeightsMixin):
    """Choosing, filing, recording and retiring starter pictures."""

    async def wants_starters(self, person_ids: Sequence[str]) -> list[str]:
        """Of these People, the ones starter pictures are for: nobody with any reference row at all.

        Not her own pictures, not starters already filed, and not starters already retired: a
        starter retired for a "no" is the memory of that answer, and filing the box's pictures again
        would ask the same wrong questions. See `Store.without_references`.
        """
        await self._require_enabled()
        return await self._store.without_references(person_ids)

    async def starters_count(self, door: BoxPicturesSeam) -> list[str]:
        """Everybody the press "Use stash-box pictures as starters" would act on, in link order.

        Linked to a stash-box and holding no reference of any kind. Counted before anything is
        asked of a box: the count is what the press shows first, and a count
        that cost a request per person would be the request it asks permission for.
        """
        await self._require_enabled()
        return await self.starters_wanted(await door.linked_people())

    async def starters_wanted(self, person_ids: Sequence[str]) -> list[str]:
        """Of these People, the ones a press of "Use stash-box pictures as starters" is for.

        `wants_starters`, less everybody every one of whose box pictures this model already refused
        (`schema._CREATE_STARTER_REFUSALS`): asking again would fetch the same pictures and refuse
        them the same way, and the row would offer a Run for them for ever. What the count shows and
        what the press then runs over, so the two cannot disagree.
        """
        wanted = await self.wants_starters(person_ids)
        if not wanted:
            return []
        configured = await self.configuration()
        refused = await self._store.starters_refused(wanted, recognizer=configured.recognizer)
        return [person_id for person_id in wanted if person_id not in refused]

    async def file_starters(
        self, person_id: str, pictures: Sequence[tuple[str, bytes]]
    ) -> list[str]:
        """File a stash-box's pictures of somebody as STARTER references. Hands back their ids.

        ## What a starter is, and the rule that makes one safe

        Sift can recognize nobody it has no reference for, and most People in a library that links
        a stash-box arrive that way: a name, the box's photo as a cover, nothing to compare a face
        with. A starter is one of that box's photos, checked exactly as a picture in an imported
        folder is (`Auditor`: one face, big enough, sharp, not running off the edge; then the
        near-copy and odd-one-out checks over the set) and filed with `Origin.SEED`.

        **A starter may only make Sift ASK.** Somebody known by starters alone is judged at
        `ALWAYS_ASK` (`_attach_bar`), so every face that resembles her waits under Needs your input
        and none is named on its own. The reason is the link: a box is linked on a NAME, so a
        same-name stranger's photos arrive here exactly as hers would, and asking costs a press
        where a wrong name written on files costs a library.

        **A starter is retired** the moment she has a reference of her own (the first face of hers
        somebody confirms, a folder, a pack: `Store.add_reference`), and at the first "no" said
        about her while starters were all Sift had (`Store.reject`). Retired, not deleted: the row
        is what stops the same picture being filed again. Nor do starters count towards anything
        that measures what Sift knows: the bar, Strength, the People Sift can recognize, the list of files
        filed under somebody whose one face did not match (`Store.filed_but_unrecognised`).

        ## Refusals

        A group photo is refused (more than one face and no way to say which is her), and so is
        every other picture the checks refuse, each logged with its reason. Nothing is filed for
        somebody who has any reference row by the time the pictures arrive; `wants_starters` asked
        before they were fetched, and this asks again because a confirmation can land in between.
        """
        await self._require_enabled()
        if not await self._store.without_references([person_id]):
            return []
        configured = await self.configuration()
        if not pictures:
            # No picture at all, which reaches here only as every box she is linked to answering
            # with none: a box that could not be asked hands the job None, and the job does not
            # call this for her (`BoxPicturesSeam`). Remembered as a refusal of nothing, so she
            # leaves "starters for N people" rather than staying on it after every Run.
            await self._store.remember_starters_refused(
                person_id, recognizer=configured.recognizer, pictures=0
            )
            return []
        detector, recognizer = await self._models(configured)
        auditor = Auditor(self._settings, detector, recognizer)
        report = PersonReport(name=person_id, person_id=person_id)
        sources: list[str] = []
        for index, (source, blob) in enumerate(pictures[:STARTERS_PER_PERSON]):
            # Named for the log a lost device leaves (`runner.IN_FLIGHT`), and only while it reads.
            checking = IN_FLIGHT.set(
                f"starter picture {index + 1} of {person_id} from {source}, {len(blob)} bytes"
            )
            try:
                report.candidates.append(
                    await auditor.examine_bytes(blob, Path(f"{source} picture {index + 1}"))
                )
            finally:
                IN_FLIGHT.reset(checking)
            sources.append(source)
        mark_near_duplicates(report)
        mark_odd_ones_out(report)
        filed: list[str] = []
        kept: list[tuple[int, np.ndarray, Vector]] = []
        for index, candidate in enumerate(report.candidates):
            if candidate.findings:
                # A near-copy is usable and adds nothing; every other finding is a refusal. Logged
                # either way, with the box, so "why did she get no starters" has an answer.
                log.info(
                    "faces.starter.refused",
                    person_id=person_id,
                    source=sources[index],
                    picture=index + 1,
                    findings=[finding.value for finding in candidate.findings],
                    detail=candidate.detail,
                )
                continue
            # A picture with no finding is always described (`Auditor._judge` builds it with its
            # square and its numbers together), so this narrows the types and refuses nothing.
            if candidate.vector is not None and candidate.chip is not None:  # pragma: no branch
                kept.append((index, candidate.chip, candidate.vector))
        if not kept:
            # EVERY PICTURE REFUSED, kept where the count reads it, so she leaves "starters for N
            # people" instead of being fetched and refused again on every Run. Somebody with no
            # picture at all is remembered above, before any check is run.
            await self._store.remember_starters_refused(
                person_id, recognizer=configured.recognizer, pictures=len(report.candidates)
            )
            return []
        encoded = await cropping.encode([chip for _, chip, _ in kept], self._settings)
        for (index, _, vector), picture in zip(kept, encoded, strict=True):
            candidate = report.candidates[index]
            reference_id = await self._store.add_reference(
                person_id,
                vector=vector,
                quality=candidate.quality,
                crop=picture,
                origin=Origin.SEED,
                recognizer=recognizer.revision,
                pixels=candidate.pixels,
                source=sources[index],
            )
            # None only where this person already holds this very picture, and `without_references`
            # above found her holding none: a second run filing the same picture at the same moment
            # is the one way here, and it has filed it already.
            if reference_id is not None:  # pragma: no branch
                filed.append(reference_id)
        await self._store.forget_starters_refused(person_id)
        log.info(
            "faces.starter.filed",
            person_id=person_id,
            filed=len(filed),
            looked_at=len(report.candidates),
        )
        return filed

    async def record_starters(
        self, filed: Mapping[str, Sequence[str]], sources: Sequence[str]
    ) -> None:
        """Write one run's starters into History, with the People as its subjects and an Undo.

        The Undo retires what the run filed (`retire_starters`), exactly as a "no" does:
        Sift stops asking from those pictures and does not file them again.
        """
        if self._recorder is None:
            return
        pictures = sum(len(ids) for ids in filed.values())
        people = [person_id for person_id, ids in filed.items() if ids]
        if not pictures:
            return
        boxes = " and ".join(sorted(set(sources))) or "a stash-box"
        title = (
            f"Added {pictures} starter {'picture' if pictures == 1 else 'pictures'} from {boxes} "
            f"for {people_counted(len(people))}"
        )
        async with self._store.database.write() as connection:
            # Rung on the receipt's own commit, so a tab that re-read on the press's earlier bell
            # reads again with this receipt in it, never one receipt short.
            announce(EVERY_ADMIN, About.LIBRARY)
            await self._recorder.record_on(
                connection,
                queue=STARTERS_QUEUE,
                user_id=None,
                via=VIA_FACES,
                title=title,
                detail=(
                    "A face that looks like a person whose only pictures are starters waits "
                    "under Needs your input. Sift never names it without you. The starters step "
                    "aside once you confirm one of the person's faces, or answer No about one."
                ),
                # The boxes too, so the line is worded from the record rather than read back out
                # of the title (`jobs.StarterRecords.worded`).
                payload=json.dumps(
                    {
                        "references": {p: list(filed[p]) for p in sorted(people)},
                        "boxes": sorted(set(sources)),
                    }
                ),
                subjects=[Subject(kind="person", id=person_id) for person_id in sorted(people)],
            )

    async def retire_starters(self, reference_ids: Sequence[str]) -> int:
        """Retire these starters, where they are still in use. How many. What an Undo does."""
        retired = await self._store.retire_starters(reference_ids)
        if retired:
            log.info("faces.starter.retired", pictures=retired)
        return retired
