# SPDX-License-Identifier: AGPL-3.0-or-later
"""Everything this feature reads and writes, and nothing that decides anything."""

from __future__ import annotations

import time
from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass

from sift.kernel.access import catalog
from sift.kernel.access.stamps import bump_stamps_for_object
from sift.kernel.access.viewer import ObjectType
from sift.kernel.changes import About, announce
from sift.kernel.content import Lack
from sift.kernel.db import Connection, Database, in_clause, point_read
from sift.kernel.sorting import sort_key
from sift.slices.watermarks import signatures, weights

#: Paged so a sweep is resumable and never holds a worker for hours.
PAGE = 500

#: Older SQLite builds cap a statement at 999 variables.
_AT_A_TIME = 500

_COUNT_READ = "SELECT COUNT(*) AS total FROM watermark_scans WHERE revision = ?"
#: Only readings the current models and matcher stand behind.
_COUNT_FOUND = (
    "SELECT COUNT(*) AS total FROM watermark_reads WHERE revision = ? AND matcher_version = ?"
)

#: The "from this site, poster unknown" row: the site's name with an empty username.
_UNATTRIBUTED_ON = point_read(
    "watermarks.unattributed_username",
    """
    SELECT ac.id AS id
      FROM usernames ac
      JOIN sites p ON p.id = ac.site_id
     WHERE p.name = ? AND ac.name = ''
    """,
)

_USERNAMES_ON = """
SELECT ac.name AS name
  FROM usernames ac
  JOIN sites p ON p.id = ac.site_id
 WHERE p.name = ? AND ac.name <> ''
"""

#: A bare `@name` names no site, so the library answers; NOCASE matches the column's index.
_SITES_WITH_USERNAME = """
SELECT p.name AS site, ac.id AS id, ac.name AS name
  FROM usernames ac
  JOIN sites p ON p.id = ac.site_id
 WHERE ac.name = ? COLLATE NOCASE AND ac.name <> ''
"""

#: Made if missing; `sift` and `watermark` are the words a filing from this pass carries.
_MAKE_TAG = """
INSERT INTO tags (id, name, name_sort, created_at, created_by_kind, created_by_via)
VALUES (?, ?, ?, ?, 'sift', 'watermark')
ON CONFLICT(name) DO NOTHING
"""

#: By name, so a renamed tag is made again under the old name.
_TAG_BY_NAME = "SELECT id FROM tags WHERE name = ?"

#: `OR IGNORE` keeps the first decision time on a second read.
_WEAR_TAG = (
    "INSERT OR IGNORE INTO asset_tags (asset_id, tag_id, source, decided_at) VALUES (?, ?, ?, ?)"
)

_REMEMBER = (
    "INSERT INTO watermark_scans (asset_id, revision, identity, found, scanned_at) "
    "VALUES (?, ?, ?, ?, ?) ON CONFLICT(asset_id) DO UPDATE SET "
    "revision = excluded.revision, identity = excluded.identity, found = excluded.found, "
    "scanned_at = excluded.scanned_at"
)

#: `revision` is read from the scan row written just before, so the two cannot differ.
_WRITE_READ = (
    "INSERT INTO watermark_reads "
    "(asset_id, text, kind, site, username, confidence, frame_ms, revision, matcher_version, "
    "read_at) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, "
    "(SELECT s.revision FROM watermark_scans AS s WHERE s.asset_id = ?), ?, ?) "
    "ON CONFLICT(asset_id) DO UPDATE SET "
    "text = excluded.text, kind = excluded.kind, site = excluded.site, username = excluded.username, "
    "confidence = excluded.confidence, frame_ms = excluded.frame_ms, "
    "revision = excluded.revision, matcher_version = excluded.matcher_version, "
    "read_at = excluded.read_at"
)

_CLEAR_READ = "DELETE FROM watermark_reads WHERE asset_id = ?"

#: The Site's id, by the name the filing used, in the filing's own transaction.
_NAME_SITE = (
    "UPDATE watermark_reads SET site_id = (SELECT s.id FROM sites s WHERE s.name = ?)"
    " WHERE asset_id = ? AND site <> ''"
)

_UNFILE = "DELETE FROM asset_usernames WHERE username_id = ? AND source = ? AND asset_id IN (?*)"

_REFUSE = (
    "INSERT INTO watermark_refusals (asset_id, created_at) VALUES (?, ?) "
    "ON CONFLICT(asset_id) DO NOTHING"
)

_FORGET_SCANS = "DELETE FROM watermark_scans"
_FORGET_READS = "DELETE FROM watermark_reads"

#: Only this feature's tables, never `assets`: an upper bound on the catch-up count.
_LACKS_READING = (
    "(NOT EXISTS (SELECT 1 FROM watermark_scans s"
    " WHERE s.asset_id = a.id AND s.revision = ? AND s.identity = a.identity)"
    " OR EXISTS (SELECT 1 FROM watermark_reads m"
    " WHERE m.asset_id = a.id AND m.matcher_version IS NOT ?))"
    " AND NOT EXISTS (SELECT 1 FROM watermark_refusals r WHERE r.asset_id = a.id)"
)


@dataclass(frozen=True, slots=True)
class Read:
    text: str
    #: See `signatures.SITE`; says whether `site` means anything here.
    kind: str
    #: Empty, not None, for a kind that names no site (see `schema.py`).
    site: str
    username: str | None
    confidence: float
    frame_ms: int | None


