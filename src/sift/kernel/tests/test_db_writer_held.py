# SPDX-License-Identifier: AGPL-3.0-or-later
"""A write block that holds the writer is said while it holds it and when it lets go.

Every write in the application queues behind the one writer, and a block that runs for minutes
stalls the jobs, the polls and the screens together, so the log goes silent at the moment it is
needed. The writer itself keeps talking: a line every few seconds while a block is held past
`WRITER_HELD_SECONDS`, naming the statement it is on, and one line at the end for a block over
`WRITE_HELD_BUDGET_MS`.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast

import pytest

from sift.kernel import db as db_module
from sift.kernel.db import Database

pytestmark = pytest.mark.integration


@pytest.fixture
def warnings(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    said: list[tuple[str, dict[str, Any]]] = []
    from sift.kernel import db_writer

    for spoken in (db_module.log, db_writer.log):
        monkeypatch.setattr(spoken, "warning", lambda event, **fields: said.append((event, fields)))
    return said


async def test_a_block_holding_the_writer_is_said_while_held_and_when_it_lets_go(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, warnings: list[tuple[str, dict[str, Any]]]
) -> None:
    monkeypatch.setattr(db_module, "WRITER_HELD_SECONDS", 0.05)
    monkeypatch.setattr(db_module, "WRITE_HELD_BUDGET_MS", 100)
    database = Database(tmp_path / "held.sqlite3")
    await database.connect()
    try:
        async with database.write() as connection:
            await connection.execute("CREATE TABLE held (n INTEGER)")
            await connection.execute("INSERT INTO held VALUES (1)")
            await asyncio.sleep(0.5)
    finally:
        await database.close()

    held = [fields for event, fields in warnings if event == "db.writer_held"]
    assert len(held) >= 2, "said every so many seconds while the block runs, not once"
    # The block's own two statements, then whatever the before-commit hooks ran.
    assert any(f["statement"].startswith("insert:held") for f in held)
    assert held[-1]["statements"] >= 2
    ended = [fields for event, fields in warnings if event == "db.write_held"]
    assert len(ended) == 1 and ended[0]["statements"] >= 2 and ended[0]["blocks"] == 1
    assert ended[0]["held_ms"] >= 150


async def test_the_held_lines_stop_when_the_block_lets_go(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, warnings: list[tuple[str, dict[str, Any]]]
) -> None:
    """Every line after the first is a timer of its own: the block's end cancels the next one."""
    monkeypatch.setattr(db_module, "WRITER_HELD_SECONDS", 0.02)
    database = Database(tmp_path / "held.sqlite3")
    await database.connect()
    loop = asyncio.get_running_loop()
    try:
        async with database.write() as connection:
            await connection.execute("CREATE TABLE held (n INTEGER)")
            said_twice = asyncio.Event()
            loop.call_later(0.1, said_twice.set)
            await said_twice.wait()
        said = sum(1 for event, _fields in warnings if event == "db.writer_held")
        later = asyncio.Event()
        loop.call_later(0.2, later.set)
        await later.wait()
    finally:
        await database.close()
    assert said >= 2
    assert sum(1 for event, _fields in warnings if event == "db.writer_held") == said


async def test_a_short_write_is_not_said_and_the_next_block_counts_afresh(
    tmp_path: Path, warnings: list[tuple[str, dict[str, Any]]]
) -> None:
    database = Database(tmp_path / "quick.sqlite3")
    await database.connect()
    try:
        async with database.write() as connection:
            await connection.execute("CREATE TABLE quick (n INTEGER)")
            await connection.execute("INSERT INTO quick VALUES (1)")
        async with database.write() as connection:
            await connection.execute("INSERT INTO quick VALUES (2)")
            assert cast(Any, connection).statements == 1
    finally:
        await database.close()

    assert not [event for event, _ in warnings if event.startswith("db.write")]


async def test_the_writer_keeps_its_temporary_tables_in_memory_and_a_reader_on_disk(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "temp.sqlite3")
    await database.connect()
    try:
        async with database.write() as writer:
            (on_writer,) = await writer.execute_fetchall("PRAGMA temp_store")
        async with database.read() as reader:
            (on_reader,) = await reader.execute_fetchall("PRAGMA temp_store")
    finally:
        await database.close()

    assert int(on_writer[0]) == 2, "memory"
    assert int(on_reader[0]) == 0, "the default, a file"


async def test_under_load_the_held_line_comes_once_a_while_and_counts_the_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, warnings: list[tuple[str, dict[str, Any]]]
) -> None:
    monkeypatch.setattr(db_module, "WRITE_HELD_BUDGET_MS", 0)
    monkeypatch.setattr(db_module, "WRITE_HELD_SAY_SECONDS", 60.0)
    database = Database(tmp_path / "busy.sqlite3")
    await database.connect()
    try:
        for sql in (
            "CREATE TABLE busy0 (n INTEGER)",
            "CREATE TABLE busy1 (n INTEGER)",
            "CREATE TABLE busy2 (n INTEGER)",
            "CREATE TABLE busy3 (n INTEGER)",
        ):
            async with database.write() as connection:
                await connection.execute(sql)
    finally:
        await database.close()

    said = [fields for event, fields in warnings if event == "db.write_held"]
    assert [one["blocks"] for one in said] == [1], "the first is said; the next three are counted"
