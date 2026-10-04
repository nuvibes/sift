# SPDX-License-Identifier: AGPL-3.0-or-later
"""The pictures a person is recognised by: her own, the starters, and the entries of a pack."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from pathlib import Path

from sift.kernel.db import Connection, Row, in_clause
from sift.kernel.ids import new_id
from sift.kernel.paging import MAX_PAGE_SIZE
from sift.kernel.sql_splice import splice
from sift.slices.faces import recognize
from sift.slices.faces.models import (
    AskedBy,
    Attribution,
    Origin,
    Reference,
    Vector,
)
from sift.slices.faces.receipts import IDENTIFIED_QUEUE
from sift.slices.faces.store_models import ModelsStore
from sift.slices.faces.store_pictures import (
    _digest,
    _read_pictures,
    _write_pictures,
)
from sift.slices.faces.store_records import (
    _RETIRE_STARTERS,
    _reference,
    now_ms,
)

#: Every appearance a recognizer described, with its clearest description (see `library_faces`).
_LIBRARY_FACES = (
    "SELECT t.id AS track_id, t.asset_id AS asset_id, t.person_id AS person_id, "
    "t.attribution AS attribution, t.pile_id AS pile_id, d.embedding AS embedding "
    "FROM face_tracks AS t "
    "JOIN face_scans AS s ON s.asset_id = t.asset_id AND s.recognizer = ? "
    "JOIN face_detections AS d ON d.id = ("
    "  SELECT best.id FROM face_detections AS best "
    "   WHERE best.track_id = t.id "
    "   ORDER BY best.quality DESC, best.id LIMIT 1"
    ") {{SCOPE}}ORDER BY t.id"
)
_LIBRARY_FACES_ALL = splice(_LIBRARY_FACES, SCOPE="")
_LIBRARY_FACES_OF_FILE = splice(_LIBRARY_FACES, SCOPE="WHERE t.asset_id = ? ")


class ReferencesStore(ModelsStore):
    """The pictures a person is recognised by: her own, the starters, and the entries of a pack."""

    # --- references --------------------------------------------------------------------------

    async def add_reference(
        self,
        person_id: str,
        *,
        vector: Vector,
        quality: float,
        crop: bytes | None,
        origin: Origin,
        recognizer: str,
        digest: str | None = None,
        pack_id: str | None = None,
        pixels: int | None = None,
        track_id: str | None = None,
        asset_id: str | None = None,
        source: str | None = None,
    ) -> str | None:
        """Add one reference face. Hands back nothing if that person already has this exact one.

        A reference of her OWN (anything but a starter) retires her starters in the same
        transaction. Keyed by the picture's identity (or the one a picture-less pack states), so
        the same face from a folder, a pack and a confirmation is one reference. `pixels` is the
        face's size before it was warped to the stored square, unrecoverable after; null for a
        pack without pictures.
        """
        pictures: list[tuple[Path, bytes]] = []
        async with self._db.write() as connection:
            reference_id = await self.add_reference_on(
                connection,
                person_id,
                pictures=pictures,
                vector=vector,
                quality=quality,
                crop=crop,
                origin=origin,
                recognizer=recognizer,
                digest=digest,
                pack_id=pack_id,
                pixels=pixels,
                track_id=track_id,
                asset_id=asset_id,
                source=source,
            )
        await self.write_pictures(pictures)
        return reference_id

    async def write_pictures(self, pictures: Sequence[tuple[Path, bytes]]) -> None:
        """Put the pictures an `add_reference_on` left to its caller on the disk, once its write
        has landed."""
        if pictures:
            await asyncio.to_thread(_write_pictures, pictures)

    async def add_reference_on(
        self,
        connection: Connection,
        person_id: str,
        *,
        pictures: list[tuple[Path, bytes]],
        vector: Vector,
        quality: float,
        crop: bytes | None,
        origin: Origin,
        recognizer: str,
        digest: str | None = None,
        pack_id: str | None = None,
        pixels: int | None = None,
        track_id: str | None = None,
        asset_id: str | None = None,
        source: str | None = None,
    ) -> str | None:
        """`add_reference` inside the caller's write. The picture to write is added to `pictures`
        for the caller to put on the disk after the write lands (`write_pictures`)."""
        if crop is None and digest is None:
            raise ValueError("a reference needs either its picture or the identity of one")
        digest = digest or _digest(crop or b"")
        existing = list(
            await connection.execute_fetchall(
                "SELECT id FROM face_references WHERE person_id = ? AND crop_digest = ?",
                (person_id, digest),
            )
        )
        if existing:
            # The person already holds this picture and the appearance is still its source, so the
            # row is CLAIMED, or a second confirmation would write nothing; only an unclaimed row,
            # since one reference belongs to one appearance.
            if track_id is not None:
                await connection.execute(
                    "UPDATE face_references SET track_id = ?, asset_id = ? "
                    "WHERE id = ? AND track_id IS NULL",
                    (track_id, asset_id, str(existing[0]["id"])),
                )
            return None

        reference_id = new_id()
        path = self.crop_path(self.reference_root, reference_id)
        stamp = now_ms()
        await connection.execute(
            "INSERT INTO face_references (id, person_id, crop_path, crop_digest, embedding, "
            "quality, origin, pack_id, recognizer, pixels, track_id, asset_id, source, "
            "created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                reference_id,
                person_id,
                self.relative(self.reference_root, reference_id) if crop is not None else None,
                digest,
                recognize.pack(vector),
                quality,
                origin.value,
                pack_id,
                recognizer,
                pixels,
                track_id,
                asset_id,
                source,
                stamp,
            ),
        )
        if origin is not Origin.SEED:
            await connection.execute(_RETIRE_STARTERS, (stamp, person_id))
        if crop is not None:
            pictures.append((path, crop))
        return reference_id

    # --- what a pack brought and could not place -----------------------------------------------

    async def keep_pack_entry(
        self,
        *,
        pack_id: str,
        name: str,
        aliases: Sequence[str],
        links: Sequence[str],
        source: str | None = None,
        confirmed: int | None = None,
    ) -> str:
        """Hold somebody a pack named, without making them a Person. Returns the entry's id.

        Re-importing the same pack finds the entry already there and hands back the same id, which
        is what stops a second import doubling anything, and what lets an entry keep the person
        it was eventually claimed by. `source` is the folder it was read from where that is not
        the pack's own name, and `confirmed` the confirmed faces the file said they had: both
        written here alone, with the row, so the first folder to bring somebody stays theirs; an
        entry made before folders were named takes its name from the first import that brings it again.
        """
        entry_id = new_id()
        await self._db.execute(
            "INSERT INTO pack_entries"
            " (id, pack_id, name, aliases, links, created_at, source, confirmed)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(pack_id, name COLLATE NOCASE)"
            " DO UPDATE SET source = COALESCE(pack_entries.source, excluded.source)",
            (
                entry_id,
                pack_id,
                name,
                json.dumps(list(aliases)),
                json.dumps(list(links)),
                now_ms(),
                source,
                confirmed,
            ),
        )
        row = await self._db.fetch_one(
            "SELECT id FROM pack_entries WHERE pack_id = ? AND name = ? COLLATE NOCASE",
            (pack_id, name),
        )
        return str(row["id"]) if row is not None else entry_id

    async def keep_entry_face(
        self,
        entry_id: str,
        *,
        vector: Vector,
        quality: float,
        crop: bytes | None,
        digest: str,
        recognizer: str,
    ) -> str | None:
        """One face under an unclaimed entry. None if the entry already holds this exact picture.

        Stored in the shape a reference is stored in, because that is what it becomes the moment
        somebody claims the entry: the claim is a copy, not a conversion.
        """
        face_id = new_id()
        path = self.crop_path(self.reference_root, face_id)
        held = await self._db.fetch_one(
            "SELECT id FROM pack_entry_faces WHERE entry_id = ? AND crop_digest = ?",
            (entry_id, digest),
        )
        if held is not None:
            return None
        if crop is not None:
            await asyncio.to_thread(_write_pictures, [(path, crop)])
        await self._db.execute(
            "INSERT INTO pack_entry_faces "
            "(id, entry_id, crop_path, crop_digest, embedding, quality, recognizer) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                face_id,
                entry_id,
                self.relative(self.reference_root, face_id) if crop is not None else None,
                digest,
                recognize.pack(vector),
                quality,
                recognizer,
            ),
        )
        return face_id

    async def add_references(
        self,
        person_id: str,
        faces: Sequence[tuple[Vector, float, bytes | None, str | None, int | None]],
        *,
        origin: Origin,
        recognizer: str,
        pack_id: str | None = None,
    ) -> int:
        """`add_reference` for a batch: one transaction for the rows, one write for the pictures.

        Each face is (vector, quality, picture or None, digest or None, pixels). A pack of hundreds
        of people is one batch, not a transaction and a file per face. Returns how
        many were new: the same identity rule as the single form, so a picture already held for
        this person adds nothing.
        """
        stamp = now_ms()
        written: list[tuple[Path, bytes]] = []
        added = 0
        async with self._db.write() as connection:
            for vector, quality, crop, digest, pixels in faces:
                if crop is None and digest is None:
                    raise ValueError("a reference needs either a picture or the identity of one")
                identity = digest or _digest(crop or b"")
                held = list(
                    await connection.execute_fetchall(
                        "SELECT id FROM face_references WHERE person_id = ? AND crop_digest = ?",
                        (person_id, identity),
                    )
                )
                if held:
                    continue
                reference_id = new_id()
                path = self.crop_path(self.reference_root, reference_id)
                await connection.execute(
                    "INSERT INTO face_references (id, person_id, crop_path, crop_digest, embedding, "
                    "quality, origin, pack_id, recognizer, pixels, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        reference_id,
                        person_id,
                        self.relative(self.reference_root, reference_id)
                        if crop is not None
                        else None,
                        identity,
                        recognize.pack(vector),
                        quality,
                        origin.value,
                        pack_id,
                        recognizer,
                        pixels,
                        stamp,
                    ),
                )
                if crop is not None:
                    written.append((path, crop))
                added += 1
            if added and origin is not Origin.SEED:
                await connection.execute(_RETIRE_STARTERS, (stamp, person_id))
        if written:
            await asyncio.to_thread(_write_pictures, written)
        return added

    async def unclaimed_entries(self, name: str | None = None) -> list[Row]:
        """Everybody a pack named who is still nobody here, optionally narrowed to one name.

        Matched on the name OR any of the also-known-as names the pack carried, because that is
        how somebody added by hand months later would be recognized as the entry: they typed one
        of the names the pack already knew.
        """
        if name is None:
            return await self._db.fetch_all(
                "SELECT * FROM pack_entries WHERE claimed_person_id IS NULL "
                "ORDER BY name COLLATE NOCASE"
            )
        rows = await self._db.fetch_all(
            "SELECT * FROM pack_entries WHERE claimed_person_id IS NULL "
            "AND (name = ? COLLATE NOCASE OR EXISTS ("
            "  SELECT 1 FROM json_each(pack_entries.aliases) WHERE value = ? COLLATE NOCASE"
            ")) ORDER BY name COLLATE NOCASE",
            (name, name),
        )
        return rows

    #: How a swap names the packs it holds People under (`f"swap {session} {person}"`, where the
    #: swap hands them over). A swap's People are a suggestion about somebody this library already
    #: has, never a person to make or to claim by face, so the pass over facial fingerprints leaves
    #: them held for the person's own page to offer.
    SWAPPED_PACKS = "swap "

    async def fingerprints_held(self, recognizer: str) -> list[tuple[str, bytes]]:
        """Every face of every entry the pass over facial fingerprints may still place, as
        (entry, description): unclaimed, not declined, from a facial fingerprints file or a
        folder of people, and described by `recognizer` (another model's numbers mean nothing
        here). A swap's entries are left out (see `SWAPPED_PACKS`). Raw bytes, because the caller
        stacks them into one array and a tuple of floats per face would cost ten times the memory.
        """
        rows = await self._db.fetch_all(
            "SELECT e.id AS entry_id, f.embedding AS embedding FROM pack_entries e"
            " JOIN face_packs p ON p.id = e.pack_id"
            " JOIN pack_entry_faces f ON f.entry_id = e.id"
            " WHERE e.claimed_person_id IS NULL AND e.declined_at IS NULL"
            " AND substr(p.name, 1, ?) != ? AND f.recognizer = ? ORDER BY e.id, f.id",
            (len(self.SWAPPED_PACKS), self.SWAPPED_PACKS, recognizer),
        )
        return [(str(row["entry_id"]), bytes(row["embedding"])) for row in rows]

    async def fingerprints_stamp(self) -> tuple[int, int, int]:
        """What the held entries look like now, cheaply: how many are waiting, how many faces are
        held, and the newest entry. A cached description of them is good while this is unchanged:
        an import adds faces, a claim or a decline takes an entry out of the waiting count."""
        row = await self._db.fetch_one(
            "SELECT (SELECT COUNT(*) FROM pack_entries"
            "        WHERE claimed_person_id IS NULL AND declined_at IS NULL) AS waiting,"
            " (SELECT COUNT(*) FROM pack_entry_faces) AS faces,"
            " (SELECT COALESCE(MAX(created_at), 0) FROM pack_entries) AS newest",
            (),
        )
        if row is None:  # pragma: no cover (an aggregate always answers one row)
            return (0, 0, 0)
        return (int(row["waiting"]), int(row["faces"]), int(row["newest"]))

    async def open_groups(self, recognizer: str) -> list[tuple[str, bytes]]:
        """Every open group of faces made by `recognizer`, with its middle: what a held entry is
        compared with to ask a group whether it is that person."""
        rows = await self._db.fetch_all(
            "SELECT id, centroid FROM face_piles WHERE status = 'open' AND recognizer = ?"
            " ORDER BY id",
            (recognizer,),
        )
        return [(str(row["id"]), bytes(row["centroid"])) for row in rows]

    async def entry(self, entry_id: str) -> Row | None:
        """One entry with the name of the file or folder it came from (`pack`): its own folder
        where a folder import gave one, the pack's name otherwise."""
        return await self._db.fetch_one(
            "SELECT e.*, COALESCE(e.source, p.name) AS pack"
            " FROM pack_entries e JOIN face_packs p ON p.id = e.pack_id WHERE e.id = ?",
            (entry_id,),
        )

    async def library_faces(
        self, recognizer: str, asset_id: str | None = None
    ) -> list[tuple[str, str, str | None, str | None, str | None, bytes]]:
        """Every appearance described by `recognizer` (or one file's), with its clearest
        description chosen as `unattributed` chooses it: (track, file, person, attribution, group,
        description). What the pass over facial fingerprints compares the held entries with, and
        the attribution is what tells it whose own pictures already match the face."""
        if asset_id is None:
            rows = await self._db.sweep_all(
                _LIBRARY_FACES_ALL, (recognizer,), what="facial fingerprints"
            )
        else:
            rows = await self._db.fetch_all(_LIBRARY_FACES_OF_FILE, (recognizer, asset_id))
        return [
            (
                str(row["track_id"]),
                str(row["asset_id"]),
                None if row["person_id"] is None else str(row["person_id"]),
                None if row["attribution"] is None else str(row["attribution"]),
                None if row["pile_id"] is None else str(row["pile_id"]),
                bytes(row["embedding"]),
            )
            for row in rows
        ]

    async def entry_faces(self, entry_id: str) -> list[Row]:
        return await self._db.fetch_all(
            "SELECT * FROM pack_entry_faces WHERE entry_id = ? ORDER BY quality DESC", (entry_id,)
        )

    async def mark_entry_claimed(self, entry_id: str, person_id: str) -> None:
        await self._db.execute(
            "UPDATE pack_entries SET claimed_person_id = ? WHERE id = ?", (person_id, entry_id)
        )

    async def claim_entry_on(self, connection: Connection, entry_id: str, person_id: str) -> bool:
        """Mark an entry claimed by a person inside the caller's write, only while it is nobody's.
        False when somebody else's write placed it first."""
        rows = await connection.execute_fetchall(
            "UPDATE pack_entries SET claimed_person_id = ? "
            "WHERE id = ? AND claimed_person_id IS NULL RETURNING id",
            (person_id, entry_id),
        )
        return bool(list(rows))

    async def decline_entry(self, entry_id: str) -> None:
        """Hold an entry again and keep the pass over facial fingerprints from placing it: what an
        Undo of the person it made or claimed leaves. Adding the person by hand still finds it
        (`unclaimed_entries` reads declined entries too)."""
        await self._db.execute(
            "UPDATE pack_entries SET claimed_person_id = NULL, declined_at = ? WHERE id = ?",
            (now_ms(), entry_id),
        )

    async def references(self, person_id: str | None = None) -> list[Reference]:
        if person_id is None:
            rows = await self._db.fetch_all(
                "SELECT * FROM face_references ORDER BY person_id, id", ()
            )
        else:
            rows = await self._db.fetch_all(
                "SELECT * FROM face_references WHERE person_id = ? ORDER BY id", (person_id,)
            )
        return [_reference(row) for row in rows]

    async def people_with_chosen_references(self, recognizer: str) -> list[str]:
        """Everybody described by at least one reference that is not a starter, in name order.

        Who a whole-library pack carries: a starter is a stash-box's photo that only lets Sift
        ask, and a pack is read back as references somebody chose. Only this model's references
        count, since a pack names one model and its numbers mean nothing to another.
        """
        rows = await self._db.fetch_all(
            "SELECT p.id FROM people p WHERE EXISTS (SELECT 1 FROM face_references r"
            " WHERE r.person_id = p.id AND r.origin != 'seed' AND r.recognizer = ?)"
            " ORDER BY p.name_sort, p.name, p.id",
            (recognizer,),
        )
        return [str(row["id"]) for row in rows]

    async def reference_tracks(self, track_ids: Sequence[str]) -> set[str]:
        """Which of these appearances actually contributed a reference: several rules can decline
        one, and a screen marks which did.

        From the claim written on the reference, and from the crop's identity as well: a rescan
        cuts fresh crops with new digests, so the identity alone would not survive it.
        """
        if not track_ids:
            return set()
        # `in_clause` is the one thing allowed to build an IN list: it adds only `?` and `,`.
        sql, values = in_clause(
            """
            SELECT DISTINCT d.track_id AS track_id
              FROM face_references r
              JOIN face_detections d ON d.crop_digest = r.crop_digest
             WHERE d.track_id IN (?*)
            """,
            track_ids,
        )
        found = {str(row["track_id"]) for row in await self._db.fetch_all(sql, tuple(values))}
        claimed, values = in_clause(
            "SELECT DISTINCT track_id FROM face_references WHERE track_id IN (?*)",
            track_ids,
        )
        found |= {str(row["track_id"]) for row in await self._db.fetch_all(claimed, tuple(values))}
        return found

    async def claim_references_in(self, person_id: str, asset_id: str, track_id: str) -> int:
        """Hand one file's orphaned references to the appearance that replaces them.

        A rescan cuts appearances under new ids, so the claim points at nothing while the file
        has not moved: hence `asset_id` on a reference. Called from `_name_again`. Only rows whose
        claim is DEAD; with two confirmed appearances of one person in a file, whichever is put
        back first claims the orphans.
        """
        # On the WRITE connection: a write through a borrowed read connection collides with the
        # writer.
        async with self._db.write() as connection:
            rows = list(
                await connection.execute_fetchall(
                    "UPDATE face_references SET track_id = ?, asset_id = ? "
                    " WHERE person_id = ? AND asset_id = ? "
                    "   AND (track_id IS NULL "
                    "        OR track_id NOT IN (SELECT id FROM face_tracks WHERE asset_id = ?)) "
                    "RETURNING id",
                    (track_id, asset_id, person_id, asset_id, asset_id),
                )
            )
        return len(rows)

    async def confirmed_reference_counts(self, *, recognizer: str) -> dict[str, int]:
        """How many of each person's references are faces people confirmed (`Origin.CONFIRMED`),
        among the pictures `recognizer` measured; absent rather than zero.

        What the two rules that let Sift act on its own names stand on (`tuning.GROUP_NAMING_
        REFERENCES`, `tuning.LEARNING_REFERENCES`): never Sift's own recognitions, which would let
        a name it added earn the right to add more.
        """
        rows = await self._db.fetch_all(
            "SELECT person_id AS person_id, COUNT(*) AS faces FROM face_references "
            "WHERE recognizer = ? AND origin = ? GROUP BY person_id",
            (recognizer, Origin.CONFIRMED.value),
        )
        return {str(row["person_id"]): int(row["faces"]) for row in rows}

    async def recognitions_to_learn(
        self, person_id: str, *, recognizer: str, at_least: float
    ) -> list[tuple[str, str, float]]:
        """Her faces Sift named on its own at `at_least` or above that may become references:
        `(face, file, confidence)`, the surest first, one per file.

        One per FILE, and none from a file that already gave her a reference of any origin: a
        video she is in throughout would otherwise be most of her description, and a rescan, which
        cuts the faces again under new ids, would file the same moment twice.
        """
        rows = await self._db.fetch_all(
            "SELECT t.id AS track_id, t.asset_id AS asset_id, t.confidence AS confidence "
            "FROM face_tracks AS t "
            "JOIN face_scans AS s ON s.asset_id = t.asset_id AND s.recognizer = ? "
            "WHERE t.person_id = ? AND t.attribution = ? AND t.confidence >= ? "
            "AND NOT EXISTS (SELECT 1 FROM face_references AS r "
            "                 WHERE r.person_id = t.person_id AND r.asset_id = t.asset_id) "
            "ORDER BY t.confidence DESC, t.id",
            (recognizer, person_id, Attribution.MATCHED.value, at_least),
        )
        chosen: dict[str, tuple[str, str, float]] = {}
        for row in rows:
            asset_id = str(row["asset_id"])
            if asset_id not in chosen:
                chosen[asset_id] = (str(row["track_id"]), asset_id, float(row["confidence"]))
        return list(chosen.values())

    async def add_recognized_references(
        self,
        person_id: str,
        faces: Sequence[tuple[str, str, Vector, float, bytes, int | None]],
        *,
        recognizer: str,
    ) -> dict[str, str]:
        """File faces Sift recognized as her references (`Origin.RECOGNIZED`), in one transaction.

        Each face is (appearance, file, vector, quality, picture, pixels). Returns the rows made,
        keyed by the appearance. A picture she already holds adds nothing, by the identity rule
        `add_reference` keeps. Her starters are not retired here: somebody known from enough
        confirmed faces to learn from has none in use.
        """
        stamp = now_ms()
        written: list[tuple[Path, bytes]] = []
        made: dict[str, str] = {}
        async with self._db.write() as connection:
            for track_id, asset_id, vector, quality, crop, pixels in faces:
                identity = _digest(crop)
                held = list(
                    await connection.execute_fetchall(
                        "SELECT id FROM face_references WHERE person_id = ? AND crop_digest = ?",
                        (person_id, identity),
                    )
                )
                if held:
                    continue
                reference_id = new_id()
                await connection.execute(
                    "INSERT INTO face_references (id, person_id, crop_path, crop_digest, embedding, "
                    "quality, origin, recognizer, pixels, track_id, asset_id, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        reference_id,
                        person_id,
                        self.relative(self.reference_root, reference_id),
                        identity,
                        recognize.pack(vector),
                        quality,
                        Origin.RECOGNIZED.value,
                        recognizer,
                        pixels,
                        track_id,
                        asset_id,
                        stamp,
                    ),
                )
                written.append((self.crop_path(self.reference_root, reference_id), crop))
                made[track_id] = reference_id
        if written:
            await asyncio.to_thread(_write_pictures, written)
        return made

    async def unlearn_recognitions(self, person_id: str, asset_ids: Sequence[str]) -> int:
        """Take out of her references every face Sift recognized in these files whose name no
        longer stands. How many went.

        A recognized reference rests on its name: once the face it came from is not named as her
        (an Undo, a No, or the face cut again by a rescan), the reference goes. A face still named
        as her keeps it. Rows only, as every removal of a reference: the picture files are the
        tidy-up's.
        """
        wanted = list(dict.fromkeys(asset_ids))
        gone = 0
        async with self._db.write() as connection:
            for start in range(0, len(wanted), MAX_PAGE_SIZE):
                sql, values = in_clause(
                    "DELETE FROM face_references WHERE person_id = ? AND origin = ? "
                    "AND asset_id IN (?*) "
                    "AND NOT EXISTS (SELECT 1 FROM face_tracks AS t "
                    "                 WHERE t.id = face_references.track_id "
                    "                   AND t.person_id = face_references.person_id "
                    "                   AND t.attribution IN (?, ?)) "
                    "RETURNING id",
                    wanted[start : start + MAX_PAGE_SIZE],
                )
                rows = list(
                    await connection.execute_fetchall(
                        sql,
                        (
                            person_id,
                            Origin.RECOGNIZED.value,
                            *values,
                            Attribution.MATCHED.value,
                            Attribution.CONFIRMED.value,
                        ),
                    )
                )
                gone += len(rows)
        return gone

    async def reference_count(self, person_id: str) -> int:
        """How many reference faces one person has. What matching's reliability rests on.

        Her OWN: a starter picture is not counted, in use or retired. It lends no strength (it may
        only make Sift ask), and counted here it would say Sift knows her from pictures of
        somebody a stash-box gave the same name. See `starters_of` for what her page says instead.
        """
        row = await self._db.fetch_one(
            "SELECT COUNT(*) AS n FROM face_references WHERE person_id = ? AND origin != 'seed'",
            (person_id,),
        )
        return 0 if row is None else int(row["n"])

    async def own_reference_ids(self, person_id: str) -> list[str]:
        """The ids of her own reference pictures: the ones `reference_count` counts, and the ones
        a match rests on (a starter only ever makes Sift ask)."""
        rows = await self._db.fetch_all(
            "SELECT id FROM face_references WHERE person_id = ? AND origin != 'seed' ORDER BY id",
            (person_id,),
        )
        return [str(row["id"]) for row in rows]

    async def pictures_in_use(self, person_id: str) -> int:
        """How many pictures Sift can compare a face with for her: her own, and the starters still
        in use. What a question about her rests on. A starter counts here where `reference_count`
        leaves it out, because a starter is exactly what lets Sift ASK."""
        row = await self._db.fetch_one(
            "SELECT COUNT(*) AS n FROM face_references "
            "WHERE person_id = ? AND (origin != 'seed' OR retired_at IS NULL)",
            (person_id,),
        )
        return 0 if row is None else int(row["n"])

    async def questions_sift_asked(self, person_id: str) -> list[tuple[str, str, float | None]]:
        """Her questions the arithmetic asked and nobody has answered: `(face, file, confidence)`.

        Only `AskedBy.MATCH`, read by the rule `asked` reads an older row by: a question a group
        asked was offered because somebody named that group, and one an Undo put back
        (`AskedBy.UNDONE`) is a person's "ask me instead". Neither is Sift's own guess.
        """
        rows = await self._db.fetch_all(
            "SELECT id, asset_id, confidence FROM face_tracks "
            "WHERE person_id = ? AND attribution = ? "
            "AND COALESCE(asked_by, CASE WHEN confidence IS NULL THEN 'group' ELSE 'match' END) = ? "
            "ORDER BY id",
            (person_id, Attribution.SUGGESTED.value, AskedBy.MATCH.value),
        )
        return [
            (
                str(row["id"]),
                str(row["asset_id"]),
                None if row["confidence"] is None else float(row["confidence"]),
            )
            for row in rows
        ]

    async def standing_recognitions(self, person_id: str, since: str) -> list[tuple[str, str]]:
        """Sift's own match runs about her still standing, made no earlier than decision `since`.

        `(receipt id, payload)`, oldest first: every receipt written under `IDENTIFIED_QUEUE` by
        nobody (a pass, not a press) that names her and has not been taken back. What an Undo of a
        naming reads to find the recognitions that rested on the pictures it took away; a run
        before the naming cannot have rested on them. Read from the record's own tables, which
        exist only where the record does: without them there is nothing standing to take back.
        """
        present = await self._db.fetch_all(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN "
            "('workbench_decisions', 'workbench_decision_subjects')",
            (),
        )
        if len(present) < 2:
            return []
        rows = await self._db.fetch_all(
            "SELECT d.id AS id, d.payload AS payload FROM workbench_decision_subjects AS s "
            "CROSS JOIN workbench_decisions AS d ON d.id = s.decision_id "
            "WHERE s.kind = 'person' AND s.subject_id = ? AND d.queue = ? "
            "AND d.user_id IS NULL AND d.reversed_at IS NULL "
            "AND d.decided_at >= COALESCE("
            "  (SELECT n.decided_at FROM workbench_decisions AS n WHERE n.id = ?), 0) "
            "ORDER BY d.decided_at, d.id",
            (person_id, IDENTIFIED_QUEUE, since),
        )
        return [(str(row["id"]), str(row["payload"])) for row in rows]

    async def starters_of(self, person_id: str) -> tuple[int, int, tuple[str, ...]]:
        """Her starter pictures: how many are in use, how many are retired, and where from.

        What her page marks ("Starter pictures from FansDB"), kept apart from `reference_count` so
        the strength of what Sift knows her by is never mixed with what it may only ask from.
        """
        rows = await self._db.fetch_all(
            "SELECT retired_at IS NULL AS in_use, COUNT(*) AS n, "
            "GROUP_CONCAT(DISTINCT COALESCE(source, '')) AS sources "
            "FROM face_references WHERE person_id = ? AND origin = 'seed' GROUP BY in_use",
            (person_id,),
        )
        in_use = sum(int(row["n"]) for row in rows if int(row["in_use"]))
        retired = sum(int(row["n"]) for row in rows if not int(row["in_use"]))
        sources = sorted(
            {one for row in rows for one in str(row["sources"] or "").split(",") if one}
        )
        return in_use, retired, tuple(sources)

    async def starter_boxes(self, person_ids: Sequence[str]) -> dict[str, tuple[str, ...]]:
        """The stash-boxes each of these People's starter pictures in use came from, by name,
        sorted; absent for somebody with none, or whose pictures were filed before a starter
        recorded its box. What "Compared with FansDB's pictures of her" names."""
        wanted = list(dict.fromkeys(person_ids))
        boxes: dict[str, set[str]] = {}
        for start in range(0, len(wanted), MAX_PAGE_SIZE):
            sql, values = in_clause(
                "SELECT DISTINCT person_id, source FROM face_references "
                "WHERE origin = 'seed' AND retired_at IS NULL AND source IS NOT NULL "
                "AND source != '' AND person_id IN (?*)",
                wanted[start : start + MAX_PAGE_SIZE],
            )
            for row in await self._db.fetch_all(sql, tuple(values)):
                boxes.setdefault(str(row["person_id"]), set()).add(str(row["source"]))
        return {person_id: tuple(sorted(names)) for person_id, names in boxes.items()}

    async def retire_starters(self, reference_ids: Sequence[str]) -> int:
        """Mark these starters retired where they are still in use. How many were.

        Starters only, and only in use: an id naming somebody's own reference, or one already
        retired, moves nothing, which is what lets an Undo be pressed twice.
        """
        wanted = list(dict.fromkeys(reference_ids))
        retired = 0
        stamp = now_ms()
        async with self._db.write() as connection:
            for start in range(0, len(wanted), MAX_PAGE_SIZE):
                sql, values = in_clause(
                    "UPDATE face_references SET retired_at = ? WHERE id IN (?*) "
                    "AND origin = 'seed' AND retired_at IS NULL RETURNING id",
                    wanted[start : start + MAX_PAGE_SIZE],
                )
                rows = list(await connection.execute_fetchall(sql, (stamp, *values)))
                retired += len(rows)
        return retired

    async def starters_in_use(self, person_id: str) -> list[str]:
        """Her starters still in use, by id. What a press reads before it files her own pictures,
        so its receipt can name the starters that press retired (`_RETIRE_STARTERS`)."""
        rows = await self._db.fetch_all(
            "SELECT id FROM face_references "
            "WHERE person_id = ? AND origin = 'seed' AND retired_at IS NULL ORDER BY id",
            (person_id,),
        )
        return [str(row["id"]) for row in rows]

    async def bring_back_starters(self, person_id: str, reference_ids: Sequence[str]) -> int:
        """Put these retired starters back in use, where she has no reference of her own. How many.

        The undo of the retirement her first own reference caused: once an Undo has taken her
        own pictures away again, starters are all Sift has of her, which is the state they were
        filed for. **Only while she has none of her own**, checked in the statement, so a picture
        filed since (or one the undo did not take) keeps them retired, which is the rule
        `_RETIRE_STARTERS` keeps. Only the ids the receipt names, which are the ones its press
        found in use and left retired.

        The bound, said plainly: a retired row does not say why it was retired, so a starter the
        Undo of its own run also reached, after the press had retired it, comes back with the rest.
        """
        wanted = list(dict.fromkeys(reference_ids))
        back = 0
        async with self._db.write() as connection:
            for start in range(0, len(wanted), MAX_PAGE_SIZE):
                sql, values = in_clause(
                    "UPDATE face_references SET retired_at = NULL WHERE person_id = ? "
                    "AND origin = 'seed' AND retired_at IS NOT NULL AND id IN (?*) "
                    "AND NOT EXISTS (SELECT 1 FROM face_references AS own "
                    "WHERE own.person_id = ? AND own.origin != 'seed') RETURNING id",
                    wanted[start : start + MAX_PAGE_SIZE],
                )
                rows = list(await connection.execute_fetchall(sql, (person_id, *values, person_id)))
                back += len(rows)
        return back

    async def without_references(self, person_ids: Sequence[str]) -> list[str]:
        """Of these People, the ones with no reference row of any kind, in the order given.

        Who starter pictures are for. ANY row counts, a retired starter included: a starter that
        was retired for a "no" is the memory of that answer, and somebody who has one is not
        offered starters again: the same pictures would only ask the same wrong questions.
        """
        wanted = list(dict.fromkeys(person_ids))
        if not wanted:
            return []
        held: set[str] = set()
        for start in range(0, len(wanted), MAX_PAGE_SIZE):
            sql, values = in_clause(
                "SELECT DISTINCT person_id FROM face_references WHERE person_id IN (?*)",
                wanted[start : start + MAX_PAGE_SIZE],
            )
            held |= {str(row["person_id"]) for row in await self._db.fetch_all(sql, tuple(values))}
        return [person_id for person_id in wanted if person_id not in held]

    async def starters_refused(self, person_ids: Sequence[str], *, recognizer: str) -> set[str]:
        """Of these People, the ones every one of whose stash-box pictures this model refused.

        See `schema._CREATE_STARTER_REFUSALS`. A row written under another model does not count.
        """
        wanted = list(dict.fromkeys(person_ids))
        refused: set[str] = set()
        for start in range(0, len(wanted), MAX_PAGE_SIZE):
            sql, values = in_clause(
                "SELECT person_id FROM face_starter_refusals "
                "WHERE recognizer = ? AND person_id IN (?*)",
                wanted[start : start + MAX_PAGE_SIZE],
            )
            rows = await self._db.fetch_all(sql, (recognizer, *values))
            refused |= {str(row["person_id"]) for row in rows}
        return refused

    async def remember_starters_refused(
        self, person_id: str, *, recognizer: str, pictures: int
    ) -> None:
        """Write down that every stash-box picture of this person was refused, by this model."""
        async with self._db.write() as connection:
            await connection.execute(
                "INSERT INTO face_starter_refusals (person_id, recognizer, pictures, refused_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(person_id) DO UPDATE SET "
                "recognizer = excluded.recognizer, pictures = excluded.pictures, "
                "refused_at = excluded.refused_at",
                (person_id, recognizer, pictures, now_ms()),
            )

    async def forget_starters_refused(self, person_id: str) -> None:
        """Take the note away: something of theirs was filed, so they were not refused."""
        async with self._db.write() as connection:
            await connection.execute(
                "DELETE FROM face_starter_refusals WHERE person_id = ?", (person_id,)
            )

    async def remove_references_from_track(self, person_id: str, track_id: str) -> int:
        """Take back the reference photos one appearance gave a person, as taking a name off must.

        Matched on the picture, recomputing the identity from this track's crops, since a
        reference is keyed by its picture: a row filed by hand from the same picture is the same
        row and goes too. An Undo whose receipt names the rows its press created uses
        `remove_references_by_id` instead.
        """
        rows = await self._db.fetch_all(
            "SELECT crop_path FROM face_detections WHERE track_id = ?", (track_id,)
        )

        # The pictures are read first and the deletes run together on the WRITE connection: a
        # statement that writes through a borrowed read connection collides with the writer and
        # fails with "database is locked".
        def read() -> list[str]:
            paths = self._resolve_all(str(row["crop_path"] or "") for row in rows)
            return [_digest(picture) for picture in _read_pictures(paths)]

        digests = await asyncio.to_thread(read)
        if not digests:
            return 0
        gone = 0
        async with self._db.write() as connection:
            for digest in digests:
                removed = list(
                    await connection.execute_fetchall(
                        "DELETE FROM face_references WHERE person_id = ? AND crop_digest = ? "
                        "RETURNING id",
                        (person_id, digest),
                    )
                )
                gone += len(removed)
        return gone

    async def remove_references_by_id(self, person_id: str, reference_ids: Sequence[str]) -> int:
        """Delete these references of one person, by the ids the decision that filed them kept.

        What an Undo removes when its receipt names the rows its press CREATED. Removing by the
        pictures instead (`remove_references_from_track`) also reaches a row the press only met:
        the same picture filed earlier by hand is one row, since a reference is keyed by its
        picture, and the press claimed it rather than filing it. That row is the earlier
        decision's, so it stays.

        Rows only, as the removal by pictures does: the picture files are the tidy-up's.
        """
        wanted = list(dict.fromkeys(reference_ids))
        gone = 0
        async with self._db.write() as connection:
            for start in range(0, len(wanted), MAX_PAGE_SIZE):
                sql, values = in_clause(
                    "DELETE FROM face_references WHERE person_id = ? AND id IN (?*) RETURNING id",
                    wanted[start : start + MAX_PAGE_SIZE],
                )
                rows = list(await connection.execute_fetchall(sql, (person_id, *values)))
                gone += len(rows)
        return gone