class Store:
    def __init__(self, database: Database) -> None:
        self._db = database

    @staticmethod
    def _now() -> int:
        return int(time.time())

    def write(self) -> AbstractAsyncContextManager[Connection]:
        """A transaction, so a filing and its receipt are one action."""
        return self._db.write()

    async def unread(self, revision: str, *, limit: int = PAGE) -> list[str]:
        """Files nothing has looked at yet, asked of the kernel: `assets` carries permissions."""
        return await catalog.unread_by_picture(self._db, revision, limit=limit)

    async def unread_count(self, revision: str, *, limit: int = PAGE) -> int:
        return await catalog.count_unread_by_picture(self._db, revision, limit=limit)

    async def unread_among(self, revision: str, asset_ids: Sequence[str]) -> set[str]:
        """Which of these files nothing has looked at yet, by the sweep's own rule."""
        return await catalog.unread_by_picture_among(self._db, revision, asset_ids)

    def lack(self, revision: str) -> Lack:
        """Not read at this revision, as one term of the catch-up count (see `_LACKS_READING`)."""
        return Lack(_LACKS_READING, (revision, signatures.MATCHER_VERSION))

    async def read_count(self, revision: str) -> int:
        row = await self._db.fetch_one(_COUNT_READ, (revision,))
        return int(row["total"]) if row else 0

    async def found_count(self) -> int:
        """How many files carry a reading the loaded models and matcher stand behind."""
        row = await self._db.fetch_one(_COUNT_FOUND, (weights.REVISION, signatures.MATCHER_VERSION))
        return int(row["total"]) if row else 0

    async def unattributed_on(self, connection: Connection, site: str) -> str | None:
        found = await (await connection.execute(_UNATTRIBUTED_ON.sql, (site,))).fetchone()
        return None if found is None else str(found["id"])

    async def site_of_on(self, connection: Connection, username_id: str) -> str | None:
        """The Site one Username is on, read inside a write."""
        found = await (
            await connection.execute("SELECT site_id FROM usernames WHERE id = ?", (username_id,))
        ).fetchone()
        return None if found is None or found["site_id"] is None else str(found["site_id"])

    async def usernames_on(self, connection: Connection, site: str) -> list[str]:
        """Every username already known on one site."""
        rows = await (await connection.execute(_USERNAMES_ON, (site,))).fetchall()
        return [str(row["name"]) for row in rows]

    async def sites_with_username(
        self, connection: Connection, name: str
    ) -> list[tuple[str, str, str]]:
        """Every site a username of exactly this name is on: site, id and the library's spelling."""
        rows = await (await connection.execute(_SITES_WITH_USERNAME, (name,))).fetchall()
        return [(str(row["site"]), str(row["id"]), str(row["name"])) for row in rows]

    async def tag_on(
        self, connection: Connection, *, asset_id: str, tag: str, tag_id: str, source: str
    ) -> bool:
        """Put an ordinary tag on a file in the caller's transaction; True if the file gained it."""
        await connection.execute(_MAKE_TAG, (tag_id, tag, sort_key(tag), self._now()))
        found = await (await connection.execute(_TAG_BY_NAME, (tag,))).fetchone()
        if found is None:  # pragma: no cover - the insert above makes it or it was already there
            return False
        wanted = str(found["id"])
        cursor = await connection.execute(_WEAR_TAG, (asset_id, wanted, source, self._now()))
        if not cursor.rowcount:
            return False
        # A tag carries shares and hides, so a new file under it changes who may see what.
        announce(await bump_stamps_for_object(connection, ObjectType.TAG, wanted), About.LIBRARY)
        return True

    async def remember_on(
        self, connection: Connection, *, asset_id: str, revision: str, identity: str, found: int
    ) -> None:
        await connection.execute(_REMEMBER, (asset_id, revision, identity, found, self._now()))

    async def record_on(self, connection: Connection, *, asset_id: str, read: Read) -> None:
        await connection.execute(
            _WRITE_READ,
            (
                asset_id,
                read.text,
                read.kind,
                read.site,
                read.username,
                read.confidence,
                read.frame_ms,
                asset_id,
                signatures.MATCHER_VERSION,
                self._now(),
            ),
        )

    async def name_site_on(self, connection: Connection, asset_id: str, site: str) -> None:
        """Keep the id of the Site called `site` on this file's reading (see `_NAME_SITE`)."""
        await connection.execute(_NAME_SITE, (site, asset_id))

    async def clear_read_on(self, connection: Connection, asset_id: str) -> None:
        await connection.execute(_CLEAR_READ, (asset_id,))

    async def unfile_on(
        self, connection: Connection, *, username_id: str, source: str, asset_ids: Sequence[str]
    ) -> int:
        """Remove the filings one decision wrote, by username, source and files; returns a count."""
        wanted = list(asset_ids)
        removed = 0
        for at in range(0, len(wanted), _AT_A_TIME):
            statement, bound = in_clause(_UNFILE, wanted[at : at + _AT_A_TIME])
            cursor = await connection.execute(statement, [username_id, source, *bound])
            removed += int(cursor.rowcount or 0)
        return removed

    async def refuse_on(self, connection: Connection, asset_ids: Sequence[str]) -> None:
        """Remember, in the undo's transaction, not to file these files from a watermark again."""
        now = self._now()
        await connection.executemany(_REFUSE, [(one, now) for one in asset_ids])

    async def forget_everything(self) -> int:
        """Throw away every reading so the library is read again; filings stay. Returns a count."""
        async with self._db.write() as connection:
            cursor = await connection.execute(_FORGET_READS)
            forgotten = cursor.rowcount
            await connection.execute(_FORGET_SCANS)
        return max(forgotten, 0)
