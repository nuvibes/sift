# SPDX-License-Identifier: AGPL-3.0-or-later
"""The questions duplicate-finding asks of the content tables.

They are here rather than in the feature that wants them, because the asset tables carry the
permission rules and a query written against them anywhere else is a query with no permission
check in it. That is a lint, not a convention. So the feature gets these answers and no
ability to ask anything else.

Neither read is scoped to a viewer, and both are reached only from an admin-only surface or from a
job running as the system. That is the same arrangement the deleter has, and it is the reason the
gate belongs at the route: a maintenance screen showing an admin every copy of every file is the
point of it, and a scan that skipped what it was not allowed to see would leave duplicates
undetected for the least visible half of a library.

The questions are genuinely different, and keeping them apart is what stops the feature
conflating them:

`fingerprints` answers *what might look alike*: one row per asset, carrying its digest and its
perceptual hashes. Comparing them is the feature's business; this only hands them over.

`redundancies` answers *what is already known to be the same*: an asset sitting in more than one
place at once. There is no comparison involved and no judgement to make: identical bytes are one
asset with several locations by construction, so this is a fact the content model already holds
rather than something to go looking for.

`places_of` answers *where each of these files is sitting*, for a screen showing several files that
look alike side by side. The near-duplicate screen cannot be read without it: the pictures are the
same picture (that is why they are on screen together), and the folder each copy is in is
regularly the only thing that tells them apart. One read for a whole page of them, because the
per-asset version of this question is a permission walk each and a page is hundreds.

`measures_of` answers *the numbers a keeper rule compares*: how big, how many pixels, how long
ago. Separate from the fingerprints above even though both are columns of one table, because they
are read at different moments for different reasons: a fingerprint is read once per scan to find
pairs, and these are read whenever the queue is drawn, to say which file of a group a rule would
keep. Folding them together would make every screen read pay for the hashes and every scan pay for
the sizes.
"""

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
    """What one asset offers a near-duplicate comparison.

    `identity` is carried alongside the perceptual hashes on purpose. Two assets can never share an
    digest (that is what makes them two assets), so the feature needs it to prove a pair is a
    *near* duplicate rather than an exact one, without asking a second question.
    """

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
    """The numbers a keeper rule is allowed to compare, for one file.

    Deliberately five columns and not the asset. A rule that could see the whole row could be
    written against anything on it (a title, a rating, who imported it), and the one property
    this feature needs is that the rule is decidable from facts about the FILE, so that the answer
    is the same for everybody and can be explained in a sentence on screen.
    """

    asset_id: str
    size_bytes: int | None
    width: int | None
    height: int | None
    #: When this library first saw the file. Never a release date: that is a fact about the work
    #: rather than about the copy, and two copies of one clip share it.
    added_at: int


@dataclass(frozen=True, slots=True)
class Place:
    """Where one asset's bytes are sitting right now, as a screen would say it.

    The library folder's id rather than its name, because the name is a fact this viewer is scoped
    to (a guest may be shown a folder inside a library without being shown the library), and
    resolving it belongs where the viewer is known. This carries the two halves and the caller
    joins them.
    """

    asset_id: str
    root_id: str
    #: The path inside that library folder, filename and all.
    rel_path: str


@dataclass(frozen=True, slots=True)
class Redundancy:
    """An asset whose bytes sit in more than one place at once.

    `copies` is always two or more, and every one of them is `present`: a location Sift has
    recorded as missing is not a copy anybody can reclaim, and counting it would offer to free
    space that is already free.
    """

    asset_id: str
    identity: str
    media_type: str
    copies: tuple[Copy, ...]

    @property
    def reclaimable_bytes(self) -> int:
        """What removing the extras would free: every copy but one.

        One copy is the file. The rest are the redundancy, and they are what this offers to
        remove, so the largest is kept and the total is of the others, rather than a flat
        `size * (n - 1)` that would be wrong whenever two copies differ in recorded size.

        A location with no recorded size counts as nothing. Guessing it from a sibling would be
        inventing a number for a screen whose entire purpose is to state one accurately.
        """
        sizes = sorted((copy.size_bytes or 0) for copy in self.copies)
        return sum(sizes[:-1])


