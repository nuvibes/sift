# SPDX-License-Identifier: AGPL-3.0-or-later
"""What Sift builds from an asset: where each picture is kept, which files still lack one, and the sweep of old recipes."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from sift.kernel.changes import announce_arrival
from sift.kernel.content.hashing import digest_cache_file
from sift.kernel.content.identity_models import (
    PICTURE_KINDS,
    RECIPE_VERSIONS,
    Derivative,
    DerivativeKind,
    FolderMedia,
    _made_for_sql,
    derivative_from_row,
)
from sift.kernel.content.identity_paths import (
    _confined_cache_file,
    check_rel_path,
    derivative_relpath,
    params_key,
)
from sift.kernel.content.identity_store import StoreCore
from sift.kernel.content.presence import HAS_A_PRESENT_COPY
from sift.kernel.db import Row, in_clause
from sift.kernel.ids import new_id
from sift.kernel.log import get_logger
from sift.kernel.paging import MAX_PAGE_SIZE
from sift.kernel.sql_splice import splice
from sift.kernel.threads import on_serving_thread

log = get_logger(__name__)

_DERIVATIVES_OF_ASSETS = "SELECT * FROM derivatives WHERE asset_id IN (?*) ORDER BY asset_id, kind"

# Same asset, kind and settings is the same derivative: a rebuild replaces the row.
_ADD_DERIVATIVE = """
INSERT INTO derivatives
       (id, asset_id, kind, rel_cache_path, params, size_bytes, content_hash, recipe_version,
        created_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(asset_id, kind, params) DO UPDATE SET
    rel_cache_path = excluded.rel_cache_path,
    size_bytes     = excluded.size_bytes,
    content_hash   = excluded.content_hash,
   -- The replacement was built by whatever recipe is in use now, which is the whole point of
   -- rebuilding it. A row left at the old number would be picked up by the next catch-up pass
   -- and built all over again.
    recipe_version = excluded.recipe_version,
    created_at     = excluded.created_at
RETURNING *
"""

_DERIVATIVES_OF_ASSET = "SELECT * FROM derivatives WHERE asset_id = ? ORDER BY kind, params"

#: For a feature switched off: the rows carry the paths, so the files go with them.
_DERIVATIVES_OF_KIND = "SELECT * FROM derivatives WHERE kind = ?"

_DELETE_DERIVATIVES_OF_KIND = "DELETE FROM derivatives WHERE kind = ? RETURNING id"

_DERIVATIVE_TOTALS = "SELECT COUNT(*) AS total, COALESCE(SUM(size_bytes), 0) AS bytes FROM derivatives WHERE kind = ?"

_ALL_DERIVATIVE_PATHS = "SELECT rel_cache_path FROM derivatives"

# Every file that COULD have a picture (`probed_at`, not the derivative table: a rebuild is for the
# missing and the wrong ones), with no `LIMIT`: the confirm dialog's count must be whole.
_THUMBNAILABLE = """
SELECT id FROM assets
WHERE probed_at IS NOT NULL
ORDER BY added_at, id
"""

_COUNT_THUMBNAILABLE = "SELECT COUNT(*) AS total FROM assets WHERE probed_at IS NOT NULL"

#: Which of THESE files lack a derivative of a kind at the recipe in use: an older recipe's row is
#: work still to do. `>=`, not `=`: a row a newer Sift built is not work for this one.
_LACKING_DERIVATIVE = """
SELECT a.id FROM assets a
WHERE a.id IN (?*)
  AND a.probed_at IS NOT NULL
  AND {{MADE_FOR}}
  AND NOT EXISTS (SELECT 1 FROM derivatives d
                   WHERE d.asset_id = a.id AND d.kind = ? AND d.recipe_version >= ?)
"""

_LACKING_DERIVATIVE_OF = {
    kind: splice(_LACKING_DERIVATIVE, MADE_FOR=_made_for_sql(kind)) for kind in DerivativeKind
}

# Files whose hover clip was built with other settings. A file with NO clip is not here: this
# replaces what exists, and a scan fills in what does not.
_PREVIEWS_OF_ANOTHER_RECIPE = """
SELECT DISTINCT asset_id AS id FROM derivatives
WHERE kind = 'preview' AND params <> ?
ORDER BY asset_id
"""

_COUNT_PREVIEWS_OF_ANOTHER_RECIPE = """
SELECT COUNT(DISTINCT asset_id) AS total FROM derivatives
WHERE kind = 'preview' AND params <> ?
"""

# One asset's clips built some other way, to go once the replacement is in place.
_SUPERSEDED_PREVIEWS = """
SELECT * FROM derivatives
WHERE asset_id = ? AND kind = 'preview' AND params <> ?
"""

_FORGET_DERIVATIVE = "DELETE FROM derivatives WHERE id = ?"

#: Every HEIF still (`heif.HEIF_MIMES`) by id, with when its readable copy was written.
_HEIF_STILLS = """
SELECT a.id AS id, d.created_at AS copied_at
  FROM assets a
  LEFT JOIN derivatives d ON d.asset_id = a.id AND d.kind = 'rendition'
 WHERE a.media_type = 'image' AND COALESCE(a.mime, '') IN ('image/heic', 'image/avif')
   AND a.id > ?
 ORDER BY a.id
 LIMIT ?
"""

#: Videos measured as needing repair with no repaired copy: what a switch turned back ON must find,
#: as the backfill pass only looks at unmeasured files.
_NEEDING_REMUX = splice(
    """
SELECT a.id FROM assets a
WHERE a.interleave_gap >= ?
  AND NOT EXISTS (SELECT 1 FROM derivatives d WHERE d.asset_id = a.id AND d.kind = 'remux')
  AND {{PRESENT}}
ORDER BY a.added_at, a.id
LIMIT ?
""",
    PRESENT=HAS_A_PRESENT_COPY,
)

#: What counts as a still: a GIF too, as a folder of GIFs is a set of pictures. Video is not.
_STILL_TYPES = frozenset({"image", "gif"})

_STILLS_AMONG = "SELECT id FROM assets WHERE media_type IN ('image','gif') AND id IN (?*)"

#: What one folder directly holds (never the subtree), by present copies only, and never an
#: archive's member, which belongs to the archive's own set (`set_from_archive`).
_FOLDER_MEDIA = """
SELECT a.id AS id, a.media_type AS media_type, l.rel_path AS rel_path
  FROM asset_locations l
  JOIN assets a ON a.id = l.asset_id
 WHERE l.folder_id = ?
   AND l.member_path IS NULL
   AND l.status = 'present'
 ORDER BY l.rel_path COLLATE NOCASE
"""


class Derivatives(StoreCore):
    """What is built from each asset, and which files still lack it."""

    async def derivatives_of(self, asset_ids: Sequence[str]) -> dict[str, list[Derivative]]:
        """The generated pictures of each of these files, in one read, keyed by file."""
        found: dict[str, list[Derivative]] = {}
        wanted = list(dict.fromkeys(asset_ids))
        for start in range(0, len(wanted), MAX_PAGE_SIZE):
            sql, params = in_clause(_DERIVATIVES_OF_ASSETS, wanted[start : start + MAX_PAGE_SIZE])
            for row in await self._db.fetch_all(sql, params):
                found.setdefault(str(row["asset_id"]), []).append(derivative_from_row(row))
        return found

    async def derivative_paths(self) -> set[str]:
        """Every cache path a derivative row names, for finding the files that no row does."""
        # Whole-library on purpose: the disk cannot be asked about a subset.
        rows = await self._db.sweep_all(_ALL_DERIVATIVE_PATHS, (), what="cache paths on disk")
        return {str(row["rel_cache_path"]) for row in rows}

    def derivative_path(
        self,
        asset_id: str,
        kind: DerivativeKind,
        *,
        extension: str,
        params: Mapping[str, Any] | None = None,
    ) -> Path:
        """Where to write a derivative: always in the cache, the only directory Sift owns."""
        relative = derivative_relpath(asset_id, kind, extension=extension, params=params)
        return self._settings.cache_dir / relative

    async def derivative_at(self, rel_cache_path: str) -> Path | None:
        """The file a derivative row points at, re-confined on the way out as `path_of` does.

        None for an absent file (the cache is disposable); a path outside the cache raises."""
        try:
            candidate = self._settings.cache_dir / check_rel_path(rel_cache_path)
        except ValueError:
            raise ValueError("a derivative row names a path that is not a relative path") from None
        # On the SERVING threads: on the shared pool every picture would queue behind background
        # work. One hop, not two (see `_confined_cache_file`).
        return await on_serving_thread(_confined_cache_file, candidate, self._settings.cache_dir)

    async def _write_pictures(self, sql: str, params: tuple[Any, ...]) -> list[Row]:
        """A write to one of a file's pictures, told as an arrival when a row moved: a picture
        never touches the file's own row, so an open tile would not otherwise redraw."""
        async with self._db.write() as connection:
            before = connection.total_changes
            rows = list(await connection.execute_fetchall(sql, params))
            if connection.total_changes != before:
                await announce_arrival(connection)
            return rows

    async def add_derivative(
        self,
        asset_id: str,
        kind: DerivativeKind,
        *,
        extension: str,
        params: Mapping[str, Any] | None = None,
        size_bytes: int | None = None,
    ) -> Derivative:
        """Record a derivative at the path this module chose. The path, the digest (None when
        unreadable) and the recipe are decided HERE, never by a caller, so no caller can write into
        a library or stamp a wrong number; screens are told through `_write_pictures`.
        """
        relative = derivative_relpath(asset_id, kind, extension=extension, params=params)
        digest = (
            await digest_cache_file(self._settings.cache_dir / relative)
            if kind in PICTURE_KINDS
            else None
        )
        rows = await self._write_pictures(
            _ADD_DERIVATIVE,
            (
                new_id(),
                asset_id,
                kind.value,
                relative,
                params_key(params),
                size_bytes,
                digest,
                RECIPE_VERSIONS[kind],
                self._now(),
            ),
        )
        return derivative_from_row(rows[0])

    async def needing_remux(self, gap: int, limit: int) -> list[str]:
        """Files needing repair with no repaired copy, for a switch turned back on; a file with no
        readable copy is left out, as its job could only fail."""
        rows = await self._db.fetch_all(_NEEDING_REMUX, (gap, limit))
        return [row["id"] for row in rows]

    async def lacking_derivative(self, kind: DerivativeKind, asset_ids: Sequence[str]) -> set[str]:
        """Which of these files have no derivative of this kind."""
        if not asset_ids:
            return set()
        sql, params = in_clause(_LACKING_DERIVATIVE_OF[kind], list(asset_ids))
        rows = await self._db.fetch_all(sql, [*params, kind.value, RECIPE_VERSIONS[kind]])
        return {str(row["id"]) for row in rows}

    async def thumbnailable(self) -> list[str]:
        """Every file a picture could be made of, ids only: one job each, and the queue paces."""
        rows = await self._db.sweep_all(_THUMBNAILABLE, what="thumbnailable assets")
        return [row["id"] for row in rows]

    async def previews_of_another_recipe(self, params: Mapping[str, Any]) -> list[str]:
        """Every file whose hover clip was built some other way; whole, as `thumbnailable` is."""
        rows = await self._db.sweep_all(
            _PREVIEWS_OF_ANOTHER_RECIPE, (params_key(params),), what="previews to rebuild"
        )
        return [row["id"] for row in rows]

    async def previews_of_another_recipe_count(self, params: Mapping[str, Any]) -> int:
        """How many files a preview rebuild would touch, for the confirm dialog."""
        (row,) = await self._db.fetch_all(_COUNT_PREVIEWS_OF_ANOTHER_RECIPE, (params_key(params),))
        return int(row["total"])

    async def forget_superseded_previews(
        self, asset_id: str, params: Mapping[str, Any]
    ) -> list[str]:
        """Drop the rows of this file's older clips, only once the replacement is recorded, and
        name their files for the caller to delete after: a stray file is swept, a stray row breaks.
        """
        rows = await self._db.fetch_all(_SUPERSEDED_PREVIEWS, (asset_id, params_key(params)))
        paths = [str(row["rel_cache_path"]) for row in rows]
        for row in rows:
            await self._write_pictures(_FORGET_DERIVATIVE, (str(row["id"]),))
        return paths

    async def thumbnailable_count(self) -> int:
        """How many files a rebuild would touch, for the confirm dialog."""
        # Unpacked: a bare aggregate always answers with one row.
        (row,) = await self._db.fetch_all(_COUNT_THUMBNAILABLE, ())
        return int(row["total"])

    async def heif_stills(
        self, *, after: str = "", limit: int = MAX_PAGE_SIZE
    ) -> list[tuple[str, int | None]]:
        """Every HEIF still by id, with when its whole-picture copy was written (None before it
        has one), a page after `after`: a result older than the copy was read from one tile."""
        rows = await self._db.fetch_all(_HEIF_STILLS, (after, limit))
        return [
            (str(row["id"]), None if row["copied_at"] is None else int(row["copied_at"]))
            for row in rows
        ]

    async def stills_among(self, asset_ids: Sequence[str]) -> list[str]:
        """Which of these are photographs or GIFs, IN THE ORDER GIVEN: a gallery arrives numbered.
        Unscoped: it answers what files ARE, not who may see them."""
        if not asset_ids:
            return []
        query, params = in_clause(_STILLS_AMONG, asset_ids)
        rows = await self._db.fetch_all(query, params)
        still = {str(row["id"]) for row in rows}
        return [asset_id for asset_id in asset_ids if asset_id in still]

    async def folder_media(self, folder_id: str) -> FolderMedia:
        """What one folder directly holds: its stills in name order (a shoot's numbering), and how
        much of it moves. Unscoped: a background pass asks it, and nothing here is served."""
        rows = await self._db.fetch_all(_FOLDER_MEDIA, (folder_id,))
        stills = [str(row["id"]) for row in rows if row["media_type"] in _STILL_TYPES]
        return FolderMedia(still_ids=stills, moving=len(rows) - len(stills))

    async def derivatives(self, asset_id: str) -> list[Derivative]:
        rows = await self._db.fetch_all(_DERIVATIVES_OF_ASSET, (asset_id,))
        return [derivative_from_row(row) for row in rows]

    async def derivative_totals(self, kind: DerivativeKind) -> tuple[int, int]:
        """How many derivatives of one kind there are, and their bytes together: a count, no files."""
        (row,) = await self._db.fetch_all(_DERIVATIVE_TOTALS, (kind.value,))
        return int(row["total"]), int(row["bytes"])

    async def drop_derivatives(self, kind: DerivativeKind) -> tuple[int, int]:
        """Remove every derivative of one kind (all rebuildable), returning how many and their
        bytes. Files first, rows after: a row left over is found by the sweep, a file is not."""
        rows = await self._db.fetch_all(_DERIVATIVES_OF_KIND, (kind.value,))
        freed = 0
        for row in rows:
            derivative = derivative_from_row(row)
            path = await self.derivative_at(derivative.rel_cache_path)
            if path is None:
                continue
            freed += derivative.size_bytes or 0
            await asyncio.to_thread(path.unlink, True)
        removed = await self._write_pictures(_DELETE_DERIVATIVES_OF_KIND, (kind.value,))
        if removed:
            log.info(
                "content.derivatives_dropped", kind=kind.value, count=len(removed), bytes=freed
            )
        return len(removed), freed
