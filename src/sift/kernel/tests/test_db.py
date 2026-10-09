# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the database kernel, against a real SQLite file in WAL mode: a mock reproduces none of its bugs."""

from __future__ import annotations

import asyncio
import contextlib
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import aiosqlite
import pytest

from sift.kernel import db as db_module
from sift.kernel.db import (
    DEFAULT_READERS,
    PRAGMAS,
    Connection,
    Database,
    DatabaseError,
    Initializer,
    SqliteCapabilities,
    _refuse_writes,
    after_commit,
    check_sqlite_capabilities,
    in_clause,
    point_read,
    probe_sqlite,
    readers_for,
    register_connection_extension,
    register_schema_initializer,
    registered_point_reads,
)

pytestmark = pytest.mark.usefixtures("clean_registry")


@dataclass
class Recorded:
    """What the check logged, by event name and fields."""

    info: list[tuple[str, dict[str, object]]] = field(default_factory=list)
    warnings: list[tuple[str, dict[str, object]]] = field(default_factory=list)


@pytest.fixture
def said(monkeypatch: pytest.MonkeyPatch) -> Recorded:
    """The lines the check wrote: the logger is recorded rather than the output read, which writes
    structured lines straight to the stream, never `caplog`, and fails under a parallel run."""
    from sift.kernel import db_writer

    recorded = Recorded()
    for spoken in (db_module.log, db_writer.log):
        monkeypatch.setattr(
            spoken, "info", lambda event, **fields: recorded.info.append((event, fields))
        )
        monkeypatch.setattr(
            spoken, "warning", lambda event, **fields: recorded.warnings.append((event, fields))
        )
    return recorded


@pytest.mark.unit
def test_the_real_library_is_probed_not_assumed() -> None:
    """The probe asks the library itself, so it reports this machine's version and options."""
    found = probe_sqlite()

    assert found.version == sqlite3.sqlite_version
    assert found.fts5 is True  # the suite refuses to run without it


@pytest.mark.unit
def test_a_library_that_refuses_fts5_is_reported_as_not_having_it() -> None:
    """The probe has to be an answer from the library rather than a constant. A closed connection
    is the cheapest way to have a real one refuse a real statement."""
    closed = sqlite3.connect(":memory:")
    closed.close()

    assert db_module._fts5_present(closed) is False


@pytest.mark.unit
def test_a_library_that_refuses_the_extension_switch_is_reported_as_lacking_it() -> None:
    """One of the two ways it can be missing: the library refuses the call."""
    closed = sqlite3.connect(":memory:")
    closed.close()

    assert db_module._extension_loading_available(closed) is False


@pytest.mark.unit
def test_a_python_with_no_such_method_is_reported_as_lacking_it() -> None:
    """The other way, and it is a different exception: some Python builds have no such method at
    all, and that has to come back as an answer rather than as a crash during boot."""
    no_method = cast(sqlite3.Connection, object())

    assert db_module._extension_loading_available(no_method) is False


@pytest.mark.unit
def test_a_site_library_that_can_do_the_job_is_not_blocked() -> None:
    """A version floor would refuse a perfectly good library for being older than a number.
    Nothing about the version is allowed to stop a boot."""
    ancient = SqliteCapabilities(version="3.31.0", fts5=True, load_extension=True)

    assert check_sqlite_capabilities(ancient) is ancient


@pytest.mark.unit
def test_a_sqlite_without_fts5_refuses_to_boot() -> None:
    with pytest.raises(DatabaseError) as caught:
        check_sqlite_capabilities(
            SqliteCapabilities(version="3.46.1", fts5=False, load_extension=True)
        )

    message = str(caught.value)
    assert "3.46.1" in message
    assert "FTS5" in message
    # A missing feature and a traceback tell someone nothing they can act on. The message has to
    # say why it matters and what would fix it.
    assert "search" in message
    assert "build option" in message


@pytest.mark.unit
def test_a_sqlite_that_cannot_load_extensions_still_boots() -> None:
    """Nothing here loads an extension, so refusing to start over it would block an install that
    works. It is recorded rather than fatal."""
    without = SqliteCapabilities(version="3.46.1", fts5=True, load_extension=False)

    assert check_sqlite_capabilities(without) is without


@pytest.mark.unit
def test_the_missing_extension_support_is_said_out_loud(
    said: Recorded,
) -> None:
    """Recorded is only useful if it is findable later. The line is what a feature built on an
    extension has to point at when it turns out not to work here."""
    check_sqlite_capabilities(SqliteCapabilities(version="3.46.1", fts5=True, load_extension=False))

    assert "sqlite.no_extension_loading" in [event for event, _ in said.warnings]


@pytest.mark.unit
def test_a_working_library_is_not_warned_about(said: Recorded) -> None:
    """The warning has to mean something when it appears, which it stops doing if it appears on
    every boot."""
    check_sqlite_capabilities(SqliteCapabilities(version="3.46.1", fts5=True, load_extension=True))

    assert said.warnings == []


@pytest.mark.unit
def test_the_version_and_the_capabilities_are_logged(said: Recorded) -> None:
    """Which library a machine turned out to have is the first question asked of a database
    problem, and it is not answerable after the fact unless boot wrote it down."""
    check_sqlite_capabilities(SqliteCapabilities(version="3.46.1", fts5=True, load_extension=True))

    assert said.info == [
        ("sqlite.capabilities", {"version": "3.46.1", "fts5": True, "load_extension": True})
    ]


@pytest.mark.unit
def test_the_callers_that_run_before_logging_can_check_without_saying_anything(
    said: Recorded,
) -> None:
    """The command-line entry point checks before logging is configured, and the console
    password-reset tool is not a place for a line about the database. Both run the same check.
    A line written from either would come out in a different format, or in the middle of a
    prompt."""
    check_sqlite_capabilities(
        SqliteCapabilities(version="3.46.1", fts5=True, load_extension=False), announce=False
    )

    assert said.info == []
    assert said.warnings == []


@pytest.mark.unit
def test_the_pragma_set_is_exactly_what_the_schema_assumes() -> None:
    assert PRAGMAS == (
        "PRAGMA journal_mode=WAL",
        "PRAGMA foreign_keys=ON",
        "PRAGMA synchronous=NORMAL",
        "PRAGMA busy_timeout=5000",
        "PRAGMA trusted_schema=OFF",
        "PRAGMA cache_size=-8192",
    )


@pytest.mark.integration
async def test_pragmas_are_live_on_a_pooled_read_connection(temp_db: Database) -> None:
    """Not on the writer, where they are easy to get right: on a connection out of the pool.

    `foreign_keys` is per-connection. Set it on the writer only and every read connection quietly
    ignores every cascade in the schema, which looks like nothing at all until data goes missing.
    """
    async with temp_db.read() as connection:
        cursor = await connection.execute("PRAGMA foreign_keys")
        row = await cursor.fetchone()
        assert row is not None and row[0] == 1

        cursor = await connection.execute("PRAGMA journal_mode")
        row = await cursor.fetchone()
        assert row is not None and row[0].lower() == "wal"

        cursor = await connection.execute("PRAGMA busy_timeout")
        row = await cursor.fetchone()
        assert row is not None and row[0] == 5000


@pytest.mark.integration
async def test_a_bad_foreign_key_is_rejected(temp_db: Database) -> None:
    """Proves the pragma above is doing something, rather than merely reporting itself as on."""
    async with temp_db.write() as connection:
        await connection.execute("CREATE TABLE parent (id TEXT PRIMARY KEY)")
        await connection.execute(
            "CREATE TABLE child ("
            "  id        TEXT PRIMARY KEY,"
            "  parent_id TEXT NOT NULL REFERENCES parent(id) ON DELETE CASCADE"
            ")"
        )

    with pytest.raises(sqlite3.IntegrityError):
        await temp_db.execute(
            "INSERT INTO child (id, parent_id) VALUES (?, ?)", ("c1", "no-such-parent")
        )

    # And the cascade it exists to enable actually cascades.
    await temp_db.execute("INSERT INTO parent (id) VALUES (?)", ("p1",))
    await temp_db.execute("INSERT INTO child (id, parent_id) VALUES (?, ?)", ("c1", "p1"))
    await temp_db.execute("DELETE FROM parent WHERE id = ?", ("p1",))

    assert await temp_db.fetch_all("SELECT id FROM child") == []


