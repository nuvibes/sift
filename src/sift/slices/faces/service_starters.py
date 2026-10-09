# SPDX-License-Identifier: AGPL-3.0-or-later
"""Starter pictures: a stash-box's photos of somebody Sift has no reference for, filed so Sift
may ask about her, never name her on their strength alone."""

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

#: A box's pictures of one person looked at, at most; the odd-one-out check needs four.
STARTERS_PER_PERSON = 5


class StartersMixin(WeightsMixin):
    """Choosing, filing, recording and retiring starter pictures."""

    async def wants_starters(self, person_ids: Sequence[str]) -> list[str]:
        """Of these People, those with no reference row at all, retired starters included."""
        await self._require_enabled()
        return await self._store.without_references(person_ids)

    async def starters_count(self, door: BoxPicturesSeam) -> list[str]:
        """Everybody "Use stash-box pictures as starters" acts on, counted before any request."""
        await self._require_enabled()
        return await self.starters_wanted(await door.linked_people())

    async def starters_wanted(self, person_ids: Sequence[str]) -> list[str]:
        """`wants_starters`, less those whose every box picture this model already refused."""
        wanted = await self.wants_starters(person_ids)
        if not wanted:
            return []
        configured = await self.configuration()
        refused = await self._store.starters_refused(wanted, recognizer=configured.recognizer)
        return [person_id for person_id in wanted if person_id not in refused]

    async def file_starters(
        self, person_id: str, pictures: Sequence[tuple[str, bytes]]
    ) -> list[str]:
        """File a stash-box's pictures of somebody as starter references. Hands back their ids.

        Checked as an imported folder's pictures are (`Auditor`), filed as `Origin.SEED`. A starter
        only ever makes Sift ask, since a box is linked on a name a stranger may share; it retires
        when she has a reference of her own or at the first "no" about her.
        """
        await self._require_enabled()
        if not await self._store.without_references([person_id]):
            return []
        configured = await self.configuration()
        if not pictures:
            # Remembered as a refusal of nothing, so she leaves the count after a Run.
            await self._store.remember_starters_refused(
                person_id, recognizer=configured.recognizer, pictures=0
            )
            return []
        detector, recognizer = await self._models(configured)
        auditor = Auditor(self._settings, detector, recognizer)
        report = PersonReport(name=person_id, person_id=person_id)
        sources: list[str] = []
        for index, (source, blob) in enumerate(pictures[:STARTERS_PER_PERSON]):
            # Named for the log a lost device leaves (`runner.IN_FLIGHT`).
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
        kept = _kept_starters(person_id, report, sources)
        if not kept:
            # Every picture refused, remembered so she is not fetched and refused on every Run.
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
            # None only where a concurrent run filed this very picture already.
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
        """Write one run's starters into History, with an Undo that retires them."""
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
            # Rung on the receipt's own commit, so a re-read tab finds it.
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
                # The boxes too, so the line is worded from the record (`StarterRecords.worded`).
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


def _kept_starters(
    person_id: str, report: PersonReport, sources: Sequence[str]
) -> list[tuple[int, np.ndarray, Vector]]:
    """The pictures no check refused, each with its index, square and description."""
    kept: list[tuple[int, np.ndarray, Vector]] = []
    for index, candidate in enumerate(report.candidates):
        if candidate.findings:
            # A near-copy adds nothing; every finding is logged with the box.
            log.info(
                "faces.starter.refused",
                person_id=person_id,
                source=sources[index],
                picture=index + 1,
                findings=[finding.value for finding in candidate.findings],
                detail=candidate.detail,
            )
            continue
        # Always true without a finding; narrows the types.
        if candidate.vector is not None and candidate.chip is not None:  # pragma: no branch
            kept.append((index, candidate.chip, candidate.vector))
    return kept