# Every asset the probe has fingerprinted. Having *a* fingerprint is the filter rather than
# `probed_at IS NOT NULL`: a file that was probed but yielded no frame at all has nothing to
# compare, and carrying it would put a row through the whole matcher to discover that.
#
# Two columns rather than one, because a video and a photograph are now fingerprinted by different
# things. A video carries `video_phash` and a still carries `phash`, and a video imported before
# Sift knew how to compute the first one has only the second, which describes one frame of it and
# is not comparable to anything the matcher now asks. Such a row arrives here and is dropped by the
# matcher rather than filtered out in SQL, so that the count of files not yet fingerprinted is
# something a screen can state rather than something a query silently swallowed.
#
# Ordered by id so a scan is deterministic. Two runs over an unchanged library do the same work in
# the same order, which is what makes a comparison-count assertion meaningful.
_FINGERPRINTS = """
SELECT id, identity, media_type, phash, videohash, video_phash, duration_ms,
       fingerprint_version, oshash, size_bytes
  FROM assets
 WHERE phash IS NOT NULL OR video_phash IS NOT NULL
 ORDER BY id
"""

#: The same read within a set of files: a caller composes the set's statement into `{{FILES}}`
#: once, at import, and hands the whole statement back with what it binds.
FINGERPRINTS_WITHIN = _FINGERPRINTS.replace(
    " WHERE phash IS NOT NULL OR video_phash IS NOT NULL\n",
    " WHERE (phash IS NOT NULL OR video_phash IS NOT NULL)\n   AND id IN ({{FILES}})\n",
)

# Videos the fingerprint pass has not reached yet.
#
# The same WHERE as the pass's own batch query, deliberately, so the number on the screen and the
# work the dashboard is showing are the same population counted two ways. A file the pass has tried
# and cannot read is written down with an empty exact hash rather than left NULL, so it leaves this
# count permanently, which is what stops it being a queue nobody can empty.
#
# Video only, because video is the only kind with a backfill pass: a photograph's fingerprint is
# taken by the probe as the file arrives, so a still with none is a file that has not been probed
# at all, and that is the import queue's business rather than this screen's.
#
# AND SO DOES A FILE CARRYING A STANDING VERDICT. A refusal met during the PROBE leaves the columns
# as they were rather than emptying them, so the empty-hash rule above does not cover it: without
# this, a video whose frames the decoder will not read would sit in "not fingerprinted yet" for the
# life of the library, describing work that is never going to happen.
# It is counted next door instead, in its own words.
#
# AND A FILE FINGERPRINTED UNDER AN OLDER ALGORITHM IS WAITING TOO. The version column is what lets
# the pass do only the files that need it when the hashes improve; this count has to agree with
# the pass, or the screen says "nothing waiting" while the pass works through thousands.
_AWAITING_FINGERPRINT = """
SELECT COUNT(*) AS total FROM assets a
 WHERE a.id IN (SELECT id FROM assets WHERE media_type = 'video' AND oshash IS NULL
                UNION
                SELECT id FROM assets WHERE fingerprint_version < ? AND media_type = 'video')
   AND NOT EXISTS (SELECT 1 FROM file_verdicts v
                    WHERE v.asset_id = a.id AND v.product = ? AND v.transient = 0)
"""

# Files nothing can ever compare: the fingerprint pass read them and the decoder refused the bytes.
#
# Every kind, unlike the count above, and for the reason that count is video-only: this is not about
# which pass owes the file work, it is about what the matcher has to compare with, and a photograph
# with no frame hash is as uncomparable as a video with no grid. A transient verdict is not counted:
# it is about a moment, and the next scan of the file clears it.
_CANNOT_FINGERPRINT = """
SELECT COUNT(*) AS total FROM file_verdicts
 WHERE product = ? AND transient = 0
"""

# Assets stored in more than one place, one page of them, with every copy of each.
#
# The asset ids worth reporting are settled first, in a subquery, and only then joined back for
# their locations. The alternative (grouping the joined rows and filtering on the count) reads
# every location in the library to answer a question about a small fraction of them.
#
# The paging happens INSIDE that subquery rather than on the joined rows, and the difference is
# load-bearing. A limit on the join counts *locations*, so a page of twenty would end halfway
# through an asset with three copies and offer to reclaim two of them: a list whose last row is
# a lie about how many copies exist. Limiting the ids first means a page is twenty assets and
# every copy of each of them.
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

# One asset's copies, asked for by name. What a request to release a copy is checked against.
#
# Its own statement because the alternative is reading every redundant asset in the library to find
# out about one of them, so that a single delete sweeps the lot.
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

