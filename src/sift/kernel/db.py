# SPDX-License-Identifier: AGPL-3.0-or-later
"""SQLite: the connections, the single-writer guard, and the door to the whole database layer.

Everything Sift stores lives in one SQLite file in WAL mode. ONE WRITER: writes funnel through one
connection behind an async lock, while WAL readers in a pool never block. EVERY FEATURE OWNS ITS
TABLES: each registers an initializer (`db_schema`), run at boot in dependency order.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiosqlite

from sift.kernel.db_base import (
    DATABASE_FILENAME,
    IN_MARKER,
    PRAGMAS,
    Connection,
    DatabaseError,
    Initializer,
    IntegrityError,
    Params,
    Row,
)
from sift.kernel.db_judged import (
    _BUDGET,
    _ITER_CHUNK,
    _NAME_SAFE,
    _NAMES,
    _PLACEHOLDER_RUN,
    _STEP_CELLS,
    STEP_EVERY,
    _counting_cell,
    _judged,
    _JudgedWriter,
    _ran_on,
    _readable,
    _step_counter,
    statement_budget,
    statement_name,
)
from sift.kernel.db_library import (
    VERDICT_CURRENT,
    VERDICT_EMPTY,
    VERDICT_NEWER,
    VERDICT_OLDER,
    VERDICT_UNREADABLE,
    LibraryReport,
    LibraryVerdict,
    _read_only_uri,
    adopt_database,
    copy_database_aside,
    copy_library_aside,
    execute_blocking,
    fetch_blocking,
    inspect_database,
    inspect_library,
    library_database,
)
from sift.kernel.db_readers import (
    _BARE_AGGREGATE,
    _PLACEHOLDER,
    _POINT_READS,
    _PROBE_READS,
    _PROBE_STATEMENT,
    _PROBE_WARMUP_READS,
    _SCHEMA_COOKIE,
    _SQL_COMMENT,
    _TABLE_AFTER,
    _WRITES,
    LIBRARY_SIZED_TABLES,
    NAMES_KEPT,
    POINT_READ_LIMIT_SECONDS,
    SLOW_QUERY_FACTOR,
    SLOW_QUERY_FLOOR_MS,
    SLOW_QUERY_MS,
    STATEMENT_SETTLED_RUNS,
    STATEMENT_WINDOW,
    STATEMENTS_KEPT,
    PointRead,
    StatementBudget,
    StatementRun,
    _all_rows,
    _one_row,
    _parse_the_schema,
    _refuse_writes,
    is_whole_library_read,
    point_read,
    registered_point_reads,
    time_a_point_read,
)
from sift.kernel.db_schema import (
    _EXTENSIONS,
    _INVARIANTS,
    _REGISTRY,
    STEPS_LAST_SHIPPED_IN,
    ExtensionLoader,
    Invariant,
    SchemaComponent,
    _ordered_components,
    add_schema_dependency,
    register_connection_extension,
    register_schema_initializer,
    register_schema_invariant,
    registered_components,
    registered_invariants,
    too_old_to_bring_forward,
)
from sift.kernel.log import get_logger, timing_hook

log = get_logger(__name__)

#: Every name the database layer defines, so callers and gates read one door.
__all__ = [
    "CHECKPOINT_INTERVAL_SECONDS",
    "DATABASE_FILENAME",
    "DEFAULT_READERS",
    "IN_MARKER",
    "LIBRARY_SIZED_TABLES",
    "NAMES_KEPT",
    "POINT_READ_LIMIT_SECONDS",
    "PRAGMAS",
    "READER_HEADROOM",
    "RECLAIM_WORTH_SAYING_BYTES",
    "SLOW_QUERY_FACTOR",
    "SLOW_QUERY_FLOOR_MS",
    "SLOW_QUERY_MS",
    "STATEMENTS_KEPT",
    "STATEMENT_SETTLED_RUNS",
    "STATEMENT_WINDOW",
    "STATISTICS_INTERVAL_SECONDS",
    "STATISTICS_MIN_INTERVAL_SECONDS",
    "STEPS_LAST_SHIPPED_IN",
    "STEP_EVERY",
    "VERDICT_CURRENT",
    "VERDICT_EMPTY",
    "VERDICT_NEWER",
    "VERDICT_OLDER",
    "VERDICT_UNREADABLE",
    "_AFTER_COMMIT",
    "_ANALYZE_EVERY_TABLE",
    "_ANALYZE_WHAT_MOVED",
    "_BARE_AGGREGATE",
    "_BUDGET",
    "_EXTENSIONS",
    "_INVARIANTS",
    "_IN_SWEEP",
    "_IN_WRITE",
    "_NAMES",
    "_NAME_SAFE",
    "_PLACEHOLDER",
    "_PLACEHOLDER_RUN",
    "_POINT_READS",
    "_PROBE_READS",
    "_PROBE_STATEMENT",
    "_PROBE_WARMUP_READS",
    "_REGISTRY",
    "_SCHEMA_COOKIE",
    "_SQL_COMMENT",
    "_TABLE_AFTER",
    "_WRITES",
    "Connection",
    "Database",
    "DatabaseError",
    "ExtensionLoader",
    "Initializer",
    "IntegrityError",
    "Invariant",
    "LibraryReport",
    "LibraryVerdict",
    "Params",
    "PointRead",
    "Row",
    "SchemaComponent",
    "SqliteCapabilities",
    "StatementBudget",
    "StatementRun",
    "_all_rows",
    "_extension_loading_available",
    "_fts5_present",
    "_judged",
    "_log_bytes",
    "_one_row",
    "_ordered_components",
    "_parse_the_schema",
    "_read_only_uri",
    "_readable",
    "_refuse_writes",
    "add_schema_dependency",
    "adopt_database",
    "after_commit",
    "check_sqlite_capabilities",
    "copy_database_aside",
    "copy_library_aside",
    "execute_blocking",
    "fetch_blocking",
    "in_clause",
    "inspect_database",
    "inspect_library",
    "is_whole_library_read",
    "keep_the_log_folded",
    "keep_the_statistics_current",
    "library_database",
    "point_read",
    "probe_sqlite",
    "readers_for",
    "register_connection_extension",
    "register_schema_initializer",
    "register_schema_invariant",
    "registered_components",
    "registered_invariants",
    "registered_point_reads",
    "statement_budget",
    "statement_name",
    "time_a_point_read",
    "too_old_to_bring_forward",
]
# How many read connections to open when nobody says: the fallback for a tool or a test without a
# worker pool (the app sizes it with `readers_for`).
DEFAULT_READERS = 4

#: Spare read connections beyond the job workers, so an ordinary request never queues behind them.
READER_HEADROOM = 4


def readers_for(workers: int) -> int:
    """How many read connections a pool of `workers` job workers needs: more than the workers,
    which hold them, or every request from the browser queues behind background work."""
    return max(DEFAULT_READERS, workers + READER_HEADROOM)


#: How often the write-ahead log is offered a fold: rarely, since a landed one is a burst of copying.
CHECKPOINT_INTERVAL_SECONDS = 300.0


#: Below this much reclaimed, folding the log is not worth a line in anybody's log.
RECLAIM_WORTH_SAYING_BYTES = 8 * 1024 * 1024


#: How often a long-lived process refreshes the planner's statistics: daily, SQLite's own advice.
STATISTICS_INTERVAL_SECONDS = 24 * 60 * 60.0

#: The least time between two refreshes, whoever asked, so passes draining together pay once.
STATISTICS_MIN_INTERVAL_SECONDS = 60.0

#: Two forms: `PRAGMA optimize` re-analyzes what SQLite thinks would benefit and is nearly free once
#: current, so it is the timer's; `0x10002` considers every table at a real cost each time, so it is
#: run once, at boot. Full `ANALYZE` is not used.
_ANALYZE_WHAT_MOVED = "PRAGMA optimize"
_ANALYZE_EVERY_TABLE = "PRAGMA optimize(0x10002)"


def _log_bytes(database_path: Path) -> int:
    """How big the write-ahead log is right now. Zero when there is not one."""
    try:
        return (database_path.parent / (database_path.name + "-wal")).stat().st_size
    except OSError:
        return 0


async def keep_the_log_folded(
    database: Database,
    stop: asyncio.Event,
    *,
    interval: float = CHECKPOINT_INTERVAL_SECONDS,
) -> None:
    """Offer the write-ahead log a chance to fold back, on a timer, until told to stop. Offered,
    never forced: a busy install skips one and takes the next, and without this the moment the log
    needs never arrives under continuous background work."""
    while not stop.is_set():
        with suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)
        if stop.is_set():
            return
        try:
            # Sized either side, because the pragma cannot say how much it reclaimed.
            before = await asyncio.to_thread(_log_bytes, database.path)
            folded, remaining = await database.fold_the_log_back()
            after = await asyncio.to_thread(_log_bytes, database.path)
            if folded and before - after >= RECLAIM_WORTH_SAYING_BYTES:
                log.info(
                    "db.log_folded",
                    reclaimed_mb=round((before - after) / 1_000_000, 1),
                    log_mb=round(after / 1_000_000, 1),
                )
            elif not folded:
                # Not a failure; a quiet line so a log that never shrinks has an explanation.
                log.debug("db.log_busy", log_mb=round(after / 1_000_000, 1), pages=remaining)
        except Exception:
            # Housekeeping does not get to take the application down with it.
            log.exception("db.log_fold_failed")


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


@dataclass(frozen=True)
class SqliteCapabilities:
    """What the SQLite this process linked can actually do."""

    version: str
    fts5: bool
    load_extension: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "fts5": self.fts5,
            "load_extension": self.load_extension,
        }


def _fts5_present(connection: sqlite3.Connection) -> bool:
    """Whether FTS5 is there, asked by building one: a compile option no version number tells."""
    try:
        connection.execute("CREATE VIRTUAL TABLE probe USING fts5(x)")
    except sqlite3.Error:
        return False
    return True


def _extension_loading_available(connection: sqlite3.Connection) -> bool:
    """Whether extensions can be loaded at all (Python or SQLite may lack it); left off."""
    try:
        connection.enable_load_extension(True)
    except (AttributeError, sqlite3.Error):
        return False
    connection.enable_load_extension(False)
    return True


def probe_sqlite() -> SqliteCapabilities:
    """Ask the library what it can do, in memory, before the data directory is opened."""
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA trusted_schema=OFF")
    try:
        return SqliteCapabilities(
            version=sqlite3.sqlite_version,
            fts5=_fts5_present(connection),
            load_extension=_extension_loading_available(connection),
        )
    finally:
        connection.close()


def check_sqlite_capabilities(
    capabilities: SqliteCapabilities | None = None,
    *,
    announce: bool = True,
) -> SqliteCapabilities:
    """Refuse to run on a SQLite that cannot do what Sift needs, and record what it can.

    A capability rather than a version: what matters is how the machine's SQLite was built. Only
    FTS5 stops a boot, since search is made of it; extension loading is recorded. Once at boot.
    `announce` is off for callers before logging is set up, or console tools.
    """
    found = capabilities if capabilities is not None else probe_sqlite()

    if not found.fts5:
        raise DatabaseError(
            f"Sift needs a SQLite built with FTS5, and the one it found ({found.version}) does "
            "not have it.\n"
            "FTS5 is the full-text index every search runs against, so without it there is no "
            "search at all.\n"
            "It is a build option and not a version, so a newer SQLite is not necessarily a fix. "
            "The container image ships one that has it."
        )

    if not announce:
        return found

    log.info(
        "sqlite.capabilities",
        version=found.version,
        fts5=found.fts5,
        load_extension=found.load_extension,
    )
    if not found.load_extension:
        log.warning(
            "sqlite.no_extension_loading",
            detail=(
                "This SQLite cannot load extensions. Everything Sift does today works without "
                "them; features that are built on one will not."
            ),
        )
    return found


def in_clause(sql: str, values: Sequence[Any]) -> tuple[str, list[Any]]:
    """Expand the one query shape SQLite cannot parameterize: `IN (?, ?, ?)`.

        query, params = in_clause("SELECT * FROM tags WHERE id IN (?*)", tag_ids)

    The only characters this can add are `?` and `,`, which is what exempts it from the
    no-string-built-SQL rule. Interpolating a name here would be a different, dangerous function.
    """
    if sql.count(IN_MARKER) != 1:
        raise ValueError(f"the query needs exactly one {IN_MARKER} marker: {sql!r}")
    if not values:
        # `IN ()` is a syntax error, and "match nothing" is the caller's decision to make.
        raise ValueError("in_clause needs at least one value")

    placeholders = "(" + ",".join("?" * len(values)) + ")"
    return sql.replace(IN_MARKER, placeholders), list(values)


# Whether this TASK is already inside one of the two exclusive guards, where a second would
# deadlock: per task, since another task waiting its turn is right. Checked before the lock.
_IN_WRITE: ContextVar[bool] = ContextVar("sift_db_in_write", default=False)
_IN_SWEEP: ContextVar[bool] = ContextVar("sift_db_in_sweep", default=False)

# What to do once the write this task is inside has landed: per task and transaction, here because
# only this module knows a transaction committed, and dropped unrun when the write fails.
_AFTER_COMMIT: ContextVar[list[Callable[[], None]] | None] = ContextVar(
    "sift_db_after_commit", default=None
)


def after_commit(work: Callable[[], None]) -> None:
    """Do this once the write in progress has committed, and not at all if it does not: refused
    outside a write, run with the writer lock released, a failure logged since the change is stored."""
    pending = _AFTER_COMMIT.get()
    if pending is None:
        raise DatabaseError(
            "after_commit() was called outside a write(): there is no transaction for this to "
            "belong to, so it would run at a moment the caller did not choose. Open the write "
            "first, or do the work directly."
        )
    pending.append(work)


class Database:
    """The handle: one write connection behind a lock, plus a pool of readers. Kernel only: a
    direct read of assets walks past the access layer's permission checks."""

    def __init__(self, path: Path, *, readers: int = DEFAULT_READERS) -> None:
        if readers < 1:
            raise ValueError("a database needs at least one read connection")
        self.path = path
        self._readers = readers
        self._writer: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()
        self._read_pool: asyncio.Queue[aiosqlite.Connection] = asyncio.Queue()
        # Read connections open; up to `_readers`, those past the first few opened on first need.
        self._opened = 0
        # The sweep lane: one connection of its own, never drawn from the pool above. See `sweep`.
        self._sweeper: aiosqlite.Connection | None = None
        self._sweep_lock = asyncio.Lock()
        self._sweeping: str | None = None
        self._open_connections: list[aiosqlite.Connection] = []
        self._extensions: frozenset[str] = frozenset()
        # The inline lane for point reads on a fast enough machine; None sends every read to a thread.
        self._point: sqlite3.Connection | None = None
        self._point_thread: int | None = None
        self._point_read_seconds = float("inf")
        # The cookie the inline connection last parsed at, and its parse in flight (`_inline`).
        self._point_cookie: int | None = None
        self._point_parsing: asyncio.Task[None] | None = None
        # When the statistics were last refreshed, on the monotonic clock, since the wall clock moves.
        self._statistics_at = float("-inf")

    @classmethod
    def for_data_dir(cls, data_dir: Path, *, readers: int = DEFAULT_READERS) -> Database:
        return cls(data_dir / DATABASE_FILENAME, readers=readers)

    async def connect(self) -> None:
        """Open the write connection and the read pool. Idempotent within a process."""
        if self._writer is not None:
            return

        await asyncio.to_thread(self.path.parent.mkdir, parents=True, exist_ok=True)
        self._writer = await self._open(writer=True)
        self._sweeper = await self._open()
        for _ in range(min(self._readers, DEFAULT_READERS)):
            self._read_pool.put_nowait(await self._open())
            self._opened += 1
        await self._decide_where_point_reads_run()

        log.info(
            "db.open",
            path=str(self.path),
            readers=self._readers,
            point_reads="inline" if self._point is not None else "threaded",
            point_read_us=round(self._point_read_seconds * 1_000_000, 1),
        )

    async def _decide_where_point_reads_run(self) -> None:
        """Time this machine, and open the inline connection only if it earns one: timed on a
        thread, opened here on the loop's own thread that will use it. No half-on state."""
        self._point_read_seconds = await asyncio.to_thread(time_a_point_read, self.path)
        if self._point_read_seconds > POINT_READ_LIMIT_SECONDS:
            return
        # Not tied to this thread: its schema is parsed on a thread (`_parse_point_schema`).
        connection = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.row_factory = sqlite3.Row
        for pragma in PRAGMAS:
            connection.execute(pragma)
        # Sealed shut as well as refused by `_refuse_writes`, so no form of write can pass.
        connection.execute("PRAGMA query_only=ON")
        cell = _counting_cell(connection)
        if cell is not None:
            connection.set_progress_handler(_step_counter(cell), STEP_EVERY)
        self._point = connection
        self._point_thread = threading.get_ident()
        await self._parse_point_schema(connection)

    @property
    def point_reads_inline(self) -> bool:
        """Whether point reads run on the event loop on this machine. Shown on the health screen:
        an admin comparing two installs has to be able to see which of them is which."""
        return self._point is not None

    @property
    def point_read_seconds(self) -> float:
        """What one bounded read of this database measured at start-up, in seconds."""
        return self._point_read_seconds

    def _inline(self, statement: str | PointRead) -> sqlite3.Connection | None:
        """The connection this statement may run on right here, or None to hand it to a thread: a
        declared point read, a connection that exists, on its own thread, schema current."""
        if not isinstance(statement, PointRead):
            return None
        if self._point is None:
            return None
        if threading.get_ident() != self._point_thread:
            return None
        if self._point_parsing is not None:
            return None
        # A schema behind the file's would parse whole on the loop; the cookie read parses nothing,
        # so the read that finds it moved goes to a thread, and so does the parse.
        cookie = int(self._point.execute(_SCHEMA_COOKIE).fetchone()[0])
        if cookie != self._point_cookie:
            self._point_parsing = asyncio.get_running_loop().create_task(
                self._parse_point_schema(self._point)
            )
            return None
        return self._point

    async def _parse_point_schema(self, point: sqlite3.Connection) -> None:
        """Bring the inline connection's schema up to the file's, on a thread, while `_inline`
        sends every read elsewhere. The cookie is read first, so a schema moved meanwhile is
        parsed again. `close` waits for a parse in flight, so the connection outlives it."""
        try:
            self._point_cookie = await asyncio.to_thread(_parse_the_schema, point)
        except sqlite3.Error as error:
            log.warning("db.point_schema_unread", detail=str(error))
        finally:
            self._point_parsing = None

    @property
    def readers(self) -> int:
        """How many read connections the pool may hold; past the first few, opened on need."""
        return self._readers

    async def resize_readers(self, wanted: int) -> bool:
        """Make the read pool the right size for the worker count. True when it actually changed.

        The worker count is a setting raised while the process runs, and a pool no bigger than the
        workers leaves the browser nothing to borrow (`readers_for`). Growing raises the ceiling
        `read` opens up to when every open one is out; shrinking waits for a connection to be
        given back, so nothing in flight is closed.
        """
        if self._writer is None:
            raise DatabaseError("the database is not open: call connect() first")
        if wanted < 1:
            raise ValueError("a database needs at least one read connection")
        if wanted == self._readers:
            return False

        self._readers = wanted
        while self._opened > wanted:
            connection = await self._read_pool.get()
            self._opened -= 1
            self._open_connections.remove(connection)
            _STEP_CELLS.pop(id(connection), None)
            await connection.close()
        return True

    @property
    def extensions(self) -> frozenset[str]:
        """Which registered extensions actually loaded; a feature reports itself unavailable when
        its name is absent. Empty before the database is open."""
        return self._extensions

    async def _open(self, *, writer: bool = False) -> aiosqlite.Connection:
        if writer:
            path = str(self.path)
            connection: aiosqlite.Connection = await _JudgedWriter(
                lambda: sqlite3.connect(path), _ITER_CHUNK
            )
        else:
            connection = await aiosqlite.connect(self.path)
        cell = _counting_cell(connection)
        if cell is not None:
            await connection.set_progress_handler(_step_counter(cell), STEP_EVERY)
        connection.row_factory = aiosqlite.Row
        for pragma in PRAGMAS:
            await connection.execute(pragma)
        await connection.commit()
        await self._load_extensions(connection)
        self._open_connections.append(connection)
        return connection

    async def _load_extensions(self, connection: aiosqlite.Connection) -> None:
        """Load every registered extension into one connection, and never fail the boot over it.
        Loading is turned back off afterwards, always, so no later statement can load code."""
        if not _EXTENSIONS:
            return

        try:
            await connection.enable_load_extension(True)
        except (AttributeError, sqlite3.Error) as failure:
            # Either half can be missing: a Python compiled without the method, or a SQLite
            # compiled without the capability. One answer covers both, and it is not fatal.
            self._extensions = frozenset()
            log.warning("db.extensions_unavailable", detail=str(failure))
            return

        loaded: set[str] = set()
        try:
            for name, load in _EXTENSIONS.items():
                try:
                    await load(connection)
                except Exception as failure:
                    log.warning("db.extension_failed", extension=name, detail=str(failure))
                else:
                    loaded.add(name)
        finally:
            await connection.enable_load_extension(False)

        self._extensions = frozenset(loaded)

    async def refresh_statistics(
        self, *, reason: str, every_table: bool = False, force: bool = False
    ) -> bool:
        """Re-analyze the tables whose statistics have gone stale. True when it actually ran.

        SQLite chooses indexes from counts it writes only when asked, and stale counts make a
        statement pick the wrong side of a join for ever, silently. Runs at boot after the
        migrations, at the end of every whole-library pass, and daily; on the writer, since it
        writes. `every_table` is the boot form (`_ANALYZE_EVERY_TABLE`); `reason` says who asked.
        Debounced, except with `force`, which is a person pressing a button.
        """
        if self._writer is None:
            raise DatabaseError("the database is not open: call connect() first")
        now = time.monotonic()
        if not force and now - self._statistics_at < STATISTICS_MIN_INTERVAL_SECONDS:
            return False
        # Set before the work, so a second caller waiting for the writer is turned away.
        self._statistics_at = now
        statement = _ANALYZE_EVERY_TABLE if every_table else _ANALYZE_WHAT_MOVED
        started = time.perf_counter()
        async with self.write() as connection:
            cursor = await connection.execute(statement)
            await cursor.close()
        log.info(
            "db.statistics_refreshed",
            reason=reason,
            every_table=every_table,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
        )
        return True

    async def fold_the_log_back(self) -> tuple[bool, int]:
        """Fold the write-ahead log back into the database. Returns (did it, pages still there).

        The reset needs a moment with no reader in it, which a busy pool never has on its own, so
        the log would only grow. `TRUNCATE` gives up rather than waits, which makes it safe on a
        timer. Through the writer's guard, since run at the connection under another transaction
        it answers "table is locked" and silently does nothing.
        """
        async with self.write() as writer:
            cursor = await writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            try:
                row = await cursor.fetchone()
            finally:
                await cursor.close()
        if row is None:  # pragma: no cover (the pragma always answers)
            return False, 0
        # (busy, log pages, pages copied): on success TRUNCATE reports zero for both counts, so all
        # this can say is whether it got in.
        busy, remaining = int(row[0]), int(row[1])
        return busy == 0, remaining

    async def close(self) -> None:
        for connection in self._open_connections:
            _STEP_CELLS.pop(id(connection), None)
            await connection.close()
        self._open_connections.clear()
        if self._point_parsing is not None:
            await self._point_parsing
        if self._point is not None:
            _STEP_CELLS.pop(id(self._point), None)
            self._point.close()
            self._point = None
            self._point_thread = None
            self._point_cookie = None
        self._writer = None
        self._sweeper = None
        self._read_pool = asyncio.Queue()
        self._opened = 0
        log.info("db.close", path=str(self.path))

    def _require_writer(self) -> aiosqlite.Connection:
        if self._writer is None:
            raise DatabaseError("the database is not open: call connect() first")
        return self._writer

    @asynccontextmanager
    async def write(self) -> AsyncIterator[aiosqlite.Connection]:
        """The single-writer guard. Every write goes through here, and only one runs at a time.

        Commits on a clean exit and rolls back on an exception; keep the block short, as every
        writer waits on it. Not reentrant, and it raises rather than hang for ever on a lock this
        task holds. `after_commit` work runs once it closes cleanly, with the lock given up.
        """
        connection = self._require_writer()
        if _IN_WRITE.get():
            raise DatabaseError(
                "write() cannot be opened inside another write(): it would wait for a lock this "
                "task already holds and never return. Pass the connection you already have down to "
                "whatever needs it, so the whole write is one transaction."
            )
        pending: list[Callable[[], None]] = []
        async with self._write_lock:
            held = _IN_WRITE.set(True)
            carried = _AFTER_COMMIT.set(pending)
            try:
                yield connection
                await connection.commit()
            except BaseException:
                await connection.rollback()
                # Whatever was registered described a change that did not happen.
                pending.clear()
                raise
            finally:
                _IN_WRITE.reset(held)
                _AFTER_COMMIT.reset(carried)
        # Outside the lock, deliberately: the next writer is already free to start. Nothing here is
        # allowed to take the write back, so a failure is reported and the rest still run.
        for work in pending:
            try:
                work()
            except Exception:
                log.exception("db.after_commit_failed")

    @asynccontextmanager
    async def read(self) -> AsyncIterator[aiosqlite.Connection]:
        """Borrow a read connection. Not serialized: WAL readers run concurrently."""
        if self._writer is None:
            raise DatabaseError("the database is not open: call connect() first")
        if self._read_pool.empty() and self._opened < self._readers:
            # Counted before the await, so two borrowers at once cannot both pass the ceiling.
            self._opened += 1
            try:
                connection = await self._open()
            except BaseException:
                self._opened -= 1
                raise
        else:
            connection = await self._read_pool.get()
        try:
            yield connection
        finally:
            self._read_pool.put_nowait(connection)

    @asynccontextmanager
    async def sweep(self, what: str = "sweep") -> AsyncIterator[aiosqlite.Connection]:
        """The lane for whole-library reads. One at a time, on a connection of its own.

        The cost of a scan is rows crossing into Python, and it multiplies with every reader doing
        it at once, so the passes that read a whole table take turns: more background throughput
        AND far better interactive latency than no lane. Its own connection, so the browser's pool
        is never held. Nothing slow may happen inside the block. `what` names the pass for the
        health screen. The block always ends its transaction: in WAL a transaction left open would
        hand the next pass an older picture of the library.
        """
        if self._sweeper is None:
            raise DatabaseError("the database is not open: call connect() first")
        if _IN_SWEEP.get():
            raise DatabaseError(
                "sweep() cannot be opened inside another sweep(): it would wait for a lane this "
                "task already holds and never return. One lane, one pass: read what the pass needs "
                "in this block, or close it and open another."
            )
        async with self._sweep_lock:
            held = _IN_SWEEP.set(True)
            self._sweeping = what
            try:
                yield self._sweeper
                await self._sweeper.commit()
            except BaseException:
                await self._sweeper.rollback()
                raise
            finally:
                self._sweeping = None
                _IN_SWEEP.reset(held)

    @property
    def sweeping(self) -> str | None:
        """Which whole-library pass holds the lane right now, or None. Read by the health screen:
        "the wait is eight seconds" is another question, and "a duplicate scan holds the lane" is
        an answer."""
        return self._sweeping

    async def sweep_all(
        self, sql: str, params: Params = (), *, what: str = "sweep"
    ) -> list[aiosqlite.Row]:
        """One whole-library read through the lane, the single-statement form of `sweep`."""
        _refuse_writes(sql)
        with _judged("db.sweep", sql, sql, params) as timing:
            async with self.sweep(what) as connection:
                timing.acquired()
                _ran_on(timing, connection)
                cursor = await connection.execute(sql, params)
                try:
                    rows = list(await cursor.fetchall())
                finally:
                    await cursor.close()
                # How WIDE it was: a row count reads the same on an idle box and a loaded one.
                timing.measured(rows=len(rows))
                return rows

    async def execute(self, sql: str, params: Params = ()) -> None:
        """Run one write statement and commit it."""
        with _judged("db.write", sql, sql, params) as timing:
            async with self.write() as connection:
                # Everything before this was queueing behind whoever held the writer, and the wait
                # is not the statement. See `Timing.acquired`.
                timing.acquired()
                _ran_on(timing, connection)
                # The plain form: this statement is already judged, with its wait.
                await aiosqlite.Connection.execute(connection, sql, params)

    async def fetch_all(self, statement: str | PointRead, params: Params = ()) -> list[Row]:
        sql = statement.sql if isinstance(statement, PointRead) else statement
        _refuse_writes(sql)
        inline = self._inline(statement)
        with _judged("db.read", statement, sql, params) as timing:
            if inline is not None:
                _ran_on(timing, inline)
                # No `acquired()`: nothing was queued for, so the whole of this is the work and
                # splitting it into a wait and a run would report a wait that never happened.
                rows = _all_rows(inline, sql, params)
                timing.measured(rows=len(rows))
                return rows
            async with self.read() as connection:
                # Borrowing a connection can WAIT, and the wait is not the query.
                timing.acquired()
                _ran_on(timing, connection)
                cursor = await connection.execute(sql, params)
                try:
                    rows = list(await cursor.fetchall())
                finally:
                    await cursor.close()
                # How WIDE it was: a row count reads the same on an idle box and a loaded one.
                timing.measured(rows=len(rows))
                return rows

    async def fetch_one(self, statement: str | PointRead, params: Params = ()) -> Row | None:
        sql = statement.sql if isinstance(statement, PointRead) else statement
        _refuse_writes(sql)
        inline = self._inline(statement)
        with _judged("db.read", statement, sql, params) as timing:
            if inline is not None:
                _ran_on(timing, inline)
                return _one_row(inline, sql, params)
            async with self.read() as connection:
                timing.acquired()
                _ran_on(timing, connection)
                cursor = await connection.execute(sql, params)
                try:
                    return await cursor.fetchone()
                finally:
                    await cursor.close()

    # --- schema ---------------------------------------------------------------------------

    async def initialize_schema(self) -> None:
        """Bring every registered component up to its declared version, in dependency order.

        A second boot with nothing changed does nothing at all: each component's recorded version
        already matches, so its initializer is not called.
        """
        # Read once, on the writer, and kept as each step lands: a step's DDL makes the next reader
        # asked parse the whole schema again, and the writer already has it.
        async with self.write() as connection:
            await connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_version ("
                "  component TEXT PRIMARY KEY,"
                "  version   INTEGER NOT NULL"
                ")"
            )
            cursor = await connection.execute("SELECT component, version FROM schema_version")
            recorded = {
                str(row["component"]): int(row["version"]) for row in await cursor.fetchall()
            }
            await cursor.close()

        # Every component is asked before any changes, so a library is never left half forward.
        refused = too_old_to_bring_forward(recorded)
        if refused is not None:
            raise DatabaseError(refused)

        for component in _ordered_components():
            on_disk = recorded.get(component.name, 0)

            if on_disk == component.version:
                continue

            if on_disk > component.version:
                # Through a newer Sift: running an older initializer over it destroys data.
                raise DatabaseError(
                    f"this database was last used by a newer version of Sift "
                    f"({component.name} schema {on_disk}, this build understands "
                    f"{component.version}). Upgrade Sift, or restore an older backup."
                )

            with timing_hook("db.migrate", component=component.name, to_version=component.version):
                async with self.write() as connection:
                    await component.initialize(connection, on_disk)
                    await connection.execute(
                        "INSERT INTO schema_version (component, version) VALUES (?, ?) "
                        "ON CONFLICT(component) DO UPDATE SET version = excluded.version",
                        (component.name, component.version),
                    )
            recorded[component.name] = component.version

            log.info(
                "db.schema.applied",
                component=component.name,
                from_version=on_disk,
                to_version=component.version,
            )

        for name in sorted(_INVARIANTS):
            with timing_hook("db.invariant", component=name):
                async with self.write() as connection:
                    await _INVARIANTS[name](connection)

        # Last, since a migration that rebuilds a table invalidates what the planner knew of it.
        await self.refresh_statistics(reason="boot", every_table=True)
        # Parsed for the inline connection on a thread, before anything is served.
        if self._point is not None and self._point_parsing is None:
            await self._parse_point_schema(self._point)

    async def schema_version(self, component: str) -> int:
        """The version recorded on disk. 0 means the component has never been initialized."""
        row = await self.fetch_one(
            "SELECT version FROM schema_version WHERE component = ?", (component,)
        )
        return 0 if row is None else int(row["version"])