@pytest.mark.integration
async def test_a_write_storm_never_hits_a_locked_database(temp_db: Database) -> None:
    """Eight workers, two hundred writes each, no `database is locked`.

    Necessary but not sufficient, and worth being honest about: this passes even with the lock
    taken out, because a single statement on one connection is already serialized by the driver's
    own thread queue. What the lock is actually for is the two tests below.
    """
    await temp_db.execute("CREATE TABLE counter (id TEXT PRIMARY KEY, worker INTEGER)")

    async def writer(worker: int) -> None:
        for n in range(200):
            await temp_db.execute(
                "INSERT INTO counter (id, worker) VALUES (?, ?)", (f"{worker}-{n}", worker)
            )

    await asyncio.gather(*(writer(w) for w in range(8)))

    rows = await temp_db.fetch_all("SELECT COUNT(*) AS n FROM counter")
    assert rows[0]["n"] == 8 * 200


@pytest.mark.integration
async def test_write_transactions_never_interleave(temp_db: Database) -> None:
    """The property the lock actually provides.

    Every transaction runs on the same connection, so if two are open together their statements
    land in the same SQLite transaction: one's commit publishes the other's half-finished work,
    and one's rollback throws it away. Whoever holds the writer holds it alone, start to finish.
    """
    await temp_db.execute("CREATE TABLE t (id TEXT PRIMARY KEY)")
    events: list[str] = []

    async def transaction(name: str) -> None:
        async with temp_db.write() as connection:
            events.append(f"enter {name}")
            await connection.execute("INSERT INTO t (id) VALUES (?)", (f"{name}-1",))
            await asyncio.sleep(0)  # hand the event loop a chance to interleave, if it can
            await connection.execute("INSERT INTO t (id) VALUES (?)", (f"{name}-2",))
            events.append(f"exit {name}")

    await asyncio.gather(*(transaction(f"t{i}") for i in range(6)))

    for i in range(0, len(events), 2):
        opened, closed = events[i], events[i + 1]
        assert opened.startswith("enter "), f"transactions overlapped: {events}"
        assert closed == opened.replace("enter ", "exit "), f"transactions overlapped: {events}"


@pytest.mark.integration
async def test_one_transaction_rolling_back_cannot_discard_another(temp_db: Database) -> None:
    """The consequence of the above, and the reason it is not a stylistic preference.

    Without the lock these two share a transaction: the failing one's rollback destroys the
    committed work of the one that succeeded, and nothing anywhere reports an error.
    """
    await temp_db.execute("CREATE TABLE t (id TEXT PRIMARY KEY)")

    async def succeeds() -> None:
        async with temp_db.write() as connection:
            await connection.execute("INSERT INTO t (id) VALUES (?)", ("keep",))
            await asyncio.sleep(0.01)

    async def fails() -> None:
        with pytest.raises(RuntimeError, match="boom"):
            async with temp_db.write() as connection:
                await connection.execute("INSERT INTO t (id) VALUES (?)", ("discard",))
                raise RuntimeError("boom")

    await asyncio.gather(succeeds(), fails())

    rows = await temp_db.fetch_all("SELECT id FROM t")
    assert [row["id"] for row in rows] == ["keep"]


@pytest.mark.integration
async def test_a_failed_write_leaves_nothing_behind(temp_db: Database) -> None:
    """A transaction that raises halfway must roll the first half back, or a crash mid-migration
    leaves a schema that is neither the old one nor the new one."""
    await temp_db.execute("CREATE TABLE t (id TEXT PRIMARY KEY)")

    with pytest.raises(sqlite3.IntegrityError):
        async with temp_db.write() as connection:
            await connection.execute("INSERT INTO t (id) VALUES (?)", ("first",))
            await connection.execute("INSERT INTO t (id) VALUES (?)", ("first",))  # duplicate PK

    assert await temp_db.fetch_all("SELECT id FROM t") == []


@pytest.mark.integration
async def test_reads_are_not_serialized_behind_each_other(temp_db: Database) -> None:
    """Readers come from a pool and run concurrently. If they were serialized, the second could
    not start until the first let go, and this would deadlock rather than pass."""
    await temp_db.execute("CREATE TABLE t (id TEXT PRIMARY KEY)")

    async with temp_db.read() as first, temp_db.read() as second:
        assert first is not second


def test_the_read_pool_is_bigger_than_the_worker_pool() -> None:
    """The pool hands out a fixed number of connections and borrowing waits when none is free, so
    a pool no bigger than the job workers means every browser request queues behind background
    work: with fewer connections than workers, a single-row lookup waits behind an import."""
    for workers in (1, 2, 4, 8, 16):
        assert readers_for(workers) > workers, f"{workers} workers can starve the web server"


def test_a_tiny_worker_pool_still_gets_a_usable_number_of_readers() -> None:
    """One worker on a two-core box must not mean two connections: a single page of the grid
    issues several reads, and they would then queue behind each other."""
    assert readers_for(1) >= DEFAULT_READERS


@pytest.mark.integration
async def test_a_read_is_not_blocked_by_the_job_workers_holding_connections(
    tmp_path: Path,
) -> None:
    """The fault itself, in miniature: hold as many connections as there are workers and check that
    one more read still goes through. With a pool sized to the workers this blocks forever."""
    workers = 8
    database = Database(tmp_path / "busy.sqlite3", readers=readers_for(workers))
    await database.connect()
    try:
        await database.execute("CREATE TABLE t (id TEXT PRIMARY KEY)")
        async with contextlib.AsyncExitStack() as busy:
            for _ in range(workers):
                await busy.enter_async_context(database.read())
            # The web server's read, with every worker holding one. A second is what makes the
            # point: one spare connection would pass this and still serialize the whole interface.
            async with asyncio.timeout(5):
                async with database.read() as spare:
                    assert spare is not None
                async with database.read() as another:
                    assert another is not None
    finally:
        await database.close()


@pytest.mark.integration
async def test_using_a_closed_database_says_so(tmp_path: Path) -> None:
    database = Database(tmp_path / "unopened.sqlite3")
    with pytest.raises(DatabaseError, match="not open"):
        async with database.write():
            pass


def _initializer(order: list[str], name: str) -> Initializer:
    """A stand-in for a feature's schema: record that it ran, create a table.

    The DDL is a literal rather than a name interpolated into a CREATE TABLE. Building it with an
    f-string would trip the no-string-built-SQL rule, and rightly: a test that has to be exempted
    from the project's own rule is a test written the way the project forbids.
    """
    ddl = _TEST_TABLES[name]

    async def initialize(connection: aiosqlite.Connection, from_version: int) -> None:
        order.append(name)
        await connection.execute(ddl)

    return initialize


_TEST_TABLES = {
    "parent": "CREATE TABLE IF NOT EXISTS parent (id TEXT PRIMARY KEY)",
    "child": "CREATE TABLE IF NOT EXISTS child (id TEXT PRIMARY KEY)",
    "a": "CREATE TABLE IF NOT EXISTS a (id TEXT PRIMARY KEY)",
    "b": "CREATE TABLE IF NOT EXISTS b (id TEXT PRIMARY KEY)",
    "dup": "CREATE TABLE IF NOT EXISTS dup (id TEXT PRIMARY KEY)",
    "thing": "CREATE TABLE IF NOT EXISTS thing (id TEXT PRIMARY KEY)",
    "recorded": "CREATE TABLE IF NOT EXISTS recorded (id TEXT PRIMARY KEY)",
}


