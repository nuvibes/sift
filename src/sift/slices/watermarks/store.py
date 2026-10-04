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

#: How many files one sweep claims at a time. A cursorless sweep would read the whole library into
#: memory to queue it; a page means the job asks for itself again with the next lot, which is what
#: makes it resumable and what stops one job holding a worker for hours.
PAGE = 500

#: The most ids one DELETE may name. SQLite's variable limit was 999 on builds still in the wild,
#: and a filing undone over a large selection is exactly where that is reached.
_AT_A_TIME = 500

_COUNT_READ = "SELECT COUNT(*) AS total FROM watermark_scans WHERE revision = ?"
#: How many files carry a reading MADE BY WHAT IS CONFIGURED NOW, which is the question the
#: screen beside `_COUNT_READ` is really asking, not every reading ever made.
#:
#: A reading produced by a model that is no longer loaded, or decided by a matcher that has since
#: been improved, is a record of something that happened rather than a current fact about the file.
#: Counting it beside "files read", which is conditioned on the revision, would put two numbers
#: on one pane that answer two different questions while reading as a pair.
_COUNT_FOUND = (
    "SELECT COUNT(*) AS total FROM watermark_reads WHERE revision = ? AND matcher_version = ?"
)

#: The row that means "from this site, poster unknown": the username a filing under a site with
#: no name read lands on. Found by the site's name and the empty name together, which is unique.
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

#: Where in the whole library one username already exists, EXACTLY, whatever the site.
#:
#: The other direction from `_USERNAMES_ON`, and it is a different question rather than the same one
#: asked twice. A mark carrying an address says which site it is on and the name is compared
#: against that site's usernames; a bare `@name` says nothing about the site, so the LIBRARY is what
#: answers, and only where it answers with exactly one site is there anything to act on.
#:
#: `COLLATE NOCASE` matches the index the usernames table carries on this column, so this is a
#: lookup rather than a scan. The poster-unknown rows are excluded by the emptiness test: they are
#: not usernames anybody is named by.
_SITES_WITH_USERNAME = """
SELECT p.name AS site, ac.id AS id, ac.name AS name
  FROM usernames ac
  JOIN sites p ON p.id = ac.site_id
 WHERE ac.name = ? COLLATE NOCASE AND ac.name <> ''
"""

#: The tag a mark that names no site puts on the file, made if the library has never had it.
#:
#: `sift` and `watermark` in the statement rather than bound, because this file has exactly one
#: caller and one answer: a tag written from here was written by a pass reading the picture, never
#: by a person and never by any other pass. Those are the same two words `catalog.by_sift` and
#: `vocabulary.VIA_WATERMARK` carry into a filing, so the tag's own record and the mark on the files
#: wearing it say one thing.
_MAKE_TAG = """
INSERT INTO tags (id, name, name_sort, created_at, created_by_kind, created_by_via)
VALUES (?, ?, ?, ?, 'sift', 'watermark')
ON CONFLICT(name) DO NOTHING
"""

#: Found by NAME and not by a remembered id, which is the behaviour a tag that applies itself has
#: to have: rename it and the next file read makes a new one under the old name, which is what
#: somebody renaming it means. The name collation is case-insensitive, so there cannot be two.
_TAG_BY_NAME = "SELECT id FROM tags WHERE name = ?"

#: `OR IGNORE` because a file already carrying the tag is not an error (a second read of the same
#: file finds the same band) and because it keeps the FIRST answer to "when was this decided".
_WEAR_TAG = (
    "INSERT OR IGNORE INTO asset_tags (asset_id, tag_id, source, decided_at) VALUES (?, ?, ?, ?)"
)

_REMEMBER = (
    "INSERT INTO watermark_scans (asset_id, revision, identity, found, scanned_at) "
    "VALUES (?, ?, ?, ?, ?) ON CONFLICT(asset_id) DO UPDATE SET "
    "revision = excluded.revision, identity = excluded.identity, found = excluded.found, "
    "scanned_at = excluded.scanned_at"
)

#: The reading, with what produced it and what decided it.
#:
#: `revision` is read back out of the scan row rather than taken as an argument, and that is the
#: same argument the backfill makes: `Service._settle` writes the scan row first and this second, in
#: one transaction, so the subquery reads exactly the revision this pass has just recorded and the
#: two cannot say different things. Handed in, they would be two copies of one fact and the failure
#: would be silent.
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

#: The Site a reading names, by id, set once the reading's filing has been written (which may have
#: just made the Site). By the name the filing used, in the same transaction, which is the one
#: moment that name and the Site are certainly the same: the Site's name now, read through the
#: site's key, where the word read is the name a Site may since have been renamed from. Read by id
#: from then on.
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

