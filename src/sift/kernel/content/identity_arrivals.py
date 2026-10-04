# SPDX-License-Identifier: AGPL-3.0-or-later
"""Taking a file in: the asset its bytes are, and the older whole-file digests still waiting to be read again."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence

from sift.kernel.changes import announce_arrival
from sift.kernel.content.hashing import hash_file, identity_file
from sift.kernel.content.identity_models import (
    Asset,
    Ingested,
    Location,
    VerdictProduct,
    asset_from_row,
)
from sift.kernel.content.identity_paths import check_rel_path
from sift.kernel.content.identity_store import StoreCore
from sift.kernel.db import Connection, IntegrityError
from sift.kernel.ingress import IngressResult, MediaType
from sift.kernel.log import get_logger
from sift.kernel.text import clean_stored_text

log = get_logger(__name__)

# A row at `identity_version = 0` holds the whole-file digest until the re-identifying pass brings
# it forward; meanwhile a file arriving with a waiting row's size is digested both ways, so a copy
# of an old file finds its asset rather than becoming a second one.

#: Whether any row is still waiting. A row the pass has given up on for good is not waiting, or
#: it would keep every import reading whole files.
_LEGACY_IDENTITIES_REMAIN = """
SELECT 1 FROM assets a
 WHERE a.identity_version = 0
   AND NOT EXISTS (SELECT 1 FROM file_verdicts v
                    WHERE v.asset_id = a.id AND v.product = ? AND v.transient = 0)
 LIMIT 1
"""

#: Whether a waiting row could be a file of this many bytes: bytes of another length cannot have
#: its digest. A row with no size counts; a row given up on only for now still waits.
_LEGACY_OF_SIZE = """
SELECT 1 FROM assets a
 WHERE a.identity_version = 0
   AND (a.size_bytes = ? OR a.size_bytes IS NULL)
   AND NOT EXISTS (SELECT 1 FROM file_verdicts v
                    WHERE v.asset_id = a.id AND v.product = ? AND v.transient = 0)
 LIMIT 1
"""

#: An old row for these exact bytes, by the whole-file digest it was written with.
_LEGACY_BY_WHOLE_DIGEST = "SELECT * FROM assets WHERE identity = ? AND identity_version = 0"

#: Bring one old row forward; the version in the WHERE makes it safe to run twice.
_ADOPT_IDENTITY = """
UPDATE assets
   SET whole_digest = identity, identity = ?, identity_version = 1
 WHERE id = ? AND identity_version = 0
RETURNING *
"""

#: The same exclusion as `_LEGACY_IDENTITIES_REMAIN`, so the page and the flag are one set.
_LEGACY_IDENTITY_PAGE = """
SELECT a.id FROM assets a
 WHERE a.identity_version = 0
   AND NOT EXISTS (SELECT 1 FROM file_verdicts v
                    WHERE v.asset_id = a.id AND v.product = ? AND v.transient = 0)
 ORDER BY a.added_at, a.id
 LIMIT ?
"""

_COUNT_LEGACY_IDENTITIES = """
SELECT COUNT(*) AS total FROM assets a
 WHERE a.identity_version = 0
   AND NOT EXISTS (SELECT 1 FROM file_verdicts v
                    WHERE v.asset_id = a.id AND v.product = ? AND v.transient = 0)