@pytest.mark.integration
async def test_initializers_run_in_dependency_order(temp_db: Database) -> None:
    order: list[str] = []
    # Registered in the wrong order on purpose: import order must not be what decides this.
    register_schema_initializer("child", 1, _initializer(order, "child"), depends_on=["parent"])
    register_schema_initializer("parent", 1, _initializer(order, "parent"))

    await temp_db.initialize_schema()

    assert order == ["parent", "child"]


@pytest.mark.unit
async def test_a_dependency_cycle_fails_at_boot(temp_db: Database) -> None:
    order: list[str] = []
    register_schema_initializer("a", 1, _initializer(order, "a"), depends_on=["b"])
    register_schema_initializer("b", 1, _initializer(order, "b"), depends_on=["a"])

    with pytest.raises(DatabaseError, match="cycle"):
        await temp_db.initialize_schema()


@pytest.mark.unit
async def test_depending_on_something_unregistered_fails_at_boot(temp_db: Database) -> None:
    order: list[str] = []
    register_schema_initializer("a", 1, _initializer(order, "a"), depends_on=["ghost"])

    with pytest.raises(DatabaseError, match="not registered"):
        await temp_db.initialize_schema()


@pytest.mark.unit
def test_a_component_cannot_be_registered_twice() -> None:
    order: list[str] = []
    register_schema_initializer("dup", 1, _initializer(order, "dup"))
    with pytest.raises(ValueError, match="twice"):
        register_schema_initializer("dup", 1, _initializer(order, "dup"))


@pytest.mark.unit
@pytest.mark.parametrize("baseline", [0, 3])
def test_a_baseline_outside_the_versions_is_refused(baseline: int) -> None:
    """The baseline is the first version a library may be at and still be opened: below one there
    is no such version, and above the pin there is no step that reaches it."""
    order: list[str] = []
    with pytest.raises(ValueError, match="between 1 and its version"):
        register_schema_initializer("floored", 2, _initializer(order, "thing"), baseline=baseline)


@pytest.mark.integration
async def test_a_second_boot_changes_nothing(temp_db: Database) -> None:
    order: list[str] = []
    register_schema_initializer("thing", 1, _initializer(order, "thing"))

    await temp_db.initialize_schema()
    await temp_db.initialize_schema()

    assert order == ["thing"], "the initializer ran again on a database that was already current"
    assert await temp_db.schema_version("thing") == 1


@pytest.mark.integration
async def test_a_migration_applies_once_and_only_once(tmp_path: Path) -> None:
    """The whole point of recording a version: an ALTER TABLE that runs twice is an error, and a
    migration that never runs is a missing column."""
    path = tmp_path / "migrate.sqlite3"
    applied: list[int] = []

    async def v1(connection: aiosqlite.Connection, from_version: int) -> None:
        applied.append(from_version)
        await connection.execute("CREATE TABLE IF NOT EXISTS notes (id TEXT PRIMARY KEY)")

    async def v2(connection: aiosqlite.Connection, from_version: int) -> None:
        applied.append(from_version)
        if from_version < 1:
            await connection.execute("CREATE TABLE IF NOT EXISTS notes (id TEXT PRIMARY KEY)")
        if from_version < 2:
            await connection.execute("ALTER TABLE notes ADD COLUMN body TEXT")

    # First boot, at version 1.
    first = Database(path)
    await first.connect()
    register_schema_initializer("notes", 1, v1)
    await first.initialize_schema()
    await first.execute("INSERT INTO notes (id) VALUES (?)", ("n1",))
    await first.close()

    # A new build of Sift ships version 2 of the same tables.
    import sift.kernel.db as db

    db._REGISTRY.clear()

    db._INVARIANTS.clear()
    register_schema_initializer("notes", 2, v2)

    second = Database(path)
    await second.connect()
    await second.initialize_schema()
    assert await second.schema_version("notes") == 2
    assert applied == [0, 1], "the migration did not see the version it was upgrading from"

    # The column landed, and the row that was already there survived.
    await second.execute("UPDATE notes SET body = ? WHERE id = ?", ("hello", "n1"))
    row = await second.fetch_one("SELECT id, body FROM notes WHERE id = ?", ("n1",))
    assert row is not None and row["body"] == "hello"

    # Booting the same build again re-runs nothing.
    third = Database(path)
    await third.connect()
    await third.initialize_schema()
    assert applied == [0, 1]
    await third.close()
    await second.close()


@pytest.mark.unit
def test_a_dependency_may_be_declared_after_both_are_registered() -> None:
    """For a component that keeps something over another component's table: it says so once both
    exist, and the order is resolved when a database is opened rather than at import."""
    order: list[str] = []
    register_schema_initializer("parent", 1, _initializer(order, "parent"))
    register_schema_initializer("child", 1, _initializer(order, "child"))

    db_module.add_schema_dependency("child", "parent")

    assert db_module.registered_components()["child"].depends_on == ("parent",)


@pytest.mark.unit
def test_the_same_dependency_declared_twice_is_recorded_once() -> None:
    order: list[str] = []
    register_schema_initializer("parent", 1, _initializer(order, "parent"))
    register_schema_initializer("child", 1, _initializer(order, "child"), depends_on=["parent"])

    db_module.add_schema_dependency("child", "parent")

    assert db_module.registered_components()["child"].depends_on == ("parent",)


@pytest.mark.unit
def test_a_dependency_for_a_component_nobody_registered_is_refused() -> None:
    with pytest.raises(ValueError, match="not registered"):
        db_module.add_schema_dependency("ghost", "parent")


@pytest.mark.unit
def test_an_invariant_cannot_be_registered_twice() -> None:
    """Two under one name means whichever imported last runs on every boot, silently."""

    async def hold(_connection: Connection) -> None:
        return None

    db_module.register_schema_invariant("one", hold)

    with pytest.raises(ValueError, match="registered twice"):
        db_module.register_schema_invariant("one", hold)

    assert db_module.registered_invariants()["one"] is hold


@pytest.mark.unit
def test_the_invariants_cannot_be_edited_through_the_reader() -> None:
    async def hold(_connection: Connection) -> None:
        return None

    db_module.register_schema_invariant("one", hold)
    db_module.registered_invariants().clear()

    assert db_module.registered_invariants()["one"] is hold


async def _boot(path: Path, name: str, version: int, initialize: Initializer) -> Database:
    """A fresh handle onto `path` carrying one registered component, booted. The caller closes it.

    The registry is process-global and every component in it is applied at boot, so a test that
    stands up a second build of the same component has to clear it first: otherwise the first
    build's version is still registered and the second registration is refused as a duplicate.
    """
    import sift.kernel.db as db

    db._REGISTRY.clear()

    db._INVARIANTS.clear()
    register_schema_initializer(name, version, initialize)
    database = Database(path)
    await database.connect()
    await database.initialize_schema()
    return database


