# SPDX-License-Identifier: AGPL-3.0-or-later
"""Loop writes.

Unscoped, like every other slice's writes: nothing here decides who may do anything, and the route
resolves the file through the access layer before calling. That is what stops a loop being written
against a file the user may not be shown, which would be a way to confirm one exists.

The one rule this service enforces itself is the one that cannot be left to a caller: a loop must
have length, and its end must sit inside the file it is cut from. The database carries the first as
a CHECK, so no write path can produce a zero-length loop; the second needs the file's duration and
so lives here.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from sift.kernel.audience import Audience
from sift.kernel.changes import About, telling, who_may_see_a_file
from sift.kernel.db import Database, Row
from sift.kernel.ids import new_id
from sift.kernel.log import get_logger
from sift.kernel.wiring import Part

log = get_logger(__name__)


async def _whoever_may_see(database: Database) -> Audience:
    """Every admin and every user given anything. Read before the write, so `telling` still says
    nothing when no row moved."""
    async with database.read() as connection:
        return await who_may_see_a_file(connection)


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

#: Every mark that describes exactly this stretch of this video.
#:
#: By the STRETCH rather than by an id, because nothing knows the id. What is being retired is
#: whatever stood for a piece of a video, at the moment that piece becomes a file, and the
#: only thing the two have in common is the stretch itself. An id would have to travel from the
#: screen, through an edit request, into a background job and back out of it, which is a loop id
#: crossing a feature that must not know loops exist.
#:
#: Plural on purpose: two marks of one stretch are two rows saying the same thing, and one file now
#: says it better than both.
_SUPERSEDED = "SELECT id, name FROM loops WHERE asset_id = ? AND start_ms = ? AND end_ms = ?"

#: The retired mark's tags, put on the row that replaces it.
#:
#: `loop_tags` is `ON DELETE CASCADE`, so without this the tags on a mark are gone the moment it is
#: retired, silently, on an operation with no undo. A tag on a mark is a word about that MOMENT,
#: and the file replacing it is that moment, so it is the same word about the same thing.
#: `added_at` comes ACROSS rather than being stamped now, for the reason the paragraph above gives:
#: it is the same word about the same moment, so it was put there when it was put there.
_MOVE_MARK_TAGS = """
INSERT INTO loop_tags (loop_id, tag_id, added_at)
SELECT ?, tag_id, added_at FROM loop_tags WHERE loop_id = ?
ON CONFLICT DO NOTHING
"""

#: And its name, if it had one and the new row has none. Only fills a blank: a name already on the
#: survivor was chosen for it, and an older mark's must not overwrite that.
_TAKE_NAME_FROM = """
UPDATE loops SET name = (SELECT name FROM loops WHERE id = ?)
 WHERE id = ? AND name IS NULL
