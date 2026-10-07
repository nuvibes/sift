# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a pass over a file found: its scan row, its faces and their descriptions, and the
runs that decide which files still want one."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from sift.kernel.access.history_faces import SCAN_FACTS
from sift.kernel.content import Lack, VerdictProduct
from sift.kernel.db import Connection, in_clause
from sift.kernel.ids import new_id
from sift.kernel.jobs.families import Family
from sift.kernel.jobs.ledger import LAST_RUN_FOR_PRODUCTS
from sift.kernel.ledger import Actor, record_event
from sift.kernel.paging import MAX_PAGE_SIZE
from sift.kernel.sampling import FACE_SAMPLING_VERSION
from sift.kernel.vocabulary import VIA_FACES, Subject
from sift.slices.faces import recognize
from sift.slices.faces.models import (
    Appearance,
    ScanStatus,
    Vector,
)
from sift.slices.faces.store_pictures import (
    PicturesStore,
    _digest,
    _remove_pictures,
    _write_pictures,
)
from sift.slices.faces.store_records import (
    _PILES_PER_READ,
    PRODUCT,
    PassRecord,
    Scan,
    StoredTrack,
    _scan,
    _scan_row,
    _track,
    now_ms,
)
from sift.slices.faces.tuning import MIN_PIXELS, QUALITY_VERSION, RUN_MEMORY_MS

#: Written by both the replacing and the resumed pass, so the two cannot record a scan differently.
#: `reached_ms` is null once a pass has run its plan out. A replacing pass is the whole account of
#: the file, so its refusal counts replace; `_EXTEND_SCAN` adds them.
_RECORD_SCAN = """
INSERT INTO face_scans (asset_id, status, depth, coverage, frames_sampled, track_count,
                        identified_count, detector, recognizer, settings_digest, settings_shape,
                        settings_density, quality_version, sampling_version, scanned_at,
                        reached_ms, refused_small, refused_closer, cut_short,
                        refused_largest, refused_blurred, refused_turned, refused_edge)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(asset_id) DO UPDATE SET
  status = excluded.status, depth = excluded.depth, coverage = excluded.coverage,
  frames_sampled = excluded.frames_sampled, track_count = excluded.track_count,
  identified_count = excluded.identified_count, detector = excluded.detector,
  recognizer = excluded.recognizer, settings_digest = excluded.settings_digest,
  settings_shape = excluded.settings_shape, settings_density = excluded.settings_density,
  quality_version = excluded.quality_version, sampling_version = excluded.sampling_version,
  scanned_at = excluded.scanned_at, reached_ms = excluded.reached_ms,
  refused_small = excluded.refused_small, refused_closer = excluded.refused_closer,
  cut_short = excluded.cut_short, refused_largest = excluded.refused_largest,
  refused_blurred = excluded.refused_blurred, refused_turned = excluded.refused_turned,
  refused_edge = excluded.refused_edge
"""


#: The same write for a RESUMED pass: the refusal counts and the moments read are ADDED to the
#: earlier stretch's, whose faces it keeps (`extend_pass`). NULL plus a count stays NULL: a total
#: over part of a file is not a total.
_EXTEND_SCAN = """
INSERT INTO face_scans (asset_id, status, depth, coverage, frames_sampled, track_count,
                        identified_count, detector, recognizer, settings_digest, settings_shape,
                        settings_density, quality_version, sampling_version, scanned_at,
                        reached_ms, refused_small, refused_closer, cut_short,
                        refused_largest, refused_blurred, refused_turned, refused_edge)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(asset_id) DO UPDATE SET
  status = excluded.status, depth = excluded.depth, coverage = excluded.coverage,
  frames_sampled = face_scans.frames_sampled + excluded.frames_sampled,
  track_count = excluded.track_count,
  identified_count = excluded.identified_count, detector = excluded.detector,
  recognizer = excluded.recognizer, settings_digest = excluded.settings_digest,
  settings_shape = excluded.settings_shape, settings_density = excluded.settings_density,
  quality_version = excluded.quality_version, sampling_version = excluded.sampling_version,
  scanned_at = excluded.scanned_at, reached_ms = excluded.reached_ms,
  refused_small = face_scans.refused_small + excluded.refused_small,
  refused_closer = face_scans.refused_closer + excluded.refused_closer,
  cut_short = excluded.cut_short,
  refused_largest = max(face_scans.refused_largest, excluded.refused_largest),
  refused_blurred = face_scans.refused_blurred + excluded.refused_blurred,
  refused_turned = face_scans.refused_turned + excluded.refused_turned,
  refused_edge = face_scans.refused_edge + excluded.refused_edge
"""


