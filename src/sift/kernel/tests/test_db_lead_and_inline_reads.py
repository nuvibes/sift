# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the database kernel.

Everything here runs against a real SQLite file in WAL mode. The bugs this module can have
(a pragma that did not stick, a writer that collides with another writer, a migration that runs
twice) do not reproduce against a mock.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from pathlib import Path
from typing import cast

import pytest
from structlog.testing import capture_logs

from sift.kernel import db as db_module
from sift.kernel import db_schema as db_schema_module
from sift.kernel.db import (
    Database,
    point_read,
    register_schema_initializer,
)
from sift.kernel.tests.test_db import _initializer
from sift.testing.logs import uncached_log

pytestmark = pytest.mark.usefixtures("clean_registry")


# --- what the machine's SQLite can do ----------------------------------------------------


@pytest.mark.unit
def test_only_one_component_may_lead(monkeypatch: pytest.MonkeyPatch) -> None:
    """The one that leads comes up before every other, so two leaders would leave the order to
    whichever imported first; the second is refused and the first keeps the place."""
    monkeypatch.setattr(db_schema_module, "_REGISTRY", {})
    order: list[str] = []
    register_schema_initializer("a", 1, _initializer(order, "a"), leads=True)
    with pytest.raises(ValueError, match="another already does"):
        register_schema_initializer("b", 1, _initializer(order, "b"), leads=True)
    assert "b" not in db_module.registered_components()
    assert db_module.registered_components()["a"].leads


@pytest.mark.unit
async def test_a_parse_that_fails_is_logged_and_the_next_read_tries_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A schema the inline connection could not read leaves it off for that read and says so;
    the next read finds the schema still moved and asks again, rather than the lane staying shut."""
    declared = point_read("test.by_id", "SELECT v FROM t WHERE id = ?")
    database = Database(tmp_path / "test.sqlite3", readers=1)
    await database.connect()
    real = db_module._parse_the_schema

    def unreadable(_connection: sqlite3.Connection) -> int:
        raise sqlite3.OperationalError("disk I/O error")

    try:
        async with database.write() as connection:
            await connection.execute("CREATE TABLE t (id TEXT PRIMARY KEY, v INTEGER)")
        monkeypatch.setattr(db_module, "_parse_the_schema", unreadable)
        uncached_log(monkeypatch, db_module)
        with capture_logs() as logs:
            assert database._inline(declared) is None
            failed = database._point_parsing
            assert failed is not None
            await failed
        assert [one["detail"] for one in logs if one["event"] == "db.point_schema_unread"] == [
            "disk I/O error"
        ]
        assert database._point_parsing is None

        monkeypatch.setattr(db_module, "_parse_the_schema", real)
        assert database._inline(declared) is None
        # Read through a cast: the check above narrowed the attribute, and `_inline` set it since.
        again = cast("asyncio.Task[None] | None", database._point_parsing)
        assert again is not None
        await again
        assert database._inline(declared) is not None
    finally:
        await database.close()


@pytest.mark.unit
async def test_closing_waits_for_a_parse_in_flight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The parse runs on a thread over the inline connection, so closing that connection under
    it would pull it from beneath a running statement: the close waits for the parse first."""
    declared = point_read("test.by_id", "SELECT v FROM t WHERE id = ?")
    database = Database(tmp_path / "test.sqlite3", readers=1)
    await database.connect()
    real = db_module._parse_the_schema
    let_go = threading.Event()

    def held(connection: sqlite3.Connection) -> int:
        let_go.wait(5)
        return real(connection)

    async with database.write() as connection:
        await connection.execute("CREATE TABLE t (id TEXT PRIMARY KEY, v INTEGER)")
    monkeypatch.setattr(db_module, "_parse_the_schema", held)
    assert database._inline(declared) is None
    parsing = database._point_parsing
    assert parsing is not None

    closing = asyncio.create_task(database.close())
    await asyncio.sleep(0.05)
    assert not closing.done(), "the close went ahead while the parse still held the connection"
    let_go.set()
    await closing

    assert parsing.done() and parsing.exception() is None
    assert not database.point_reads_inline


