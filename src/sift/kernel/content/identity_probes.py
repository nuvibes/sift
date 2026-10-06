# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the readers took off a file: the decoder's answer, its kind, and its perceptual fingerprints."""

from __future__ import annotations

import json
from collections.abc import Sequence

from sift.kernel.content.identity_models import (
    PROBE_VERSION,
    Asset,
    ProbeKeep,
    VerdictProduct,
    asset_from_row,
)
from sift.kernel.content.identity_store import StoreCore
from sift.kernel.content.perceptual import FINGERPRINT_VERSION
from sift.kernel.content.presence import HAS_A_PRESENT_COPY
from sift.kernel.db import in_clause
from sift.kernel.ingress import CLASSIFIER_VERSION, MediaType
from sift.kernel.ledger import Actor, record_event
from sift.kernel.log import get_logger
from sift.kernel.sql_splice import splice
from sift.kernel.vocabulary import (
    FINGERPRINTS_EMPTY,
    VIA_FINGERPRINT,
    Subject,
)

log = get_logger(__name__)

# What the probe learned. Every column is the probe's, so a re-probe writes its NULLs too. `mime`
# is the exception (COALESCE): the ingress gate set it, and a NULL leaves the gate's answer.
_RECORD_PROBE = """
UPDATE assets
   SET width       = ?,
       height      = ?,
       duration_ms = ?,
       fps         = ?,
       container   = ?,
       vcodec      = ?,
       acodec      = ?,
       bit_depth   = ?,
       color_transfer = ?,
       phash       = ?,
       videohash   = ?,
       mime        = COALESCE(?, mime),
       interleave_gap = ?,
       oshash      = ?,
       video_phash = ?,
      -- Which generation of `perceptual` wrote the four above. Bound, never chosen by a caller:
      -- this statement is only ever reached by a pass that has just computed them.
       fingerprint_version = ?,
       audio_channels = ?,
       audio_sample_rate = ?,
       video_duration_ms = ?,
       probed_at   = ?
 WHERE id = ?
RETURNING *
"""

# THE TOOL'S WHOLE ANSWER, one row per file, so a field needed later is a migration rather than a
# pass. Place-naming keys are stripped first (`media_jobs.ffmpeg.probe_body`).
_KEEP_PROBE = """
INSERT INTO asset_probes (asset_id, probe_version, tool, body, probed_at)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(asset_id) DO UPDATE SET
    probe_version = excluded.probe_version,
    tool          = excluded.tool,
    body          = excluded.body,
    probed_at     = excluded.probed_at
"""

#: Read files whose reading is not kept (or kept under an older `PROBE_VERSION`, which is how a
#: change to the reading reaches files already read), for the one-ffprobe-a-file catch-up.
_ASSETS_LACKING_PROBE_ROWS = """
SELECT a.id FROM assets a
WHERE a.probed_at IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM asset_probes p
                   WHERE p.asset_id = a.id AND p.probe_version >= ?)
ORDER BY a.added_at, a.id
LIMIT ?
"""

#: The sound's shape, the picture's length and its drawn size, for the keep-probe catch-up: named
#: columns only, as `_RECORD_PROBE` would blank the rest. A size is never blanked.
_RECORD_AUDIO_SHAPE = """
UPDATE assets SET audio_channels = ?, audio_sample_rate = ?, video_duration_ms = ?,
       width = COALESCE(?, width), height = COALESCE(?, height)
 WHERE id = ?
"""

_COUNT_ASSETS_LACKING_PROBE_ROWS = """
SELECT COUNT(*) AS total FROM assets a
WHERE a.probed_at IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM asset_probes p
                   WHERE p.asset_id = a.id AND p.probe_version >= ?)
"""

