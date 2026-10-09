# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading a User's figures, and the few writes the helper and the recaps make."""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Final
from weakref import WeakKeyDictionary

from sift.kernel import when as machine
from sift.kernel.access.arrivals import Fetch
from sift.kernel.audience import Audience
from sift.kernel.changes import About, announce, telling
from sift.kernel.content.view_rule import counts_as_a_view
from sift.kernel.db import Connection, Database, Row
from sift.kernel.ids import new_id
from sift.kernel.memo import MarkedMemo
from sift.slices.insights.metrics import (
    ANYTHING,
    DAY_OPINION_FILES,
    DAY_PLAYS,
    DAY_RAN_ON_FILES,
    METRICS,
    METRICS_VERSION,
    MINUTES,
    rows_of,
)

#: Days the helper has not reached yet still counted live; older would read all history per visit.
LIVE_DAYS: Final = 7

#: How a statement is asked: a read, returning rows. The database's own `fetch_all` is one; a
#: write connection's `execute_fetchall` is another, for the helper reading inside its transaction.


@dataclass(frozen=True)
class DayRow:
    """One figure for one day: counted over everything, and the part of it that was hidden."""

    day: str
    metric: str
    key: str
    whole: int
    hidden: int

    def shown(self, *, locked: bool) -> int:
        """The figure a reader draws: all of it while the vault is open, the rest while locked."""
        return self.whole - self.hidden if locked else self.whole


@dataclass(frozen=True)
class Figures:
    """What `rows` answers: the rows, and which days were counted live or not counted at all."""

    rows: tuple[DayRow, ...]
    #: Days in the span counted live from the raw tables: today, and any recent day not yet added.
    live_days: tuple[str, ...]
    #: Days in the span the helper has not reached and that are too old to count live.
    missing_days: tuple[str, ...]


@dataclass(frozen=True)
class RecapRow:
    """One recap as stored. `body` is the cards as the maker built them, opaque here."""

    id: str
    user_id: str
    period: str
    made_at: int
    seen_at: int | None
    body: str
    #: The statements' version it was made by (`metrics.METRICS_VERSION`); behind it is a recap
    #: made before a correction.
    metrics_version: int = METRICS_VERSION
    #: The people and files its reader took out of it before sharing it.
    left_out: tuple[str, ...] = ()


# --- days ------------------------------------------------------------------------------------


def local_today(now: float | None = None) -> date:
    """Today on the server machine's clock; there is no time zone setting."""
    return machine.today(now)


def day_bounds(day: date) -> tuple[int, int]:
    """The day as seconds, `[start, end)`, from its two midnights on the server machine's clock."""
    return machine.day_bounds(day)


def _days(day_from: date, day_to: date) -> list[date]:
    return [day_from + timedelta(days=n) for n in range((day_to - day_from).days + 1)]


# --- counting one day -------------------------------------------------------------------------

#: A per-file key is the id or `<kind>:<id>`, so the id is read after the first colon.
_HIDDEN_FILES = """
SELECT f.value AS asset_id
  FROM json_each(:files) f
 WHERE NOT EXISTS (SELECT 1 FROM viewer_assets va
                    WHERE va.user_id = :user AND va.asset_id = f.value AND va.concealed = 0)
   AND (EXISTS (SELECT 1 FROM viewer_assets va
                 WHERE va.user_id = :user AND va.asset_id = f.value)
        OR NOT EXISTS (SELECT 1 FROM insight_days d
                        WHERE d.user_id = :user AND d.day = :day
                          AND d.metric IN ('sittings:file', 'rated:file', 'o:file',
                                           'starred:file', 'first_file', 'last_file',
                                           'new_favourites:file', 'rediscovered:file')
                          AND substr(d.key, instr(d.key, ':') + 1) = f.value
                          AND d.hidden = 0))
"""


async def views_of_the_day(fetch: Fetch, user_id: str, day: date) -> tuple[list[str], set[str]]:
    """The day's sittings that were views, by the player's own rule, and every sitting's files."""
    start, end = day_bounds(day)
    rows = await fetch(DAY_PLAYS, {"user": user_id, "start": start, "end": end})
    views: list[str] = []
    files: set[str] = set()
    for row in rows:
        if row["asset_id"] is not None:
            files.add(str(row["asset_id"]))
        if counts_as_a_view(str(row["media_type"]), row["length_ms"], int(row["watched_ms"])):
            views.append(str(row["id"]))
    return views, files


