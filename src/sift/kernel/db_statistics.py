# SPDX-License-Identifier: AGPL-3.0-or-later
"""The query planner's statistics: which tables are stale, and the refresh on a timer."""

from __future__ import annotations

import asyncio
import re
from contextlib import suppress
from typing import TYPE_CHECKING

import aiosqlite

from sift.kernel.log import get_logger

if TYPE_CHECKING:
    from sift.kernel.db import Database

log = get_logger(__name__)


#: How often a long-lived process refreshes the planner's statistics: daily, SQLite's own advice.
STATISTICS_INTERVAL_SECONDS = 24 * 60 * 60.0

#: The least time between two refreshes, whoever asked, so passes draining together pay once.
STATISTICS_MIN_INTERVAL_SECONDS = 60.0

#: The timer's form: `PRAGMA optimize` re-analyzes what SQLite thinks would benefit and is nearly
#: free once current. The boot's form analyzes each table `stale_tables` finds, one write each, since
#: `optimize(0x10002)` held the writer 732 ms in one block on a 182,000-file library and still left
#: a partial index counted empty.
_ANALYZE_WHAT_MOVED = "PRAGMA optimize"

#: Rows of each index an analysis reads: enough for the planner, and a bound on the writer's hold.
_ANALYSIS_LIMIT = "PRAGMA analysis_limit=400"

#: A table whose rows have grown or shrunk this many times over since its statistics were written
#: is analyzed again at boot. `optimize` waits for far more, and a plan chosen on a third of the
#: rows can walk the wrong index on every write.
STALE_FACTOR = 2

# What the planner was told of every table, with each index's definition beside it.
_STATISTICS_KEPT = (
    "SELECT s.tbl, s.idx, s.stat, i.sql FROM sqlite_stat1 s"
    " JOIN sqlite_master t ON t.type = 'table' AND t.name = s.tbl"
    " LEFT JOIN sqlite_master i ON i.type = 'index' AND i.name = s.idx"
)
_HAS_STATISTICS = "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sqlite_stat1'"
_TABLES = (
    "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite%'"
    " AND sql NOT LIKE 'CREATE VIRTUAL TABLE%'"
)
_ANY_ROW = 'SELECT 1 FROM "{table}" LIMIT 1'
#: Every index with its own statement, by table, and every index the statistics have counted.
_INDEXES = "SELECT tbl_name, name FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL"
_INDEXES_COUNTED = "SELECT tbl, idx FROM sqlite_stat1 WHERE idx IS NOT NULL"
_ROWS_NOW = 'SELECT COUNT(*) FROM "{table}"'
_HOLDS_A_ROW = 'SELECT 1 FROM "{table}" WHERE {where} LIMIT 1'
_ANALYZE_ONE = 'ANALYZE "{table}"'

#: A partial index's condition, and the only names a statement here is built from.
_PARTIAL = re.compile(r"\bWHERE\b(.*)$", re.IGNORECASE | re.DOTALL)
_PLAIN_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


async def _statistics_kept(
    connection: aiosqlite.Connection,
) -> tuple[dict[str, int], dict[str, list[str]], set[str]]:
    """What sqlite_stat1 says: each table's counted rows, the probes for partial indexes counted
    empty, and every table analyzed at all."""
    told: dict[str, int] = {}
    hollow: dict[str, list[str]] = {}
    analyzed: set[str] = set()
    kept = await connection.execute_fetchall(_HAS_STATISTICS)
    for table, index, stat, ddl in (
        await connection.execute_fetchall(_STATISTICS_KEPT) if kept else ()
    ):
        analyzed.add(table)
        counted = str(stat).split(maxsplit=1)[0]
        if not _PLAIN_NAME.fullmatch(table) or not counted.isdigit():
            continue
        partial = _PARTIAL.search(ddl) if ddl else None
        if partial is None:
            told[table] = max(told.get(table, 0), int(counted))
        elif counted == "0" and _PLAIN_NAME.fullmatch(index):
            # Names checked plain above and a condition read from this library's own schema.
            # nosemgrep: sift-no-string-built-sql
            probe = _HOLDS_A_ROW.format(table=table, where=partial.group(1))
            hollow.setdefault(table, []).append(probe)
    return told, hollow, analyzed


async def stale_tables(connection: aiosqlite.Connection) -> list[str]:
    """The tables whose statistics no longer describe them: never analyzed and holding a row, rows
    moved `STALE_FACTOR` times over, or a partial index counted empty that now holds a row, which
    the planner takes as free to walk whatever it holds."""
    told, hollow, analyzed = await _statistics_kept(connection)
    stale = []
    for (table,) in await connection.execute_fetchall(_TABLES):
        if not _PLAIN_NAME.fullmatch(table) or table in analyzed:
            continue
        # nosemgrep: sift-no-string-built-sql (a plain name off sqlite_master)
        if await connection.execute_fetchall(_ANY_ROW.format(table=table)):
            stale.append(table)
    for table in sorted(told.keys() | hollow.keys()):
        # nosemgrep: sift-no-string-built-sql (a plain name off sqlite_stat1)
        rows = next(iter(await connection.execute_fetchall(_ROWS_NOW.format(table=table))))[0]
        was = told.get(table)
        if was is not None and max(was, rows, 1) >= STALE_FACTOR * max(min(was, rows), 1):
            stale.append(table)
            continue
        for probe in hollow.get(table, ()):
            if await connection.execute_fetchall(probe):
                stale.append(table)
                break
    stale += await _with_an_uncounted_index(connection, analyzed, set(stale))
    return sorted(stale)


async def _with_an_uncounted_index(
    connection: aiosqlite.Connection, analyzed: set[str], already: set[str]
) -> list[str]:
    """Tables holding rows with an index made after their last analyze: it has no row of its own,
    so the planner cannot weigh it and seeks through an older one (the claim: 13 s a call)."""
    if not analyzed:
        return []
    counted = {(row[0], row[1]) for row in await connection.execute_fetchall(_INDEXES_COUNTED)}
    found: list[str] = []
    for table, index in await connection.execute_fetchall(_INDEXES):
        if table not in analyzed or table in already or table in found:
            continue
        if (table, index) in counted or not _PLAIN_NAME.fullmatch(table):
            continue
        # An empty table's plain index is never counted, and misleads nothing.
        probe = _ANY_ROW.format(table=table)  # nosemgrep: sift-no-string-built-sql (a plain name)
        if await connection.execute_fetchall(probe):  # nosemgrep: sift-no-string-built-sql
            found.append(table)
    return found


async def keep_the_statistics_current(
    database: Database,
    stop: asyncio.Event,
    *,
    interval: float = STATISTICS_INTERVAL_SECONDS,
) -> None:
    """Refresh what the query planner believes about the tables, on a timer, until told to stop:
    the outer bound for a process left running for days."""
    while not stop.is_set():
        with suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)
        if stop.is_set():
            return
        try:
            await database.refresh_statistics(reason="daily")
        except Exception:
            # Housekeeping does not get to take the application down with it.
            log.exception("db.statistics_refresh_failed")