# THE SAME WRITE WITH THE FINGERPRINTS LEFT ALONE, for a scan-only read. A second statement, not
# a flag: `_RECORD_PROBE` sets every column it names, so passing None would BLANK them.
_RECORD_PROBE_KEEPING_FINGERPRINTS = """
UPDATE assets
   SET width       = ?,
       height      = ?,
       duration_ms = ?,
       fps         = ?,
       container   = ?,
       vcodec      = ?,
       acodec      = ?,
       bit_depth   = ?,
       color_transfer = ?,
       mime        = COALESCE(?, mime),
       interleave_gap = ?,
      -- The sound's shape is read from the same ffprobe answer as the picture's, so a pass that
      -- deliberately skipped the fingerprints still knows it. `fingerprint_version` is the one
      -- column this statement leaves alone beside the four hashes themselves: it describes them.
       audio_channels = ?,
       audio_sample_rate = ?,
       video_duration_ms = ?,
       probed_at   = ?
 WHERE id = ?
RETURNING *
"""

# The four fingerprints of one decode on their own (`_RECORD_PROBE` would blank the rest), with
# the generation stamp bound here so no caller can claim another generation's.
_RECORD_FINGERPRINTS = """
UPDATE assets
   SET phash = ?, videohash = ?, oshash = ?, video_phash = ?, fingerprint_version = ?
 WHERE id = ?
"""

_EVERY_FINGERPRINT = (
    "SELECT id, oshash, video_phash FROM assets WHERE oshash IS NOT NULL OR video_phash IS NOT NULL"
)

_RECORD_STILL_AT = "UPDATE assets SET still_at_ms = ? WHERE id = ?"

# THE ROWS A CLASSIFIER CHANGE HAS NOT REACHED YET. It TERMINATES: the pass stamps every row it
# reads, so only a row whose file could not be read stays below the line.
_UNCLASSIFIED = """
SELECT id FROM assets
WHERE classified_version < ?
ORDER BY added_at, id
LIMIT ?
"""

_ANY_UNCLASSIFIED = "SELECT EXISTS (SELECT 1 FROM assets WHERE classified_version < ?) AS found"

# What a re-read decided, in these columns and no others; length and rate are the probe's.
_RECLASSIFY = """
UPDATE assets SET media_type = ?, mime = ?, container = ?, classified_version = ? WHERE id = ?
"""

# Refused by the classifier in use: only stamped, as quarantining a held file is not this pass's.
_CLASSIFIED_AS_IT_WAS = "UPDATE assets SET classified_version = ? WHERE id = ?"

# A row whose kind its own bytes contradict, put below the line so the reclassify pass types it.
_BELOW_THE_CLASSIFIER_LINE = "UPDATE assets SET classified_version = 0 WHERE id = ?"

# Every READ file missing a fingerprint its kind ought to have (a photograph `phash`, a GIF also
# `videohash`, a video all four), or holding an older generation's; asking a photograph for a video
# fingerprint would never finish. Not a file given up on for good, nor one with no present copy:
# either would sit at the head of this oldest-first order for ever. Written out in full, never
# concatenated; a test holds the copies in step
# (`test_everything_read_and_missing_a_fingerprint_is_offered_to_the_backfill`).
_UNFINGERPRINTED_ASSETS = splice(
    """
SELECT a.id FROM assets a
WHERE a.probed_at IS NOT NULL
  AND {{PRESENT}}
  AND (
       (a.media_type = 'image' AND a.phash IS NULL)
    OR (a.media_type = 'gif'   AND (a.phash IS NULL OR a.videohash IS NULL))
    OR (a.media_type NOT IN ('image', 'gif')
        AND (a.phash IS NULL OR a.videohash IS NULL
             OR a.oshash IS NULL OR a.video_phash IS NULL))
   -- OR WHAT IT HAS IS AN OLDER GENERATION'S. NULL here is not "old", it is "none taken yet",
   -- and the lines above already say what a file of each kind is missing; this adds the one case
   -- they cannot see: every hash present, taken by a version of the arithmetic that no longer
   -- agrees with the one in use. Without it the first improvement to any of the four can only be
   -- applied by decoding every file in the library again.
    OR a.fingerprint_version < ?
  )
  AND NOT EXISTS (SELECT 1 FROM file_verdicts v
                   WHERE v.asset_id = a.id AND v.product = ? AND v.transient = 0)
ORDER BY a.added_at, a.id
LIMIT ? OFFSET ?
""",
    PRESENT=HAS_A_PRESENT_COPY,
)

