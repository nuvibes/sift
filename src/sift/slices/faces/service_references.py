# SPDX-License-Identifier: AGPL-3.0-or-later
"""The pictures a person is recognized by: how many there are and what they are worth, filing them
from a folder of folders, and handing on what an import held for a name.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
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

if TYPE_CHECKING:  # numpy is only needed for a signature
    import numpy as np

log = get_logger(__name__)


class EntryTaken(Exception):
    """Another write placed a held entry first: the write that would have placed it again is
    undone whole."""


@dataclass(frozen=True, slots=True)
class EntryHeld:
    """One held entry as its claim writes it: each face row with its picture, and the origin the
    references take."""

    entry_id: str
    origin: Origin
    faces: tuple[tuple[Row, bytes | None], ...]


@dataclass(frozen=True, slots=True)
class Strength:
    """How well Sift can recognize one person, and what that rests on.

    The target travels with the count rather than being known by the screen. A bar drawn against a
    number the client holds its own copy of is a bar that goes on saying "good" after the number
    behind it has moved.
    """

    references: int
    target: int
    floor: int
    strong: int = 0
    """Where recognition becomes dependable rather than merely working. The middle of three bands,
    because "fine" and "as good as it gets" are different answers and only one of them is worth
    acting on."""
    starters: int = 0
    """Her STARTER pictures in use: a stash-box's, which make Sift ask about her and never name
    her. Apart from `references`, which they never add to: see `Store.reference_count`."""
    starters_retired: int = 0
    """Starters retired, at a face of hers somebody confirmed or at a "no"."""
    starters_from: tuple[str, ...] = ()
    """The stash-boxes they came from, by name."""

    @property
    def fraction(self) -> float:
        """How far toward dependable this person is, capped at one: the page's bar."""
        return min(1.0, self.references / self.strong) if self.strong else 0.0

    @property
    def verdict(self) -> str:
        """What to say about it, in one word this user can act on.

        Three bands rather than two, from the measured curve: below the floor is a coin toss, up to
        `strong` it works with gaps, past `strong` it is dependable, and at the target there is
        nothing left worth doing. Collapsing the middle two would say "not there yet" to somebody
        whose person is already being recognized reliably.
        """
        if self.references == 0:
            return "none"
        if self.references < self.floor:
            return "weak"
        if self.references < self.strong:
            return "fair"
        if self.references < self.target:
            return "good"
        return "strong"


def band_of(confirmed: int) -> str:
    """The word a person's page bands this many confirmed faces with (`Strength.verdict`), for a
    list that draws many people and must say what the page says about each."""
    return Strength(
        references=confirmed,
        target=tuning.GOOD_REFERENCES,
        floor=tuning.MIN_REFERENCES,
        strong=tuning.STRONG_REFERENCES,
    ).verdict


@dataclass(frozen=True, slots=True)
class Strengths:
    """Everybody's `Strength` in one go, with the bands every one of them is read against.

    The wall's reading and the person's page read the same object, so a card on the wall and the
    page it opens cannot say two different words about one number.
    """

    people: dict[str, Strength]
    target: int
    floor: int
    strong: int


