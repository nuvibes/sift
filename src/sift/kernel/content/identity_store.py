# SPDX-License-Identifier: AGPL-3.0-or-later
"""The state every part of the content store shares, and the writes they all go through."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import Any

from sift.kernel.changes import About, announce, announce_arrival, who_may_see_a_file
from sift.kernel.config import Settings
from sift.kernel.content.identity_models import Asset, Location, asset_from_row, location_from_row
from sift.kernel.db import Connection, Database, Row, in_clause
from sift.kernel.ids import new_id
from sift.kernel.ingress import CLASSIFIER_VERSION, MediaType
from sift.kernel.paging import MAX_PAGE_SIZE
from sift.kernel.sorting import sort_key

# The same bytes a second time add a location, never a second asset: the digest's unique index
# decides in one statement, with no read-then-write for two importers to race.
# `DO UPDATE SET identity = excluded.identity` writes nothing; it is there because `DO NOTHING`
# returns no row. The rest of an existing row is kept (its probe results, its first name).
# `filename_sort` is written WITH the name, always (`tests/gates/test_one_ordering.py`).
# `classified_version` is BOUND, never defaulted, or the row would claim the column's first
# generation.
_UPSERT_ASSET = """
INSERT INTO assets
       (id, identity, identity_version, media_type, mime, size_bytes, original_filename,
        filename_sort, added_at, classified_version)
VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(identity) DO UPDATE SET identity = excluded.identity
RETURNING *
"""

# A path holds one file, which the unique index says and this honours: a file replaced in place
# re-points its path at the new content rather than growing a second row nobody could tell apart.
# `first_seen_at` is deliberately not updated: it records when Sift first saw that path, and a
# file coming back from a NAS that was offline for a week has not just appeared.
_ADD_LOCATION = """
INSERT INTO asset_locations (
    id, asset_id, root_id, folder_id, rel_path, filename,
    size_bytes, mtime, status, first_seen_at, last_seen_at,
    archive_rel_path, member_path
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'present', ?, ?, ?, ?)
ON CONFLICT(root_id, rel_path) DO UPDATE SET
    asset_id         = excluded.asset_id,
    folder_id        = excluded.folder_id,
    filename         = excluded.filename,
    size_bytes       = excluded.size_bytes,
    mtime            = excluded.mtime,
    status           = 'present',
    last_seen_at     = excluded.last_seen_at,
   -- Written on the update as well as on the insert. A picture that stops being inside an archive
   -- and becomes a file of its own would otherwise keep pointing into the archive it left: a
   -- full-row writer blanks what it omits, and these two are the columns easiest to forget.
    archive_rel_path = excluded.archive_rel_path,
    member_path      = excluded.member_path
RETURNING *
"""

# The files among these that are nowhere any more. The delete is its own check: a row with a copy
# left is not touched, and a row already gone is not reported.
_END_UNPLACED = """
DELETE FROM assets
 WHERE id IN (?*)
   AND NOT EXISTS (SELECT 1 FROM asset_locations l WHERE l.asset_id = assets.id)
RETURNING id
"""

#: A file that has a place again is not stranded, whatever it was before.
_UNSTRAND = "UPDATE assets SET stranded_at = NULL WHERE id = ? AND stranded_at IS NOT NULL"


class StoreCore:
    """The database, the settings and the clock, and the writes every part of the store goes through."""

    def __init__(
        self,
        database: Database,
        settings: Settings,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._db = database
        self._settings = settings
        #: Whether any row waits to be re-identified, once asked. See `legacy_identities_remain`.
        self._legacy_remaining: bool | None = None
        self._clock = clock

    def _now(self) -> int:
        return int(self._clock())

    async def _end_unplaced(self, connection: Connection, asset_ids: Sequence[str]) -> list[str]:
        """Remove the rows among these with no copy left. The one statement that ends a file."""
        if not asset_ids:
            return []
        ended: list[str] = []
        wanted = list(dict.fromkeys(asset_ids))
        for start in range(0, len(wanted), MAX_PAGE_SIZE):
            sql, params = in_clause(_END_UNPLACED, wanted[start : start + MAX_PAGE_SIZE])
            rows = await connection.execute_fetchall(sql, params)
            ended.extend(str(row["id"]) for row in rows)
        return ended

    async def _write(self, sql: str, params: tuple[Any, ...]) -> list[Row]:
        async with self._db.write() as connection:
            return list(await connection.execute_fetchall(sql, params))

    async def _write_record(self, sql: str, params: tuple[Any, ...]) -> list[Row]:
        """A write to a file's own record, told to whoever may be drawing that file, here once
        for every caller, and only when a row moved."""
        async with self._db.write() as connection:
            before = connection.total_changes
            rows = list(await connection.execute_fetchall(sql, params))
            if connection.total_changes != before:
                announce(await who_may_see_a_file(connection), About.LIBRARY)
            return rows

    async def _write_moving_files(
        self,
        sql: str,
        params: tuple[Any, ...],
        *,
        then: tuple[str, tuple[Any, ...]] | None = None,
    ) -> list[Row]:
        """A write that changes where a file is, announced only when a row moved.

        `then` is a second statement of the same fact, committed with the first and only where
        the first found its row."""
        async with self._db.write() as connection:
            rows = list(await connection.execute_fetchall(sql, params))
            if rows:
                if then is not None:
                    await connection.execute(*then)
                await announce_arrival(connection)
            return rows

    async def _upsert_asset(
        self,
        connection: Connection,
        *,
        digest: str,
        media: MediaType,
        size_bytes: int,
        original_filename: str | None,
        now: int,
    ) -> tuple[Asset, bool]:
        """Runs on the caller's connection so it can share a transaction with the location."""
        minted = new_id()
        rows = list(
            await connection.execute_fetchall(
                _UPSERT_ASSET,
                (
                    minted,
                    digest,
                    media.kind.value,
                    media.mime,
                    size_bytes,
                    original_filename,
                    sort_key(original_filename) if original_filename else None,
                    now,
                    CLASSIFIER_VERSION,
                ),
            )
        )
        asset = asset_from_row(rows[0])
        # An id other than the one just minted means the digest's index hit an existing asset.
        return asset, asset.id == minted

    async def _add_location(
        self,
        connection: Connection,
        *,
        asset_id: str,
        root_id: str,
        folder_id: str | None,
        rel_path: str,
        size_bytes: int | None,
        mtime: int | None,
        now: int,
        archive_rel_path: str | None = None,
        member_path: str | None = None,
    ) -> Location:
        rows = list(
            await connection.execute_fetchall(
                _ADD_LOCATION,
                (
                    new_id(),
                    asset_id,
                    root_id,
                    folder_id,
                    rel_path,
                    rel_path.rsplit("/", 1)[-1],
                    size_bytes,
                    mtime,
                    now,
                    now,
                    archive_rel_path,
                    member_path,
                ),
            )
        )
        # A place again, so no longer stranded: the folder came back, or the bytes turned up
        # somewhere else Sift can read.
        await connection.execute(_UNSTRAND, (asset_id,))
        return location_from_row(rows[0])