# Where one redundant asset sits on the page above's list, counting from zero, or no row at all when
# it is not on the list (a copy let go since leaves it with one, and it is gone from the list).
#
# The list's own order, which is the asset id, so its place is how many redundant assets come
# before it. The count stops at the anchor rather than reading the whole library: it walks the
# same `asset_id` order the page walks, and to the same depth the page at that place would.
# What a page asked for by its first row starts at (`kernel.paging.resume_at`).
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

# How many there are and what the extras add up to, without reading a single path.
#
# Two aggregates in one statement because both are for the same sentence on the same screen, and
# asking twice is two sweeps of the same rows to answer one question.
#
# `SUM - MAX` per asset is `Redundancy.reclaimable_bytes` written in SQL: every copy but the
# largest. A location with no recorded size counts as nothing in both (SQLite's `SUM` and `MAX`
# both skip NULL, and the Python sorts it to the front as a zero), so the two agree on a library
# with sizes missing, which is the case that would otherwise put a different number on the card
# than on the screen behind it.
_REDUNDANT_TOTALS = """
SELECT COUNT(*)                AS assets,
       COALESCE(SUM(extra), 0) AS reclaimable
  FROM (SELECT COALESCE(SUM(size_bytes), 0) - COALESCE(MAX(size_bytes), 0) AS extra
          FROM asset_locations
         WHERE status = 'present'
         GROUP BY asset_id
        HAVING COUNT(*) > 1)
"""


# Where each of these files is, for a screen drawing several of them side by side.
#
# `status = 'present'` because a copy on a drive that is not plugged in is not where the file is;
# ordered so the FIRST row per asset is the one a reader would be told about, which is the same
# rule the file's own record page applies. One statement for a whole page: the per-asset form of
# this question costs a permission walk each, and a page of near-duplicate groups is hundreds of
# files.
_PLACES_OF = """
SELECT asset_id, root_id, rel_path
  FROM asset_locations
 WHERE status = 'present'
   AND asset_id IN (?*)
 ORDER BY asset_id, first_seen_at, id
"""


# What a keeper rule compares, for the files it is being asked about.
#
# By primary key, in chunks the caller's own list bounds, so this reads the assets in the queue
# and never the library. A whole-table sweep would be one query instead of several and would read
# rows for every file that is not in any group, which on a library where duplicates are a small
# fraction is most of it.
_MEASURES_OF = """
SELECT id, size_bytes, width, height, added_at
  FROM assets
 WHERE id IN (?*)
"""