#: The same condition, asked about a page of ids.
_UNFINGERPRINTED_AMONG = splice(
    """
SELECT a.id FROM assets a
WHERE a.id IN (?*)
  AND a.probed_at IS NOT NULL
  AND {{PRESENT}}
  AND (
       (a.media_type = 'image' AND a.phash IS NULL)
    OR (a.media_type = 'gif'   AND (a.phash IS NULL OR a.videohash IS NULL))
    OR (a.media_type NOT IN ('image', 'gif')
        AND (a.phash IS NULL OR a.videohash IS NULL
             OR a.oshash IS NULL OR a.video_phash IS NULL))
   -- OR WHAT IT HAS IS AN OLDER GENERATION'S. NULL here is not "old", it is "none taken yet",
   -- and the lines above already say what a file of each kind is missing; this adds the one case
   -- they cannot see: every hash present, taken by a version of the arithmetic that no longer
   -- agrees with the one in use. Without it the first improvement to any of the four can only be
   -- applied by decoding every file in the library again.
    OR a.fingerprint_version < ?
  )
  AND NOT EXISTS (SELECT 1 FROM file_verdicts v
                   WHERE v.asset_id = a.id AND v.product = ? AND v.transient = 0)
""",
    PRESENT=HAS_A_PRESENT_COPY,
)

#: The same condition, counted; see `_UNFINGERPRINTED_ASSETS` for why it is written out.
_COUNT_UNFINGERPRINTED = splice(
    """
SELECT COUNT(*) AS total FROM assets a
WHERE a.probed_at IS NOT NULL
  AND {{PRESENT}}
  AND (
       (a.media_type = 'image' AND a.phash IS NULL)
    OR (a.media_type = 'gif'   AND (a.phash IS NULL OR a.videohash IS NULL))
    OR (a.media_type NOT IN ('image', 'gif')
        AND (a.phash IS NULL OR a.videohash IS NULL
             OR a.oshash IS NULL OR a.video_phash IS NULL))
   -- OR WHAT IT HAS IS AN OLDER GENERATION'S. NULL here is not "old", it is "none taken yet",
   -- and the lines above already say what a file of each kind is missing; this adds the one case
   -- they cannot see: every hash present, taken by a version of the arithmetic that no longer
   -- agrees with the one in use. Without it the first improvement to any of the four can only be
   -- applied by decoding every file in the library again.
    OR a.fingerprint_version < ?
  )
  AND NOT EXISTS (SELECT 1 FROM file_verdicts v
                   WHERE v.asset_id = a.id AND v.product = ? AND v.transient = 0)
""",
    PRESENT=HAS_A_PRESENT_COPY,
)