async def hidden_files(fetch: Fetch, user_id: str, day: date, files: Iterable[str]) -> list[str]:
    """Which of these files count as hidden for this User on this day. See the module docstring."""
    wanted = sorted(set(files))
    if not wanted:
        return []
    rows = await fetch(
        _HIDDEN_FILES, {"user": user_id, "day": day.isoformat(), "files": json.dumps(wanted)}
    )
    return [str(row["asset_id"]) for row in rows]


async def count_day(
    fetch: Fetch, user_id: str, day: date, metrics: Iterable[str] = METRICS
) -> list[DayRow]:
    """Every asked-for figure of one User's one day, counted from the raw tables."""
    asked = sorted(set(metrics))
    unknown = [metric for metric in asked if metric not in METRICS]
    if unknown:
        raise ValueError(f"not a metric: {', '.join(unknown)}")
    start, end = day_bounds(day)
    base = {"user": user_id, "start": start, "end": end}
    (anything,) = await fetch(ANYTHING, base)
    if not anything["anything"] and not await rows_of("files_added", fetch, base):
        return []
    views, files = await views_of_the_day(fetch, user_id, day)
    for row in await fetch(DAY_OPINION_FILES, base):
        files.add(str(row["asset_id"]))
    # The evening's run past midnight is this day's too (`metrics_visits.LATEST_FINISH`).
    for row in await fetch(DAY_RAN_ON_FILES, base):
        files.add(str(row["asset_id"]))
    hidden = await hidden_files(fetch, user_id, day, files)
    params = {**base, "views": json.dumps(views), "hidden": json.dumps(hidden)}
    counted: list[DayRow] = []
    iso = day.isoformat()
    for metric in asked:
        for row in await rows_of(metric, fetch, params):
            whole = int(row["whole"] or 0)
            # A zero is not filed: an absent row reads as zero. The two minutes of the day are the
            # exception, where 0 is midnight and a real answer.
            if whole == 0 and metric not in MINUTES:
                continue
            counted.append(
                DayRow(iso, metric, str(row["key"] or ""), whole, int(row["hidden"] or 0))
            )
    return counted


# --- reading ----------------------------------------------------------------------------------

_STAMP = "SELECT cache_stamp FROM users WHERE id = ?"
_PROGRESS = "SELECT added_up_to FROM insight_progress WHERE user_id = ?"
_STORED = """
SELECT day, metric, key, whole, hidden, split_at
  FROM insight_days
 WHERE user_id = :user AND day >= :from AND day <= :to
   AND metric IN (SELECT value FROM json_each(:metrics))
"""


async def stamp_of(database: Database, user_id: str) -> int:
    """The User's `cache_stamp`: what a split is measured against. 0 for a User that is gone."""
    row = await database.fetch_one(_STAMP, (user_id,))
    return int(row["cache_stamp"]) if row is not None else 0


#: The first day this User has any row for: where "all" starts. Bounded by the one User's rows.
_FIRST_DAY = "SELECT MIN(day) AS first FROM insight_days WHERE user_id = ?"

#: Whether this User has ever had a sitting added up: what tells an empty library from a quiet
#: period. Bounded by the LIMIT and by the one User's rows.
_EVER_SAT = (
    "SELECT 1 AS sat FROM insight_days WHERE user_id = ? AND metric = 'sittings' AND whole > 0"
    " LIMIT 1"
)


async def first_day(database: Database, user_id: str) -> date | None:
    """The first day this User has a row for, or None for a User with none."""
    row = await database.fetch_one(_FIRST_DAY, (user_id,))
    return None if row is None or row["first"] is None else date.fromisoformat(str(row["first"]))


async def ever_sat(database: Database, user_id: str) -> bool:
    """Whether this User has ever had a sitting added up."""
    return await database.fetch_one(_EVER_SAT, (user_id,)) is not None


async def added_up_to(database: Database, user_id: str) -> date | None:
    """The last finished day the helper has added up for this User, or None before its first."""
    row = await database.fetch_one(_PROGRESS, (user_id,))
    return date.fromisoformat(str(row["added_up_to"])) if row is not None else None


