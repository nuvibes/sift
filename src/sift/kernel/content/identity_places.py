# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where each asset sits: its locations, the archives that carry them, and whether each copy is there."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path

from sift.kernel.archives import extract_member
from sift.kernel.changes import announce_arrival
from sift.kernel.content.identity_models import Carrier, Location, location_from_row
from sift.kernel.content.identity_paths import _confine_to_root, check_rel_path
from sift.kernel.content.identity_store import StoreCore
from sift.kernel.content.placeless import STRANDED
from sift.kernel.db import in_clause
from sift.kernel.forgetting import forget_everywhere
from sift.kernel.log import get_logger
from sift.kernel.paging import MAX_PAGE_SIZE
from sift.kernel.threads import on_serving_thread

log = get_logger(__name__)

# The asset is untouched: what was recorded about it stays, and the digest reconnects it.
_MARK_MISSING = """
UPDATE asset_locations
   SET status = 'missing'
 WHERE id = ? AND status = 'present'
RETURNING *
"""

# The same bytes back at a missing path (a drive plugged in again): only status and time change.
_MARK_PRESENT = """
UPDATE asset_locations
   SET status = 'present', last_seen_at = ?
 WHERE id = ? AND status = 'missing'
RETURNING *
"""

# The same bytes, a new address, keeping the location id everything holds on to. Present again,
# as a rename can be what explains a location a scan marked missing.
_RELOCATE_LOCATION = """
UPDATE asset_locations
   SET root_id      = ?,
       folder_id    = ?,
       rel_path     = ?,
       filename     = ?,
       status       = 'present',
       last_seen_at = ?
 WHERE id = ?
RETURNING *
"""

_DELETE_LOCATION = "DELETE FROM asset_locations WHERE id = ? RETURNING id"

_DELETE_LOCATIONS = "DELETE FROM asset_locations WHERE id IN (?*) RETURNING asset_id"

_LOCATIONS_OF_ASSETS = (
    "SELECT * FROM asset_locations WHERE asset_id IN (?*) ORDER BY asset_id, first_seen_at, id"
)

_EVERY_LOCATION = "SELECT * FROM asset_locations ORDER BY asset_id, first_seen_at, id"

#: Which files carry any of another library's identities (OSHash, video fingerprint, or a place:
#: folder, path, last part), each list one JSON array. A place probes the folded-name index.
_CARRIERS = """
SELECT 'oshash' AS kind, lower(a.oshash) AS key, NULL AS root_id, NULL AS rel_path,
       a.id AS asset_id
  FROM assets a
 WHERE lower(a.oshash) IN (SELECT value FROM json_each(?))
UNION ALL
SELECT 'phash', lower(a.video_phash), NULL, NULL, a.id
  FROM assets a
 WHERE lower(a.video_phash) IN (SELECT value FROM json_each(?))
UNION ALL
SELECT 'place', NULL, l.root_id, l.rel_path, l.asset_id
  FROM json_each(?) AS w
 CROSS JOIN asset_locations AS l
 WHERE lower(l.filename) = lower(json_extract(w.value, '$[2]'))
   AND l.root_id = json_extract(w.value, '$[0]')
   AND lower(l.rel_path) = lower(json_extract(w.value, '$[1]'))
"""

_COUNT_LOCATIONS = "SELECT COUNT(*) AS remaining FROM asset_locations WHERE asset_id = ?"

_LOCATIONS_OF_ASSET = "SELECT * FROM asset_locations WHERE asset_id = ? ORDER BY first_seen_at, id"

_LOCATION_AT = "SELECT * FROM asset_locations WHERE root_id = ? AND rel_path = ?"

_LOCATION_BY_ID = "SELECT * FROM asset_locations WHERE id = ?"

_ROOT_PATH = "SELECT abs_path FROM library_roots WHERE id = ?"