class ReferencesMixin(WeightsMixin, GroupingMixin):
    """Adding and reading a person's reference pictures."""

    async def roster(self, prefix: str = "") -> list[tuple[str, str, int, int]]:
        """Who Sift can already recognize, their id, and how many reference faces each has.

        Read before adding somebody, to answer "do I have them already" without hunting. Keyed on
        having references rather than on being a Person: somebody with none is not recognizable,
        and listing them here would answer yes to a question whose real answer is no.

        The id travels with the name so the screen can link a name to that person's own page: the
        answer to "do I have them" is very often followed by "show me them".
        """
        if not await self.enabled():
            return []
        # The last is how many STARTER pictures she has in use, so the list can say Sift knows her
        # only well enough to ask. See `Store.roster`.
        return [
            (person_id, name, faces, starters)
            for person_id, name, faces, _, starters in await self._store.roster(prefix)
        ]

    async def reference_strengths(self) -> Strengths:
        """Everybody's reference count in one go, with the target and the floor they are read against.

        `recognition_of` answers for one person and is right for a person's own page. A picker is
        the other shape: it draws several people together, and asking per row turns choosing a name
        into one request per keystroke per candidate. This is the same numbers in one answer.

        The target and floor travel with the counts for the reason the meter's do: a screen
        holding its own copy of the threshold goes on saying "weak" after the number behind it
        moves.

        Each person's `Strength`, not a bare count: the verdict is the same property the person's
        own page reads, computed once here, so no screen bands the number for itself. A wall that
        did its own banding could say "Identifies them" over a person the page calls "well".
        """
        bands = {
            "target": tuning.GOOD_REFERENCES,
            "floor": tuning.MIN_REFERENCES,
            "strong": tuning.STRONG_REFERENCES,
        }
        if not await self.enabled():
            return Strengths(people={}, **bands)
        held = await self._store.reference_counts()
        return Strengths(
            people={
                person_id: Strength(
                    references=n,
                    target=tuning.GOOD_REFERENCES,
                    floor=tuning.MIN_REFERENCES,
                    strong=tuning.STRONG_REFERENCES,
                )
                for person_id, n in held.items()
            },
            **bands,
        )

    async def recognition_of(self, person_id: str, viewer: Viewer | None = None) -> Strength:
        """How reliably Sift can recognize one person, and what it rests on.

        The number that was invisible. Matching against a person compares a new face with every
        reference they have, so somebody with three references under-matches, correctly, quietly,
        and with nothing on any screen to say so. Import six hundred people in one go and the ones
        with two usable photos look exactly like the ones with fifty, until somebody notices they
        are never recognized.

        Reported and never enforced. Below the target Sift still matches; it is just worse at it,
        and the useful thing is to say so where somebody can act on it.
        """
        in_use, retired, sources = await self._store.starters_of(person_id)
        return Strength(
            references=await self._store.reference_count(person_id, viewer=viewer),
            target=tuning.GOOD_REFERENCES,
            floor=tuning.MIN_REFERENCES,
            strong=tuning.STRONG_REFERENCES,
            starters=in_use,
            starters_retired=retired,
            starters_from=sources,
        )

    def scratch_root(self) -> Path:
        """Where an upload waiting to be read is put, made if it is not there.

        Under the cache directory rather than the system temp area, on purpose. The container's
        `/tmp` is a small in-memory mount; a folder of six hundred people's photographs written
        there would fill the machine's memory instead of a disk. The cache is a real volume, it is
        rebuildable, and it is already where everything large and temporary lives.
        """
        scratch = self._settings.cache_dir / "incoming"
        scratch.mkdir(parents=True, exist_ok=True)
        return scratch

    async def import_person_folder(self, folder: Path, *, source: str | None) -> PersonReport:
        """One person's folder of `import_folder`, checked and held before the next is read.

        One at a time so a long import keeps what it has done when it is stopped, and holds one
        person's pictures in memory rather than the whole gallery's. `source` is the name of the
        folder of people it sits in: what the entry says it came from.
        """
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
        report = await Auditor(self._settings, detector, recognizer).person(folder)
        usable = [
            (item.vector, item.chip, item.quality, item.pixels)
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
        usable: Sequence[tuple[Vector, np.ndarray, float, int]],
        recognizer: str,
        *,
        source: str | None,
    ) -> int:
        """Hold what a FOLDER brought for somebody this library has nobody for.

        The same holding place a pack's unplaced people go to, and deliberately so: what is held is
        a name and some faces, and where they were read from stops mattering the moment they are
        held. Adding that person later claims these by exactly the same route.

        Idempotent for the same reason the pack path is: the entry is matched on its name and each
        face on the identity of its picture, so re-running the import over the same folders writes
        nothing at all. Returns how many faces were held new.

        Every folder's entries hang off the one standing pack, so the entry itself keeps the
        folder's name (`source`): the list of who is waiting, and the line under a person it
        creates, name the folder rather than the pack.
        """
        if not usable:
            return 0
        pack_id = await self._store.folder_import_pack(recognizer)
        entry_id = await self._store.keep_pack_entry(
            pack_id=pack_id, name=name, aliases=(), links=(), source=source or None
        )
        pictures = await cropping.encode([chip for _, chip, _, _ in usable], self._settings)
        held = 0
        for (vector, _, quality, _pixels), picture in zip(usable, pictures, strict=True):
            face_id = await self._store.keep_entry_face(
                entry_id,
                vector=vector,
                quality=quality,
                crop=picture,
                digest=cropping.digest(picture),
                recognizer=recognizer,
            )
            held += face_id is not None
        return held

    async def release_deleted(self) -> int:
        """Put faces back among the questions after the person they named was deleted.

        Two things have to happen that the key does not do. It cuts `person_id` loose on its own, so
        the face stops being theirs, but it leaves `attribution` and `confidence` behind,
        describing a decision about nobody. And a named face is in no pile, because naming took it
        out of one, so cutting it loose does not put it anywhere: it is neither identified nor
        waiting, and shows up on no screen at all until somebody presses "Group them again".

        So the stale columns are cleared and the piles are rebuilt. Returns how many faces came back,
        for the log: nothing acts on it.

        Not narrowed to one person, deliberately. The row naming them is already gone by the time
        this runs, which is what makes the clean-up possible at all: every track carrying an
        attribution with no person attached is one of these, whoever it was.
        """
        await self._require_enabled()
        released = await self._store.forget_attribution_without_a_person()
        if released:
            await self.regroup()
            log.info("faces.person.released", faces=released)
        return released

    async def claim_for(self, person_id: str, name: str) -> int:
        """Give a person the faces a pack was holding for that name. Returns how many arrived.

        Called when somebody is created and the name they were given is one a pack already knew,
        by that name or by any of the also-known-as names it carried. The faces become ordinary
        references, which is what makes the person recognizable from the moment they exist rather
        than from the next time somebody imports anything.

        Idempotent twice over: a reference is keyed by its picture, and the entry is marked claimed
        so a second pass over the same name finds nothing left to give.
        """
        await self._require_enabled()
        return await self._claim(person_id, name)

    async def _claim(self, person_id: str, name: str) -> int:
        """`claim_for` without asking whether recognition is on: a facial fingerprints file is
        taken in with recognition off too (`import_pack(while_off=True)`), and what it held waits
        until the switch is on."""
        arrived = 0
        for entry in await self._store.unclaimed_entries(name):
            arrived += len(await self._claim_entry(entry, person_id))
        if arrived:
            log.info("faces.pack.claimed", faces=arrived)
        return arrived

    async def _claim_entry(self, entry: Row, person_id: str) -> list[str]:
        """Give one held entry's faces to a person as references and mark it claimed by her, in
        one write. Returns the references written new, by id: what an Undo of the claim takes away
        again. Nothing is written where another write claimed the entry first."""
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
        """What a claim of this entry writes, read before its write: the faces, their pictures,
        and the origin they take.

        WHERE the held faces came from decides their origin, which is not always a pack. A folder
        import holds its people in the same place a pack does (`_hold_folder_faces`), under one
        standing row, and a reference read off somebody's own folder of pictures is ADDED, never a
        download's PACK. The source cannot be worked out afterwards (the claim is a copy and the
        entry is marked spent), so it is decided here, where the entry still says which it was.
        """
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
        """`_claim_entry` inside the caller's write. Raises `EntryTaken`, which undoes the whole
        write, where another write claimed the entry first."""
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
        """The names a pack is holding that this one would claim. What a suggestion is built from.

        Read-only, so a screen can offer "this looks like somebody from your import" before
        anything is written. Empty is the ordinary answer and means exactly what it says.
        """
        return [str(row["name"]) for row in await self._store.unclaimed_entries(name)]

    async def held_for(self, person_id: str, name: str) -> int:
        """How many face descriptions `claim_for` would give this person now. Read-only.

        What an offer to add them counts, so the number is the one the press would add: a
        description this person already has as a reference (the same picture) is not counted,
        because claiming it adds nothing.
        """
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
        """Somebody by that name, made if they are not there. None if the name is blank.

        Matched before it is created, ignoring case, which is what stops pressing this twice
        producing two of somebody. It is the same match a pack import uses, so a name typed here
        and a name read from a folder resolve to the same person rather than to two spellings.

        `by` is the user who TYPED the name, and it is what makes this road different from the
        other two callers of `create_person`: nobody read this off anything, somebody wrote it into
        a field over a group of faces. The row says so.
        """
        await self._require_enabled()
        wanted = name.strip()
        if not wanted:
            return None
        known = await self._store.existing_people([wanted])
        return known.get(wanted.casefold()) or await self._store.create_person(wanted, by_user(by))
