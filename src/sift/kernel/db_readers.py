# SPDX-License-Identifier: AGPL-3.0-or-later
"""The read side: which statements are whole-library or point reads, their names and their budgets."""

from __future__ import annotations

import re
import sqlite3
import statistics
import sys
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from sift.kernel.db_base import PRAGMAS, DatabaseError, Params, Row

# The flat WARNING bar until a statement has a number of its own (`StatementBudget`).
SLOW_QUERY_MS = 100.0

#: Runs of one statement before its own usual cost judges it.
STATEMENT_SETTLED_RUNS = 20

#: How many recent runs of one statement are kept: recent, because a library grows.
STATEMENT_WINDOW = 64

#: How many times its own usual cost a run must be to warn (orders, not percentages).
SLOW_QUERY_FACTOR = 8.0

#: ...and never below this, where a statement starts being visible to somebody waiting.
SLOW_QUERY_FLOOR_MS = 50.0

#: How many distinct statements are remembered: a bound on a mistake, since the code fixes the set.
STATEMENTS_KEPT = 1024

#: How many worked-out names are kept, keyed by TEXT (`in_clause` writes one per list length).
NAMES_KEPT = 4096

#: Statements that must never go through a read connection, where they collide with the writer.
_WRITES = ("insert", "update", "delete", "replace", "create", "drop", "alter", "vacuum")


#: The tables that grow with the media (a row per file, folder or face): what "whole-library"
#: means to the lane and its gate.
LIBRARY_SIZED_TABLES = frozenset(
    {
        "assets",
        "asset_locations",
        "folders",
        "derivatives",
        "face_tracks",
        "face_detections",
        "face_scans",
        # One row per press on a face group, pressed as often as a library is scanned.
        "face_confirmations",
        "face_rejections",
        "face_rejected",
        "semantic_frames",
        # What files belong to: one row per file per tag, person, collection, username or set.
        "asset_tags",
        "asset_people",
        "collection_items",
        "asset_usernames",
        "photo_set_items",
        "song_files",
        # The stored verdict, a row per user per file, and the folder tree's ancestry.
        "viewer_assets",
        "folder_ancestry",
        # A press on a folder writes a row per file.
        "workbench_decision_subjects",
    }
)

#: A comment, in either spelling, stripped so a word in it cannot make a sweep look bounded.
_SQL_COMMENT = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)

#: What a table name looks like after FROM or JOIN, with an optional schema prefix.
_TABLE_AFTER = re.compile(
    r"\b(?:FROM|JOIN)\s+(?:[A-Za-z_][A-Za-z0-9_]*\.)?([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE
)

#: A bound value: positional or named. Its presence is what says a query was filtered to a subject.
_PLACEHOLDER = re.compile(r"\?|:[A-Za-z_][A-Za-z0-9_]*")

#: One row back whatever the table holds: an aggregate over everything, with nothing to group by.
_BARE_AGGREGATE = re.compile(r"\ASELECT\s+(?:COUNT|MAX|MIN|SUM|AVG|TOTAL)\s*\(", re.IGNORECASE)


def is_whole_library_read(sql: str) -> bool:
    """Whether this statement reads a library-sized table without filtering it. Narrow is any of:
    a bound value (a `WHERE` alone is not: `folder_id IS NOT NULL` filters nothing), a LIMIT, or a
    bare aggregate (one row crosses into Python). A `GROUP BY` is still a row per file."""
    text = _SQL_COMMENT.sub(" ", sql).strip()
    if not text.upper().startswith(("SELECT", "WITH")):
        return False
    if _PLACEHOLDER.search(text):
        return False
    if re.search(r"\bLIMIT\b", text, re.IGNORECASE):
        return False
    if _BARE_AGGREGATE.match(text) and not re.search(r"\bGROUP\s+BY\b", text, re.IGNORECASE):
        return False
    return any(table.lower() in LIBRARY_SIZED_TABLES for table in _TABLE_AFTER.findall(text))


# Point reads: a key lookup costs less than the thread handoff, but cost is not readable from the
# text, so the statements that may run inline are named one by one and a gate proves each plan.


@dataclass(frozen=True)
class PointRead:
    """A statement about ONE subject, which may run on the event loop; made only by `point_read`."""

    name: str
    sql: str


_POINT_READS: dict[str, PointRead] = {}


def point_read(name: str, sql: str) -> PointRead:
    """Declare a statement constant-time; `name` is what its plan gate reports if it stops seeking."""
    if name in _POINT_READS:
        raise ValueError(f"point read {name!r} is registered twice")
    text = _SQL_COMMENT.sub(" ", sql)
    if not _PLACEHOLDER.search(text) and any(
        table.lower() in LIBRARY_SIZED_TABLES for table in _TABLE_AFTER.findall(text)
    ):
        # Unbound over a table that grows with the library is a walk of it; a small table read
        # whole costs what a seek does. Knowable without a database.
        raise ValueError(
            f"point read {name!r} binds no value over a library-sized table: a statement that "
            "asks about one subject binds it, and one that binds nothing reads the whole table"
        )
    registered = PointRead(name, sql)
    _POINT_READS[name] = registered
    return registered


def registered_point_reads() -> dict[str, PointRead]:
    """Every statement declared constant-time, copied. The gate reads it; nothing mutates it."""
    return dict(_POINT_READS)


@dataclass(frozen=True)
class StatementRun:
    """One statement as a step-counting database saw it: what the statement ledger records."""

    stage: str
    name: str
    steps: int
    sql: str
    params: Params


