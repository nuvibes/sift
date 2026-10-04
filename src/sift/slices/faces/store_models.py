# SPDX-License-Identifier: AGPL-3.0-or-later
"""The models behind the descriptions: describing again under a new one, the packs and weights
installed, and forgetting everything."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from sift.kernel.db import Connection, Row, in_clause
from sift.kernel.ids import new_id
from sift.kernel.ledger import Actor
from sift.slices.faces.forget_all import Progress, Touched, forget_all
from sift.slices.faces.models import (
    Attribution,
)
from sift.slices.faces.receipts import FINGERPRINTS_QUEUE
from sift.slices.faces.store_pictures import (
    PicturesStore,
    _remove_pictures,
)
from sift.slices.faces.store_records import (
    _STAMP_PILE,
    Remeasured,
    now_ms,
)

#: The tables that remember a decision by a copy of the face's description, so the decision
#: outlives a rescan; each is restamped by the same statement when a face is measured again.
_REMEMBERED_DESCRIPTIONS: tuple[str, ...] = (
    "UPDATE face_confirmations SET embedding = ?, recognizer = ? "
    "WHERE asset_id = ? AND embedding = ?",
    "UPDATE face_ignored SET embedding = ?, recognizer = ? WHERE asset_id = ? AND embedding = ?",
    "UPDATE face_grouping SET embedding = ?, recognizer = ? WHERE asset_id = ? AND embedding = ?",
    "UPDATE face_rejected SET embedding = ?, recognizer = ? WHERE asset_id = ? AND embedding = ?",
)


class ModelsStore(PicturesStore):
    """Describing again under a new model; packs, weights, and forgetting everything."""

    # --- measuring again, with a different model --------------------------------------------

    async def measured_by_others(self, recognizer: str, *, limit: int) -> list[str]:
        """Files whose faces were described by another model, oldest scan first: the remeasure
        pass's walk, where a restamped file leaves the set so the same query is the next page."""
        rows = await self._db.fetch_all(
            "SELECT asset_id FROM face_scans WHERE recognizer != ? "
            "ORDER BY scanned_at, asset_id LIMIT ?",
            (recognizer, limit),
        )
        return [str(row["asset_id"]) for row in rows]

    async def count_measured_by_others(self, recognizer: str) -> int:
        """How many files' faces are still described by a model other than this one."""
        row = await self._db.fetch_one(
            "SELECT COUNT(*) AS n FROM face_scans WHERE recognizer != ?", (recognizer,)
        )
        return 0 if row is None else int(row["n"])

    async def references_measured_by_others(
        self, recognizer: str, *, limit: int
    ) -> list[tuple[str, str]]:
        """Reference faces described by another model that still have their picture, and so
        can be described again: id and stored picture path."""
        rows = await self._db.fetch_all(
            "SELECT id, crop_path FROM face_references "
            "WHERE recognizer != ? AND crop_path IS NOT NULL ORDER BY id LIMIT ?",
            (recognizer, limit),
        )
        return [(str(row["id"]), str(row["crop_path"])) for row in rows]

    async def count_unmeasurable_references(self, recognizer: str) -> int:
        """Reference faces described by another model that arrived as numbers alone: they can
        never be measured again, so the count is reported for whoever imported the pack."""
        row = await self._db.fetch_one(
            "SELECT COUNT(*) AS n FROM face_references WHERE recognizer != ? AND crop_path IS NULL",
            (recognizer,),
        )
        return 0 if row is None else int(row["n"])

    async def detections_of_file(self, asset_id: str) -> list[tuple[str, str, bytes]]:
        """Every stored face of one file: id, stored picture path and the description as held."""
        rows = await self._db.fetch_all(
            "SELECT d.id AS id, d.crop_path AS crop_path, d.embedding AS embedding "
            "FROM face_detections AS d JOIN face_tracks AS t ON t.id = d.track_id "
            "WHERE t.asset_id = ? ORDER BY d.id",
            (asset_id,),
        )
        return [(str(row["id"]), str(row["crop_path"]), bytes(row["embedding"])) for row in rows]

    async def remeasure_file(
        self,
        asset_id: str,
        measured: Sequence[Remeasured],
        *,
        gone: Sequence[str],
        recognizer: str,
    ) -> int:
        """Write one file's faces as the new model describes them, and carry every decision.

        One transaction. Each description and its strength is replaced, and the memories that key
        a decision by a description's copy are given the new one: after this the old numbers are
        gone. The scan row is restamped, taking the file out of `measured_by_others`.

        A `matched` or `suggested` attribution compared old numbers and is undone (the re-match
        after the pass only reads faces nobody is attached to); `confirmed` stays. A face whose
        picture is gone (`gone`) is deleted, with its appearance if it was the last face of it;
        returns how many appearances went that way.
        """
        removed = 0
        async with self._db.write() as connection:
            for face in measured:
                await connection.execute(
                    "UPDATE face_detections SET embedding = ?, strength = ? WHERE id = ?",
                    (face.embedding, face.strength, face.id),
                )
                for table in _REMEMBERED_DESCRIPTIONS:
                    await connection.execute(
                        table, (face.embedding, recognizer, asset_id, face.previous)
                    )
            if gone:
                sql, params = in_clause("DELETE FROM face_detections WHERE id IN (?*)", list(gone))
                await connection.execute(sql, params)
                emptied = list(
                    await connection.execute_fetchall(
                        "DELETE FROM face_tracks WHERE asset_id = ? AND NOT EXISTS ("
                        "  SELECT 1 FROM face_detections AS d WHERE d.track_id = face_tracks.id"
                        ") RETURNING id",
                        (asset_id,),
                    )
                )
                removed = len(emptied)
            await connection.execute(
                "UPDATE face_tracks SET person_id = NULL, confidence = NULL, attribution = NULL, "
                "attributed_at = NULL WHERE asset_id = ? AND attribution IN (?, ?)",
                (asset_id, Attribution.MATCHED.value, Attribution.SUGGESTED.value),
            )
            await connection.execute(
                "UPDATE face_scans SET recognizer = ? WHERE asset_id = ?", (recognizer, asset_id)
            )
            # Every pile holding one of these faces is stamped again and answers null until the
            # sweep reaches its other files: a middle over two models describes nobody, and the
            # regroup after the sweep rebuilds it.
            piles = await connection.execute_fetchall(
                "SELECT DISTINCT pile_id FROM face_tracks "
                "WHERE asset_id = ? AND pile_id IS NOT NULL",
                (asset_id,),
            )
            for pile in piles:
                await connection.execute(_STAMP_PILE, (str(pile["pile_id"]),))
        return removed

    async def forget_reference(self, reference_id: str) -> None:
        """Delete one reference whose picture has gone: nothing to show or to match with."""
        await self._db.execute("DELETE FROM face_references WHERE id = ?", (reference_id,))

    async def remeasure_reference(
        self, reference_id: str, *, embedding: bytes, recognizer: str
    ) -> None:
        """Write one reference face as the new model describes it, and restamp it."""
        await self._db.execute(
            "UPDATE face_references SET embedding = ?, recognizer = ? WHERE id = ?",
            (embedding, recognizer, reference_id),
        )

    async def remove_references(
        self, *, person_id: str | None = None, pack_id: str | None = None
    ) -> int:
        """Delete references and their pictures; by pack, so a hand-added reference survives."""
        if person_id is not None:
            rows = await self._db.fetch_all(
                "SELECT id, crop_path FROM face_references WHERE person_id = ?", (person_id,)
            )
            await self._db.execute("DELETE FROM face_references WHERE person_id = ?", (person_id,))
        elif pack_id is not None:
            rows = await self._db.fetch_all(
                "SELECT id, crop_path FROM face_references WHERE pack_id = ?", (pack_id,)
            )
            await self._db.execute("DELETE FROM face_references WHERE pack_id = ?", (pack_id,))
        else:
            raise ValueError("removing references needs a person or a pack to remove them for")

        stored = [str(row["crop_path"] or "") for row in rows]
        await asyncio.to_thread(lambda: _remove_pictures(self._resolve_all(stored)))
        return len(rows)

    # --- packs -------------------------------------------------------------------------------

    async def pack_by_name(self, name: str) -> Row | None:
        return await self._db.fetch_one(
            "SELECT * FROM face_packs WHERE name = ? COLLATE NOCASE", (name,)
        )

    async def record_pack(
        self, *, name: str, version: str, recognizer: str, dimension: int, digest: str
    ) -> str:
        """Record a pack, keyed by name, and hand back its id.

        **The same pack again** (same digest) changes nothing. **A later edition** (same name,
        other contents) replaces what the earlier one brought. Its references go **before** the
        pack row: the database removes them with the pack, and the pictures behind them, which only
        this side can delete, would stay on the disk for ever.
        """
        existing = await self.pack_by_name(name)
        if existing is not None:
            if str(existing["digest"]) == digest:
                return str(existing["id"])
            await self.remove_references(pack_id=str(existing["id"]))
            # The faces its entries held go the same way, for the same reason.
            held = await self._db.fetch_all(
                "SELECT f.crop_path FROM pack_entry_faces f JOIN pack_entries e"
                " ON e.id = f.entry_id WHERE e.pack_id = ?",
                (str(existing["id"]),),
            )
            stored = [str(row["crop_path"] or "") for row in held]
            await asyncio.to_thread(lambda: _remove_pictures(self._resolve_all(stored)))
            await self._db.execute("DELETE FROM face_packs WHERE id = ?", (str(existing["id"]),))
        pack_id = new_id()
        await self._db.execute(
            "INSERT INTO face_packs (id, name, version, recognizer, dimension, digest, "
            "installed_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (pack_id, name, version, recognizer, dimension, digest, now_ms()),
        )
        return pack_id

    #: The standing entry that holds what a FOLDER import could not place: one row to hang entries
    #: off, so `UNIQUE(pack_id, name)` merges two folders for the same person into one entry.
    FOLDER_IMPORTS = "Folders you imported"

    async def folder_import_pack(self, recognizer: str) -> str:
        """The standing row folder imports hang their unplaced people off. Made once, then found;
        never replaced, since what it holds was read off this machine and has no new edition."""
        existing = await self.pack_by_name(self.FOLDER_IMPORTS)
        if existing is not None:
            return str(existing["id"])
        pack_id = new_id()
        await self._db.execute(
            "INSERT INTO face_packs (id, name, version, recognizer, dimension, digest, "
            "installed_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (pack_id, self.FOLDER_IMPORTS, "", recognizer, 0, "", now_ms()),
        )
        return pack_id

    #: How a swap names the packs it holds People under: those wait on the person's own page, never
    #: in the list of entries waiting for a matching face.
    _SWAPPED = "swap "

    async def waiting_entries(self) -> list[Row]:
        """Everybody a facial fingerprints file or a folder brought who is nobody here yet, newest
        first: the entry, its name, how many faces it holds, the file or folder it came from
        (`source`: the entry's own folder where a folder import gave one, the pack's name
        otherwise), how many confirmed faces the file said they had (`confirmed`, None where it
        said nothing) and when it was taken in."""
        return await self._db.fetch_all(
            "SELECT e.id AS entry_id, e.name AS name, e.created_at AS added_at,"
            " COALESCE(e.source, p.name) AS source, e.confirmed AS confirmed,"
            " (SELECT COUNT(*) FROM pack_entry_faces f WHERE f.entry_id = e.id) AS faces"
            " FROM pack_entries e JOIN face_packs p ON p.id = e.pack_id"
            " WHERE e.claimed_person_id IS NULL AND substr(p.name, 1, ?) != ?"
            " ORDER BY e.id DESC",
            (len(self._SWAPPED), self._SWAPPED),
        )

    async def remove_entry_on(self, connection: Connection, entry_id: str) -> Row | None:
        """Forget one waiting entry and the faces under it, inside the caller's write. Answers the
        entry as it stood with its source and the pictures to delete once the write has landed
        (`crops`), or None where it is gone or already somebody's."""
        rows = list(
            await connection.execute_fetchall(
                "SELECT e.id AS entry_id, e.name AS name, COALESCE(e.source, p.name) AS source,"
                " (SELECT json_group_array(f.crop_path) FROM pack_entry_faces f"
                "  WHERE f.entry_id = e.id AND f.crop_path IS NOT NULL) AS crops"
                " FROM pack_entries e JOIN face_packs p ON p.id = e.pack_id"
                " WHERE e.id = ? AND e.claimed_person_id IS NULL",
                (entry_id,),
            )
        )
        if not rows:
            return None
        await connection.execute("DELETE FROM pack_entries WHERE id = ?", (entry_id,))
        return rows[0]

    async def remove_entry_pictures(self, crops: Sequence[str]) -> None:
        """Delete the pictures a removed entry held, after the write that forgot them."""
        await asyncio.to_thread(lambda: _remove_pictures(self._resolve_all(crops)))

    async def created_from_fingerprints(
        self, person_ids: Sequence[str], *, act: str
    ) -> dict[str, str]:
        """For each of these People created from facial fingerprints (a receipt whose act is
        `act`), the file or folder their History line names (`from`), read from the record itself
        and only while it stands. Empty where the record's tables are absent."""
        present = await self._db.fetch_all(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN "
            "('workbench_decisions', 'workbench_decision_subjects')",
            (),
        )
        if len(present) < 2 or not person_ids:
            return {}
        sql, params = in_clause(
            "SELECT s.subject_id AS person_id, json_extract(d.payload, '$.from') AS source"
            " FROM workbench_decision_subjects s"
            " CROSS JOIN workbench_decisions d ON d.id = s.decision_id"
            " WHERE s.kind = 'person' AND s.subject_id IN (?*) AND d.queue = ?"
            " AND d.reversed_at IS NULL AND json_extract(d.payload, '$.act') = ?"
            " AND json_extract(d.payload, '$.person_id') = s.subject_id"
            " ORDER BY d.decided_at",
            list(person_ids),
        )
        rows = await self._db.fetch_all(sql, (*params, FINGERPRINTS_QUEUE, act))
        return {str(row["person_id"]): str(row["source"]) for row in rows if row["source"]}

    async def pack_recognizer(self, pack_id: str) -> str | None:
        row = await self._db.fetch_one("SELECT recognizer FROM face_packs WHERE id = ?", (pack_id,))
        return None if row is None else str(row["recognizer"])

    async def packs(self) -> list[Row]:
        return await self._db.fetch_all("SELECT * FROM face_packs ORDER BY name", ())

    # --- installed models --------------------------------------------------------------------

    async def record_weight(self, weight_id: str, revision: str, digest: str, size: int) -> None:
        await self._db.execute(
            "INSERT INTO face_weights (id, revision, digest, size_bytes, installed_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET revision = excluded.revision, "
            "digest = excluded.digest, size_bytes = excluded.size_bytes, "
            "installed_at = excluded.installed_at",
            (weight_id, revision, digest, size, now_ms()),
        )

    async def weights(self) -> list[Row]:
        return await self._db.fetch_all("SELECT * FROM face_weights ORDER BY id", ())

    # --- clearing out --------------------------------------------------------------------------

    async def forget_everything(
        self,
        *,
        actor: Actor,
        touched: Touched | None = None,
        progress: Progress | None = None,
    ) -> int:
        """Delete every face, every reference and every picture behind them. See `forget_all`."""
        return await forget_all(
            self._db,
            actor=actor,
            roots=(self.detected_root, self.reference_root),
            touched=touched,
            progress=progress,
        )