"""

_FORGET = "DELETE FROM loops WHERE id = ?"

#: Marks whose own still has not been built.
#:
#: A mark is drawn as a frame of its video AT ITS OWN MOMENT, which is a derivative filed under
#: `params`, so "has this one been built" is a question about the moment and not only about the
#: video. `json_extract` rather than a string comparison against canonical JSON: the stored form is
#: compact and sorted, and a hand-built `'{"at_ms":' || start_ms || '}'` would be a second
#: implementation of that canonicalisation sitting in a query, free to disagree with the one in
#: `params_key` the day either changes.
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

#: The shortest loop worth having. Below this it is a mis-click rather than a Loop, and the player
#: sets both ends by pressing one button twice, so a double press would otherwise leave a loop
#: nobody can see on the timeline and nobody meant to make.
MIN_LOOP_MS = 250

#: Told that a mark at this moment of this video wants a still. Injected at boot, because the job
#: type belongs to another feature and a slice never imports another slice: the composition root
#: knows both. Absent means nothing is built, which is what the service's own tests run with.
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
        """Whether this exact stretch of this file is already a mark: the question a second pass
        over the same source (a Stash import run again) asks before it makes one."""
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
        """Mark a stretch of a file.

        `duration_ms` is the file's own, and it is passed in rather than looked up because the route
        has already resolved the file through the access layer and holds it. None means the file was
        never probed, and then there is nothing to check the end against, so the end is taken as
        given rather than refused, which is the honest reading of "we do not know how long this is".
        """
        self._check(start_ms, end_ms, duration_ms)
        async with telling(self._db, await _whoever_may_see(self._db), About.LIBRARY) as connection:
            rows = list(
                await connection.execute_fetchall(
                    _INSERT,
                    (new_id(), asset_id, start_ms, end_ms, name, created_by, self._now()),
                )
            )
        loop = loop_from_row(rows[0])
        # OUTSIDE the write, deliberately. Asking for the still enqueues a job, which is a write of
        # its own, and the write guard is not reentrant: a second one taken inside the first
        # deadlocks the whole application rather than failing.
        await self.wants_still(loop.asset_id, loop.start_ms)
        return loop

    async def wants_still(self, asset_id: str, start_ms: int) -> None:
        """Ask for the picture this mark is drawn as, if anything is listening.

        Failing to build a still is a mark that wears its video's own picture, so it is worth a log
        line and never worth failing the write that somebody actually asked for.
        """
        if self._wants_still is None:
            return
        try:
            await self._wants_still(asset_id, start_ms)
        except Exception:  # pragma: no cover (defensive; the queue does not raise in practice)
            log.warning("loops.still_not_queued", asset_id=asset_id, start_ms=start_ms)

    async def marks_without_still(self, limit: int) -> Sequence[Row]:
        """Every distinct video-and-moment with no still yet, oldest first, up to `limit`.

        Distinct, because two marks that begin at the same millisecond of one video are one picture:
        the still is filed under `(asset_id, kind, params)` and both would ask for the same row.
        """
        return await self._db.fetch_all(_WITHOUT_STILL, (limit,))

    @staticmethod
    def _check(start_ms: int, end_ms: int, duration_ms: int | None) -> None:
        if start_ms < 0:
            raise Refused("a loop cannot start before the file does")
        if end_ms - start_ms < MIN_LOOP_MS:
            raise Refused("a loop that short is a mis-click")
        if duration_ms is not None and start_ms >= duration_ms:
            raise Refused("a loop cannot start after the file ends")
        # ...and it cannot END after the file does either. A mark running past the end lies about
        # itself: the player stops at the end of the video, so the tile says one length and playing
        # it gives another. Equal is allowed: marking to the very end is a real thing to want.
        if duration_ms is not None and end_ms > duration_ms:
            raise Refused("a loop cannot end after the file does")

    async def rename(self, loop_id: str, name: str | None) -> Loop | None:
        async with telling(self._db, await _whoever_may_see(self._db), About.LIBRARY) as connection:
            rows = list(await connection.execute_fetchall(_RENAME, (name, loop_id)))
        return loop_from_row(rows[0]) if rows else None

    async def forget_many(self, loop_ids: Sequence[str]) -> int:
        """Forget a selection of marks in ONE write. Returns how many rows went.

        The one way a mark is forgotten: no file is touched and no byte moves. One transaction and
        one announcement for the whole selection rather than one of each per row, the arrangement
        bulk delete uses (see `Deleter.remove_many`): forty marks one at a time would be forty
        round trips, each telling every screen holding a list that the library had changed.

        Callers resolve WHICH loops may go before calling: this writes what it is given.
        """
        if not loop_ids:
            return 0
        async with telling(self._db, await _whoever_may_see(self._db), About.LIBRARY) as connection:
            gone = 0
            for loop_id in loop_ids:
                cursor = await connection.execute(_DELETE, (loop_id,))
                gone += cursor.rowcount if cursor.rowcount > 0 else 0
            return gone

    async def supersede(self, asset_id: str, start_ms: int, end_ms: int, *, into: str) -> int:
        """Retire any mark that described exactly this stretch of this video. Returns how many.

        Called when that stretch has just become a file of its own. The mark and the clip are then
        two rows for one thing, and the wall would draw both: one that opens the clip and one that
        opens the whole video with the piece marked on it, which is the pair somebody looking at the
        screen would call a duplicate.

        **What the mark KNEW moves first, and only then is it removed.** The same order the username
        fold in the site merge takes, for the same reason: on an operation with no undo, the
        removal is last. A mark carries more than its two numbers: it can be named, and it can be
        tagged, and `loop_tags` is `ON DELETE CASCADE`, so a version of this that only deleted
        would destroy the mark's tags, silently, while claiming nothing was lost. The claim is true
        only because this is what makes it true.

        Ordered the other way round (removing before the new row exists), an insert that failed
        would leave a library with neither the mark nor the clip on the wall.
        """
        async with telling(self._db, await _whoever_may_see(self._db), About.LIBRARY) as connection:
            marks = list(
                await connection.execute_fetchall(_SUPERSEDED, (asset_id, start_ms, end_ms))
            )
            for row in marks:
                gone = str(row["id"])
                if gone == into:
                    # Nothing to do, and removing it would delete the row that just replaced it.
                    # Not reachable today: a produced file is a different asset from the one it
                    # was cut from, but the day something is cut from itself, this is the line
                    # between a no-op and losing the only row.
                    continue
                await connection.execute(_MOVE_MARK_TAGS, (into, gone))
                await connection.execute(_TAKE_NAME_FROM, (gone, into))
                await connection.execute(_FORGET, (gone,))
        return len([row for row in marks if str(row["id"]) != into])

    async def set_tag(self, loop_id: str, tag_id: str, *, on: bool) -> None:
        async with telling(self._db, await _whoever_may_see(self._db), About.LIBRARY) as connection:
            if on:
                await connection.execute(_SET_TAG, (loop_id, tag_id, self._now()))
            else:
                await connection.execute(_UNSET_TAG, (loop_id, tag_id))

    async def tags_on(self, loop_id: str) -> list[Row]:
        """The tags on one, by name.

        Rows rather than a type, for the reason the collection reader beside it gives: the
        tag's shape belongs to the tags slice, and importing it here would couple the two.
        """
        return await self._db.fetch_all(_TAGS_ON, (loop_id,))


#: The one loop service.
SERVICE: Part[LoopService] = Part("loops")