#: Never read at this revision, as a condition on the assets row for the catch-up pass's count.
#:
#: **This feature's OWN tables and the row's own two columns, and nothing else**: the same shape
#: the two passes beside it use, and for the reason written beside theirs: SQL against `assets`
#: from a slice answers for files the user asking may not be allowed to see, and a term handed
#: to the content store is the one place a count that spans that table may be made.
#:
#: So it is an UPPER BOUND and says so: it counts a file whose only copy has gone and a file
#: nothing has managed to measure, neither of which this pass can open. What a page actually does
#: is decided by `unread_among`, which is the kernel's statement and tests both. The count being a
#: little high is the honest direction for the two to differ in: a pass that offers a file and
#: then finds nothing to open leaves it unread and says so, where a count that left readable files
#: out would hide work nobody could find.
_LACKS_READING = (
    "(NOT EXISTS (SELECT 1 FROM watermark_scans s"
    " WHERE s.asset_id = a.id AND s.revision = ? AND s.identity = a.identity)"
    " OR EXISTS (SELECT 1 FROM watermark_reads m"
    " WHERE m.asset_id = a.id AND m.matcher_version IS NOT ?))"
    " AND NOT EXISTS (SELECT 1 FROM watermark_refusals r WHERE r.asset_id = a.id)"
)