@pytest.mark.unit
async def test_a_machine_too_slow_for_inline_reads_boots_without_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The boot parses the inline connection's schema only where there is one to parse."""
    monkeypatch.setattr(
        db_module, "time_a_point_read", lambda _path: db_module.POINT_READ_LIMIT_SECONDS * 2
    )
    database = Database(tmp_path / "test.sqlite3", readers=1)
    await database.connect()
    try:
        await database.initialize_schema()
        assert not database.point_reads_inline
        assert database._point_parsing is None
    finally:
        await database.close()


@pytest.mark.unit
async def test_an_inline_read_sees_a_write_that_has_just_landed(tmp_path: Path) -> None:
    """The failure this would have if the connection ever held a transaction open: no error, no
    lock, just an older answer than the one somebody asked for."""
    declared = point_read("test.by_id", "SELECT v FROM t WHERE id = ?")
    database = Database(tmp_path / "test.sqlite3", readers=1)
    await database.connect()
    try:
        assert database.point_reads_inline
        async with database.write() as connection:
            await connection.execute("CREATE TABLE t (id TEXT PRIMARY KEY, v INTEGER)")
            await connection.execute("INSERT INTO t VALUES ('a', 1)")
        first = await database.fetch_one(declared, ("a",))
        await database.execute("UPDATE t SET v = 2 WHERE id = 'a'")
        second = await database.fetch_one(declared, ("a",))
    finally:
        await database.close()

    assert first is not None and first["v"] == 1
    assert second is not None and second["v"] == 2


@pytest.mark.unit
async def test_the_inline_connection_cannot_write_at_all(tmp_path: Path) -> None:
    """`_refuse_writes` turns a write away by reading the statement. This is the other half: the
    door itself is incapable of one, including the forms a leading word cannot describe."""
    database = Database(tmp_path / "test.sqlite3", readers=1)
    await database.connect()
    try:
        assert database.point_reads_inline
        inline = database._point
        assert inline is not None
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            inline.execute("CREATE TABLE t (id TEXT)")
    finally:
        await database.close()


@pytest.mark.unit
async def test_a_slow_disk_keeps_every_read_on_a_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The case the boot measurement exists for. Nothing is opened, so there is no half-on state:
    the statement still answers, through the driver, exactly as it always did."""
    declared = point_read("test.by_id", "SELECT v FROM t WHERE id = ?")
    monkeypatch.setattr(
        db_module, "time_a_point_read", lambda _path: db_module.POINT_READ_LIMIT_SECONDS * 2
    )
    database = Database(tmp_path / "test.sqlite3", readers=1)
    await database.connect()
    try:
        assert not database.point_reads_inline
        assert database.point_read_seconds == pytest.approx(db_module.POINT_READ_LIMIT_SECONDS * 2)
        async with database.write() as connection:
            await connection.execute("CREATE TABLE t (id TEXT PRIMARY KEY, v INTEGER)")
            await connection.execute("INSERT INTO t VALUES ('a', 1)")
        row = await database.fetch_one(declared, ("a",))
        rows = await database.fetch_all(declared, ("a",))
    finally:
        await database.close()

    assert row is not None and row["v"] == 1
    assert [dict(one) for one in rows] == [{"v": 1}]


@pytest.mark.unit
async def test_a_plain_statement_never_takes_the_inline_connection(tmp_path: Path) -> None:
    """The eligibility is carried by the statement, so text that merely looks the same is not it."""
    declared = point_read("test.by_id", "SELECT v FROM t WHERE id = ?")
    database = Database(tmp_path / "test.sqlite3", readers=1)
    await database.connect()
    try:
        assert database._inline(declared) is not None
        assert database._inline(declared.sql) is None
    finally:
        await database.close()


@pytest.mark.unit
def test_timing_a_point_read_on_a_database_that_cannot_be_opened_refuses_inline(
    tmp_path: Path,
) -> None:
    """Anything that goes wrong has to read as "too slow", which is the answer that changes
    nothing. A directory is a path SQLite cannot open."""
    assert db_module.time_a_point_read(tmp_path) == float("inf")


@pytest.mark.unit
def test_timing_a_point_read_on_a_file_that_is_not_a_database_refuses_inline(
    tmp_path: Path,
) -> None:
    """The other half of the same rule: the file opens and the first statement is what fails."""
    not_a_database = tmp_path / "not.sqlite3"
    not_a_database.write_bytes(b"this is not a database" * 100)

    assert db_module.time_a_point_read(not_a_database) == float("inf")
