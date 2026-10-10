# SPDX-License-Identifier: AGPL-3.0-or-later
"""The pictures a person is recognized by: how many and what they are worth, filing them from a
folder of folders, and handing on what an import held for a name."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from sift.kernel.access import Viewer, by_user
from sift.kernel.db import Connection, Row
from sift.kernel.log import get_logger
from sift.slices.faces import crop as cropping
from sift.slices.faces import recognize, tuning
from sift.slices.faces.models import Origin, Vector
from sift.slices.faces.references import Auditor, PersonReport, picture_digests
from sift.slices.faces.service_grouping import GroupingMixin
from sift.slices.faces.service_weights import WeightsMixin
from sift.slices.faces.store_strength import Counted

if TYPE_CHECKING:  # numpy is only needed for a signature
    import numpy as np

log = get_logger(__name__)


class EntryTaken(Exception):
    """Another write placed a held entry first; the would-be second write is undone whole."""


@dataclass(frozen=True, slots=True)
class EntryHeld:
    """One held entry as its claim writes it: each face row with its picture, and the origin."""

    entry_id: str
    origin: Origin
    faces: tuple[tuple[Row, bytes | None], ...]


@dataclass(frozen=True, slots=True)
class Strength:
    """How well Sift recognizes one person here: of the faces it named as her, the share that
    were right by the answers given (`rate`), what it rests on, and the bands, so no screen bands
    it itself. A face still awaiting an answer is no part of it."""

    references: int
    target: int
    floor: int
    strong: int = 0
    starters: int = 0
    """Her STARTER pictures in use: a stash-box's, which make Sift ask about her and never name
    her. Apart from `references`, which they never add to: see `Store.reference_count`."""
    starters_retired: int = 0
    """Starters retired, at a face of hers somebody confirmed or at a "no"."""
    starters_from: tuple[str, ...] = ()
    """The stash-boxes they came from, by name."""
    counted: Counted = field(default_factory=Counted)

    @property
    def rate(self) -> float | None:
        """Of the faces named as her, the share that were right by the answers: a match stands,
        a Yes was right, a No was wrong; None before any. The awaiting ones are not counted."""
        decided = self.counted.matched + self.counted.yes + self.counted.no
        return (self.counted.matched + self.counted.yes) / decided if decided else None

    @property
    def fraction(self) -> float:
        """The page's bar: the rate, or nothing before there is one."""
        return self.rate or 0.0

    @property
    def verdict(self) -> str:
        """The band, as a token the screen words, from the pictures and then the rate."""
        if self.references == 0:
            return "none"
        if self.references < tuning.FEWEST_REFERENCES:
            return "few"
        rate = self.rate
        if rate is None:
            return "unseen"
        if rate < tuning.RATE_FAIR:
            return "weak"
        if rate < tuning.RATE_GOOD:
            return "fair"
        return "good" if rate < tuning.RATE_STRONG else "strong"


def band_of(confirmed: int) -> str:
    """The word a chooser bands a person's count of confirmed faces with."""
    if confirmed == 0:
        return "none"
    if confirmed < tuning.MIN_REFERENCES:
        return "weak"
    if confirmed < tuning.STRONG_REFERENCES:
        return "fair"
    return "good" if confirmed < tuning.GOOD_REFERENCES else "strong"


@dataclass(frozen=True, slots=True)
class Strengths:
    """Everybody's `Strength` in one go, read by the wall and the page alike."""

    people: dict[str, Strength]
    target: int
    floor: int
    strong: int


