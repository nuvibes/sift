# SPDX-License-Identifier: AGPL-3.0-or-later
"""The questions duplicate-finding asks of the content tables, which carry the permission rules.

Unscoped: reached only from an admin surface or a system job, the same as the deleter."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sift.kernel.content.identity import LocationStatus, VerdictProduct
from sift.kernel.content.perceptual import FINGERPRINT_VERSION
from sift.kernel.db import Database, Row, in_clause
from sift.kernel.paging import MAX_PAGE_SIZE


@dataclass(frozen=True, slots=True)
class Fingerprint:
    """What one asset offers a near-duplicate comparison; `identity` proves a pair is not exact."""

    asset_id: str
    identity: str
    media_type: str
    phash: str | None
    videohash: str | None
    video_phash: str | None = None
    duration_ms: int | None = None
    """Carried because length is a second opinion, and a cheap one.

    Two unrelated videos that collide on a fingerprint almost never also agree on how long they
    are, so the comparison uses this to throw out a coincidence that no threshold could tell from
    a real match. None means nobody knows, and an unknown length is not evidence either way."""
    fingerprint_version: int | None = None
    """Which generation of the hashes above this row carries. Two rows compare only when they
    agree here: a fingerprint from another generation does not mean the same thing, and a swap
    between two installs is the reader that meets two generations most."""
    oshash: str | None = None
    """The Stash-compatible exact key, carried so a reader holding another install's facts can
    confirm an exact match; it never decides a near one."""
    size_bytes: int | None = None
    """The size, the other half of the exact key."""


@dataclass(frozen=True, slots=True)
class Copy:
    """One of the places a redundant asset sits."""

    location_id: str
    root_id: str
    rel_path: str
    filename: str
    size_bytes: int | None
    status: LocationStatus


@dataclass(frozen=True, slots=True)
class Measure:
    """The five numbers a keeper rule may compare, facts about the file and nothing else."""

    asset_id: str
    size_bytes: int | None
    width: int | None
    height: int | None
    #: When this library first saw the file, never a release date, which copies share.
    added_at: int


@dataclass(frozen=True, slots=True)
class Place:
    """Where one asset's bytes sit, by library folder id, since its name is scoped to a viewer."""

    asset_id: str
    root_id: str
    rel_path: str


@dataclass(frozen=True, slots=True)
class Redundancy:
    """An asset whose bytes sit in two or more present places at the same time."""

    asset_id: str
    identity: str
    media_type: str
    copies: tuple[Copy, ...]

    @property
    def reclaimable_bytes(self) -> int:
        """What removing the extras frees: every copy but the largest; unknown sizes count zero."""
        sizes = sorted((copy.size_bytes or 0) for copy in self.copies)
        return sum(sizes[:-1])


# Every asset with a fingerprint, ordered by id so a scan is deterministic; a video with only
# an old `phash` is dropped by the matcher, so the screen can count it.
_FINGERPRINTS = """
SELECT id, identity, media_type, phash, videohash, video_phash, duration_ms,
       fingerprint_version, oshash, size_bytes
  FROM assets
 WHERE phash IS NOT NULL OR video_phash IS NOT NULL
 ORDER BY id
"""

#: The same read within a set of files composed into `{{FILES}}` at import.
FINGERPRINTS_WITHIN = _FINGERPRINTS.replace(
    " WHERE phash IS NOT NULL OR video_phash IS NOT NULL\n",
    " WHERE (phash IS NOT NULL OR video_phash IS NOT NULL)\n   AND id IN ({{FILES}})\n",
)

# Videos the fingerprint pass has not reached, by the pass's own WHERE so the counts agree;
# a standing verdict leaves it, and an older algorithm's fingerprint is still waiting.
_AWAITING_FINGERPRINT = """
SELECT COUNT(*) AS total FROM assets a
 WHERE a.id IN (SELECT id FROM assets WHERE media_type = 'video' AND oshash IS NULL
                UNION
                SELECT id FROM assets WHERE fingerprint_version < ? AND media_type = 'video')
   AND NOT EXISTS (SELECT 1 FROM file_verdicts v
                    WHERE v.asset_id = a.id AND v.product = ? AND v.transient = 0)
"""

# Files the decoder refused, of every kind; a transient verdict clears at the next scan.
_CANNOT_FINGERPRINT = """
SELECT COUNT(*) AS total FROM file_verdicts
 WHERE product = ? AND transient = 0
"""

