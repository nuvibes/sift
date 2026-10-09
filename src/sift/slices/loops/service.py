# SPDX-License-Identifier: AGPL-3.0-or-later
"""Loop writes, unscoped: the route resolves the file first; a loop must sit inside its file."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from sift.kernel.changes import About, telling, who_may_see_a_file
from sift.kernel.db import Database, Row
from sift.kernel.ids import new_id
from sift.kernel.log import get_logger
from sift.kernel.wiring import Part

log = get_logger(__name__)


class Refused(ValueError):
    """A loop that could not describe a stretch of the file it names."""


@dataclass(frozen=True, slots=True)
class Loop:
    """A loop as the table holds it. What a screen is shown is `LoopView`."""

    id: str
    asset_id: str
    name: str | None
    start_ms: int
    end_ms: int
    created_at: int


def loop_from_row(row) -> Loop:  # type: ignore[no-untyped-def]
    return Loop(
        id=str(row["id"]),
        asset_id=str(row["asset_id"]),
        name=row["name"],
        start_ms=int(row["start_ms"]),
        end_ms=int(row["end_ms"]),
        created_at=int(row["created_at"]),
    )


_INSERT = """
INSERT INTO loops (id, asset_id, start_ms, end_ms, name, created_by, created_at)
VALUES (?, ?, ?, ?, ?, ?, ?)
RETURNING *
"""
_RENAME = "UPDATE loops SET name = ? WHERE id = ? RETURNING *"
_RETIME = "UPDATE loops SET start_ms = ?, end_ms = ? WHERE id = ? RETURNING *"
_DELETE = "DELETE FROM loops WHERE id = ?"

#: Every mark of exactly this stretch: nothing knows the id, so the stretch is the key.
_SUPERSEDED = "SELECT id, name FROM loops WHERE asset_id = ? AND start_ms = ? AND end_ms = ?"

#: Move the retired mark's tags, which `ON DELETE CASCADE` would otherwise destroy.
_MOVE_MARK_TAGS = """
INSERT INTO loop_tags (loop_id, tag_id, added_at)
SELECT ?, tag_id, added_at FROM loop_tags WHERE loop_id = ?
ON CONFLICT DO NOTHING
"""

#: And its name, only filling a blank on the new row.
_TAKE_NAME_FROM = """
UPDATE loops SET name = (SELECT name FROM loops WHERE id = ?)
 WHERE id = ? AND name IS NULL
"""

_FORGET = "DELETE FROM loops WHERE id = ?"

#: Marks whose own still is not built; `json_extract` so the canonical form is not redone here.
_WITHOUT_STILL = """
SELECT l.asset_id, l.start_ms
  FROM loops l
 WHERE NOT EXISTS (
   SELECT 1 FROM derivatives d  -- nosemgrep: sift-no-asset-sql-outside-kernel
    WHERE d.asset_id = l.asset_id AND d.kind = 'thumb'
      AND json_extract(d.params, '$.at_ms') = l.start_ms)
 GROUP BY l.asset_id, l.start_ms
 ORDER BY l.asset_id, l.start_ms
 LIMIT ?
"""

_SET_TAG = (
    "INSERT INTO loop_tags (loop_id, tag_id, added_at) VALUES (?, ?, ?) ON CONFLICT DO NOTHING"
)
_UNSET_TAG = "DELETE FROM loop_tags WHERE loop_id = ? AND tag_id = ?"
_TAGS_ON = """
SELECT t.id, t.name
  FROM tags t JOIN loop_tags lt ON lt.tag_id = t.id
 WHERE lt.loop_id = ?
 ORDER BY COALESCE(t.name_sort, t.name), t.id