#: A year of two periods for a household of Users, each a handful of numbers.
READ_SPLITS_KEPT: Final = 4096

#: Per database, so two never answer for each other and a dropped one takes its splits along.
_READ_SPLITS: WeakKeyDictionary[Database, MarkedMemo[dict[tuple[str, str], int]]] = (
    WeakKeyDictionary()
)


async def fresh_hidden(
    fetch: Fetch, user_id: str, day: date, metrics: Iterable[str]
) -> dict[tuple[str, str], int]:
    """Which part of each of these metrics' rows of one day is hidden now, counted afresh."""
    return {
        (row.metric, row.key): row.hidden for row in await count_day(fetch, user_id, day, metrics)
    }


async def resplit(fetch: Fetch, user_id: str, day: date, stored: Sequence[DayRow]) -> list[DayRow]:
    """The stored rows of one day with their `hidden` worked out again now."""
    fresh = await fresh_hidden(fetch, user_id, day, {row.metric for row in stored})
    return _split_with(stored, fresh)


async def _read_split(
    database: Database, user_id: str, day: date, stamp: int, stored: Sequence[DayRow]
) -> list[DayRow]:
    """`resplit` for a reader: worked out once per day and stamp, then answered from memory."""
    memo = _READ_SPLITS.get(database)
    if memo is None:
        memo = _READ_SPLITS[database] = MarkedMemo(kept=READ_SPLITS_KEPT)
    metrics = frozenset(row.metric for row in stored)
    fresh = await memo.get(
        (user_id, day.isoformat(), metrics),
        str(stamp),
        lambda: fresh_hidden(database.fetch_all, user_id, day, metrics),
    )
    return _split_with(stored, fresh)


def _split_with(stored: Sequence[DayRow], fresh: dict[tuple[str, str], int]) -> list[DayRow]:
    """The stored rows with the fresh hidden parts: see `resplit` for the rules."""
    out: list[DayRow] = []
    for row in stored:
        hidden = fresh.get((row.metric, row.key), row.hidden)
        out.append(DayRow(row.day, row.metric, row.key, row.whole, min(hidden, row.whole)))
    return out


async def rows(
    database: Database,
    user_id: str,
    day_from: date,
    day_to: date,
    metrics: Iterable[str],
    *,
    now: float | None = None,
) -> Figures:
    """Every row of the asked metrics over `[day_from, day_to]` for one User. See the docstring."""
    asked = sorted(set(metrics))
    unknown = [metric for metric in asked if metric not in METRICS]
    if unknown:
        raise ValueError(f"not a metric: {', '.join(unknown)}")
    today = local_today(now)
    day_to = min(day_to, today)
    if day_to < day_from:
        return Figures((), (), ())
    stamp = await stamp_of(database, user_id)
    reached = await added_up_to(database, user_id)
    stored = await database.fetch_all(
        _STORED,
        {
            "user": user_id,
            "from": day_from.isoformat(),
            "to": min(day_to, reached).isoformat() if reached is not None else "",
            "metrics": json.dumps(asked),
        },
    )
    by_day: dict[str, list[DayRow]] = {}
    stale: set[str] = set()
    for row in stored:
        day = str(row["day"])
        by_day.setdefault(day, []).append(
            DayRow(day, str(row["metric"]), str(row["key"]), int(row["whole"]), int(row["hidden"]))
        )
        if int(row["split_at"]) < stamp:
            stale.add(day)
    out: list[DayRow] = []
    for day, day_rows in sorted(by_day.items()):
        if day in stale:
            out.extend(
                await _read_split(database, user_id, date.fromisoformat(day), stamp, day_rows)
            )
        else:
            out.extend(day_rows)
    live: list[str] = []
    missing: list[str] = []
    for when in _days(day_from, day_to):
        if reached is not None and when <= reached:
            continue
        if (today - when).days < LIVE_DAYS:
            out.extend(await count_day(database.fetch_all, user_id, when, asked))
            live.append(when.isoformat())
        else:
            missing.append(when.isoformat())
    return Figures(tuple(out), tuple(live), tuple(missing))


async def today(
    database: Database,
    user_id: str,
    metrics: Iterable[str] = METRICS,
    *,
    now: float | None = None,
) -> list[DayRow]:
    """Today's rows, counted live from the raw tables by the same statements the helper uses."""
    return await count_day(database.fetch_all, user_id, local_today(now), metrics)