@dataclass(frozen=True, slots=True)
class RedundantTotals:
    """How much is stored twice, across the whole library.

    Whole-library and not scoped to whoever is asking, the same way the review queue's own totals
    are. What the vault conceals is a file's NAME and the paths of its copies, and no page ever
    prints one it may not. But a count and a number of bytes describe a population rather than a
    file, and holding them to the vault would mean resolving permission for every asset in the
    library to draw one card.
    """

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
        """Every fingerprinted asset, for the matcher to compare; only those `within` (a statement
        composed from `FINGERPRINTS_WITHIN`, and what it binds) where one is given.

        Read in one go rather than streamed. A library large enough for this to matter is tens of
        thousands of rows of a few hundred bytes (tens of megabytes, held for the length of a
        scan), and the thing that actually threatens a modest machine here is the number of
        *comparisons*, not the number of rows. That is the feature's problem to bound, and it
        cannot bound it without seeing the whole set.
        """
        # Deliberately whole-library, and it stays that way: the block index the matcher
        # is built on is built from every fingerprint at once, so there is no narrower
        # question to ask. What it needs is the lane, not a narrower read.
        if within is None:
            rows = await self._db.sweep_all(_FINGERPRINTS, what="duplicate fingerprints")
        else:
            # The set's size, not the library's: a viewer's own files.
            statement, binds = within
            rows = await self._db.fetch_all(statement, binds)
        # On a thread: one object per fingerprinted file, a whole library's worth.
        return await asyncio.to_thread(_fingerprints_of, rows)

    async def awaiting_fingerprint(self) -> int:
        """How many videos cannot be compared yet, because nothing has fingerprinted them.

        The one number that makes an empty review queue readable. Without it "Sift has not found
        any files it is unsure about" and "Sift has not looked at nine hundred of your videos yet"
        are the same sentence on the screen, and there is no way to tell which one is being said.
        """
        # Unpacked rather than guarded: a bare aggregate over one table answers with exactly one
        # row on an empty table as readily as on a full one, so a "there was no row" fallback is a
        # line no test can reach and a reader has to work out is dead.
        (row,) = await self._db.fetch_all(
            _AWAITING_FINGERPRINT, (FINGERPRINT_VERSION, VerdictProduct.FINGERPRINTS.value)
        )
        return int(row["total"])

    async def cannot_fingerprint(self) -> int:
        """How many files can never be compared, because the decoder refused their frames.

        The other half of the number above, and they are two sentences rather than one: a file
        nothing has looked at yet is work in flight, and a file nothing can read is work that will
        not happen. Folded together, an empty queue reads as one that is still filling.
        """
        (row,) = await self._db.fetch_all(_CANNOT_FINGERPRINT, (VerdictProduct.FINGERPRINTS.value,))
        return int(row["total"])

    async def redundancies_page(self, *, limit: int, offset: int) -> list[Redundancy]:
        """One page of them, whole assets at a time. See `_REDUNDANCIES_PAGE`."""
        return _fold(
            await self._db.fetch_all(_REDUNDANCIES_PAGE, {"limit": limit, "offset": offset})
        )

    async def redundancy_position(self, asset_id: str) -> int | None:
        """Where one asset sits on the list `redundancies_page` pages, or None when it is not on it.

        See `_REDUNDANCY_POSITION`. None for an asset stored once (which is what letting its last
        extra copy go makes of it), and for an id that names nothing.
        """
        row = await self._db.fetch_one(_REDUNDANCY_POSITION, {"asset_id": asset_id})
        return None if row is None else int(row["at"])

    async def redundancy_for(self, asset_id: str) -> Redundancy | None:
        """One asset's copies, or None where it has fewer than two.

        Fewer than two is not an error and not an empty answer to a different question: it is the
        statement that this asset is not redundant, which is exactly what a request to release one
        of its copies has to be refused on. Losing the last copy is what ends an asset, and the
        check that stops it is this returning None.
        """
        found = _fold(await self._db.fetch_all(_REDUNDANCY_FOR, {"asset_id": asset_id}))
        if not found or len(found[0].copies) < 2:
            return None
        return found[0]

    async def redundant_totals(self) -> RedundantTotals:
        """How many assets are stored more than once, and what the extra copies add up to.

        Read without touching a path, so a card on a board and a poll on a timer can both ask for
        it. See `RedundantTotals` for why it is not scoped to whoever is asking.
        """
        # Through the sweep lane, unlike the page above it, and the difference is not a style
        # choice. A page can stop: the grouping walks the index on `asset_id` in order and gives up
        # after `limit + offset` groups. A total cannot (it has to visit every group to know how
        # many there are), so it is a whole-library read and belongs on the lane that exists to
        # keep one of those from taking the connection pool down with it.
        #
        # Unpacked rather than guarded: a bare aggregate over one subquery answers with exactly one
        # row on an empty library as readily as on a full one, so a fallback here is a line no test
        # can reach. The same reasoning as `awaiting_fingerprint` above.
        (row,) = await self._db.sweep_all(_REDUNDANT_TOTALS, what="duplicate copy totals")
        return RedundantTotals(assets=int(row["assets"]), reclaimable_bytes=int(row["reclaimable"]))

    async def places_of(self, asset_ids: Sequence[str]) -> dict[str, Place]:
        """Where each of these files currently sits, keyed by file. Absent means nowhere readable.

        The first present copy wins, which is the same rule the file's own record page uses: a
        copy on an unplugged drive has no place worth reporting when there is one in front of you.

        Chunked at one page so the caller's own list is what bounds the read, never a cap silently
        truncating it. An empty list asks nothing rather than being a query about nothing.
        """
        found: dict[str, Place] = {}
        wanted = list(dict.fromkeys(asset_ids))
        for start in range(0, len(wanted), MAX_PAGE_SIZE):
            sql, params = in_clause(_PLACES_OF, wanted[start : start + MAX_PAGE_SIZE])
            for row in await self._db.fetch_all(sql, params):
                # `setdefault`, so the first row per asset is the one kept: the ORDER BY is what
                # decides which that is, and a later copy must not overwrite it.
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
        """What a keeper rule compares about each of these files, keyed by file.

        Absent means there is no such asset any more, which is ordinary: a pair outlives neither of
        its files, but a list of ids gathered a moment ago can name one that has just gone. The
        caller treats an absent measure as a figure nobody has, which is what stops a rule deciding
        about it (see the grouping module).
        """
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
    """Flat location rows back into one entry per asset.

    Both queries order by asset id precisely so this is a single pass rather than a dictionary
    built over the whole result.
    """
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