"""

#: Below this it is a mis-click, a double press of the one button.
MIN_LOOP_MS = 250

#: Injected at boot: the job type belongs to another feature.
StillWanted = Callable[[str, int], Awaitable[None]]


_MARKED = "SELECT 1 FROM loops WHERE asset_id = ? AND start_ms = ? AND end_ms = ? LIMIT 1"


class LoopService:
    """Loop writes. Takes only the database: a loop names no grants and moves no picture."""

    def __init__(
        self,
        database: Database,
        *,
        clock: Callable[[], float] = time.time,
        wants_still: StillWanted | None = None,
    ) -> None:
        self._db = database
        self._clock = clock
        self._wants_still = wants_still

    def _now(self) -> int:
        return int(self._clock())

    async def marked(self, asset_id: str, start_ms: int, end_ms: int) -> bool:
        """Whether this exact stretch of this file is already a mark, asked before an import."""
        return await self._db.fetch_one(_MARKED, (asset_id, start_ms, end_ms)) is not None

    async def create(
        self,
        *,
        asset_id: str,
        start_ms: int,
        end_ms: int,
        name: str | None,
        created_by: str | None,
        duration_ms: int | None,
    ) -> Loop:
        """Mark a stretch of a file; an unprobed file's end is taken as given."""
        self._check(start_ms, end_ms, duration_ms)
        async with telling(self._db, who_may_see_a_file, About.LIBRARY) as connection:
            rows = list(
                await connection.execute_fetchall(
                    _INSERT,
                    (new_id(), asset_id, start_ms, end_ms, name, created_by, self._now()),
                )
            )
        loop = loop_from_row(rows[0])
        # Outside the write: the write guard is not reentrant, so a nested one deadlocks.
        await self.wants_still(loop.asset_id, loop.start_ms)
        return loop

    async def wants_still(self, asset_id: str, start_ms: int) -> None:
        """Ask for the picture this mark is drawn as; a failure is logged, never raised."""
        if self._wants_still is None:
            return
        try:
            await self._wants_still(asset_id, start_ms)
        except Exception:  # pragma: no cover (defensive; the queue does not raise in practice)
            log.warning("loops.still_not_queued", asset_id=asset_id, start_ms=start_ms)

    async def marks_without_still(self, limit: int) -> Sequence[Row]:
        """Every distinct video-and-moment with no still yet, oldest first, up to `limit`."""
        return await self._db.fetch_all(_WITHOUT_STILL, (limit,))

    @staticmethod
    def _check(start_ms: int, end_ms: int, duration_ms: int | None) -> None:
        if start_ms < 0:
            raise Refused("a loop cannot start before the file does")
        if end_ms - start_ms < MIN_LOOP_MS:
            raise Refused("a loop that short is a mis-click")
        if duration_ms is not None and start_ms >= duration_ms:
            raise Refused("a loop cannot start after the file ends")
        # It cannot end after the file either; ending exactly at the end is allowed.
        if duration_ms is not None and end_ms > duration_ms:
            raise Refused("a loop cannot end after the file does")

    async def rename(self, loop_id: str, name: str | None) -> Loop | None:
        async with telling(self._db, who_may_see_a_file, About.LIBRARY) as connection:
            rows = list(await connection.execute_fetchall(_RENAME, (name, loop_id)))
        return loop_from_row(rows[0]) if rows else None

    async def forget_many(self, loop_ids: Sequence[str]) -> int:
        """Forget a selection of marks in one write and one announcement; returns how many went."""
        if not loop_ids:
            return 0
        async with telling(self._db, who_may_see_a_file, About.LIBRARY) as connection:
            gone = 0
            for loop_id in loop_ids:
                cursor = await connection.execute(_DELETE, (loop_id,))
                gone += cursor.rowcount if cursor.rowcount > 0 else 0
            return gone

    async def supersede(self, asset_id: str, start_ms: int, end_ms: int, *, into: str) -> int:
        """Retire every mark of this exact stretch, name and tags moved first; returns how many."""
        async with telling(self._db, who_may_see_a_file, About.LIBRARY) as connection:
            marks = list(
                await connection.execute_fetchall(_SUPERSEDED, (asset_id, start_ms, end_ms))
            )
            for row in marks:
                gone = str(row["id"])
                if gone == into:
                    # Nothing to do, and removing it would delete the row that just replaced it.
                    continue
                await connection.execute(_MOVE_MARK_TAGS, (into, gone))
                await connection.execute(_TAKE_NAME_FROM, (gone, into))
                await connection.execute(_FORGET, (gone,))
        return len([row for row in marks if str(row["id"]) != into])

    async def set_tag(self, loop_id: str, tag_id: str, *, on: bool) -> None:
        async with telling(self._db, who_may_see_a_file, About.LIBRARY) as connection:
            if on:
                await connection.execute(_SET_TAG, (loop_id, tag_id, self._now()))
            else:
                await connection.execute(_UNSET_TAG, (loop_id, tag_id))

    async def tags_on(self, loop_id: str) -> list[Row]:
        """The tags on one, by name."""
        return await self._db.fetch_all(_TAGS_ON, (loop_id,))


SERVICE: Part[LoopService] = Part("loops")