# --- the helper's writes ----------------------------------------------------------------------

_FORGET_DAY = "DELETE FROM insight_days WHERE user_id = ? AND day = ?"
_WRITE_ROW = (
    "INSERT INTO insight_days (user_id, day, metric, key, whole, hidden, split_at)"
    " VALUES (?, ?, ?, ?, ?, ?, ?)"
)
_ADVANCE = (
    "INSERT INTO insight_progress (user_id, added_up_to) VALUES (?, ?)"
    " ON CONFLICT(user_id) DO UPDATE SET added_up_to = excluded.added_up_to"
)
_RESPLIT_ROW = (
    "UPDATE insight_days SET hidden = ?, split_at = ?"
    " WHERE user_id = ? AND day = ? AND metric = ? AND key = ?"
)
_RESTAMP_DAY = "UPDATE insight_days SET split_at = ? WHERE user_id = ? AND day = ? AND split_at < ?"


async def write_day(
    connection: Connection, user_id: str, day: date, counted: Sequence[DayRow], stamp: int
) -> None:
    """File one finished day's rows and move the User's progress past it, in the caller's write."""
    iso = day.isoformat()
    await connection.execute(_FORGET_DAY, (user_id, iso))
    await connection.executemany(
        _WRITE_ROW,
        [(user_id, iso, row.metric, row.key, row.whole, row.hidden, stamp) for row in counted],
    )
    await connection.execute(_ADVANCE, (user_id, iso))


#: A day added up again (`rollup`): its `dirty_from` moves past it only if no mark landed while it
#: was counted (`dirty_mark` unchanged), and goes once it is past the last day added up.
_PAST_DIRTY = (
    "UPDATE insight_progress"
    " SET dirty_from = CASE WHEN :next <= added_up_to THEN :next END"
    " WHERE user_id = :user AND dirty_mark = :mark"
)


async def write_day_again(
    connection: Connection,
    user_id: str,
    day: date,
    counted: Sequence[DayRow],
    stamp: int,
    mark: int,
) -> None:
    """File a day added up again over its old rows, without moving the User's progress."""
    iso = day.isoformat()
    await connection.execute(_FORGET_DAY, (user_id, iso))
    await connection.executemany(
        _WRITE_ROW,
        [(user_id, iso, row.metric, row.key, row.whole, row.hidden, stamp) for row in counted],
    )
    await connection.execute(
        _PAST_DIRTY,
        {"next": (day + timedelta(days=1)).isoformat(), "user": user_id, "mark": mark},
    )


async def start_progress(connection: Connection, user_id: str, day: date) -> None:
    """Say where a User's adding-up starts: the day before the first thing there is to count."""
    await connection.execute(_ADVANCE, (user_id, day.isoformat()))


async def write_split(
    connection: Connection, user_id: str, day: date, split: Sequence[DayRow], stamp: int
) -> None:
    """Write a day's re-worked-out split down, in the caller's write."""
    iso = day.isoformat()
    await connection.executemany(
        _RESPLIT_ROW,
        [(row.hidden, stamp, user_id, iso, row.metric, row.key) for row in split],
    )
    await connection.execute(_RESTAMP_DAY, (stamp, user_id, iso, stamp))


# A recap is frozen: written twice, the first is kept, except by `remake_recap`.

_WRITE_RECAP = (
    "INSERT INTO recaps (id, user_id, period, made_at, seen_at, body, metrics_version)"
    " VALUES (?, ?, ?, ?, NULL, ?, ?)"
    " ON CONFLICT(user_id, period) DO NOTHING"
)
_RECAP_OF_PERIOD = "SELECT * FROM recaps WHERE user_id = ? AND period = ?"
_RECAPS_OF = "SELECT * FROM recaps WHERE user_id = ? ORDER BY made_at DESC, id DESC"
_RECAP = "SELECT * FROM recaps WHERE user_id = ? AND id = ?"
_MARK_SEEN = "UPDATE recaps SET seen_at = ? WHERE user_id = ? AND id = ? AND seen_at IS NULL"
_LEAVE_OUT = "UPDATE recaps SET left_out = ? WHERE user_id = ? AND id = ?"