# One page of assets stored in more than one place; paged on ids inside the subquery so a
# page never ends halfway through an asset's copies.
_REDUNDANCIES_PAGE = """
SELECT a.id           AS asset_id,
       a.identity     AS identity,
       a.media_type   AS media_type,
       l.id           AS location_id,
       l.root_id      AS root_id,
       l.rel_path     AS rel_path,
       l.filename     AS filename,
       l.size_bytes   AS size_bytes,
       l.status       AS status
  FROM assets a
  JOIN asset_locations l ON l.asset_id = a.id AND l.status = 'present'
 WHERE a.id IN (SELECT asset_id
                  FROM asset_locations
                 WHERE status = 'present'
                 GROUP BY asset_id
                HAVING COUNT(*) > 1
                 ORDER BY asset_id
                 LIMIT :limit OFFSET :offset)
 ORDER BY a.id, l.rel_path
"""

# One asset's copies, checked before releasing one.
_REDUNDANCY_FOR = """
SELECT a.id           AS asset_id,
       a.identity     AS identity,
       a.media_type   AS media_type,
       l.id           AS location_id,
       l.root_id      AS root_id,
       l.rel_path     AS rel_path,
       l.filename     AS filename,
       l.size_bytes   AS size_bytes,
       l.status       AS status
  FROM assets a
  JOIN asset_locations l ON l.asset_id = a.id AND l.status = 'present'
 WHERE a.id = :asset_id
 ORDER BY l.rel_path
"""

# A redundant asset's place on the list from zero, counted only up to it; no row if absent.
_REDUNDANCY_POSITION = """
SELECT (SELECT COUNT(*)
          FROM (SELECT asset_id
                  FROM asset_locations
                 WHERE status = 'present' AND asset_id < :asset_id
                 GROUP BY asset_id
                HAVING COUNT(*) > 1)) AS at
 WHERE (SELECT COUNT(*)
          FROM asset_locations
         WHERE asset_id = :asset_id AND status = 'present') > 1
"""

# How many, and the extras' total; `SUM - MAX` matches `Redundancy.reclaimable_bytes`.
_REDUNDANT_TOTALS = """
SELECT COUNT(*)                AS assets,
       COALESCE(SUM(extra), 0) AS reclaimable
  FROM (SELECT COALESCE(SUM(size_bytes), 0) - COALESCE(MAX(size_bytes), 0) AS extra
          FROM asset_locations
         WHERE status = 'present'
         GROUP BY asset_id
        HAVING COUNT(*) > 1)
"""


# Where each file is, present copies only, the first per asset as its record page shows.
_PLACES_OF = """
SELECT asset_id, root_id, rel_path
  FROM asset_locations
 WHERE status = 'present'
   AND asset_id IN (?*)
 ORDER BY asset_id, first_seen_at, id
"""


# By primary key in chunks, so only the queued files are read, never the library.
_MEASURES_OF = """
SELECT id, size_bytes, width, height, added_at
  FROM assets
 WHERE id IN (?*)
"""


@dataclass(frozen=True, slots=True)
class RedundantTotals:
    """How much is stored twice across the library; a population, so not scoped to a viewer."""

    assets: int
    reclaimable_bytes: int


def _fingerprints_of(rows: Sequence[Row]) -> list[Fingerprint]:
    """The rows of `_FINGERPRINTS` as fingerprints, in the order they were read."""
    return [
        Fingerprint(
            asset_id=row["id"],
            identity=row["identity"],
            media_type=row["media_type"],
            phash=row["phash"],
            videohash=row["videohash"],
            video_phash=row["video_phash"],
            duration_ms=row["duration_ms"],
            fingerprint_version=row["fingerprint_version"],
            oshash=row["oshash"],
            size_bytes=row["size_bytes"],
        )
        for row in rows
    ]