class ReferencesMixin(WeightsMixin, GroupingMixin):
    """Adding and reading a person's reference pictures."""

    async def roster(self, prefix: str = "") -> list[tuple[str, str, int, int]]:
        """Who Sift can already recognize, with their id and reference count; keyed on having
        references, not on being a Person."""
        if not await self.enabled():
            return []
        # The last is her starters in use (`Store.roster`).
        return [
            (person_id, name, faces, starters)
            for person_id, name, faces, _, starters in await self._store.roster(prefix)
        ]

    async def reference_strengths(self, viewer: Viewer | None = None) -> Strengths:
        """Everybody's `Strength` in one answer, for a picker drawing several people."""
        bands = {
            "target": tuning.GOOD_REFERENCES,
            "floor": tuning.FEWEST_REFERENCES,
            "strong": tuning.STRONG_REFERENCES,
        }
        if not await self.enabled():
            return Strengths(people={}, **bands)
        held = await self._store.reference_counts()
        counted = await self._store.strength_counts(viewer=viewer)
        return Strengths(
            people={
                person_id: Strength(
                    references=n,
                    target=tuning.GOOD_REFERENCES,
                    floor=tuning.FEWEST_REFERENCES,
                    strong=tuning.STRONG_REFERENCES,
                    counted=counted.get(person_id, Counted()),
                )
                for person_id, n in held.items()
            },
            **bands,
        )

    async def recognition_of(self, person_id: str, viewer: Viewer | None = None) -> Strength:
        """How reliably Sift can recognize one person, and what it rests on: reported, never
        enforced."""
        in_use, retired, sources = await self._store.starters_of(person_id)
        counted = await self._store.strength_counts(person_id, viewer=viewer)
        return Strength(
            references=await self._store.reference_count(person_id, viewer=viewer),
            target=tuning.GOOD_REFERENCES,
            floor=tuning.FEWEST_REFERENCES,
            strong=tuning.STRONG_REFERENCES,
            counted=counted.get(person_id, Counted()),
            starters=in_use,
            starters_retired=retired,
            starters_from=sources,
        )

    def scratch_root(self) -> Path:
        """Where an upload waiting to be read is put: the cache volume, never in-memory `/tmp`."""
        scratch = self._settings.cache_dir / "incoming"
        scratch.mkdir(parents=True, exist_ok=True)
        return scratch

    async def import_person_folder(self, folder: Path, *, source: str | None) -> PersonReport:
        """One person's folder of `import_folder`, checked and held before the next is read;
        `source` is the folder of people it sits in."""
        await self._require_enabled()
        configured = await self.configuration()
        pack_id = await self._store.folder_import_pack(configured.recognizer)
        digests = await asyncio.to_thread(picture_digests, folder)
        if digests and await self._store.folder_read_before(
            pack_id, configured.recognizer, folder.name, digests
        ):
            # A stopped import read it whole: what it held is held.
            return PersonReport(name=folder.name, already=True)
        detector, recognizer = await self._models(configured)
        auditor = Auditor(
            self._settings, detector, recognizer, bar=configured.bar, keep_turned=True
        )
        report = await auditor.person(folder)
        usable = [
            (item.vector, item.chip, item.quality, item.turned)
            for item in report.usable
            if item.vector is not None and item.chip is not None
        ]
        report.added = await self._hold_folder_faces(
            report.name, usable, recognizer.revision, source=source
        )
        await self._store.keep_folder_read(pack_id, configured.recognizer, folder.name, digests)
        return report

    async def _hold_folder_faces(
        self,
        name: str,
        usable: Sequence[tuple[Vector, np.ndarray, float, bool]],
        recognizer: str,
        *,
        source: str | None,
    ) -> int:
        """Hold what a folder brought for somebody this library has nobody for, as a pack's
        unplaced people are; idempotent. Returns how many faces were held new."""
        if not usable:
            return 0
        pack_id = await self._store.folder_import_pack(recognizer)
        entry_id = await self._store.keep_pack_entry(
            pack_id=pack_id, name=name, aliases=(), links=(), source=source or None
        )
        pictures = await cropping.encode([chip for _, chip, _, _ in usable], self._settings)
        held = 0
        for (vector, _, quality, turned), picture in zip(usable, pictures, strict=True):
            face_id = await self._store.keep_entry_face(
                entry_id,
                vector=vector,
                quality=quality,
                crop=picture,
                digest=cropping.digest(picture),
                recognizer=recognizer,
                turned=turned,
            )
            held += face_id is not None
        return held

    async def release_deleted(self) -> int:
        """Put faces back among the questions after the person they named was deleted.

        Clears the decision columns the key left behind and regroups. Returns how many came back.
        """
        await self._require_enabled()
        released = await self._store.forget_attribution_without_a_person()
        if released:
            await self.regroup()
            log.info("faces.person.released", faces=released)
        return released

    async def claim_for(self, person_id: str, name: str) -> int:
        """Give a person the faces a pack was holding for that name or an alias; idempotent.
        Returns how many arrived."""
        await self._require_enabled()
        return await self._claim(person_id, name)

    async def _claim(self, person_id: str, name: str) -> int:
        """`claim_for` without asking whether recognition is on: a file taken in while off waits."""
        arrived = 0
        for entry in await self._store.unclaimed_entries(name):
            arrived += len(await self._claim_entry(entry, person_id))
        if arrived:
            log.info("faces.pack.claimed", faces=arrived)
        return arrived

    async def _claim_entry(self, entry: Row, person_id: str) -> list[str]:
        """Give one held entry's faces to a person as references and mark it hers, in one write.
        Returns the references written new, by id, for an Undo; nothing if claimed first."""
        held = await self._entry_held(entry)
        pictures: list[tuple[Path, bytes]] = []
        try:
            async with self._store.database.write() as connection:
                written = await self._claim_entry_on(connection, held, person_id, pictures)
        except EntryTaken:
            return []
        await self._store.write_pictures(pictures)
        return written

    async def _entry_held(self, entry: Row) -> EntryHeld:
        """What a claim of this entry writes, read before its write; a folder's entry files as
        ADDED, a pack's as PACK, decided while the entry still says which."""
        folder_row = await self._store.pack_by_name(self._store.FOLDER_IMPORTS)
        from_folders = None if folder_row is None else str(folder_row["id"])
        entry_id = str(entry["id"])
        origin = Origin.ADDED if str(entry["pack_id"]) == from_folders else Origin.PACK
        faces = [
            (face, await self._store.picture_bytes(face["crop_path"]))
            for face in await self._store.entry_faces(entry_id)
        ]
        return EntryHeld(entry_id=entry_id, origin=origin, faces=tuple(faces))

    async def _claim_entry_on(
        self,
        connection: Connection,
        held: EntryHeld,
        person_id: str,
        pictures: list[tuple[Path, bytes]],
    ) -> list[str]:
        """`_claim_entry` inside the caller's write; raises `EntryTaken` if claimed first."""
        if not await self._store.claim_entry_on(connection, held.entry_id, person_id):
            raise EntryTaken(held.entry_id)
        written: list[str] = []
        for face, crop in held.faces:
            reference_id = await self._store.add_reference_on(
                connection,
                person_id,
                pictures=pictures,
                vector=recognize.unpack(bytes(face["embedding"])),
                quality=float(face["quality"]),
                crop=crop,
                digest=str(face["crop_digest"]),
                origin=held.origin,
                recognizer=str(face["recognizer"]),
            )
            if reference_id is not None:
                written.append(reference_id)
        return written

    async def waiting_for(self, name: str) -> list[str]:
        """The names a pack is holding that this one would claim. Read-only."""
        return [str(row["name"]) for row in await self._store.unclaimed_entries(name)]

    async def held_for(self, person_id: str, name: str) -> int:
        """How many face descriptions `claim_for` would give this person now, new ones only."""
        await self._require_enabled()
        entries = await self._store.unclaimed_entries(name)
        if not entries:
            return 0
        theirs = {one.crop_digest for one in await self._store.references(person_id)}
        waiting: set[str] = set()
        for entry in entries:
            for face in await self._store.entry_faces(str(entry["id"])):
                digest = str(face["crop_digest"])
                if digest not in theirs:
                    waiting.add(digest)
        return len(waiting)

    async def find_or_create_person(self, name: str, *, by: str) -> str | None:
        """Somebody by that name, matched ignoring case or made, as typed by `by`; None if blank."""
        await self._require_enabled()
        wanted = name.strip()
        if not wanted:
            return None
        known = await self._store.existing_people([wanted])
        return known.get(wanted.casefold()) or await self._store.create_person(wanted, by_user(by))
