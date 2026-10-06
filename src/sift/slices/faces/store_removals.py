# SPDX-License-Identifier: AGPL-3.0-or-later
"""Taking things back: faces removed, refused or filed off a person, and the reconciling of a
file's People with its faces after any of them."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from pathlib import Path

from sift.kernel.access import (
    ObjectType,
    bump_stamps_for_object,
    clear_refusal_on,
    refuse_person_on,
)
from sift.kernel.audience import EVERY_ADMIN, NOBODY
from sift.kernel.changes import About, announce, who_may_see_a_file
from sift.kernel.db import Connection, Row, in_clause
from sift.kernel.ids import new_id
from sift.kernel.vocabulary import (
    VIA_FILENAME,
    VIA_FOLDER,
    VIA_METADATA,
    VIA_WATERMARK,
)
from sift.slices.faces import recognize
from sift.slices.faces.models import (
    Attribution,
    Vector,
)
from sift.slices.faces.store_pictures import (
    _remove_pictures,
)
from sift.slices.faces.store_records import (
    _NAMES_THE_FILE,
    _PILES_PER_READ,
    _RETIRE_STARTERS,
    SOURCE_STASH_BOX,
    FiledFace,
    FiledOff,
    _track,
    now_ms,
)
from sift.slices.faces.store_tracks import TracksStore


class RemovalsStore(TracksStore):
    """Taking things back, and a file's People reconciled with its faces after."""

    #: The words `asset_people.source` carries for a name a PASS of Sift's own put on a file: its
    #: own tuple, since `vocabulary.MADE_VIAS` spells the stash-box `stash`. `handle` (somebody
    #: joined a username by hand) and NULL (a person's hand or this slice) are left out.
    FILED_BY_A_PASS = (VIA_FOLDER, VIA_FILENAME, SOURCE_STASH_BOX, VIA_METADATA, VIA_WATERMARK)

    async def filed_but_unrecognised(self, recognizer: str, *, most: int) -> list[FiledFace]:
        """Files a pass filed under somebody whose ONE face is named as somebody ELSE.

        Named means Sift recognized it (`MATCHED`) or somebody confirmed it (`CONFIRMED`) as a
        different person: that is evidence against the filing. A face that matches nobody is not,
        since a covered or poorly lit face matches nobody whoever it is, and a question is not a
        name. **Exactly one face**: with two, the other may be her. Narrowed on the RECOGNIZER,
        since another model's names were made against other numbers. A face already refused for
        her is left out. Bounded by `FILED_FACES_AT_MOST`.
        """
        # `in_clause` answers with the list's own values alone, so the other bound values are
        # spelled out beside it.
        sql, _ = in_clause(
            "SELECT t.*, ap.person_id AS filed_person_id, ap.source AS filed_source "
            "FROM asset_people AS ap "
            "JOIN face_scans AS s ON s.asset_id = ap.asset_id AND s.recognizer = ? "
            "JOIN face_tracks AS t ON t.asset_id = ap.asset_id "
            "WHERE ap.source IN (?*) "
            "  AND t.person_id IS NOT NULL AND t.person_id != ap.person_id "
            "  AND t.attribution IN (?, ?) "
            "  AND (SELECT COUNT(*) FROM face_tracks AS o WHERE o.asset_id = ap.asset_id) = 1 "
            "  AND NOT EXISTS (SELECT 1 FROM face_rejections AS x "
            "                   WHERE x.track_id = t.id AND x.person_id = ap.person_id) "
            "ORDER BY t.id, ap.person_id LIMIT ?",
            self.FILED_BY_A_PASS,
        )
        rows = await self._db.fetch_all(
            sql,
            (
                recognizer,
                *self.FILED_BY_A_PASS,
                Attribution.MATCHED.value,
                Attribution.CONFIRMED.value,
                most,
            ),
        )
        return [
            FiledFace(
                track=_track(row),
                person_id=str(row["filed_person_id"]),
                source=str(row["filed_source"]),
            )
            for row in rows
        ]

    async def take_filed_off(self, person_id: str, asset_ids: Sequence[str]) -> list[FiledOff]:
        """Take a person off files a PASS filed her under, and remember that somebody did.

        Only a pass's filing (`FILED_BY_A_PASS`). Each row is remembered as refused in the same
        transaction (`refuse_person_on`), or a folder read again would put her straight back.
        Answers the rows as they were, for an Undo (`put_filed_back`).
        """
        wanted = list(dict.fromkeys(asset_ids))
        taken: list[FiledOff] = []
        if not wanted:
            return taken
        now = int(time.time())
        async with self._db.write() as connection:
            for start in range(0, len(wanted), _PILES_PER_READ):
                sql, _ = in_clause(
                    "SELECT asset_id, source, decided_at, box_id FROM asset_people "
                    "WHERE person_id = ? AND asset_id IN (?*) ORDER BY asset_id",
                    wanted[start : start + _PILES_PER_READ],
                )
                rows = await connection.execute_fetchall(
                    sql, (person_id, *wanted[start : start + _PILES_PER_READ])
                )
                for row in rows:
                    source = row["source"]
                    if source not in self.FILED_BY_A_PASS:
                        continue
                    asset_id = str(row["asset_id"])
                    await connection.execute(
                        "DELETE FROM asset_people WHERE asset_id = ? AND person_id = ?",
                        (asset_id, person_id),
                    )
                    await refuse_person_on(
                        connection, asset_id=asset_id, person_id=person_id, now=now
                    )
                    decided = row["decided_at"]
                    box = row["box_id"]
                    taken.append(
                        FiledOff(
                            asset_id=asset_id,
                            source=str(source),
                            decided_at=None if decided is None else int(decided),
                            box_id=None if box is None else str(box),
                        )
                    )
            if taken:
                # A person is what a share or a hide is attached to, so who is on a file moves
                # what somebody may see. In the same transaction, as every filing does.
                told = await bump_stamps_for_object(connection, ObjectType.PERSON, person_id)
                announce(told | await who_may_see_a_file(connection), About.LIBRARY)
        return taken

    async def put_filed_back(self, person_id: str, rows: Sequence[FiledOff]) -> list[str]:
        """Put back what `take_filed_off` took, each row as it was. Answers the files it reached.

        A file somebody has since put her on again keeps the row it has now (`DO NOTHING`): the
        later answer stands. The refusal written beside each row goes, since the decision it
        remembered is the one being taken back.
        """
        back: list[str] = []
        if not rows:
            return back
        async with self._db.write() as connection:
            for row in rows:
                cursor = await connection.execute(
                    "INSERT INTO asset_people (asset_id, person_id, source, decided_at, box_id) "
                    # An existence guard on the file, not a read of it: nothing of the row comes back.
                    "SELECT ?, ?, ?, ?, ? WHERE EXISTS (SELECT 1 FROM assets WHERE id = ?) "  # nosemgrep: sift-no-asset-sql-outside-kernel
                    "ON CONFLICT(asset_id, person_id) DO NOTHING",
                    (
                        row.asset_id,
                        person_id,
                        row.source,
                        row.decided_at,
                        row.box_id,
                        row.asset_id,
                    ),
                )
                await clear_refusal_on(connection, asset_id=row.asset_id, person_id=person_id)
                if cursor.rowcount > 0:
                    back.append(row.asset_id)
            if back:
                told = await bump_stamps_for_object(connection, ObjectType.PERSON, person_id)
                announce(told | await who_may_see_a_file(connection), About.LIBRARY)
        return back

    # --- faces somebody removed --------------------------------------------------------------

    async def remove_faces(self, track_ids: Sequence[str]) -> int:
        """Take these appearances away, and remember enough that a rescan does not raise them again:
        one description per appearance (its best), its file and what its quality measured. The
        pictures go too. Returns how many appearances went."""
        if not track_ids:
            return 0
        stamp = now_ms()
        pictures: list[Path] = []
        removed = 0

        async with self._db.write() as connection:
            sql, params = in_clause(
                "SELECT t.id AS track_id, t.asset_id AS asset_id, d.embedding AS embedding, "
                "d.quality AS quality, d.pixels AS pixels, d.sharpness AS sharpness, "
                "d.frontality AS frontality, d.crop_path AS crop_path, "
                # The recognizer is read from the scan, not a loaded model: removing a face must not
                # need the models, which may have been removed.
                "COALESCE(s.recognizer, '') AS recognizer "
                "FROM face_tracks AS t JOIN face_detections AS d ON d.track_id = t.id "
                "LEFT JOIN face_scans AS s ON s.asset_id = t.asset_id "
                "WHERE t.id IN (?*) ORDER BY t.id, d.quality DESC, d.id",
                list(track_ids),
            )
            rows = list(await connection.execute_fetchall(sql, params))

            seen: set[str] = set()
            stored: list[str] = []
            for row in rows:
                track_id = str(row["track_id"])
                # Held as written and turned into real paths once, below. Resolving here would ask
                # the disk a question per row while the write connection is held.
                stored.append(str(row["crop_path"]))
                pictures.append(self.cover_path(track_id))
                if track_id in seen:
                    continue
                seen.add(track_id)
                await connection.execute(
                    "INSERT INTO face_removals (id, asset_id, embedding, recognizer, quality, "
                    "pixels, sharpness, frontality, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        new_id(),
                        str(row["asset_id"]),
                        bytes(row["embedding"]),
                        str(row["recognizer"]),
                        row["quality"],
                        row["pixels"],
                        row["sharpness"],
                        row["frontality"],
                        stamp,
                    ),
                )

            # Resolved inside the transaction, so `resolve` refusing a path can abandon the delete.
            pictures.extend(await asyncio.to_thread(self._resolve_all, stored))

            gone, params = in_clause("DELETE FROM face_tracks WHERE id IN (?*)", list(track_ids))
            cursor = await connection.execute(gone, params)
            removed = max(cursor.rowcount, 0)
            # Faces > Groups and a file's faces on every admin's other tabs lose these faces.
            announce(EVERY_ADMIN, About.LIBRARY)

        await asyncio.to_thread(_remove_pictures, pictures)
        return removed

    async def assets_of(self, track_ids: Sequence[str]) -> list[str]:
        """Which files these appearances are in, each once: asked before a removal, whose rows
        answer it."""
        if not track_ids:
            return []
        sql, params = in_clause(
            "SELECT DISTINCT asset_id FROM face_tracks WHERE id IN (?*)", list(track_ids)
        )
        rows = await self._db.fetch_all(sql, params)
        return [str(row["asset_id"]) for row in rows]

    async def removals_for(self, asset_id: str, recognizer: str) -> list[Vector]:
        """The descriptions of the faces removed from one file, by the model named.

        Per file: two people can look alike within any useful threshold. Per model: a removal is a
        description with no picture to measure again, so a face removed under one model can come
        back after a model change and must be removed again.
        """
        rows = await self._db.fetch_all(
            "SELECT embedding FROM face_removals WHERE asset_id = ? AND recognizer = ?",
            (asset_id, recognizer),
        )
        return [recognize.unpack(bytes(row["embedding"])) for row in rows]

    async def removal_measurements(
        self,
    ) -> list[tuple[float, int | None, float | None, float | None]]:
        """What every removed face measured: its score and the three it came from. Evidence for a
        person placing the quality bar; nothing decides anything on it."""
        rows = await self._db.fetch_all(
            "SELECT quality, pixels, sharpness, frontality FROM face_removals ORDER BY quality", ()
        )
        return [
            (
                float(row["quality"]),
                None if row["pixels"] is None else int(row["pixels"]),
                None if row["sharpness"] is None else float(row["sharpness"]),
                None if row["frontality"] is None else float(row["frontality"]),
            )
            for row in rows
        ]

    # --- what a person decided -------------------------------------------------------------

    async def reject(self, track_id: str, person_id: str) -> bool:
        """Record that this face is not that person, and take the attribution off if it was there.

        Never expires. A face no longer there (a file deleted under an open screen) is not
        recorded and not an error.
        """
        if await self.track(track_id) is None:
            return False
        when = now_ms()
        # One transaction for the refusal, its memory and the name coming off.
        async with self._db.write() as connection:
            written = list(
                await connection.execute_fetchall(
                    "INSERT INTO face_rejections (track_id, person_id, created_at) "
                    "VALUES (?, ?, ?) ON CONFLICT(track_id, person_id) DO NOTHING RETURNING track_id",
                    (track_id, person_id, when),
                )
            )
            # REMEMBERED BY DESCRIPTION, as a name is (`remember_confirmation`), so a rescan puts it
            # back; only a refusal this press wrote, or one decision would hold two memories.
            if written:
                faces = list(
                    await connection.execute_fetchall(
                        "SELECT t.asset_id AS asset_id, d.embedding AS embedding "
                        "FROM face_tracks AS t JOIN face_detections AS d ON d.track_id = t.id "
                        "WHERE t.id = ? ORDER BY d.quality DESC, d.id LIMIT 1",
                        (track_id,),
                    )
                )
                if faces:
                    asset_id = str(faces[0]["asset_id"])
                    await connection.execute(
                        "INSERT INTO face_rejected "
                        "(id, asset_id, person_id, embedding, recognizer, created_at) "
                        "VALUES (?, ?, ?, ?, "
                        "(SELECT s.recognizer FROM face_scans AS s WHERE s.asset_id = ?), ?)",
                        (
                            new_id(),
                            asset_id,
                            person_id,
                            bytes(faces[0]["embedding"]),
                            asset_id,
                            when,
                        ),
                    )
            await connection.execute(
                "UPDATE face_tracks SET person_id = NULL, confidence = NULL, attribution = NULL "
                "WHERE id = ? AND person_id = ?",
                (track_id, person_id),
            )
            # A "no" about somebody Sift knew only by STARTERS retires them in this transaction: the
            # question came from those pictures, linked on a name. Only a refusal written now.
            if written:
                await connection.execute(_RETIRE_STARTERS, (when, person_id))
            # The question this answered leaves Faces on every admin's other tabs, named or not.
            announce(EVERY_ADMIN, About.LIBRARY)
        return True

    async def rejections_for(self, asset_id: str) -> list[tuple[str, int, Vector]]:
        """Who each face in this file was refused as, by description: the person, when, the face;
        only this model's descriptions, as `confirmations_for` explains."""
        rows = await self._db.fetch_all(
            "SELECT person_id, created_at, embedding FROM face_rejected "
            "WHERE asset_id = ? AND recognizer = (SELECT s.recognizer FROM face_scans AS s WHERE s.asset_id = ?)",
            (asset_id, asset_id),
        )
        return [
            (
                str(row["person_id"]),
                int(row["created_at"]),
                recognize.unpack(bytes(row["embedding"])),
            )
            for row in rows
        ]

    async def reject_again(self, track_id: str, person_id: str, created_at: int) -> bool:
        """Put a remembered refusal back on a freshly found face. True when it was not there.

        The refusal alone; the name is not touched. Carries the moment it was first made, so a
        history does not count it again on the day of the scan.
        """
        async with self._db.write() as connection:
            written = list(
                await connection.execute_fetchall(
                    "INSERT INTO face_rejections (track_id, person_id, created_at) "
                    "VALUES (?, ?, ?) ON CONFLICT(track_id, person_id) DO NOTHING RETURNING track_id",
                    (track_id, person_id, created_at),
                )
            )
        return bool(written)

    # --- the names this feature put on a file ------------------------------------------------

    async def reconcile_people(self, asset_id: str) -> tuple[list[str], list[str]]:
        """Make the file's list of People agree with the faces found in it. Reports what changed.

        The one place the feature writes the shared table everything else reads. **A name is only
        taken back if this feature put it there**, recorded as a claim beside the faces when the
        row is created; the insert's own result decides the claim (`DO NOTHING` on a row already
        there means it came from somewhere else). Withdrawn by the pair: the name stays until the
        last face naming her stops. Written through the batched form below.
        """
        return (await self.reconcile_people_of([asset_id]))[asset_id]

    async def reconcile_people_of(
        self, asset_ids: Sequence[str]
    ) -> dict[str, tuple[list[str], list[str]]]:
        """Bring the People on several files into line with their faces, in ONE transaction.

        The batched `reconcile_people`, one turn at the writer and one ring of the change bus at
        the end over everybody who moved. Answers what was added and removed per file, which
        decides whether the search index is told.
        """
        wanted = list(dict.fromkeys(asset_ids))
        if not wanted:
            return {}
        believed, claimed = await _named_and_claimed(self._db.fetch_all, wanted)
        async with self._db.write() as connection:
            return await _bring_into_line(connection, wanted, believed, claimed)

    async def reconcile_people_on(
        self, connection: Connection, asset_ids: Sequence[str]
    ) -> dict[str, tuple[list[str], list[str]]]:
        """`reconcile_people_of` inside the caller's write, reading the faces on its connection
        so a naming made earlier in the same write is what the files are brought into line with."""
        wanted = list(dict.fromkeys(asset_ids))
        if not wanted:
            return {}
        believed, claimed = await _named_and_claimed(connection.execute_fetchall, wanted)
        return await _bring_into_line(connection, wanted, believed, claimed)

    async def claimed_people(self, asset_id: str) -> list[str]:
        """Which of a file's People this feature put there. For tests and for the audit trail."""
        rows = await self._db.fetch_all(
            "SELECT person_id FROM face_asset_people WHERE asset_id = ? ORDER BY person_id",
            (asset_id,),
        )
        return [str(row["person_id"]) for row in rows]

    async def forget_rejection(self, track_id: str, person_id: str) -> None:
        """Forget that somebody said this face is not that person: for an undo of a bulk refusal
        only, since a refusal never expires. Silent where there is no such row.
        """
        # AND ITS MEMORY, or the refusal comes back at the next scan. Matched against every face of
        # the appearance: a pass since may have found a clearer one than the press remembered.
        async with self._db.write() as connection:
            await connection.execute(
                "DELETE FROM face_rejected WHERE person_id = ? "
                "AND asset_id = (SELECT asset_id FROM face_tracks WHERE id = ?) "
                "AND embedding IN (SELECT embedding FROM face_detections WHERE track_id = ?)",
                (person_id, track_id, track_id),
            )
            await connection.execute(
                "DELETE FROM face_rejections WHERE track_id = ? AND person_id = ?",
                (track_id, person_id),
            )

    async def rejections(self) -> dict[str, set[str]]:
        rows = await self._db.sweep_all(
            "SELECT track_id, person_id FROM face_rejections", what="face rejections"
        )
        out: dict[str, set[str]] = {}
        for row in rows:
            out.setdefault(str(row["track_id"]), set()).add(str(row["person_id"]))
        return out