@pytest.mark.integration
async def test_a_database_two_versions_behind_gets_every_step_it_missed(tmp_path: Path) -> None:
    """The guarantee the whole chained-`if` rule exists for, on a database that really is old.

    Somebody who skips a release upgrades across two steps in one go, and both have to land in the
    one boot. They cannot be retried afterwards: this records the component as fully up to date the
    moment the initializer returns, whatever it actually did, so a step passed over here is passed
    over for the life of that database. The failure is silent: the boot succeeds, the version
    reads as current, and the missing column surfaces much later as a query error with nothing to
    connect it back to the upgrade.

    So the assertion is on the columns, not on the version number. The version number is what lies.
    """
    path = tmp_path / "two-behind.sqlite3"

    async def v1(connection: aiosqlite.Connection, from_version: int) -> None:
        await connection.execute("CREATE TABLE IF NOT EXISTS notes (id TEXT PRIMARY KEY)")

    async def v3(connection: aiosqlite.Connection, from_version: int) -> None:
        if from_version < 1:
            await connection.execute("CREATE TABLE IF NOT EXISTS notes (id TEXT PRIMARY KEY)")
        if 0 < from_version < 2:
            await connection.execute("ALTER TABLE notes ADD COLUMN body TEXT")
        if 0 < from_version < 3:
            await connection.execute("ALTER TABLE notes ADD COLUMN colour TEXT")

    first = await _boot(path, "notes", 1, v1)
    await first.execute("INSERT INTO notes (id) VALUES (?)", ("n1",))
    await first.close()

    # A build two releases later, meeting the database that skipped the one in between.
    upgraded = await _boot(path, "notes", 3, v3)
    try:
        assert await upgraded.schema_version("notes") == 3
        columns = {
            row["name"]
            for row in await upgraded.fetch_all("SELECT name FROM pragma_table_info('notes')")
        }
        assert {"body", "colour"} <= columns, (
            "a step was skipped, and the version was stamped as current anyway, which is exactly "
            f"the failure that leaves no trace. The table has: {sorted(columns)}"
        )
        # What was already there came through untouched.
        row = await upgraded.fetch_one("SELECT id FROM notes WHERE id = ?", ("n1",))
        assert row is not None
    finally:
        await upgraded.close()


@pytest.mark.regression
async def test_chaining_the_steps_is_what_loses_one(tmp_path: Path) -> None:
    """The same upgrade written as a chain, to show the test above can tell the two apart.

    This is not a guarantee Sift offers: it is the bug, pinned down, so that the assertion above
    is known to be measuring something rather than passing by construction. Nothing raises here:
    the boot is a success and the version reads as current. That is the whole problem.
    """
    path = tmp_path / "chained.sqlite3"

    async def v1(connection: aiosqlite.Connection, from_version: int) -> None:
        await connection.execute("CREATE TABLE IF NOT EXISTS notes (id TEXT PRIMARY KEY)")

    async def chained_v3(connection: aiosqlite.Connection, from_version: int) -> None:
        if from_version < 1:
            await connection.execute("CREATE TABLE IF NOT EXISTS notes (id TEXT PRIMARY KEY)")
        elif from_version < 2:
            await connection.execute("ALTER TABLE notes ADD COLUMN body TEXT")
        elif from_version < 3:
            await connection.execute("ALTER TABLE notes ADD COLUMN colour TEXT")

    first = await _boot(path, "notes", 1, v1)
    await first.close()

    upgraded = await _boot(path, "notes", 3, chained_v3)
    try:
        assert await upgraded.schema_version("notes") == 3, "the version is stamped either way"
        columns = {
            row["name"]
            for row in await upgraded.fetch_all("SELECT name FROM pragma_table_info('notes')")
        }
        assert "body" in columns, "the first step it reached did apply"
        assert "colour" not in columns, (
            "the chained form applied both steps: if that is now true, the assertion in the test "
            "above is no longer measuring anything and this pair should be reconsidered"
        )
    finally:
        await upgraded.close()


@pytest.mark.integration
async def test_a_database_from_a_newer_sift_is_refused(tmp_path: Path) -> None:
    """Otherwise an older build runs an older initializer over a newer schema, which is how an
    accidental downgrade turns into data loss."""
    path = tmp_path / "newer.sqlite3"

    async def initialize(connection: aiosqlite.Connection, from_version: int) -> None:
        await connection.execute("CREATE TABLE IF NOT EXISTS t (id TEXT PRIMARY KEY)")

    import sift.kernel.db as db

    database = Database(path)
    await database.connect()
    register_schema_initializer("t", 5, initialize)
    await database.initialize_schema()
    await database.close()

    db._REGISTRY.clear()

    db._INVARIANTS.clear()
    register_schema_initializer("t", 2, initialize)  # an older build

    older = Database(path)
    await older.connect()
    with pytest.raises(DatabaseError, match="newer version"):
        await older.initialize_schema()
    await older.close()


@pytest.mark.integration
async def test_a_fresh_boot_records_what_it_applied(temp_db: Database) -> None:
    order: list[str] = []
    register_schema_initializer("recorded", 3, _initializer(order, "recorded"))

    await temp_db.initialize_schema()

    row = await temp_db.fetch_one(
        "SELECT version FROM schema_version WHERE component = ?", ("recorded",)
    )
    assert row is not None and row["version"] == 3
    assert await temp_db.schema_version("never-registered") == 0


@pytest.mark.regression
@pytest.mark.parametrize(
    "payload",
    [
        "'; DROP TABLE assets;--",
        '" OR 1=1 --',
        "1; DELETE FROM assets",
        "' UNION SELECT password_hash FROM users --",
        "\\'; DROP TABLE assets;--",
    ],
)
async def test_an_injection_payload_is_stored_as_text_and_executes_nothing(
    temp_db: Database, payload: str
) -> None:
    """There is no ORM here. Binding is the entire defence, so it gets tested as one.

    Every payload goes in as a *value* (a title someone typed, a filename off a website) and
    has to come back out byte for byte, with the table it tries to drop still standing.
    """
    await temp_db.execute("CREATE TABLE assets (id TEXT PRIMARY KEY, title TEXT)")
    await temp_db.execute("INSERT INTO assets (id, title) VALUES (?, ?)", ("a1", payload))

    row = await temp_db.fetch_one("SELECT title FROM assets WHERE id = ?", ("a1",))
    assert row is not None
    assert row["title"] == payload, "the value was mangled: it went somewhere other than a bind"

    # The table the payload tried to drop is still there, with its row in it.
    rows = await temp_db.fetch_all("SELECT id FROM assets")
    assert [r["id"] for r in rows] == ["a1"]

    # And it is inert as a search term, too.
    found = await temp_db.fetch_all("SELECT id FROM assets WHERE title = ?", (payload,))
    assert [r["id"] for r in found] == ["a1"]


@pytest.mark.regression
async def test_an_injection_payload_is_inert_in_full_text_search(temp_db: Database) -> None:
    """The MATCH path has its own parser, so it gets its own test. A payload here is a search
    query, not SQL: worst case it is nonsense syntax, never a statement."""
    async with temp_db.write() as connection:
        await connection.execute("CREATE VIRTUAL TABLE assets_fts USING fts5(title)")
        await connection.execute("INSERT INTO assets_fts (title) VALUES (?)", ("a holiday video",))

    payload = '"; DROP TABLE assets_fts;--'
    # Malformed *search syntax* is fine and expected here. An executed statement is not.
    with contextlib.suppress(sqlite3.OperationalError):
        await temp_db.fetch_all("SELECT title FROM assets_fts WHERE assets_fts MATCH ?", (payload,))

    rows = await temp_db.fetch_all("SELECT title FROM assets_fts")
    assert [r["title"] for r in rows] == ["a holiday video"]


@pytest.mark.unit
def test_in_clause_only_ever_adds_placeholders() -> None:
    query, params = in_clause("SELECT * FROM assets WHERE id IN (?*)", ["a", "b", "c"])
    assert query == "SELECT * FROM assets WHERE id IN (?,?,?)"
    assert params == ["a", "b", "c"]


@pytest.mark.unit
def test_in_clause_cannot_be_used_to_smuggle_sql() -> None:
    """The values never touch the query text, whatever they contain."""
    query, params = in_clause(
        "SELECT * FROM assets WHERE id IN (?*)", ["'; DROP TABLE assets;--", "x"]
    )
    assert query == "SELECT * FROM assets WHERE id IN (?,?)"
    assert "DROP" not in query
    assert params == ["'; DROP TABLE assets;--", "x"]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("sql", "values", "reason"),
    [
        ("SELECT * FROM assets", ["a"], "no marker"),
        ("SELECT * FROM a WHERE x IN (?*) OR y IN (?*)", ["a"], "two markers"),
        ("SELECT * FROM assets WHERE id IN (?*)", [], "no values"),
    ],
)
def test_in_clause_refuses_what_it_cannot_do_safely(
    sql: str, values: list[str], reason: str
) -> None:
    with pytest.raises(ValueError):
        in_clause(sql, values)