class Places(StoreCore):
    """Where each asset sits and whether its copies are there."""

    async def relocate(
        self,
        location_id: str,
        *,
        root_id: str,
        rel_path: str,
        folder_id: str | None = None,
    ) -> Location | None:
        """Point a location at a new path, keeping its id, or None for no such row. The index half
        of a move; the disk half is one feature's, called beside this."""
        rel_path = check_rel_path(rel_path)
        rows = await self._write_moving_files(
            _RELOCATE_LOCATION,
            (
                root_id,
                folder_id,
                rel_path,
                rel_path.rsplit("/", 1)[-1],
                self._now(),
                location_id,
            ),
        )
        if not rows:
            return None
        log.info("content.location_relocated", location_id=location_id)
        return location_from_row(rows[0])

    async def remove_location(self, location_id: str) -> bool:
        """Forget one place an asset sits, touching no file and never the asset
        (`remove_asset_if_unplaced`). False for no such location: a race, not an error."""
        rows = await self._write_moving_files(_DELETE_LOCATION, (location_id,))
        if not rows:
            return False
        log.info("content.location_removed", location_id=location_id)
        return True

    async def remove_asset_if_unplaced(self, asset_id: str) -> bool:
        """Remove the asset row once nothing places it, and whether it went: losing the LAST copy
        ends an asset, and this is the only place that is decided."""
        row = await self._db.fetch_one(_COUNT_LOCATIONS, (asset_id,))
        if row is None or row["remaining"] != 0:
            return False
        # The answer comes from the delete: a COUNT over a missing asset is zero too. No picture
        # address counter is raised, or every OTHER picture's address would change.
        async with self._db.write() as connection:
            ended = await self._end_unplaced(connection, [asset_id])
            if not ended:
                return False
            await announce_arrival(connection)

        # The stores SQLite cannot cascade, cleared OUTSIDE the write: they open their own, and the
        # guard refuses one inside another. See `kernel/forgetting.py`.
        cleared = await forget_everywhere(self._db, (asset_id,))

        log.info("content.asset_removed", asset_id=asset_id, cleared=cleared)
        return True

    async def forget_locations(self, location_ids: Sequence[str]) -> list[str]:
        """Drop these copies and end every file left with none, in one transaction and one
        announcement; returns the files that ended."""
        wanted = list(dict.fromkeys(location_ids))
        if not wanted:
            return []
        ended: list[str] = []
        async with self._db.write() as connection:
            touched: list[str] = []
            for start in range(0, len(wanted), MAX_PAGE_SIZE):
                sql, params = in_clause(_DELETE_LOCATIONS, wanted[start : start + MAX_PAGE_SIZE])
                rows = await connection.execute_fetchall(sql, params)
                touched.extend(str(row["asset_id"]) for row in rows)
            ended = await self._end_unplaced(connection, touched)
            if touched:
                await announce_arrival(connection)
        cleared = await forget_everywhere(self._db, ended)
        log.info(
            "content.locations_forgotten",
            locations=len(wanted),
            assets_ended=len(ended),
            cleared=cleared,
        )
        return ended

    async def locations_of(self, asset_ids: Sequence[str]) -> dict[str, list[Location]]:
        """Everywhere each of these files sits, in one read, keyed by file."""
        found: dict[str, list[Location]] = {}
        wanted = list(dict.fromkeys(asset_ids))
        for start in range(0, len(wanted), MAX_PAGE_SIZE):
            sql, params = in_clause(_LOCATIONS_OF_ASSETS, wanted[start : start + MAX_PAGE_SIZE])
            for row in await self._db.fetch_all(sql, params):
                found.setdefault(str(row["asset_id"]), []).append(location_from_row(row))
        return found

    async def every_location(self) -> list[Location]:
        """Every place every file sits, for a whole-library matcher (else `locations_of`)."""
        rows = await self._db.sweep_all(_EVERY_LOCATION, what="every_location")
        return [location_from_row(row) for row in rows]

    async def carriers_of(
        self,
        *,
        oshashes: Sequence[str] = (),
        phashes: Sequence[str] = (),
        places: Sequence[tuple[str, str]] = (),
    ) -> list[Carrier]:
        """Which files carry any of these identities, once per identity carried; `places` are
        (library folder id, path under it). The narrow `every_fingerprint` and `every_location`."""
        if not (oshashes or phashes or places):
            return []
        wanted_places = [[root_id, rel, rel.rsplit("/", 1)[-1]] for root_id, rel in places]
        rows = await self._db.sweep_all(
            _CARRIERS,
            (
                json.dumps([one.lower() for one in oshashes]),
                json.dumps([one.lower() for one in phashes]),
                json.dumps(wanted_places),
            ),
            what="carriers_of",
        )
        return [
            Carrier(
                kind=str(row["kind"]),
                key=row["key"],
                root_id=row["root_id"],
                rel_path=row["rel_path"],
                asset_id=str(row["asset_id"]),
            )
            for row in rows
        ]

    async def mark_present(self, location_id: str) -> bool:
        """The bytes are back, unchanged, at a missing path, so not gated or read again; False
        when the row was not missing."""
        rows = await self._write_moving_files(_MARK_PRESENT, (self._now(), location_id))
        return bool(rows)

    async def mark_missing(self, location_id: str) -> bool:
        """The bytes are not at this path any more; the asset stays. False: nothing to lose."""
        rows = await self._write_moving_files(_MARK_MISSING, (location_id,))
        if not rows:
            return False

        location = location_from_row(rows[0])
        log.info(
            "content.location_missing",
            location_id=location.id,
            asset_id=location.asset_id,
            root_id=location.root_id,
        )
        return True

    async def stranded_asset_ids(self, stranded_before: int) -> list[str]:
        """Assets with no location left, stranded at or before `stranded_before` (or at an unknown
        moment): a fresh strand is left alone while adding its folder back can still restore it."""
        rows = await self._db.fetch_all(STRANDED, (stranded_before,))
        return [str(row["id"]) for row in rows]

    async def locations(self, asset_id: str) -> list[Location]:
        """Everywhere this asset sits, missing too: a visibility check seeing fewer fails open."""
        rows = await self._db.fetch_all(_LOCATIONS_OF_ASSET, (asset_id,))
        return [location_from_row(row) for row in rows]

    async def location(self, location_id: str) -> Location | None:
        """One place, by its id. None if there is no such row."""
        row = await self._db.fetch_one(_LOCATION_BY_ID, (location_id,))
        return None if row is None else location_from_row(row)

    async def location_at(self, root_id: str, rel_path: str) -> Location | None:
        row = await self._db.fetch_one(_LOCATION_AT, (root_id, rel_path))
        return None if row is None else location_from_row(row)

    async def path_of(self, location: Location) -> Path:
        """Where a location's bytes actually are: the ONLY way from an asset to a file, so the
        checks happen once. The path is validated AGAIN on the way out, as rows outlive their
        writers. A picture inside an archive comes out here and nowhere else (`_materialised`).
        """
        if location.inside_an_archive:
            return await self._materialised(location)
        return await self.container_path_of(location)

    async def container_path_of(self, location: Location) -> Path:
        """The file on disk that HOLDS this location's bytes (for an archive member, the ARCHIVE),
        producing nothing: "is it still on disk?" asked of `path_of` would find a cached copy."""
        row = await self._db.fetch_one(_ROOT_PATH, (location.root_id,))
        if row is None:
            raise LookupError(f"location {location.id} points at a root that no longer exists")

        root = Path(str(row["abs_path"]))
        candidate = root / check_rel_path(location.archive_rel_path or location.rel_path)
        # A symlink inside the root can still lead out of it, so the resolved path is confined and
        # opened; resolving reads the disk, so it runs on the SERVING threads.
        return await on_serving_thread(_confine_to_root, candidate, root, location.id)

    #: Where a picture pulled out of an archive is put, under the cache.
    ARCHIVE_CACHE = "archives"

    #: The cap that keeps archives "indexed in place": without it the probe would leave a second
    #: copy of every picture. Two gigabytes is more than one shoot being looked at and far less
    #: than a library of them.
    ARCHIVE_CACHE_BUDGET = 2 * 1024**3

    async def _materialised(self, location: Location) -> Path:
        """One picture pulled out of its archive into the cache, when a reader needs it: a cache,
        never an unpacking. Filed under the ASSET's id (minted per set of bytes), so a copy already
        there is correct. The archive is confined as every read is; `extract_member` re-proves the
        member's name.
        """
        archive = await self.container_path_of(location)
        member = location.member_path or ""
        destination = (
            self._settings.cache_dir / self.ARCHIVE_CACHE / location.asset_id / Path(member).name
        )
        if await asyncio.to_thread(destination.is_file):
            return destination
        await asyncio.to_thread(
            extract_member, archive, member, destination, cache_dir=self._settings.cache_dir
        )
        # Only after writing a NEW one: on every hit, a free read would become a directory walk.
        await asyncio.to_thread(
            _keep_under_budget,
            self._settings.cache_dir / self.ARCHIVE_CACHE,
            self.ARCHIVE_CACHE_BUDGET,
            destination,
        )
        return destination


def _keep_under_budget(directory: Path, budget: int, keep: Path) -> None:
    """Drop the least recently ACCESSED pictures until the directory fits its budget; blocking,
    best-effort, and never `keep`, which a caller is about to open."""
    try:
        files = [path for path in directory.rglob("*") if path.is_file()]
    except OSError:  # pragma: no cover (the directory was removed under us)
        return
    total = 0
    aged: list[tuple[float, int, Path]] = []
    for path in files:
        try:
            stat = path.stat()
        except OSError:  # pragma: no cover (it went away between listing and asking)
            continue
        total += stat.st_size
        aged.append((stat.st_atime, stat.st_size, path))
    if total <= budget:
        return
    for _, size, path in sorted(aged):
        if total <= budget:
            break
        if path == keep:
            continue
        try:
            path.unlink()  # nosemgrep: sift-no-file-removal-outside-delete-trash
        except OSError:  # pragma: no cover (somebody else got there first)
            continue
        total -= size
        # Its directory holds only this picture, so it goes too.
        with suppress(OSError):
            path.parent.rmdir()  # nosemgrep: sift-no-file-removal-outside-delete-trash
