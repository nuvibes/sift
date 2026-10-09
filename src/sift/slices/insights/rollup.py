# SPDX-License-Identifier: AGPL-3.0-or-later
"""The quiet adder-up: each finished day of each User, added up once, a piece at a time."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from contextlib import suppress
from datetime import date, timedelta
from typing import Final

from sift.kernel.db import Database
from sift.kernel.log import get_logger
from sift.kernel.when import day_of
from sift.slices.insights import store
from sift.slices.insights.metrics import PICKUP_GAP_SECONDS
from sift.slices.insights.store import DayRow, count_day, day_bounds, local_today

log = get_logger(__name__)

#: How often the loop looks for a piece when there was nothing to do last time.
ROLLUP_INTERVAL_SECONDS: Final = 60.0

#: How soon it comes back when the last piece found work and there may be more.
CATCH_UP_PAUSE_SECONDS: Final = 2.0

#: How long after its midnight a day is finished. See the module docstring.
FINISHED_AFTER_SECONDS: Final = 60 * 60

#: How long past its end a finished day waits for a User still in the middle of something.
WAIT_AT_MOST_SECONDS: Final = 24 * 60 * 60

#: Whether anything somebody is waiting for is queued. Handed in by the composition root.
Waiting = Callable[[], Awaitable[bool]]

#: Handed in by the composition root, the one place holding what the recap makers need.
AfterDay = Callable[[str, date], Awaitable[object]]

#: The User furthest behind: nobody added up yet first, then the oldest `added_up_to`. Users are
#: few (a household), so this reads them all.
_BEHIND = """
SELECT u.id AS user_id, u.created_at AS created_at, p.added_up_to AS added_up_to
  FROM users u
  LEFT JOIN insight_progress p ON p.user_id = u.id
 WHERE p.added_up_to IS NULL OR p.added_up_to < :last
 ORDER BY p.added_up_to IS NOT NULL, p.added_up_to, u.id
 LIMIT 1
"""

#: NULL for a User who has done nothing yet, who starts from the day they were made.
_FIRST_FACT = """
SELECT MIN(
         COALESCE((SELECT MIN(started_at) FROM plays WHERE user_id = :user), :made),
         COALESCE((SELECT MIN(started_at) FROM theater_sessions WHERE user_id = :user), :made),
         COALESCE((SELECT MIN(at) FROM opinions WHERE user_id = :user), :made),
         :made) AS first
"""

#: One day whose split is behind its User's stamp. `ix_insight_days_split` answers it per User.
_STALE = """
SELECT d.user_id AS user_id, d.day AS day, u.cache_stamp AS stamp
  FROM users u
  JOIN insight_days d ON d.user_id = u.id AND d.split_at < u.cache_stamp
 LIMIT 1
"""

_DAY_ROWS = """
SELECT day, metric, key, whole, hidden FROM insight_days WHERE user_id = ? AND day = ?
"""

_STAMP = "SELECT cache_stamp FROM users WHERE id = ?"

#: A User with a day to add up again, and the mark it was asked at.
_DIRTY = """
SELECT user_id, dirty_from, dirty_mark, added_up_to
  FROM insight_progress
 WHERE dirty_from IS NOT NULL
 LIMIT 1
"""

#: Whether this User is still in the middle of something that began on the day or since: a
#: sitting that reported in the last half hour. Bounded by the User's own sittings since the day.
_STILL_GOING = """
SELECT EXISTS (SELECT 1 FROM plays p
                WHERE p.user_id = :user AND p.started_at >= :since AND p.made_at >= :recent)
       AS going