@pytest.mark.integration
async def test_in_clause_round_trips_through_a_real_query(temp_db: Database) -> None:
    await temp_db.execute("CREATE TABLE assets (id TEXT PRIMARY KEY)")
    for asset_id in ("a", "b", "c"):
        await temp_db.execute("INSERT INTO assets (id) VALUES (?)", (asset_id,))

    query, params = in_clause("SELECT id FROM assets WHERE id IN (?*) ORDER BY id", ["a", "c"])
    rows = await temp_db.fetch_all(query, params)

    assert [row["id"] for row in rows] == ["a", "c"]


@pytest.mark.unit
@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM face_references WHERE id = ?",
        "delete from t returning id",
        "INSERT INTO t (id) VALUES (?)",
        "UPDATE t SET x = 1",
        "  DROP TABLE t",
    ],
)
def test_a_write_cannot_go_through_a_read_connection(sql: str) -> None:
    """The read pool cannot protect anyone from a write that went through the reading door.

    A write on a borrowed reader collides with whoever holds the writer, and SQLite answers
    "database is locked", from inside a request, on the connection whose whole purpose is that it
    never blocks. A DELETE ... RETURNING is the easy way in, because it reads like a query: it
    returns rows.
    """
    with pytest.raises(DatabaseError, match="read connection"):
        _refuse_writes(sql)


@pytest.mark.unit
@pytest.mark.parametrize(
    "sql", ["SELECT 1", "  select * from t", "WITH x AS (SELECT 1) SELECT * FROM x"]
)
def test_ordinary_reads_are_not_refused(sql: str) -> None:
    _refuse_writes(sql)


async def test_the_lane_lets_one_pass_through_at_a_time(tmp_path: Path) -> None:
    """The whole promise, observed rather than inferred.

    Each pass records the number of passes inside the lane while it is inside it. Ungated, several
    of those readings are above one; that is the failure, and it is one that makes the
    application unusable.
    """
    database = Database(tmp_path / "lane.sqlite3", readers=4)
    await database.connect()
    try:
        inside = 0
        seen: list[int] = []

        async def pass_through() -> None:
            nonlocal inside
            async with database.sweep("counting"):
                inside += 1
                seen.append(inside)
                # A real pass reads rows here, and reading rows yields to the loop. Without a
                # yield of some kind nothing else ever gets a chance to be wrong, and the test
                # would pass against no lane at all.
                await asyncio.sleep(0)
                inside -= 1

        await asyncio.gather(*(pass_through() for _ in range(8)))

        assert seen == [1] * 8, "two passes were in the lane together"
    finally:
        await database.close()


async def test_a_sweep_inside_a_sweep_is_refused_rather_than_hanging(tmp_path: Path) -> None:
    """The one thing a lock like this must never do quietly.

    A second lane opened inside the first waits for a lock the same task is holding, so it never
    wakes, nothing is logged and the whole application stops. That is the least diagnosable failure
    there is, and it is why this raises instead.
    """
    database = Database(tmp_path / "nested.sqlite3", readers=2)
    await database.connect()
    try:
        with pytest.raises(DatabaseError, match="inside another sweep"):
            async with database.sweep("outer"), database.sweep("inner"):
                pass  # pragma: no cover (the refusal above is the point)
    finally:
        await database.close()


async def test_a_write_inside_a_write_is_refused_rather_than_hanging(tmp_path: Path) -> None:
    """The same fault on the write guard, the busier of the two.

    A sentence in a docstring cannot be observed while an application is frozen. This can.
    """
    database = Database(tmp_path / "nested-write.sqlite3", readers=2)
    await database.connect()
    try:
        with pytest.raises(DatabaseError, match="inside another write"):
            async with database.write(), database.write():
                pass  # pragma: no cover (the refusal above is the point)
    finally:
        await database.close()


async def test_two_tasks_may_both_want_the_same_guard(tmp_path: Path) -> None:
    """The case the refusal must NOT catch, and the reason it is per task rather than per database.

    One task waiting its turn for the writer is doing exactly the right thing. A plain flag on the
    database could not tell that apart from a task opening a second guard inside its own, and would
    refuse ordinary concurrent writes.
    """
    database = Database(tmp_path / "two-tasks.sqlite3", readers=2)
    await database.connect()
    try:
        await database.execute("CREATE TABLE marks (id INTEGER PRIMARY KEY)")

        async def add(mark: int) -> None:
            async with database.write() as connection:
                await connection.execute("INSERT INTO marks (id) VALUES (?)", (mark,))

        await asyncio.gather(*(add(mark) for mark in range(5)))

        rows = await database.fetch_all("SELECT id FROM marks ORDER BY id")
        assert [row["id"] for row in rows] == [0, 1, 2, 3, 4]
    finally:
        await database.close()


async def test_the_lane_does_not_borrow_from_the_pool(tmp_path: Path) -> None:
    """The half of the design that a limit on the pool could not have given.

    A sweep holding a pooled connection is a sweep the browser is queued behind, whatever the limit
    is. The lane has a connection of its own, so an ordinary read is answered while a whole-library
    pass is running, which is the difference between "slower" and "nothing answers at all".
    """
    database = Database(tmp_path / "own-connection.sqlite3", readers=1)
    await database.connect()
    try:
        await database.execute("CREATE TABLE marks (id INTEGER PRIMARY KEY)")
        async with database.sweep("holding"):
            # One reader in the pool, and the lane is holding a connection. If the lane took the
            # pool's, this waits for ever.
            answered = await asyncio.wait_for(database.fetch_all("SELECT id FROM marks"), timeout=5)
        assert answered == []
    finally:
        await database.close()


async def test_the_lane_says_what_is_holding_it(tmp_path: Path) -> None:
    """What the health screen reads. "The wait is eight seconds" is another question; "a duplicate
    scan holds the lane" is an answer."""
    database = Database(tmp_path / "named.sqlite3", readers=2)
    await database.connect()
    try:
        # Read into a name each time rather than asserted on the property: the property is what
        # the health screen reads, and reading it repeatedly is exactly what that screen does.
        before = database.sweeping
        async with database.sweep("duplicate fingerprints"):
            during = database.sweeping
        after = database.sweeping

        assert before is None
        assert during == "duplicate fingerprints"
        assert after is None, "a lane nobody holds must not name a pass"
    finally:
        await database.close()


async def test_a_write_through_the_lane_is_refused(tmp_path: Path) -> None:
    """The lane is a reading door and the same rule applies to it: a write through it collides with
    whoever holds the writer and reports a locked database."""
    database = Database(tmp_path / "lane-write.sqlite3", readers=2)
    await database.connect()
    try:
        with pytest.raises(DatabaseError, match="read connection"):
            await database.sweep_all("DELETE FROM sqlite_master")
    finally:
        await database.close()