async def _named_and_claimed(
    fetch: Callable[..., Awaitable[Iterable[Row]]], wanted: Sequence[str]
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Per file, who its faces name and whom this feature filed it under, read through `fetch`:
    the reader's, or the connection of the write the answer is for."""
    believed: dict[str, set[str]] = {one: set() for one in wanted}
    claimed: dict[str, set[str]] = {one: set() for one in wanted}
    for start in range(0, len(wanted), _PILES_PER_READ):
        chunk = wanted[start : start + _PILES_PER_READ]
        # Only the faces that NAME somebody. See `NAMES_THE_FILE`. A question leaves the file
        # exactly as it was, and a person whose only face here is a question comes off it.
        faces, params = in_clause(
            "SELECT DISTINCT asset_id, person_id FROM face_tracks "
            "WHERE asset_id IN (?*) AND person_id IS NOT NULL AND attribution IN (?, ?)",
            chunk,
        )
        for row in await fetch(faces, [*params, *_NAMES_THE_FILE]):
            believed[str(row["asset_id"])].add(str(row["person_id"]))
        held, params = in_clause(
            "SELECT asset_id, person_id FROM face_asset_people WHERE asset_id IN (?*)", chunk
        )
        for row in await fetch(held, params):
            claimed[str(row["asset_id"])].add(str(row["person_id"]))
    return believed, claimed


async def _bring_into_line(
    connection: Connection,
    wanted: Sequence[str],
    believed: Mapping[str, set[str]],
    claimed: Mapping[str, set[str]],
) -> dict[str, tuple[list[str], list[str]]]:
    """The write half of `reconcile_people_of`: each file's People made to agree with the faces
    that name somebody in it, and every admin told once over everybody who moved."""
    moved_by: dict[str, tuple[list[str], list[str]]] = {}
    now = now_ms()
    moved = NOBODY
    for asset_id in wanted:
        added: list[str] = []
        removed: list[str] = []
        for person_id in sorted(believed[asset_id] - claimed[asset_id]):
            # `decided_at` in SECONDS, as the shared table holds every decision; only the
            # face tables are in milliseconds.
            cursor = await connection.execute(
                "INSERT INTO asset_people (asset_id, person_id, decided_at) "
                "VALUES (?, ?, ?) ON CONFLICT(asset_id, person_id) DO NOTHING",
                (asset_id, person_id, now // 1000),
            )
            if cursor.rowcount < 1:
                continue
            await connection.execute(
                "INSERT INTO face_asset_people (asset_id, person_id, created_at) "
                "VALUES (?, ?, ?) ON CONFLICT(asset_id, person_id) DO NOTHING",
                (asset_id, person_id, now),
            )
            added.append(person_id)
            moved |= await bump_stamps_for_object(connection, ObjectType.PERSON, person_id)
        for person_id in sorted(claimed[asset_id] - believed[asset_id]):
            await connection.execute(
                "DELETE FROM asset_people WHERE asset_id = ? AND person_id = ?",
                (asset_id, person_id),
            )
            await connection.execute(
                "DELETE FROM face_asset_people WHERE asset_id = ? AND person_id = ?",
                (asset_id, person_id),
            )
            removed.append(person_id)
            moved |= await bump_stamps_for_object(connection, ObjectType.PERSON, person_id)
        moved_by[asset_id] = (added, removed)
    # Once, over everybody who moved, in the transaction: who is on a file moves what
    # somebody may see.
    announce(moved | await who_may_see_a_file(connection), About.LIBRARY)
    return moved_by
