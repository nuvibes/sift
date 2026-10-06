# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which files have been described, by which model, and what is still waiting.

This is the resumable half of the feature. The numbers are expensive and live in the vector store;
what has been done to which file is four small columns, and reading them is how a restart, an
interrupted sweep and a model change are all answered without re-reading a single file.

**Nothing here queries the assets table**, and that is a rule rather than a preference. A slice
reading it directly writes a second copy of the rule that decides who may see what, and this one
runs as a background job where nobody would notice the two disagreeing. So the work list is built
the other way round: the access layer hands out a page of what a user may see, and this says
which of those have already been described. The comparison happens in the feature; the scoping
never does.

**Which model described a file is what makes the list answerable.** Numbers from two models are not
comparable and nothing about them says so, so a file described by a model that is no longer the
configured one is waiting rather than done, and asking only whether a row exists would mean every
file is described once and never again, however the setting moved.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass

from sift.kernel.content import Lack
from sift.kernel.db import Database, in_clause
from sift.kernel.ledger import Actor, record_event
from sift.kernel.vocabulary import Subject
from sift.slices.semantic.settings import ENABLED_KEY

#: How many files one page of the work list offers. Bounds the rows one job writes, not the work:
#: the sweep queues one piece of work per file and stops.
PAGE = 500

_MARK = """
INSERT INTO semantic_indexed (asset_id, revision, frames, indexed_at)
VALUES (?, ?, ?, ?)
ON CONFLICT(asset_id) DO UPDATE SET
  revision = excluded.revision,
  frames = excluded.frames,
  indexed_at = excluded.indexed_at
"""

_FORGET = "DELETE FROM semantic_indexed WHERE asset_id = ?"

#: A batch per write, so the removal never holds the one writer for minutes.
_FORGET_SOME = (
    "DELETE FROM semantic_indexed WHERE rowid IN (SELECT rowid FROM semantic_indexed LIMIT ?)"
)
_FORGET_BATCH = 500

_COUNT_DESCRIBED = "SELECT COUNT(*) AS total FROM semantic_indexed WHERE revision = ?"

#: Whether this model has described anything at all. A row, not a number.
#:
#: The count above answers the same question and reads the whole table to do it: eventually every
#: row of the library. This is asked before a pass that would otherwise walk thousands of files one
#: at a time, so it stops at the first row it finds: flat where the count is linear.
_ANY_DESCRIBED = "SELECT 1 AS found FROM semantic_indexed WHERE revision = ? LIMIT 1"

#: Files whose description is another model's. Out of every search until described again, and
#: the number the settings screen says so with. Two index ranges, as `!=` reads every row.
_COUNT_BY_OTHERS = (
    "SELECT (SELECT COUNT(*) FROM semantic_indexed WHERE revision < ?)"
    " + (SELECT COUNT(*) FROM semantic_indexed WHERE revision > ?) AS total"
)

#: Whether any such file is left: a seek, whatever their number.
_ANY_BY_OTHERS = (
    "SELECT EXISTS (SELECT 1 FROM semantic_indexed WHERE revision < ?)"
    " OR EXISTS (SELECT 1 FROM semantic_indexed WHERE revision > ?) AS found"
)

#: The records of every file a model other than this one described. Taken with the frames they
#: describe (`VectorStore.purge_other_revisions`), or the count above would go on saying their
#: numbers are in the index after the numbers have gone.
_FORGET_OTHERS = "DELETE FROM semantic_indexed WHERE revision != ?"

_SETTLED = "SELECT asset_id FROM semantic_indexed WHERE revision = ?"

#: Not described by this model, as a term of the Build's count. See `Records.lack`.
_LACKS_DESCRIPTION = (
    "NOT EXISTS (SELECT 1 FROM semantic_indexed i WHERE i.asset_id = a.id AND i.revision = ?)"
)

#: WHEN it was described as well as by which model, because "already done" is only true of a file
#: that has not been read again since. See `SemanticService.describe_asset`.
_DESCRIBED = "SELECT revision, frames, indexed_at FROM semantic_indexed WHERE asset_id = ?"

#: When each of some files was described, by primary key. See `Records.described_at_of`.
_DESCRIBED_AT = "SELECT asset_id, indexed_at FROM semantic_indexed WHERE asset_id IN (?*)"


@dataclass(frozen=True, slots=True)
class Described:
    """What is on record about one file."""

    revision: str
    frames: int
    at_ms: int
    """When it was described, in milliseconds. Read because the revision alone cannot say whether
    a description is still ABOUT the file: nothing forgets one when a file's bytes are rewritten in
    place, so a caller deciding whether to describe again compares this against when the file was
    last read."""