async def test_raising_the_worker_count_raises_the_pool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pool follows the worker count; a reader that cannot be opened is not counted."""
    database = Database(tmp_path / "grow.sqlite3", readers=readers_for(4))
    await database.connect()
    try:
        assert database.readers == readers_for(4)
        # Boot opens a handful; the rest cost nothing until a read finds every open one out.
        assert database._read_pool.qsize() == DEFAULT_READERS < readers_for(4)
        assert await database.resize_readers(readers_for(12)) is True
        assert database.readers == readers_for(12)
        assert database.readers > 12, "twelve workers must not be able to take every connection"
        async with contextlib.AsyncExitStack() as held:
            take = [held.enter_async_context(database.read()) for _ in range(database.readers)]
            borrowed = await asyncio.wait_for(asyncio.gather(*take), timeout=5)
            assert len({id(one) for one in borrowed}) == readers_for(12), "each one held together"
        idle = [database._read_pool.get_nowait() for _ in range(readers_for(12))]
        database._readers += 1
        monkeypatch.setattr(database, "_open", lambda: asyncio.wait_for(asyncio.Event().wait(), 0))
        with pytest.raises(TimeoutError):
            await database.fetch_all("SELECT 1")
        assert database._opened == readers_for(12)
        for one in idle:
            database._read_pool.put_nowait(one)
    finally:
        await database.close()


async def test_a_resize_to_the_size_it_already_is_does_nothing(tmp_path: Path) -> None:
    """On a timer, the ordinary case changes nothing, and must not open and close connections."""
    database = Database(tmp_path / "same.sqlite3", readers=6)
    await database.connect()
    try:
        assert await database.resize_readers(6) is False
        assert database.readers == 6
    finally:
        await database.close()


async def test_lowering_it_gives_the_connections_back(tmp_path: Path) -> None:
    """Turning the number down has to actually return the memory, or the setting only works one
    way and an admin who over-committed a small machine cannot undo it."""
    database = Database(tmp_path / "shrink.sqlite3", readers=10)
    await database.connect()
    try:
        assert await database.resize_readers(4) is True
        assert database.readers == 4
        # Still a working pool afterwards, which is the thing a botched shrink breaks.
        rows = await database.fetch_all("SELECT 1 AS one")
        assert [row["one"] for row in rows] == [1]
    finally:
        await database.close()


async def test_a_shrink_waits_for_a_borrowed_connection_rather_than_closing_it(
    tmp_path: Path,
) -> None:
    """Never cancel what is in flight: a connection closed under a query is an error on screen."""
    database = Database(tmp_path / "inflight.sqlite3", readers=2)
    await database.connect()
    try:
        async with database.read():
            shrinking = asyncio.create_task(database.resize_readers(1))
            await asyncio.sleep(0)
            assert not shrinking.done(), "it closed a connection that was being used"
        assert await asyncio.wait_for(shrinking, timeout=5) is True
        assert database.readers == 1
    finally:
        await database.close()


async def test_the_pool_never_goes_to_nothing(tmp_path: Path) -> None:
    """A pool of zero is a Sift that cannot read at all, reached by arithmetic, not a decision."""
    database = Database(tmp_path / "floor.sqlite3", readers=4)
    await database.connect()
    try:
        with pytest.raises(ValueError):
            await database.resize_readers(0)
        assert database.readers == 4
    finally:
        await database.close()


async def test_a_later_pass_sees_what_was_written_since_the_last_one(tmp_path: Path) -> None:
    """The lane must not carry a read snapshot from one pass into the next.

    The connection is reused by every pass, and in WAL mode a transaction fixes what it can see at
    the moment it opens. A pass that starts one and does not end it leaves the NEXT pass reading the
    library as it stood earlier, with no error, no lock and no line in the log, just an older
    answer. A temporary table is enough to start one: a folder's face count would read zero on the
    second pass with nothing anywhere to say why.
    """
    database = Database(tmp_path / "snapshot.sqlite3", readers=2)
    await database.connect()
    try:
        await database.execute("CREATE TABLE marks (id INTEGER PRIMARY KEY)")
        await database.execute("INSERT INTO marks (id) VALUES (1)")

        # A pass that writes something of its own, which is what a fold does with its scratch table.
        async with database.sweep("first") as connection:
            await connection.execute("CREATE TEMP TABLE scratch (n INTEGER)")
            await connection.execute("INSERT INTO scratch (n) VALUES (1)")
            first = list(await connection.execute_fetchall("SELECT id FROM marks"))
        assert [row["id"] for row in first] == [1]

        await database.execute("INSERT INTO marks (id) VALUES (2)")

        async with database.sweep("second") as connection:
            second = list(await connection.execute_fetchall("SELECT id FROM marks ORDER BY id"))

        assert [row["id"] for row in second] == [1, 2], "the lane handed back a stale snapshot"
    finally:
        await database.close()


async def test_a_pass_that_fails_does_not_leave_a_transaction_open(tmp_path: Path) -> None:
    """The same guarantee on the unhappy path, which is the one that gets forgotten."""
    database = Database(tmp_path / "failed.sqlite3", readers=2)
    await database.connect()
    try:
        await database.execute("CREATE TABLE marks (id INTEGER PRIMARY KEY)")

        with contextlib.suppress(RuntimeError):
            async with database.sweep("doomed") as connection:
                await connection.execute("CREATE TEMP TABLE scratch (n INTEGER)")
                raise RuntimeError("the pass gave up")

        await database.execute("INSERT INTO marks (id) VALUES (7)")
        async with database.sweep("after") as connection:
            rows = list(await connection.execute_fetchall("SELECT id FROM marks"))
        assert [row["id"] for row in rows] == [7]
    finally:
        await database.close()


@pytest.fixture
def clean_extensions() -> Iterator[None]:
    """The registry, emptied for the test and put back afterwards.

    Emptied as well as restored: it is process-global by design (a feature registers its
    extension at import time), so a test asserting exactly which extensions loaded would otherwise
    be asserting which slices happen to have been imported by whatever ran before it.
    """
    saved = dict(db_module._EXTENSIONS)
    db_module._EXTENSIONS.clear()
    try:
        yield
    finally:
        db_module._EXTENSIONS.clear()
        db_module._EXTENSIONS.update(saved)


@pytest.mark.unit
def test_an_extension_cannot_be_registered_twice(clean_extensions: None) -> None:
    """Two loaders under one name means whichever imported last decides, silently."""
    register_connection_extension("one", _nothing_to_load)

    with pytest.raises(ValueError, match="registered twice"):
        register_connection_extension("one", _nothing_to_load)


async def _nothing_to_load(_connection: Connection) -> None:
    return None


@pytest.mark.unit
async def test_nothing_is_loaded_before_the_database_is_open(tmp_path: Path) -> None:
    """A feature that reads this on a closed database must be told nothing is there rather than
    getting an answer that is about to change."""
    assert Database(tmp_path / "test.sqlite3").extensions == frozenset()


@pytest.mark.unit
async def test_an_extension_that_loads_is_named_afterwards(
    tmp_path: Path, clean_extensions: None
) -> None:
    """The feature built on it asks this rather than discovering the answer as a syntax error from
    a query somebody has just run."""
    loaded: list[str] = []

    async def load(connection: Connection) -> None:
        loaded.append("asked")
        await connection.execute("SELECT 1")

    register_connection_extension("counter", load)
    database = Database(tmp_path / "test.sqlite3", readers=1)
    await database.connect()
    try:
        assert database.extensions == frozenset({"counter"})
        assert loaded, "the loader was recorded without being run"
    finally:
        await database.close()


@pytest.mark.unit
async def test_one_extension_failing_costs_that_one_and_not_the_boot(
    tmp_path: Path, clean_extensions: None, said: Recorded
) -> None:
    """The whole reason loading is attempted rather than required: a machine missing one file
    loses the feature built on it, and the application still starts."""

    async def angry(_connection: Connection) -> None:
        raise RuntimeError("no such file")

    register_connection_extension("missing", angry)
    register_connection_extension("present", _nothing_to_load)
    database = Database(tmp_path / "test.sqlite3", readers=1)
    await database.connect()
    try:
        assert database.extensions == frozenset({"present"})
        # One per connection: the writer and every reader each load their own.
        assert {event for event, _ in said.warnings} == {"db.extension_failed"}
    finally:
        await database.close()


@pytest.mark.unit
async def test_a_sqlite_that_cannot_load_extensions_at_all_loses_all_of_them(
    tmp_path: Path, clean_extensions: None, said: Recorded, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Either half can be missing: a Python compiled without the method, or a SQLite compiled
    without the capability, and one answer covers both."""
    register_connection_extension("counter", _nothing_to_load)

    async def refuses(self: object, _enabled: bool) -> None:
        raise sqlite3.OperationalError("not enabled")

    monkeypatch.setattr(aiosqlite.Connection, "enable_load_extension", refuses, raising=False)
    database = Database(tmp_path / "test.sqlite3", readers=1)
    await database.connect()
    try:
        assert database.extensions == frozenset()
        assert {event for event, _ in said.warnings} == {"db.extensions_unavailable"}
    finally:
        await database.close()