#: The scan row as a look left it, kept as that look's act (`_record_look`).
_SCAN_NOW = """
SELECT track_count, refused_small, refused_closer, refused_largest, refused_blurred,
       refused_turned, refused_edge
  FROM face_scans WHERE asset_id = ?
"""


async def _record_look(connection: Connection, asset_id: str) -> None:
    """Keep this look as an act of its own on the file's History, in the look's transaction: the
    scan row holds only the last look, and a look made again must not take an earlier one's line."""
    row = await (await connection.execute(_SCAN_NOW, (asset_id,))).fetchone()
    facts = {} if row is None else {key: row[index] for index, key in enumerate(SCAN_FACTS)}
    await record_event(
        connection,
        actor=Actor.sift(VIA_FACES),
        verb="face_run",
        subject=Subject(kind="asset", id=asset_id),
        payload=json.dumps(facts),
    )


_INSERT_TRACK = """
INSERT INTO face_tracks (id, asset_id, started_ms, ended_ms, seen_in, quality, created_at)
VALUES (?, ?, ?, ?, ?, ?, ?)
"""


_INSERT_DETECTION = """
INSERT INTO face_detections (id, track_id, timestamp_ms, box_x, box_y, box_w, box_h, score,
                             quality, pixels, sharpness, frontality, containment, strength,
                             agreement, crop_path, crop_digest, embedding, created_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


#: Widening an appearance the resumed pass found more of: the range only grows, the best quality
#: seen is kept.
_WIDEN_TRACK = """
UPDATE face_tracks
   SET started_ms = MIN(started_ms, :started),
       ended_ms   = MAX(ended_ms, :ended),
       seen_in    = seen_in + :seen,
       quality    = MAX(quality, :quality)
 WHERE id = :track