def _recap(row: Row) -> RecapRow:
    return RecapRow(
        id=str(row["id"]),
        user_id=str(row["user_id"]),
        period=str(row["period"]),
        made_at=int(row["made_at"]),
        seen_at=None if row["seen_at"] is None else int(row["seen_at"]),
        body=str(row["body"]),
        metrics_version=int(row["metrics_version"]),
        left_out=_ids(row["left_out"]),
    )


def _ids(stored: object) -> tuple[str, ...]:
    """A stored list of ids; none where it does not read as one."""
    try:
        held = json.loads(str(stored))
    except ValueError:
        return ()
    return tuple(str(one) for one in held) if isinstance(held, list) else ()


async def write_recap(
    database: Database, user_id: str, period: str, body: str, *, made_at: int | None = None
) -> RecapRow:
    """File a recap for a period, or hand back the one already filed; only a new one is told."""
    async with telling(database, Audience.of_user(user_id), About.MINE) as connection:
        await connection.execute(
            _WRITE_RECAP,
            (
                new_id(),
                user_id,
                period,
                int(time.time()) if made_at is None else made_at,
                body,
                METRICS_VERSION,
            ),
        )
        (row,) = await connection.execute_fetchall(_RECAP_OF_PERIOD, (user_id, period))
    return _recap(row)


_RECAPS_BEHIND = (
    "SELECT * FROM recaps WHERE user_id = ? AND metrics_version < ? ORDER BY made_at, id"
)
_REMAKE_RECAP = (
    "UPDATE recaps SET body = ?, made_at = ?, metrics_version = ?"
    " WHERE user_id = ? AND id = ? AND metrics_version < ?"
)


async def recaps_behind(database: Database, user_id: str) -> list[RecapRow]:
    """This User's recaps made by statements a later version corrected, oldest first."""
    return [
        _recap(row) for row in await database.fetch_all(_RECAPS_BEHIND, (user_id, METRICS_VERSION))
    ]


async def remake_recap(
    database: Database,
    user_id: str,
    recap_id: str,
    body: str,
    *,
    made_at: int | None = None,
    then: Callable[[Connection], Awaitable[object]] | None = None,
) -> bool:
    """Replace a recap behind `METRICS_VERSION`, once, `then` on the same write; True if it did."""
    async with telling(database, Audience.of_user(user_id), About.MINE) as connection:
        cursor = await connection.execute(
            _REMAKE_RECAP,
            (
                body,
                int(time.time()) if made_at is None else made_at,
                METRICS_VERSION,
                user_id,
                recap_id,
                METRICS_VERSION,
            ),
        )
        if cursor.rowcount > 0 and then is not None:
            await then(connection)
        return cursor.rowcount > 0


async def recaps_of(database: Database, user_id: str) -> list[RecapRow]:
    """Every recap this User has, newest first."""
    return [_recap(row) for row in await database.fetch_all(_RECAPS_OF, (user_id,))]


async def recap(database: Database, user_id: str, recap_id: str) -> RecapRow | None:
    """One of this User's recaps, or None, including when the id is somebody else's."""
    row = await database.fetch_one(_RECAP, (user_id, recap_id))
    return None if row is None else _recap(row)


async def mark_seen(
    database: Database, user_id: str, recap_id: str, *, at: int | None = None
) -> bool:
    """Draw the moment a recap was first opened. True only the first time."""
    async with database.write() as connection:
        cursor = await connection.execute(
            _MARK_SEEN, (int(time.time()) if at is None else at, user_id, recap_id)
        )
        # This User's other tabs let the announcement go: the shelf is their own list, re-read on
        # the `mine` bell (`recaps.svelte.ts`).
        if cursor.rowcount > 0:
            announce(Audience.of_user(user_id), About.MINE)
        return cursor.rowcount > 0


async def leave_out(database: Database, user_id: str, recap_id: str, ids: Sequence[str]) -> bool:
    """Keep what this User took out of their recap before sharing it. True when it is theirs."""
    async with telling(database, Audience.of_user(user_id), About.MINE) as connection:
        cursor = await connection.execute(
            _LEAVE_OUT, (json.dumps(sorted(set(ids))), user_id, recap_id)
        )
        return cursor.rowcount > 0