@pytest.mark.unit
@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id FROM assets",
        "SELECT a.id FROM assets a JOIN asset_locations l ON l.asset_id = a.id",
        "WITH visible AS (SELECT id FROM assets) SELECT id FROM visible",
        # A WHERE with nothing bound is not a filter: this exact shape filters nothing at all on
        # a real library, and a read of that shape makes the application unusable.
        "SELECT id FROM asset_locations WHERE folder_id IS NOT NULL",
        # One row per group is still one row per file.
        "SELECT folder_id, COUNT(*) FROM asset_locations GROUP BY folder_id",
        # The word LIMIT in a comment says nothing about the statement.
        "-- LIMIT is what this needs one day\nSELECT id FROM assets",
    ],
)
def test_a_read_of_the_whole_library_is_recognised(sql: str) -> None:
    assert db_module.is_whole_library_read(sql) is True


@pytest.mark.unit
@pytest.mark.parametrize(
    "sql",
    [
        # Bound: a question about a subject.
        "SELECT id FROM assets WHERE id = ?",
        "SELECT id FROM assets WHERE id = :asset_id",
        # A page is bounded by definition.
        "SELECT id FROM assets ORDER BY id LIMIT 50",
        # One row back, whatever the table holds.
        "SELECT COUNT(*) FROM assets",
        "SELECT MAX(added_at) FROM assets",
        # Not a read at all.
        "INSERT INTO assets (id) VALUES ('x')",
        # A table that grows with how many settings exist rather than with the media.
        "SELECT key, value FROM app_settings",
    ],
)
def test_a_read_that_is_not_the_whole_library_is_left_alone(sql: str) -> None:
    assert db_module.is_whole_library_read(sql) is False


@pytest.mark.unit
def test_a_database_with_no_readers_is_refused(tmp_path: Path) -> None:
    """Zero readers is a database that answers no question at all, and it would present as the
    first read hanging for ever rather than as a configuration mistake."""
    with pytest.raises(ValueError, match="at least one read connection"):
        Database(tmp_path / "test.sqlite3", readers=0)


@pytest.mark.unit
def test_a_data_directory_names_the_file_inside_it(tmp_path: Path) -> None:
    assert Database.for_data_dir(tmp_path).path == tmp_path / db_module.DATABASE_FILENAME


@pytest.mark.unit
async def test_opening_an_open_database_again_changes_nothing(tmp_path: Path) -> None:
    """Idempotent within a process: a second `connect` that opened a second writer would leave the
    first one holding the lock with nothing able to reach it."""
    database = Database(tmp_path / "test.sqlite3", readers=1)
    await database.connect()
    try:
        writer = database._writer

        await database.connect()

        assert database._writer is writer
    finally:
        await database.close()


@pytest.mark.unit
async def test_a_closed_database_refuses_every_way_in(tmp_path: Path) -> None:
    """One sentence, from every door, rather than an AttributeError from inside the driver."""
    database = Database(tmp_path / "test.sqlite3", readers=1)

    with pytest.raises(DatabaseError, match="not open"):
        await database.resize_readers(2)
    with pytest.raises(DatabaseError, match="not open"):
        async with database.read():
            pass
    with pytest.raises(DatabaseError, match="not open"):
        async with database.sweep("nothing"):
            pass


@pytest.mark.unit
async def test_a_resize_to_nothing_is_refused(tmp_path: Path) -> None:
    """The pool is what the browser reads through. Sized to zero it answers nothing, and the
    screen simply stops rather than saying so."""
    database = Database(tmp_path / "test.sqlite3", readers=2)
    await database.connect()
    try:
        with pytest.raises(ValueError, match="at least one read connection"):
            await database.resize_readers(0)
    finally:
        await database.close()


@pytest.mark.unit
async def test_a_whole_read_through_the_lane_comes_back_with_its_rows(tmp_path: Path) -> None:
    """The single-statement form, which is what most passes say. A write through it is refused for
    the same reason a write through a read connection is."""
    database = Database(tmp_path / "test.sqlite3", readers=1)
    await database.connect()
    try:
        await database.execute("CREATE TABLE things (id INTEGER PRIMARY KEY)")
        await database.execute("INSERT INTO things (id) VALUES (1), (2)")

        rows = await database.sweep_all("SELECT id FROM things ORDER BY id", what="things")

        assert [row["id"] for row in rows] == [1, 2]
        with pytest.raises(DatabaseError):
            await database.sweep_all("DELETE FROM things")
    finally:
        await database.close()


@pytest.mark.unit
def test_a_component_at_version_zero_is_refused() -> None:
    """Zero is the version a fresh database reports, so a component claiming it would be migrated
    on every boot for ever."""
    with pytest.raises(ValueError, match="must be 1 or greater"):
        register_schema_initializer("nothing", 0, _no_migration)


async def _no_migration(_connection: Connection, _on_disk: int) -> None:
    return None


@pytest.mark.unit
def test_the_probe_answers_in_a_shape_a_log_line_can_carry() -> None:
    """It is written into the boot log and into the image check, so the field names are the
    interface rather than an implementation detail."""
    probed = probe_sqlite()

    assert probed.as_dict() == {
        "version": probed.version,
        "fts5": probed.fts5,
        "load_extension": probed.load_extension,
    }


@pytest.mark.unit
def test_declaring_a_statement_a_point_read_hands_it_back_and_records_it() -> None:
    declared = point_read("test.by_id", "SELECT v FROM t WHERE id = ?")

    assert declared.sql == "SELECT v FROM t WHERE id = ?"
    assert registered_point_reads()["test.by_id"] is declared


@pytest.mark.unit
def test_the_same_point_read_name_twice_is_refused() -> None:
    """Two statements under one name means the gate proves one of them and the application runs
    the other."""
    point_read("test.by_id", "SELECT v FROM t WHERE id = ?")

    with pytest.raises(ValueError, match="registered twice"):
        point_read("test.by_id", "SELECT v FROM other WHERE id = ?")


@pytest.mark.unit
def test_a_statement_that_binds_nothing_over_the_library_cannot_be_a_point_read() -> None:
    """It asks about no particular subject, so over a table that grows with the library it walks
    the whole of it."""
    with pytest.raises(ValueError, match="binds no value"):
        point_read("test.everything", "SELECT id FROM assets")
    with pytest.raises(ValueError, match="binds no value"):
        point_read("test.joined", "SELECT r.id FROM library_roots r JOIN folders f ON 1")


@pytest.mark.unit
def test_a_small_table_read_whole_can_be_a_point_read(clean_registry: None) -> None:
    """A table that does not grow with the library costs what a seek does, read whole."""
    declared = point_read("test.small", "SELECT v FROM t")
    assert registered_point_reads()["test.small"] is declared


@pytest.mark.unit
def test_a_comment_holding_a_placeholder_does_not_make_a_statement_a_question() -> None:
    """`?` inside a comment binds nothing, and reading it as a binding would let a whole-table
    read in through the one check that is made without a database."""
    with pytest.raises(ValueError, match="binds no value"):
        point_read("test.commented", "SELECT id FROM assets -- takes no ? at all")