@dataclass(frozen=True, slots=True)
class Read:
    """What was read off one file."""

    text: str
    #: Which of the four things it was. See `signatures.SITE` and the three beside it. It is what
    #: says whether `site` means anything on this row.
    kind: str
    #: The site the address named, and the empty string on every kind that names no site. See the
    #: note beside the column in `schema.py` for why it is empty rather than absent.
    site: str
    #: The username the mark named, where it named one.
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
        """A transaction. Handed straight through so a filing and its receipt are one action."""
        return self._db.write()

    async def unread(self, revision: str, *, limit: int = PAGE) -> list[str]:
        """Files nothing has looked at yet, asked of the kernel.

        **Asked rather than written here**, and the seam is the point: the question is about
        `assets` and `asset_locations`, which carry permissions, and no feature writes SQL against
        one of those. What comes back is a list of bare ids for a pass that runs as nobody. The
        conditions, and why each of them is there, are recorded with the statement.
        """
        return await catalog.unread_by_picture(self._db, revision, limit=limit)

    async def unread_count(self, revision: str, *, limit: int = PAGE) -> int:
        """How many of those there are, counted no further than a page. Also the kernel's."""
        return await catalog.count_unread_by_picture(self._db, revision, limit=limit)

    async def unread_among(self, revision: str, asset_ids: Sequence[str]) -> set[str]:
        """Which of THESE files nothing has looked at yet. The kernel's, like `unread`.

        Asked of a page the catch-up pass already holds, rather than of the library, so that pass
        can decide a page's work by the same rule the feature's own sweep claims files by.
        """
        return await catalog.unread_by_picture_among(self._db, revision, asset_ids)

    def lack(self, revision: str) -> Lack:
        """Not read at this revision, as one term of the catch-up pass's count. See `_LACKS_READING`
        for what it counts and why it is an upper bound on what a page will do."""
        return Lack(_LACKS_READING, (revision, signatures.MATCHER_VERSION))

    async def read_count(self, revision: str) -> int:
        row = await self._db.fetch_one(_COUNT_READ, (revision,))
        return int(row["total"]) if row else 0

    async def found_count(self) -> int:
        """How many files carry a reading the models and the matcher in force would stand behind.

        The two numbers are read here rather than taken as arguments, and they are not the same kind
        of thing as the revision `read_count` is handed. That one is the pass's own tuning, which the
        service holds. These are what the CODE is: which model file is loaded and which matcher
        decided. Nothing can vary them within a run, so reading them beside the statement they
        condition keeps them from becoming a parameter every caller has to remember to pass.
        """
        row = await self._db.fetch_one(_COUNT_FOUND, (weights.REVISION, signatures.MATCHER_VERSION))
        return int(row["total"]) if row else 0

    async def unattributed_on(self, connection: Connection, site: str) -> str | None:
        """The "poster unknown" username on one site, or None where the site has none."""
        found = await (await connection.execute(_UNATTRIBUTED_ON.sql, (site,))).fetchone()
        return None if found is None else str(found["id"])

    async def site_of_on(self, connection: Connection, username_id: str) -> str | None:
        """The Site one Username is on, read inside a write, so the write can tell whoever may
        see that Site."""
        found = await (
            await connection.execute("SELECT site_id FROM usernames WHERE id = ?", (username_id,))
        ).fetchone()
        return None if found is None or found["site_id"] is None else str(found["site_id"])

    async def usernames_on(self, connection: Connection, site: str) -> list[str]:
        """Every username already known on one site.

        Bounded by the site rather than by the library: even a large library holds hundreds of
        usernames on one site, not tens of thousands, and comparing a read name against hundreds is
        arithmetic. Comparing it against every username on every site would be both slower and
        wrong. See `Service.attribute`.
        """
        rows = await (await connection.execute(_USERNAMES_ON, (site,))).fetchall()
        return [str(row["name"]) for row in rows]

    async def sites_with_username(
        self, connection: Connection, name: str
    ) -> list[tuple[str, str, str]]:
        """Every site a username of exactly this name already exists on: its name, id and spelling.

        The username comes back as the library SPELLS it rather than as the mark was read, because
        that is what a receipt has to say: a sentence naming the read spelling would send somebody
        looking for a username under a name nothing answers to.
        """
        rows = await (await connection.execute(_SITES_WITH_USERNAME, (name,))).fetchall()
        return [(str(row["site"]), str(row["id"]), str(row["name"])) for row in rows]

    async def tag_on(
        self, connection: Connection, *, asset_id: str, tag: str, tag_id: str, source: str
    ) -> bool:
        """Put one ordinary tag on one file, making it if the library has never had it.

        An ordinary tag on purpose, the way `kernel/access/lineage.py` puts one on a file Sift
        produced: it sorts, filters, searches and bulk-acts like every other tag, sits on the tag
        wall beside them, and can be taken off a file or deleted outright by somebody who stops
        wanting it. A marker of its own would do none of that without a screen being built for it.

        On the CALLER's connection, so the tag, the record of what was read and the record of
        having looked are one action: a tag present for a reading that was rolled back would be a
        claim about a file with nothing behind it.

        Answers with whether the file actually gained it, so a caller can tell a first reading from
        a second look at a file that already wears it.
        """
        await connection.execute(_MAKE_TAG, (tag_id, tag, sort_key(tag), self._now()))
        found = await (await connection.execute(_TAG_BY_NAME, (tag,))).fetchone()
        if found is None:  # pragma: no cover - the insert above makes it or it was already there
            return False
        wanted = str(found["id"])
        cursor = await connection.execute(_WEAR_TAG, (asset_id, wanted, source, self._now()))
        if not cursor.rowcount:
            return False
        # A tag is what a share or a hide hangs off, so a file joining one changes what some
        # users may see without anything of theirs being written. Same transaction, and only
        # when the file actually moved.
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
        """Keep the id of the Site called `site` as the one this file's reading names. See
        `_NAME_SITE`."""
        await connection.execute(_NAME_SITE, (site, asset_id))

    async def clear_read_on(self, connection: Connection, asset_id: str) -> None:
        await connection.execute(_CLEAR_READ, (asset_id,))

    async def unfile_on(
        self, connection: Connection, *, username_id: str, source: str, asset_ids: Sequence[str]
    ) -> int:
        """Remove the filings one decision wrote. Returns how many rows went.

        Three conditions and every one is load-bearing: the username, because a file may sit under
        several; the SOURCE word, because a filing somebody made by hand carries none and was never
        this pass's to remove; and the exact list of files the receipt names.
        """
        wanted = list(asset_ids)
        removed = 0
        for at in range(0, len(wanted), _AT_A_TIME):
            statement, bound = in_clause(_UNFILE, wanted[at : at + _AT_A_TIME])
            cursor = await connection.execute(statement, [username_id, source, *bound])
            removed += int(cursor.rowcount or 0)
        return removed

    async def refuse_on(self, connection: Connection, asset_ids: Sequence[str]) -> None:
        """Remember that these files are not to be filed from a watermark again.

        On the caller's connection, because the undo and the memory of it are one action: a refusal
        written separately could be present for a filing that is still there, or absent for one
        that has gone.
        """
        now = self._now()
        await connection.executemany(_REFUSE, [(one, now) for one in asset_ids])

    async def forget_everything(self) -> int:
        """Throw away what was read, so the library is read again. Leaves the filings standing.

        Counted from the delete rather than from `found_count`, which answers how many readings the
        models and matcher in force stand behind. Everything goes here, including the readings an
        older model made, so the number a person is shown has to be everything that went.
        """
        async with self._db.write() as connection:
            cursor = await connection.execute(_FORGET_READS)
            forgotten = cursor.rowcount
            await connection.execute(_FORGET_SCANS)
        return max(forgotten, 0)