class DuplicateReads:
    """The content-table reads duplicate-finding is allowed. One per database."""

    def __init__(self, database: Database) -> None:
        self._db = database

    async def fingerprints(
        self, *, within: tuple[str, Mapping[str, object]] | None = None
    ) -> list[Fingerprint]:
        """Every fingerprinted asset, or those `within` a composed statement, read in one go."""
        # Whole-library: the matcher's block index is built from every fingerprint.
        if within is None:
            rows = await self._db.sweep_all(_FINGERPRINTS, what="duplicate fingerprints")
        else:
            # The set's size, not the library's: a viewer's own files.
            statement, binds = within
            rows = await self._db.fetch_all(statement, binds)
        # On a thread: one object per fingerprinted file, a whole library's worth.
        return await asyncio.to_thread(_fingerprints_of, rows)

    async def awaiting_fingerprint(self) -> int:
        """How many videos nothing has fingerprinted yet, so an empty queue can be read."""
        # Unpacked: a bare aggregate always answers with exactly one row.
        (row,) = await self._db.fetch_all(
            _AWAITING_FINGERPRINT, (FINGERPRINT_VERSION, VerdictProduct.FINGERPRINTS.value)
        )
        return int(row["total"])

    async def cannot_fingerprint(self) -> int:
        """How many files can never be compared, because the decoder refused their frames."""
        (row,) = await self._db.fetch_all(_CANNOT_FINGERPRINT, (VerdictProduct.FINGERPRINTS.value,))
        return int(row["total"])

    async def redundancies_page(self, *, limit: int, offset: int) -> list[Redundancy]:
        """One page of them, whole assets at a time. See `_REDUNDANCIES_PAGE`."""
        return _fold(
            await self._db.fetch_all(_REDUNDANCIES_PAGE, {"limit": limit, "offset": offset})
        )

    async def redundancy_position(self, asset_id: str) -> int | None:
        """Where one asset sits on the list `redundancies_page` pages, or None when not on it."""
        row = await self._db.fetch_one(_REDUNDANCY_POSITION, {"asset_id": asset_id})
        return None if row is None else int(row["at"])

    async def redundancy_for(self, asset_id: str) -> Redundancy | None:
        """One asset's copies, or None under two: losing the last copy would end the asset."""
        found = _fold(await self._db.fetch_all(_REDUNDANCY_FOR, {"asset_id": asset_id}))
        if not found or len(found[0].copies) < 2:
            return None
        return found[0]

    async def redundant_totals(self) -> RedundantTotals:
        """How many assets are stored more than once, and what the extra copies add up to."""
        # Through the sweep lane: a total must visit every group, unlike a page.
        # Unpacked, as in `awaiting_fingerprint`.
        (row,) = await self._db.sweep_all(_REDUNDANT_TOTALS, what="duplicate copy totals")
        return RedundantTotals(assets=int(row["assets"]), reclaimable_bytes=int(row["reclaimable"]))

    async def places_of(self, asset_ids: Sequence[str]) -> dict[str, Place]:
        """Where each file currently sits, the first present copy winning; chunked by page."""
        found: dict[str, Place] = {}
        wanted = list(dict.fromkeys(asset_ids))
        for start in range(0, len(wanted), MAX_PAGE_SIZE):
            sql, params = in_clause(_PLACES_OF, wanted[start : start + MAX_PAGE_SIZE])
            for row in await self._db.fetch_all(sql, params):
                # The first row per asset wins, as the ORDER BY decides.
                found.setdefault(
                    str(row["asset_id"]),
                    Place(
                        asset_id=str(row["asset_id"]),
                        root_id=str(row["root_id"]),
                        rel_path=str(row["rel_path"]),
                    ),
                )
        return found

    async def measures_of(self, asset_ids: Sequence[str]) -> dict[str, Measure]:
        """What a keeper rule compares about each file, keyed by file; absent means it has gone."""
        found: dict[str, Measure] = {}
        wanted = list(dict.fromkeys(asset_ids))
        for start in range(0, len(wanted), MAX_PAGE_SIZE):
            sql, params = in_clause(_MEASURES_OF, wanted[start : start + MAX_PAGE_SIZE])
            for row in await self._db.fetch_all(sql, params):
                found[str(row["id"])] = Measure(
                    asset_id=str(row["id"]),
                    size_bytes=row["size_bytes"],
                    width=row["width"],
                    height=row["height"],
                    added_at=int(row["added_at"]),
                )
        return found


def _fold(rows: list[Row]) -> list[Redundancy]:
    """Flat location rows back into one entry per asset, in one pass over id order."""
    found: list[Redundancy] = []
    current: str | None = None
    copies: list[Copy] = []
    identity = ""
    media_type = ""

    def flush() -> None:
        if current is not None:
            found.append(
                Redundancy(
                    asset_id=current,
                    identity=identity,
                    media_type=media_type,
                    copies=tuple(copies),
                )
            )

    for row in rows:
        if row["asset_id"] != current:
            flush()
            current = row["asset_id"]
            identity = row["identity"]
            media_type = row["media_type"]
            copies = []
        copies.append(_copy_from_row(row))
    flush()

    return found


def _copy_from_row(row: Row) -> Copy:
    return Copy(
        location_id=row["location_id"],
        root_id=row["root_id"],
        rel_path=row["rel_path"],
        filename=row["filename"],
        size_bytes=row["size_bytes"],
        status=LocationStatus(row["status"]),
    )