@pytest.mark.unit
def test_the_registry_is_copied_rather_than_handed_out(clean_registry: None) -> None:
    point_read("test.by_id", "SELECT v FROM t WHERE id = ?")
    taken = registered_point_reads()
    taken.clear()

    assert "test.by_id" in registered_point_reads()


@pytest.mark.unit
async def test_a_point_read_runs_on_the_loop_when_the_disk_is_fast_enough(tmp_path: Path) -> None:
    """End to end: a fast disk opens the inline connection and a point read goes through it."""
    declared = point_read("test.by_id", "SELECT v FROM t WHERE id = ?")
    database = Database(tmp_path / "test.sqlite3", readers=1)
    await database.connect()
    try:
        assert database.point_reads_inline, "a temporary directory should be fast enough"
        async with database.write() as connection:
            await connection.execute("CREATE TABLE t (id TEXT PRIMARY KEY, v INTEGER)")
            await connection.execute("INSERT INTO t VALUES ('a', 1)")

        # The write moved the schema, so the inline connection parses it again on a thread first.
        for _ in range(500):
            if database._inline(declared) is not None:
                break
            await asyncio.sleep(0.01)
        assert database._inline(declared) is not None, "the inline connection never caught up"
        row = await database.fetch_one(declared, ("a",))
        rows = await database.fetch_all(declared, ("a",))
    finally:
        await database.close()

    assert row is not None
    assert row["v"] == 1
    assert [dict(one) for one in rows] == [{"v": 1}]


@pytest.mark.unit
async def test_a_read_from_another_thread_takes_the_driver_rather_than_the_connection(
    tmp_path: Path,
) -> None:
    """A connection belongs to its thread; from another, the driver answers instead of an error."""
    declared = point_read("test.by_id", "SELECT v FROM t WHERE id = ?")
    database = Database(tmp_path / "test.sqlite3", readers=1)
    await database.connect()
    try:
        assert database._inline(declared) is not None
        database._point_thread = -1
        assert database._inline(declared) is None

        async with database.write() as connection:
            await connection.execute("CREATE TABLE t (id TEXT PRIMARY KEY, v INTEGER)")
            await connection.execute("INSERT INTO t VALUES ('a', 1)")
        row = await database.fetch_one(declared, ("a",))
    finally:
        await database.close()

    assert row is not None and row["v"] == 1


@pytest.mark.unit
def test_the_probe_measures_a_real_read_of_the_file(tmp_path: Path) -> None:
    """A constant expression never opens a read transaction, so it measures the same on a local
    disk and on a network share, which would certify every machine. This is what says the probe
    is not that: it returns a real, positive reading against a real file."""
    database_path = tmp_path / "probe.sqlite3"
    connection = sqlite3.connect(database_path)
    connection.execute("CREATE TABLE t (id TEXT PRIMARY KEY)")
    connection.commit()
    connection.close()

    measured = db_module.time_a_point_read(database_path)

    assert 0 < measured < db_module.POINT_READ_LIMIT_SECONDS


@pytest.mark.unit
def test_the_probe_statement_reads_the_database_rather_than_a_constant() -> None:
    """A constant expression measures the same wherever the database is.

    `SELECT 1` takes the same fraction of a microsecond with the file on a local disk as with it
    on a share hundreds of times slower, because it never opens a read transaction at all.
    A probe made of one certifies every machine, including the only machine this check exists to
    refuse, and it does it while returning a perfectly plausible reading, which is why the test
    above cannot see it. SQLite's own authorizer is asked instead: it reports what a statement
    actually reads.
    """
    read_from: list[str] = []

    def authorizer(action: int, first: str | None, *_rest: object) -> int:
        if action == sqlite3.SQLITE_READ and first is not None:
            read_from.append(first)
        return sqlite3.SQLITE_OK

    connection = sqlite3.connect(":memory:")
    try:
        connection.set_authorizer(authorizer)
        connection.execute(db_module._PROBE_STATEMENT).fetchall()
    finally:
        connection.close()

    assert read_from, (
        "the probe statement reads nothing, so it measures the interpreter rather than the disk "
        "and would allow point reads onto the loop on any machine at all."
    )


async def test_work_registered_in_a_write_runs_once_it_has_committed(tmp_path: Path) -> None:
    done: list[str] = []
    database = Database(tmp_path / "after.sqlite3")
    await database.connect()
    try:
        async with database.write() as connection:
            await connection.execute("CREATE TABLE note (id TEXT)")
            after_commit(lambda: done.append("ran"))
            # Still open, so nothing has landed and nothing has run.
            assert done == []
        assert done == ["ran"]
    finally:
        await database.close()


async def test_work_registered_in_a_write_that_failed_never_runs(tmp_path: Path) -> None:
    """The whole point of registering it here rather than at the call site.

    Anything run before the commit describes a change that may still roll back; anything left to
    the caller to run afterwards is a promise every caller has to remember to keep.
    """
    done: list[str] = []
    database = Database(tmp_path / "after.sqlite3")
    await database.connect()
    try:
        with pytest.raises(RuntimeError, match="deliberate"):
            async with database.write() as connection:
                await connection.execute("CREATE TABLE note (id TEXT)")
                after_commit(lambda: done.append("ran"))
                raise RuntimeError("deliberate")
        assert done == []
    finally:
        await database.close()


async def test_registering_work_outside_a_write_is_refused(tmp_path: Path) -> None:
    """A caller here with no transaction open believes it has one. Silence would mean work that
    never runs, or runs at a moment nobody chose, and both look like nothing happening."""
    with pytest.raises(DatabaseError, match="outside a write"):
        after_commit(lambda: None)


async def test_the_writer_is_free_before_the_work_runs(tmp_path: Path) -> None:
    """A slow piece of work must not hold the single writer.

    The lock is what everything else waiting to write is queued behind, so work that ran inside it
    would make every announcement a pause for the whole application.
    """
    database = Database(tmp_path / "after.sqlite3")
    await database.connect()
    try:
        async with database.write() as connection:
            await connection.execute("CREATE TABLE note (id TEXT)")

        held: list[bool] = []
        async with database.write() as connection:
            after_commit(lambda: held.append(database._write_lock.locked()))
        assert held == [False]
    finally:
        await database.close()


async def test_one_failing_piece_of_work_does_not_stop_the_rest(tmp_path: Path) -> None:
    """By the time these run the change is stored and cannot be taken back, so a failure here is
    reported and everything else still happens."""
    done: list[str] = []

    def explode() -> None:
        raise RuntimeError("deliberate")

    database = Database(tmp_path / "after.sqlite3")
    await database.connect()
    try:
        async with database.write() as connection:
            await connection.execute("CREATE TABLE note (id TEXT)")
            after_commit(explode)
            after_commit(lambda: done.append("ran"))
        assert done == ["ran"]
    finally:
        await database.close()


async def test_two_writes_do_not_see_each_other_s_work(tmp_path: Path) -> None:
    """The list belongs to one transaction. A second write starting with the first one's work still
    pending would run it again."""
    done: list[str] = []
    database = Database(tmp_path / "after.sqlite3")
    await database.connect()
    try:
        async with database.write() as connection:
            await connection.execute("CREATE TABLE note (id TEXT)")
            after_commit(lambda: done.append("first"))
        async with database.write() as connection:
            await connection.execute("INSERT INTO note (id) VALUES ('x')")
        assert done == ["first"]
    finally:
        await database.close()


@pytest.mark.unit
async def test_a_database_with_no_extensions_registered_opens_with_none(
    tmp_path: Path, clean_extensions: None
) -> None:
    """Nothing to load is not a failure to load: the boot carries on and says there are none."""
    database = Database(tmp_path / "plain.sqlite3")
    await database.connect()
    try:
        assert database.extensions == frozenset()
    finally:
        await database.close()