class Records:
    """The reads and writes of the one ordinary table this feature owns."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def mark(self, asset_id: str, *, revision: str, frames: int, at_ms: int) -> None:
        """Record that a file has been described, replacing any earlier record of it."""
        await self._database.execute(_MARK, (asset_id, revision, frames, at_ms))

    async def forget(self, asset_id: str) -> None:
        await self._database.execute(_FORGET, (asset_id,))

    async def forget_all(self, by: Actor | None = None) -> None:
        """What removing the index does to this half of it, and the act written down.

        `by` is who pressed Remove the index: the event goes in the same transaction as the sweep,
        the last write of the removal, as Faces' own removal writes its (`faces.store`). The
        subject is the feature's switch, the one thing with an id that the act was done to: every
        file's description has gone, and the library then reads as one Smart Search never read.
        """
        while True:
            async with self._database.write() as connection:
                cursor = await connection.execute(_FORGET_SOME, (_FORGET_BATCH,))
            if not cursor.rowcount:
                break
            await asyncio.sleep(0)
        if by is None:
            return
        async with self._database.write() as connection:
            await record_event(
                connection,
                actor=by,
                verb="forgot",
                subject=Subject(kind="setting", id=ENABLED_KEY, name="Smart Search"),
            )

    async def described(self, asset_id: str) -> Described | None:
        row = await self._database.fetch_one(_DESCRIBED, (asset_id,))
        if row is None:
            return None
        return Described(
            revision=str(row["revision"]),
            frames=int(row["frames"]),
            at_ms=int(row["indexed_at"]),
        )

    async def described_at_of(self, asset_ids: Sequence[str]) -> dict[str, int]:
        """When each of these files was last described, in milliseconds, by any model. A file
        never described is absent. What the look again at HEIF photos compares against the time
        of each one's whole-picture copy (`WholePicture.read_from_a_tile`)."""
        if not asset_ids:
            return {}
        sql, params = in_clause(_DESCRIBED_AT, list(asset_ids))
        rows = await self._database.fetch_all(sql, params)
        return {str(row["asset_id"]): int(row["indexed_at"]) for row in rows}

    async def describes_anything(self, revision: str) -> bool:
        """Whether this model has described any file at all. See `_ANY_DESCRIBED`."""
        return await self._database.fetch_one(_ANY_DESCRIBED, (revision,)) is not None

    async def described_count(self, revision: str) -> int:
        """How many files this model has described. Files described by an older one do not count,
        because their numbers are not comparable with anything being searched now."""
        row = await self._database.fetch_one(_COUNT_DESCRIBED, (revision,))
        return int(row["total"]) if row is not None else 0

    async def described_by_others(self, revision: str) -> int:
        """How many files a model other than this one described. Their numbers are in the index
        and out of every search, until the Build describes them again."""
        row = await self._database.fetch_one(_COUNT_BY_OTHERS, (revision, revision))
        return int(row["total"]) if row is not None else 0

    async def any_described_by_others(self, revision: str) -> bool:
        """Whether `described_by_others` is above nought, without counting them."""
        row = await self._database.fetch_one(_ANY_BY_OTHERS, (revision, revision))
        return row is not None and bool(row["found"])

    async def forget_others(self, revision: str) -> int:
        """Forget which files a model other than this one described. How many records went.

        Only beside the purge of those models' frames: a record is a claim that numbers are held,
        and once they are not, the claim is the only thing still saying so.
        """
        async with self._database.write() as connection:
            cursor = await connection.execute(_FORGET_OTHERS, (revision,))
        return int(cursor.rowcount or 0)

    async def unsettled_among(self, asset_ids: Sequence[str], revision: str) -> set[str]:
        """Which of THESE files this model has not described. A page at a time, for the Build."""
        if not asset_ids:
            return set()
        wanted = list(asset_ids)
        sql, params = in_clause(
            "SELECT asset_id FROM semantic_indexed WHERE asset_id IN (?*) AND revision = ?", wanted
        )
        rows = await self._database.fetch_all(sql, [*params, revision])
        settled = {str(row["asset_id"]) for row in rows}
        return {one for one in wanted if one not in settled}

    def lack(self, revision: str) -> Lack:
        """Not described by this model, as a condition on the assets row for the content store to
        count the library against. The same rule as `unsettled_among`; a test that seeds a library
        and asks both holds them in step."""
        return Lack(_LACKS_DESCRIPTION, (revision,))

    async def settled_ids(self, revision: str) -> set[str]:
        """The files this model has already described, and therefore not worth describing again.

        Read as a set and compared in memory rather than joined against the asset table. It is a
        few hundred thousand short strings at the very largest, and the alternative is this feature
        writing its own version of the rule that decides what a user may see.
        """
        rows = await self._database.fetch_all(_SETTLED, (revision,))
        return {str(row["asset_id"]) for row in rows}