"""

_ASSET_BY_ID = "SELECT * FROM assets WHERE id = ?"

_EXISTING_IDS = "SELECT id FROM assets WHERE id IN (SELECT value FROM json_each(?))"

_ASSET_BY_IDENTITY = "SELECT * FROM assets WHERE identity = ?"


class Arrivals(StoreCore):
    """Taking a file in, and the identities still to be read again."""

    async def ingest(
        self,
        checked: IngressResult,
        *,
        root_id: str,
        rel_path: str,
        folder_id: str | None = None,
        archive_rel_path: str | None = None,
        member_path: str | None = None,
        mtime: int | None = None,
    ) -> Ingested:
        """Hash a file the ingress gate verified (its proof, never a path) and record what it is
        and where it sits, asset and location in ONE transaction: half of it is worse than none.
        `archive_rel_path` and `member_path` say which archive member the verified copy is.
        """
        rel_path = check_rel_path(rel_path)
        digest = await identity_file(checked)
        # Both ways while a waiting old row could be these bytes: only its own digest finds it.
        whole = await hash_file(checked) if await self._legacy_could_be(checked.size) else None
        stat = await asyncio.to_thread(checked.path.stat)
        now = self._now()
        # A caller's `mtime` wins when the hashed file is a scratch copy (an archive member): its
        # own stamp would read "changed" on every scan. See `_unchanged_since_last_scan`.
        stamped = int(stat.st_mtime) if mtime is None else mtime

        async with self._db.write() as connection:
            adopted = None
            if whole is not None:
                adopted = await self._adopt_legacy(connection, whole=whole, digest=digest)
            if adopted is not None:
                asset, is_new = adopted, False
            else:
                asset, is_new = await self._upsert_asset(
                    connection,
                    digest=digest,
                    media=checked.media,
                    size_bytes=checked.size,
                    # INDEXED: a control character kept would make it findable by nothing.
                    original_filename=clean_stored_text(checked.path.name),
                    now=now,
                )
            location = await self._add_location(
                connection,
                asset_id=asset.id,
                root_id=root_id,
                folder_id=folder_id,
                rel_path=rel_path,
                size_bytes=checked.size,
                mtime=stamped,
                now=now,
                archive_rel_path=archive_rel_path,
                member_path=member_path,
            )
            # In the same transaction, so a screen is never told about a file that then rolls back.
            await announce_arrival(connection)

        log.info(
            "content.ingested",
            asset_id=asset.id,
            location_id=location.id,
            new_asset=is_new,
            origin=str(checked.origin),
            file_type=checked.media.name,
            file_size=checked.size,
        )
        return Ingested(asset=asset, location=location, asset_is_new=is_new)

    async def upsert_asset(
        self,
        *,
        digest: str,
        media: MediaType,
        size_bytes: int,
        original_filename: str | None = None,
    ) -> tuple[Asset, bool]:
        """Find or create the asset for a digest. Returns it, and whether it is new."""
        async with self._db.write() as connection:
            return await self._upsert_asset(
                connection,
                digest=digest,
                media=media,
                size_bytes=size_bytes,
                original_filename=original_filename,
                now=self._now(),
            )

    async def add_location(
        self,
        *,
        asset_id: str,
        root_id: str,
        rel_path: str,
        folder_id: str | None = None,
        size_bytes: int | None = None,
        mtime: int | None = None,
    ) -> Location:
        """Record a place an asset's bytes sit. A path already known is updated, not duplicated."""
        async with self._db.write() as connection:
            return await self._add_location(
                connection,
                asset_id=asset_id,
                root_id=root_id,
                folder_id=folder_id,
                rel_path=check_rel_path(rel_path),
                size_bytes=size_bytes,
                mtime=mtime,
                now=self._now(),
            )

    async def get(self, asset_id: str) -> Asset | None:
        row = await self._db.fetch_one(_ASSET_BY_ID, (asset_id,))
        return None if row is None else asset_from_row(row)

    async def legacy_identities_remain(self) -> bool:
        """Whether any row waits to be re-identified; remembered, since every file taken in asks,
        and forgotten by a row brought forward or a verdict on one the pass cannot sample."""
        if self._legacy_remaining is None:
            row = await self._db.fetch_one(
                _LEGACY_IDENTITIES_REMAIN, (VerdictProduct.IDENTITY.value,)
            )
            self._legacy_remaining = row is not None
        return self._legacy_remaining

    async def _legacy_could_be(self, size_bytes: int) -> bool:
        """Whether a waiting row could hold a file of this size; the flag first, so a library with
        nothing waiting pays nothing."""
        if not await self.legacy_identities_remain():
            return False
        row = await self._db.fetch_one(_LEGACY_OF_SIZE, (size_bytes, VerdictProduct.IDENTITY.value))
        return row is not None

    async def legacy_identity_count(self) -> int:
        """How many rows are still waiting. What the banner and the pass's bar need."""
        (row,) = await self._db.fetch_all(
            _COUNT_LEGACY_IDENTITIES, (VerdictProduct.IDENTITY.value,)
        )
        return int(row["total"])

    async def legacy_identity_page(self, limit: int) -> list[str]:
        """The next files for the re-identifying pass, oldest first. A page at a time, because the
        caller is a job that asks for itself again."""
        rows = await self._db.fetch_all(
            _LEGACY_IDENTITY_PAGE, (VerdictProduct.IDENTITY.value, limit)
        )
        return [row["id"] for row in rows]

    async def adopt_identity(self, asset_id: str, digest: str) -> bool:
        """Bring one old row forward to the sampled identity. False when it is not old any more,
        or another row holds the identity; such a row stays at version 0, counted, never hidden."""
        try:
            async with self._db.write() as connection:
                rows = list(await connection.execute_fetchall(_ADOPT_IDENTITY, (digest, asset_id)))
        except IntegrityError:
            log.warning("content.identity_collision", asset_id=asset_id)
            return False
        self._legacy_remaining = None
        return bool(rows)

    async def _adopt_legacy(
        self, connection: Connection, *, whole: str, digest: str
    ) -> Asset | None:
        """An old row for these exact bytes, brought forward on the caller's connection. None
        when there is none or another row holds the identity: the caller then upserts."""
        found = list(await connection.execute_fetchall(_LEGACY_BY_WHOLE_DIGEST, (whole,)))
        if not found:
            return None
        try:
            rows = list(
                await connection.execute_fetchall(_ADOPT_IDENTITY, (digest, found[0]["id"]))
            )
        except IntegrityError:
            log.warning("content.identity_collision", asset_id=found[0]["id"])
            return None
        # One row fewer waits, and it may have been the last: the flag is asked again.
        self._legacy_remaining = None
        return asset_from_row(rows[0]) if rows else None

    async def resolve_by_identity(self, digest: str) -> Asset | None:
        """The asset for this identity: what makes a moved file a moved file, not a new one."""
        row = await self._db.fetch_one(_ASSET_BY_IDENTITY, (digest,))
        return None if row is None else asset_from_row(row)

    async def existing_ids(self, asset_ids: Sequence[str]) -> set[str]:
        """Which of these ids are still assets, in one statement, for a feature whose own table
        (a virtual one) cannot carry a foreign key back here."""
        if not asset_ids:
            return set()
        rows = await self._db.fetch_all(_EXISTING_IDS, (json.dumps(list(asset_ids)),))
        return {str(row["id"]) for row in rows}