"""


def last_finished_day(now: float) -> date:
    """The newest day that is finished at `now`: yesterday, once today is an hour old."""
    return local_today(now - FINISHED_AFTER_SECONDS) - timedelta(days=1)


async def add_up_one_day(
    database: Database, *, now: float | None = None, after_day: Sequence[AfterDay] = ()
) -> bool:
    """Add up the next finished day of the User furthest behind. False when nobody is behind."""
    moment = time.time() if now is None else now
    if await add_up_again(database):
        return True
    last = last_finished_day(moment)
    behind = await database.fetch_one(_BEHIND, {"last": last.isoformat()})
    if behind is None:
        return False
    user_id = str(behind["user_id"])
    if behind["added_up_to"] is None:
        # The first time this User is met: start from the day before the first thing of theirs
        # there is to count, so a library with history is added up from its beginning.
        made = int(behind["created_at"] or moment)
        first = await database.fetch_one(_FIRST_FACT, {"user": user_id, "made": made})
        start = day_of(int(first["first"]) if first else made)
        async with database.write() as connection:
            await store.start_progress(connection, user_id, min(start - timedelta(days=1), last))
        return True
    day = date.fromisoformat(str(behind["added_up_to"])) + timedelta(days=1)
    opened, closed = day_bounds(day)
    waited_out = moment >= closed + WAIT_AT_MOST_SECONDS
    if not waited_out and await still_going(database, user_id, opened, moment):
        return False
    started = time.perf_counter()
    # Read before the count: a hide landing in between leaves a stale stamp, split again later.
    stamp_row = await database.fetch_one(_STAMP, (user_id,))
    stamp = int(stamp_row["cache_stamp"]) if stamp_row is not None else 0
    counted = await count_day(database.fetch_all, user_id, day)
    async with database.write() as connection:
        await store.write_day(connection, user_id, day, counted, stamp)
    log.info(
        "insights.day_added_up",
        user_id=user_id,
        day=day.isoformat(),
        rows=len(counted),
        ms=round((time.perf_counter() - started) * 1000, 1),
        behind_days=(last - day).days,
        waited_out=waited_out,
    )
    if day == last:
        for make in after_day:
            try:
                await make(user_id, day)
            except Exception:
                log.exception("insights.after_day_failed", user_id=user_id, day=day.isoformat())
    return True


async def still_going(database: Database, user_id: str, since: int, now: float) -> bool:
    """Whether this User has a sitting that began at `since` or later and reported recently."""
    row = await database.fetch_one(
        _STILL_GOING,
        {"user": user_id, "since": since, "recent": int(now) - PICKUP_GAP_SECONDS},
    )
    return bool(row is not None and row["going"])


async def add_up_again(database: Database) -> bool:
    """Add up again the next day whose raw rows changed after it was added up. False when none."""
    dirty = await database.fetch_one(_DIRTY)
    if dirty is None:
        return False
    user_id = str(dirty["user_id"])
    day = date.fromisoformat(str(dirty["dirty_from"]))
    mark = int(dirty["dirty_mark"])
    started = time.perf_counter()
    stamp_row = await database.fetch_one(_STAMP, (user_id,))
    stamp = int(stamp_row["cache_stamp"]) if stamp_row is not None else 0
    counted = await count_day(database.fetch_all, user_id, day)
    async with database.write() as connection:
        await store.write_day_again(connection, user_id, day, counted, stamp, mark)
    log.info(
        "insights.day_added_up_again",
        user_id=user_id,
        day=day.isoformat(),
        rows=len(counted),
        ms=round((time.perf_counter() - started) * 1000, 1),
    )
    return True


async def split_one_stale_day(database: Database) -> bool:
    """Work one stale day's vault split out again and write it down. False when none is stale."""
    stale = await database.fetch_one(_STALE)
    if stale is None:
        return False
    user_id = str(stale["user_id"])
    day = date.fromisoformat(str(stale["day"]))
    stamp = int(stale["stamp"])
    stored = [
        DayRow(str(r["day"]), str(r["metric"]), str(r["key"]), int(r["whole"]), int(r["hidden"]))
        for r in await database.fetch_all(_DAY_ROWS, (user_id, day.isoformat()))
    ]
    split = await store.resplit(database.fetch_all, user_id, day, stored)
    async with database.write() as connection:
        await store.write_split(connection, user_id, day, split, stamp)
    log.debug("insights.day_split_again", user_id=user_id, day=day.isoformat())
    return True


async def one_piece(
    database: Database, *, now: float | None = None, after_day: Sequence[AfterDay] = ()
) -> bool:
    """Take one piece of work, if there is one. True when there may be more."""
    if await add_up_one_day(database, now=now, after_day=after_day):
        return True
    return await split_one_stale_day(database)


async def keep_the_days_added_up(
    database: Database,
    stop: asyncio.Event,
    *,
    somebody_waiting: Waiting,
    after_day: Sequence[AfterDay] = (),
    interval: float = ROLLUP_INTERVAL_SECONDS,
    catch_up: float = CATCH_UP_PAUSE_SECONDS,
) -> None:
    """Add up finished days, a piece at a time, until told to stop. See the module docstring."""
    pause = interval
    while not stop.is_set():
        with suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=pause)
        if stop.is_set():
            return
        pause = interval
        try:
            if await somebody_waiting():
                # Only the split: see "The split does not give way" in the module docstring.
                if await split_one_stale_day(database):
                    pause = catch_up
                continue
            if await one_piece(database, after_day=after_day):
                pause = catch_up
        except Exception:
            # Housekeeping does not get to take the application down with it.
            log.exception("insights.rollup_failed")


__all__ = [
    "CATCH_UP_PAUSE_SECONDS",
    "ROLLUP_INTERVAL_SECONDS",
    "add_up_again",
    "add_up_one_day",
    "day_bounds",
    "keep_the_days_added_up",
    "last_finished_day",
    "one_piece",
    "split_one_stale_day",
]