class StatementBudget:
    """What counts as slow for one statement: a multiple of its own recent median over a floor,
    since a flat bar measures the machine. The flat bar stands until `settled` runs; no ceiling,
    since a consistently costly statement is ranked by `SlowestWork`. Per process."""

    def __init__(
        self,
        *,
        window: int = STATEMENT_WINDOW,
        settled: int = STATEMENT_SETTLED_RUNS,
        factor: float = SLOW_QUERY_FACTOR,
        floor_ms: float = SLOW_QUERY_FLOOR_MS,
        unsettled_ms: float = SLOW_QUERY_MS,
        keep: int = STATEMENTS_KEPT,
    ) -> None:
        self._window = window
        self._settled = settled
        self._factor = factor
        self._floor_ms = floor_ms
        self._unsettled_ms = unsettled_ms
        self._keep = keep
        self._runs: dict[str, deque[float]] = {}
        #: None in production. A list turns step counting on for every connection opened after it
        #: is set, and every statement run is appended to it (the statement ledger's gate).
        self.heard: list[StatementRun] | None = None

    def threshold_ms(self, name: str) -> float:
        """What this statement has to beat to be worth a warning, right now."""
        runs = self._runs.get(name)
        if runs is None or len(runs) < self._settled:
            return self._unsettled_ms
        return max(self._floor_ms, statistics.median(runs) * self._factor)

    def observed(self, name: str, ran_ms: float, run: StatementRun | None = None) -> None:
        """This statement ran and took this long: only the time it RAN, never the time it waited,
        or a queue would raise its bar and hide what this exists to catch. `run` carries its steps
        where they were counted."""
        if run is not None and self.heard is not None:
            self.heard.append(run)
        runs = self._runs.get(name)
        if runs is None:
            if len(self._runs) >= self._keep:
                return
            runs = self._runs[name] = deque(maxlen=self._window)
        runs.append(ran_ms)

    def watching(self) -> int:
        """How many statements have a number of their own. Read by tests."""
        return sum(1 for runs in self._runs.values() if len(runs) >= self._settled)

    def forget(self) -> None:
        """Start again. For tests, and for nothing else: a running process never wants this."""
        self._runs.clear()


#: Above this a point read is not cheap enough for the event loop, and reads keep their thread.
POINT_READ_LIMIT_SECONDS = 50e-6

#: What the probe times: it touches the file, and needs no table of Sift's own.
_PROBE_STATEMENT = "SELECT count(*) FROM sqlite_schema"

#: Reads before the probe counts, and reads it takes the median of, so one hiccup cannot decide.
_PROBE_WARMUP_READS = 20
_PROBE_READS = 100


def time_a_point_read(database_path: Path) -> float:
    """How long one bounded read of this database costs here, in seconds, on its own connection;
    anything that goes wrong reads as too slow, the answer that changes nothing."""
    try:
        connection = sqlite3.connect(database_path)
        connection.execute("PRAGMA trusted_schema=OFF")
    except sqlite3.Error:
        return float("inf")
    try:
        for pragma in PRAGMAS:
            connection.execute(pragma)
        for _ in range(_PROBE_WARMUP_READS):
            connection.execute(_PROBE_STATEMENT).fetchall()
        timings: list[float] = []
        for _ in range(_PROBE_READS):
            started = time.perf_counter()
            connection.execute(_PROBE_STATEMENT).fetchall()
            timings.append(time.perf_counter() - started)
    except sqlite3.Error:
        return float("inf")
    finally:
        connection.close()
    timings.sort()
    return timings[len(timings) // 2]


#: The interpreter's switch interval: under a millisecond, because on Windows a wait of one or more
#: is a whole timer tick, and a reader waits one for each row it hands back while another thread
#: runs Python (a 4,000-row read beside one busy thread: 2 ms alone, over 4 s at 1 ms, 13 ms here).
READER_SWITCH_SECONDS = 0.0005


def keep_readers_in_turn() -> None:
    """Shorten the interpreter's switch interval to `READER_SWITCH_SECONDS`, never lengthen it."""
    if sys.getswitchinterval() > READER_SWITCH_SECONDS:
        sys.setswitchinterval(READER_SWITCH_SECONDS)


#: The file's schema cookie. Read from the file's header, so it answers without the schema parsed.
_SCHEMA_COOKIE = "PRAGMA schema_version"


def _parse_the_schema(connection: sqlite3.Connection) -> int:
    """Parse the schema into this connection now, and hand back the cookie it was read at."""
    cookie = int(connection.execute(_SCHEMA_COOKIE).fetchone()[0])
    connection.execute(_PROBE_STATEMENT).fetchall()
    return cookie


def _one_row(connection: sqlite3.Connection, sql: str, params: Params) -> sqlite3.Row | None:
    """One row, or none, from a synchronous connection, the statement closed on the way out: an
    active statement holds its read transaction, which fixes what the connection can see."""
    cursor = connection.execute(sql, params)
    try:
        row: Row | None = cursor.fetchone()
        return row
    finally:
        cursor.close()


def _all_rows(connection: sqlite3.Connection, sql: str, params: Params) -> list[sqlite3.Row]:
    """Every row from a synchronous connection. Closed on the way out, for the reason above."""
    cursor = connection.execute(sql, params)
    try:
        return list(cursor.fetchall())
    finally:
        cursor.close()


def _refuse_writes(sql: str) -> None:
    head = sql.lstrip().lstrip("(").split(None, 1)
    if head and head[0].lower() in _WRITES:
        raise DatabaseError(
            f"{head[0].upper()} cannot go through a read connection. Use write() or execute(). "
            "A write on a borrowed reader collides with the writer and reports a locked database."
        )