class Probes(StoreCore):
    """What the readers took off each file."""

    async def every_fingerprint(self) -> list[tuple[str, str | None, str | None]]:
        """Every file's exact hash and picture hash, in one sweep, as (asset id, oshash, phash)."""
        rows = await self._db.sweep_all(_EVERY_FINGERPRINT, what="every_fingerprint")
        return [(str(row["id"]), row["oshash"], row["video_phash"]) for row in rows]

    async def record_probe(
        self,
        asset_id: str,
        *,
        width: int | None = None,
        height: int | None = None,
        duration_ms: int | None = None,
        fps: float | None = None,
        container: str | None = None,
        vcodec: str | None = None,
        acodec: str | None = None,
        bit_depth: int | None = None,
        color_transfer: str | None = None,
        phash: str | None = None,
        videohash: str | None = None,
        mime: str | None = None,
        interleave_gap: int | None = None,
        oshash: str | None = None,
        video_phash: str | None = None,
        audio_channels: int | None = None,
        audio_sample_rate: int | None = None,
        video_duration_ms: int | None = None,
        keep: ProbeKeep | None = None,
        keep_fingerprints: bool = False,
    ) -> Asset | None:
        """Write down what a file turned out to be, once the probe has decoded it; None if the
        asset has gone. The gate's own fields are not arguments; only `mime` can be corrected.

        `keep_fingerprints` (a scan-only read) uses a statement that does not NAME the four, and
        refuses one handed in. `keep`, the tool's whole answer, lands in THIS transaction: the
        columns and the answer they were read from are one fact.
        """
        if keep_fingerprints and any(
            value is not None for value in (phash, videohash, oshash, video_phash)
        ):
            raise ValueError(
                "record_probe was given a fingerprint and asked to keep the existing ones"
            )
        # ONE write and ONE tail, whichever statement: they differ only in the columns they name.
        now = self._now()
        common = (
            width,
            height,
            duration_ms,
            fps,
            container,
            vcodec,
            acodec,
            bit_depth,
            color_transfer,
        )
        sound = (audio_channels, audio_sample_rate, video_duration_ms)
        statement, values = (
            (
                _RECORD_PROBE_KEEPING_FINGERPRINTS,
                (*common, mime, interleave_gap, *sound, now, asset_id),
            )
            if keep_fingerprints
            else (
                _RECORD_PROBE,
                (
                    *common,
                    phash,
                    videohash,
                    mime,
                    interleave_gap,
                    oshash,
                    video_phash,
                    FINGERPRINT_VERSION,
                    *sound,
                    now,
                    asset_id,
                ),
            )
        )
        kept = (
            None
            if keep is None
            else (
                _KEEP_PROBE,
                (asset_id, keep.version, keep.tool, keep.body, now),
            )
        )
        # Announced as an arrival: a player waiting on an unread file asks again when it hears it.
        rows = await self._write_moving_files(statement, values, then=kept)
        if not rows:
            return None

        asset = asset_from_row(rows[0])
        log.info(
            "content.probed",
            asset_id=asset.id,
            media_type=asset.media_type,
            width=asset.width,
            height=asset.height,
            duration_ms=asset.duration_ms,
            container=asset.container,
            vcodec=asset.vcodec,
            acodec=asset.acodec,
            interleave_gap=asset.interleave_gap,
        )
        return asset

    async def record_fingerprints(
        self,
        asset_id: str,
        *,
        phash: str | None,
        videohash: str | None,
        oshash: str | None,
        video_phash: str | None,
        name: str | None = None,
        arriving: bool = False,
    ) -> None:
        """Write the four fingerprints of one decode, and nothing else. An empty string is "looked,
        nothing to record", unlike None ("nobody looked"), so an unreadable file does not return.

        ONE History event per file (never per hash), in the same transaction, as only it can say
        when a file was looked at. `name` is the caller's, to spare a read per file; `arriving`
        writes no event, as taking a file in writes no line of its own.
        """
        async with self._db.write() as connection:
            await connection.execute(
                _RECORD_FINGERPRINTS,
                (phash, videohash, oshash, video_phash, FINGERPRINT_VERSION, asset_id),
            )
            if arriving:
                return
            await record_event(
                connection,
                # The task's word (`TASK_VIAS`), not a `MADE_VIAS` one: this pass makes no row.
                actor=Actor.sift(VIA_FINGERPRINT),
                verb="scanned",
                subject=Subject(kind="asset", id=asset_id, name=name),
                payload=None
                if any((phash, videohash, oshash, video_phash))
                else json.dumps({FINGERPRINTS_EMPTY: True}),
            )

    async def unclassified(self, limit: int) -> list[str]:
        """Files typed by an older classifier, oldest first, for the reclassify pass: a scan skips
        an unchanged file, so it would never look again."""
        rows = await self._db.fetch_all(_UNCLASSIFIED, (CLASSIFIER_VERSION, limit))
        return [row["id"] for row in rows]

    async def any_unclassified(self) -> bool:
        """Whether any file is below the classifier line: a seek of its index, for a start."""
        (row,) = await self._db.fetch_all(_ANY_UNCLASSIFIED, (CLASSIFIER_VERSION,))
        return bool(row["found"])

    async def reclassify(self, asset_id: str, media: MediaType | None) -> None:
        """Record what the classifier in use says a file is (`None`: it refuses it, and the row is
        only stamped); either way the row reaches the generation in use, so the pass finishes."""
        if media is None:
            await self._write(_CLASSIFIED_AS_IT_WAS, (CLASSIFIER_VERSION, asset_id))
            return
        await self._write(
            _RECLASSIFY,
            (str(media.kind), media.mime, media.name, CLASSIFIER_VERSION, asset_id),
        )

    async def send_to_classifier(self, asset_id: str) -> None:
        """Put a row below the classifier line, for one whose stored kind its own bytes refute:
        the reclassify pass then writes the kind and the mime together, from one answer."""
        await self._write(_BELOW_THE_CLASSIFIER_LINE, (asset_id,))

    async def unfingerprinted(self, limit: int, *, skip: int = 0) -> list[str]:
        """Read files still missing a fingerprint, oldest first (`_UNFINGERPRINTED_ASSETS`).

        `skip` steps over a page the pass could record nothing about (a file only stuck for now,
        not given up on); nothing is stored, so a later walk from the start offers them again.
        """
        if skip < 0:
            raise ValueError("a page cannot start before the first row")
        rows = await self._db.fetch_all(
            _UNFINGERPRINTED_ASSETS,
            (FINGERPRINT_VERSION, VerdictProduct.FINGERPRINTS.value, limit, skip),
        )
        return [row["id"] for row in rows]

    async def record_still_moment(self, asset_id: str, at_ms: int) -> None:
        """Where this file's tile still was cut, as the still maker chose it."""
        await self._write(_RECORD_STILL_AT, (at_ms, asset_id))

    async def assets_lacking_probe_rows(self, limit: int) -> list[str]:
        """A page of files read before their reading was kept, oldest first, for the catch-up."""
        rows = await self._db.fetch_all(_ASSETS_LACKING_PROBE_ROWS, (PROBE_VERSION, limit))
        return [str(row["id"]) for row in rows]

    async def assets_lacking_probe_rows_count(self) -> int:
        """How many of those there are, for the pass's bar."""
        (row,) = await self._db.fetch_all(_COUNT_ASSETS_LACKING_PROBE_ROWS, (PROBE_VERSION,))
        return int(row["total"])

    async def keep_probe(
        self,
        asset_id: str,
        keep: ProbeKeep,
        *,
        audio_channels: int | None = None,
        audio_sample_rate: int | None = None,
        video_duration_ms: int | None = None,
        width: int | None = None,
        height: int | None = None,
    ) -> None:
        """Store a reading of a file that already has its fields, NOT rewriting them (several come
        from elsewhere), with the sound's shape, the picture's length and, where given, its drawn
        size (`PROBE_VERSION` 2), all in one transaction.
        """
        async with self._db.write() as connection:
            await connection.execute(
                _RECORD_AUDIO_SHAPE,
                (audio_channels, audio_sample_rate, video_duration_ms, width, height, asset_id),
            )
            await connection.execute(
                _KEEP_PROBE, (asset_id, keep.version, keep.tool, keep.body, self._now())
            )

    async def unfingerprinted_among(self, asset_ids: Sequence[str]) -> set[str]:
        """Which of these files still have a fingerprint missing (see `unfingerprinted`)."""
        if not asset_ids:
            return set()
        sql, params = in_clause(_UNFINGERPRINTED_AMONG, list(asset_ids))
        rows = await self._db.fetch_all(
            sql, [*params, FINGERPRINT_VERSION, VerdictProduct.FINGERPRINTS.value]
        )
        return {str(row["id"]) for row in rows}

    async def unfingerprinted_count(self) -> int:
        """How many files still wait for a fingerprint: the bar's total, unknown to paging."""
        (row,) = await self._db.fetch_all(
            _COUNT_UNFINGERPRINTED, (FINGERPRINT_VERSION, VerdictProduct.FINGERPRINTS.value)
        )
        return int(row["total"])