"""


#: The settled rule as a `Lack`: a file with no complete scan row under the tuning bound here and
#: under code at least this new, or a fuller one. See `Store.lack`. Binds the two code versions,
#: then the digest, the shape and the density, in that order.
_LACKS_SCAN = (
    "NOT EXISTS (SELECT 1 FROM face_scans s WHERE s.asset_id = a.id AND s.coverage >= 1.0"
    " AND s.quality_version IS NOT NULL AND s.quality_version >= ?"
    " AND s.sampling_version IS NOT NULL AND s.sampling_version >= ?"
    " AND (s.settings_digest = ?"
    "      OR (s.settings_shape IS NOT NULL AND s.settings_shape = ?"
    "          AND s.settings_density IS NOT NULL AND s.settings_density >= ?)))"
)


#: A file with no scan row at all, under any rule: `_LACKS_SCAN` minus this is the files looked at
#: under another rule or settings, or by a pass that did not reach its end.
_NEVER_SCANNED = "NOT EXISTS (SELECT 1 FROM face_scans s WHERE s.asset_id = a.id)"


class FoundStore(PicturesStore):
    """What a pass over a file found, and which files still want one."""

    # --- recording a pass ------------------------------------------------------------------

    async def replace_pass(
        self,
        asset_id: str,
        appearances: Sequence[Appearance],
        crops: Sequence[Sequence[bytes]],
        record: PassRecord,
    ) -> list[str]:
        """Write what a pass found, replacing whatever the last pass found for this file. The
        pictures go to disk inside the same transaction as the rows that point at them."""
        written: list[tuple[Path, bytes]] = []
        track_ids: list[str] = []
        stamp = now_ms()

        # What the pass being replaced left on disk: read before the rows go (only they know where
        # the pictures are), unlinked after the commit (a rollback would leave rows naming nothing).
        superseded = await self._pictures_of(asset_id)

        async with self._db.write() as connection:
            await connection.execute("DELETE FROM face_tracks WHERE asset_id = ?", (asset_id,))
            for appearance, pictures in zip(appearances, crops, strict=True):
                track_id = new_id()
                track_ids.append(track_id)
                await connection.execute(
                    _INSERT_TRACK,
                    (
                        track_id,
                        asset_id,
                        appearance.started_ms,
                        appearance.ended_ms,
                        appearance.seen_in,
                        appearance.quality,
                        stamp,
                    ),
                )
                await self._write_faces(connection, track_id, appearance, pictures, stamp, written)

            await connection.execute(
                _RECORD_SCAN, _scan_row(asset_id, record, len(appearances), stamp)
            )
            await _record_look(connection, asset_id)
            await asyncio.to_thread(_write_pictures, written)

        # Committed. A picture the new pass wrote to the same place is excluded, not trusted.
        keep = {path for path, _ in written}
        await asyncio.to_thread(_remove_pictures, [p for p in superseded if p not in keep])
        return track_ids

    async def extend_pass(
        self,
        asset_id: str,
        appearances: Sequence[Appearance],
        crops: Sequence[Sequence[bytes]],
        joins: Sequence[str | None],
        record: PassRecord,
    ) -> list[str]:
        """Add what a resumed pass found to what the pass before it left, deleting nothing: the
        one write in the feature that is not a replacement.

        `joins` runs alongside `appearances`: an appearance the caller matched to one the last
        pass found names the track to widen; the rest become tracks of their own.
        """
        written: list[tuple[Path, bytes]] = []
        fresh: list[str] = []
        stamp = now_ms()

        async with self._db.write() as connection:
            for appearance, pictures, joined in zip(appearances, crops, joins, strict=True):
                if joined is None:
                    track_id = new_id()
                    fresh.append(track_id)
                    await connection.execute(
                        _INSERT_TRACK,
                        (
                            track_id,
                            asset_id,
                            appearance.started_ms,
                            appearance.ended_ms,
                            appearance.seen_in,
                            appearance.quality,
                            stamp,
                        ),
                    )
                else:
                    track_id = joined
                    await connection.execute(
                        _WIDEN_TRACK,
                        {
                            "track": track_id,
                            "started": appearance.started_ms,
                            "ended": appearance.ended_ms,
                            "seen": appearance.seen_in,
                            "quality": appearance.quality,
                        },
                    )
                await self._write_faces(connection, track_id, appearance, pictures, stamp, written)

            # Counted rather than added up, because the number wanted is how many appearances the
            # file has now: the ones this pass made plus the ones it carried on.
            counted = await (
                await connection.execute(
                    "SELECT count(*) AS held FROM face_tracks WHERE asset_id = ?", (asset_id,)
                )
            ).fetchone()
            await connection.execute(
                _EXTEND_SCAN,
                _scan_row(asset_id, record, int(counted[0]) if counted else len(fresh), stamp),
            )
            await _record_look(connection, asset_id)
            await asyncio.to_thread(_write_pictures, written)

        return fresh

    async def _write_faces(
        self,
        connection: Connection,
        track_id: str,
        appearance: Appearance,
        pictures: Sequence[bytes],
        stamp: int,
        written: list[tuple[Path, bytes]],
    ) -> None:
        """The faces of one appearance and their pictures, shared by both passes; the pictures are
        collected here and written once, inside the transaction."""
        for face, picture in zip(appearance.faces, pictures, strict=True):
            face_id = new_id()
            path = self.crop_path(self.detected_root, face_id)
            written.append((path, picture))
            await connection.execute(
                _INSERT_DETECTION,
                (
                    face_id,
                    track_id,
                    face.detection.timestamp_ms,
                    face.detection.box.x,
                    face.detection.box.y,
                    face.detection.box.width,
                    face.detection.box.height,
                    face.detection.score,
                    face.quality.score,
                    # The six the score was reduced from: the score cannot say WHY a face was poor.
                    face.quality.pixels,
                    face.quality.sharpness,
                    face.quality.frontality,
                    face.quality.containment,
                    face.quality.strength,
                    face.quality.agreement,
                    self.relative(self.detected_root, face_id),
                    _digest(picture),
                    recognize.pack(face.vector),
                    stamp,
                ),
            )

    async def appearance_vectors(self, asset_id: str) -> list[tuple[str, Vector]]:
        """One description per appearance already stored for this file, its best: what a resumed
        pass compares against, chosen as a re-match chooses."""
        rows = await self._db.fetch_all(
            "SELECT t.id AS track_id, d.embedding AS embedding FROM face_tracks AS t "
            "JOIN face_detections AS d ON d.track_id = t.id WHERE t.asset_id = ? "
            "ORDER BY t.id, d.quality DESC, d.id",
            (asset_id,),
        )
        best: dict[str, Vector] = {}
        for row in rows:
            track_id = str(row["track_id"])
            if track_id not in best:
                best[track_id] = recognize.unpack(bytes(row["embedding"]))
        return list(best.items())

    async def _pictures_of(self, asset_id: str) -> list[Path]:
        """Every picture on disk belonging to one file's faces: the crops and the covers, which
        are named after their face."""
        rows = await self._db.fetch_all(
            "SELECT t.id AS track_id, d.crop_path AS crop_path FROM face_tracks t "
            "LEFT JOIN face_detections d ON d.track_id = t.id WHERE t.asset_id = ?",
            (asset_id,),
        )

        # `resolve` asks the disk, so the whole loop goes to a thread in one hop.
        def gather() -> list[Path]:
            pictures: set[Path] = set()
            for row in rows:
                pictures.add(self.cover_path(str(row["track_id"])))
                stored = row["crop_path"]
                if stored:
                    pictures.add(self.resolve(str(stored)))
            return sorted(pictures)

        return await asyncio.to_thread(gather)

    async def set_status(self, asset_id: str, status: ScanStatus, identified: int) -> None:
        await self._db.execute(
            "UPDATE face_scans SET status = ?, identified_count = ? WHERE asset_id = ?",
            (status.value, identified, asset_id),
        )

    async def tracks_in_files(self, asset_ids: Sequence[str]) -> dict[str, list[StoredTrack]]:
        """Every face in several files in one go, keyed by file, the batched `tracks_of`. Every
        asked-for file is present, empty when nothing was found in it."""
        found: dict[str, list[StoredTrack]] = {one: [] for one in dict.fromkeys(asset_ids)}
        if not found:
            return found
        for start in range(0, len(found), _PILES_PER_READ):
            chunk = list(found)[start : start + _PILES_PER_READ]
            query, params = in_clause(
                "SELECT * FROM face_tracks WHERE asset_id IN (?*) ORDER BY started_ms", chunk
            )
            for row in await self._db.fetch_all(query, params):
                found[str(row["asset_id"])].append(_track(row))
        return found

    async def set_statuses(self, counted: Mapping[str, tuple[ScanStatus, int]]) -> None:
        """Write several files' scan statuses in ONE transaction, one turn at the writer."""
        if not counted:
            return
        async with self._db.write() as connection:
            for asset_id, (status, identified) in counted.items():
                await connection.execute(
                    "UPDATE face_scans SET status = ?, identified_count = ? WHERE asset_id = ?",
                    (status.value, identified, asset_id),
                )

    async def scan_of(self, asset_id: str) -> Scan | None:
        row = await self._db.fetch_one("SELECT * FROM face_scans WHERE asset_id = ?", (asset_id,))
        return _scan(row) if row is not None else None

    async def scanned_at_of(self, asset_ids: Sequence[str]) -> dict[str, int]:
        """When each of these files was last looked at for faces, in milliseconds. A file never
        looked at is absent."""
        if not asset_ids:
            return {}
        sql, params = in_clause(
            "SELECT asset_id, scanned_at FROM face_scans WHERE asset_id IN (?*)", list(asset_ids)
        )
        rows = await self._db.fetch_all(sql, params)
        return {str(row["asset_id"]): int(row["scanned_at"]) for row in rows}

    async def under_an_earlier_floor(
        self, floor: int, *, after: str = "", limit: int = MAX_PAGE_SIZE
    ) -> list[str]:
        """Files whose scan refused a face for size that `floor` would accept, by id, in order.

        Read off `refused_largest`, in the floor's pixels. A pass under `floor` never records one
        over it, so a file looked at again falls out. `after` pages by id.
        """
        rows = await self._db.fetch_all(
            "SELECT asset_id FROM face_scans WHERE refused_largest > ? AND refused_largest < ? "
            "AND asset_id > ? ORDER BY asset_id LIMIT ?",
            (floor, MIN_PIXELS, after, limit),
        )
        return [str(row["asset_id"]) for row in rows]

    async def settled_ids(self, digest: str, *, shape: str = "", density: float = 0.0) -> set[str]:
        """The files already looked at under this exact tuning, and so not worth looking at again.

        `settings_digest` records the tuning a result was produced under. **A pass that did not
        finish is not settled**: it is offered again and resumes. **A file looked at harder is
        settled too** (`settings_density >=`): turning the effort down does not un-look, while
        turning it up restages. The ordering is on the density, since depth and sampling are one
        control. The rest of the tuning (`shape`) must match: results that accept differently
        are incomparable. The two code versions are columns compared with `>=`. A null matches
        nothing, so an old row is offered again.

        Written out in four places (here, `unsettled_among`, `lack`, `settled_count`), one per
        shape of statement; a test asking all four the same question holds them in step.
        """
        rows = await self._db.fetch_all(
            "SELECT asset_id FROM face_scans WHERE coverage >= 1.0"
            " AND quality_version IS NOT NULL AND quality_version >= :quality"
            " AND sampling_version IS NOT NULL AND sampling_version >= :sampling"
            " AND ("
            "  settings_digest = :digest"
            "  OR (settings_shape IS NOT NULL AND settings_shape = :shape"
            "      AND settings_density IS NOT NULL AND settings_density >= :density)"
            ")",
            {
                "digest": digest,
                "shape": shape,
                "density": density,
                "quality": QUALITY_VERSION,
                "sampling": FACE_SAMPLING_VERSION,
            },
        )
        return {str(row["asset_id"]) for row in rows}

    async def unsettled_among(
        self, asset_ids: Sequence[str], digest: str, *, shape: str = "", density: float = 0.0
    ) -> set[str]:
        """Which of THESE files have not been looked at under this tuning, or a fuller one: the
        rule `settled_ids` explains, for a page the Build already holds."""
        if not asset_ids:
            return set()
        wanted = list(asset_ids)
        sql, params = in_clause(
            "SELECT s.asset_id FROM face_scans s WHERE s.asset_id IN (?*) AND s.coverage >= 1.0"
            " AND s.quality_version IS NOT NULL AND s.quality_version >= ?"
            " AND s.sampling_version IS NOT NULL AND s.sampling_version >= ?"
            " AND (s.settings_digest = ?"
            "      OR (s.settings_shape IS NOT NULL AND s.settings_shape = ?"
            "          AND s.settings_density IS NOT NULL AND s.settings_density >= ?))",
            wanted,
        )
        rows = await self._db.fetch_all(
            sql,
            [*params, QUALITY_VERSION, FACE_SAMPLING_VERSION, digest, shape, density],
        )
        settled = {str(row["asset_id"]) for row in rows}
        return {one for one in wanted if one not in settled}

    def lack(self, digest: str, *, shape: str = "", density: float = 0.0) -> Lack:
        """Not looked at under this tuning or a fuller one, as one term of the Build's count: the
        rule `settled_ids` explains, as a condition on the assets row."""
        return Lack(_LACKS_SCAN, (QUALITY_VERSION, FACE_SAMPLING_VERSION, digest, shape, density))

    def never_scanned(self) -> Lack:
        """Never looked at for faces, as one term of a count; `lack` minus this is the files
        looked at under another rule (`FaceService.backlog`). It names the faces verdict, which
        the caller must name on `lack` too, or the subtraction is off.
        """
        return Lack(_NEVER_SCANNED, (), product=VerdictProduct.FACES.value)

    async def settled_count(self, digest: str, *, shape: str = "", density: float = 0.0) -> int:
        """How many files have been looked at under this tuning, or a fuller one. One indexed count.

        It counts this feature's OWN table only: SQL against `assets` from a slice would answer
        for files the asker may not see (a gate holds it). Exact, since a scan row cascades with
        its file.
        """
        row = await self._db.fetch_one(
            "SELECT COUNT(*) AS total FROM face_scans s WHERE s.coverage >= 1.0"
            " AND s.quality_version IS NOT NULL AND s.quality_version >= :quality"
            " AND s.sampling_version IS NOT NULL AND s.sampling_version >= :sampling"
            " AND ("
            "  s.settings_digest = :digest"
            "  OR (s.settings_shape IS NOT NULL AND s.settings_shape = :shape"
            "      AND s.settings_density IS NOT NULL AND s.settings_density >= :density)"
            ")",
            {
                "digest": digest,
                "shape": shape,
                "density": density,
                "quality": QUALITY_VERSION,
                "sampling": FACE_SAMPLING_VERSION,
            },
        )
        return int(row["total"]) if row is not None else 0

    async def waiting_asset_ids(self, limit: int) -> tuple[list[str], int]:
        """Which files are still waiting to be scanned (a sample of ids), and how many in all.

        Ids only: what an asset is comes from the access layer, never a join here. Read from the
        queue's own table, the sweep's answer. The count is exact; the sample only feeds an
        estimate.
        """
        rows = await self._db.fetch_all(
            "SELECT payload FROM jobs "
            "WHERE type = 'face_scan' AND state IN ('queued', 'running') LIMIT ?",
            (limit,),
        )
        counted = await self._db.fetch_one(
            "SELECT COUNT(*) AS waiting FROM jobs "
            "WHERE type = 'face_scan' AND state IN ('queued', 'running')"
        )
        ids = []
        for row in rows:
            asset_id = json.loads(str(row["payload"])).get("asset_id")
            if asset_id:
                ids.append(str(asset_id))
        return ids, int(counted["waiting"]) if counted else len(ids)

    # --- the tuning a run started under -------------------------------------------------------

    async def remember_run(self, run_id: str, tuning: str) -> None:
        """Keep what one sweep is running under, so a change of mind halfway does not change it.
        Snapshots older than a week, far longer than any sweep, are dropped on the way past."""
        now = now_ms()
        await self._db.execute(
            "INSERT OR REPLACE INTO face_runs (id, tuning, created_at) VALUES (?, ?, ?)",
            (run_id, tuning, now),
        )
        await self._db.execute("DELETE FROM face_runs WHERE created_at < ?", (now - RUN_MEMORY_MS,))

    async def run_tuning(self, run_id: str) -> str | None:
        """What that run started under, or None, when a scan reads the live settings."""
        row = await self._db.fetch_one("SELECT tuning FROM face_runs WHERE id = ?", (run_id,))
        return str(row["tuning"]) if row is not None else None

    async def last_run_at(self) -> int | None:
        """When the last look through the library ENDED, in milliseconds, or None if none has.

        Read from the work ledger's run of the Identify pass, which records the end and whether it
        was stopped; not the newest scan row, which a single import moves.
        """
        found = await self._last_identify_run()
        return None if found is None else found[0] * 1000

    async def last_run_canceled(self) -> bool:
        """Whether that last look through the library was stopped before it finished."""
        found = await self._last_identify_run()
        return found is not None and found[1]

    async def _last_identify_run(self) -> tuple[int, bool] | None:
        """The last finished run for faces from `work_runs`: when it ended, in seconds, and whether
        it was stopped. The faces product only (older runs answer by family), the statement the
        Tasks row reads, so the two say one moment.
        """
        row = await self._db.fetch_one(
            LAST_RUN_FOR_PRODUCTS, (json.dumps([PRODUCT]), Family.IDENTIFY.value)
        )
        if row is None:
            return None
        return int(row["finished_at"]), bool(row["stopped"])
