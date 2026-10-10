# SPDX-License-Identifier: AGPL-3.0-or-later
"""The database's upkeep: the write-ahead log folded back and measured, and the planner's statistics kept true."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from sift.kernel import db as db_module
from sift.kernel import db_writer
from sift.kernel.db import (
    Database,
    DatabaseError,
    keep_the_log_folded,
    keep_the_statistics_current,
)

pytestmark = pytest.mark.usefixtures("clean_registry")


@pytest.mark.unit
async def test_folding_the_log_back_shrinks_it(tmp_path: Path) -> None:
    """The whole point, end to end against a real file rather than a mocked pragma."""
    database = Database(tmp_path / "test.sqlite3")
    await database.connect()
    try:
        await database.execute("CREATE TABLE wide (id INTEGER PRIMARY KEY, blob TEXT)")
        for _ in range(200):
            await database.execute("INSERT INTO wide (blob) VALUES (?)", ("x" * 4000,))
        log_file = tmp_path / "test.sqlite3-wal"
        assert log_file.exists() and log_file.stat().st_size > 0

        folded, remaining = await database.fold_the_log_back()

        assert folded is True
        assert remaining == 0, "it started the log over rather than only copying out of it"
        assert log_file.stat().st_size == 0, "which is the disk actually coming back"
    finally:
        await database.close()


@pytest.mark.unit
async def test_folding_an_empty_log_is_not_an_error(tmp_path: Path) -> None:
    """It runs on a timer, so the ordinary case is that there is nothing to do."""
    database = Database(tmp_path / "test.sqlite3")
    await database.connect()
    try:
        assert await database.fold_the_log_back() == (True, 0)
    finally:
        await database.close()


@pytest.mark.unit
async def test_folding_the_log_takes_the_writer_rather_than_cutting_in(tmp_path: Path) -> None:
    """It runs on the writer's connection, and a statement fired at a connection somebody else has
    a transaction open on is `database table is locked`: a fold that silently did not happen, on
    the timer whose whole purpose is that the log does not grow. Proved by the guard's own rule:
    anything going through `write()` refuses to be opened inside another one.
    """
    database = Database(tmp_path / "fold.sqlite3")
    await database.connect()
    try:
        async with database.write() as connection:
            await connection.execute("CREATE TABLE note (id TEXT)")
            with pytest.raises(DatabaseError, match="be opened inside"):
                await database.fold_the_log_back()
        assert await database.fold_the_log_back() == (True, 0), "and it folds outside one"
    finally:
        await database.close()


@pytest.mark.unit
async def test_the_keeper_stops_when_it_is_told_to(tmp_path: Path) -> None:
    """It holds the writer's connection, so it has to be gone before the database closes."""
    database = Database(tmp_path / "test.sqlite3")
    await database.connect()
    stop = asyncio.Event()
    try:
        keeper = asyncio.create_task(keep_the_log_folded(database, stop, interval=0.01))
        await asyncio.sleep(0.05)
        stop.set()
        await asyncio.wait_for(keeper, timeout=1.0)
    finally:
        await database.close()


@pytest.mark.unit
async def test_a_failed_fold_does_not_take_the_keeper_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Housekeeping that dies of one bad pass is housekeeping that silently stopped."""
    database = Database(tmp_path / "test.sqlite3")
    await database.connect()
    calls = 0

    tried_again = asyncio.Event()

    async def angry() -> tuple[bool, int]:
        nonlocal calls
        calls += 1
        if calls >= 2:
            tried_again.set()
        raise RuntimeError("no")

    monkeypatch.setattr(database, "fold_the_log_back", angry)
    stop = asyncio.Event()
    try:
        keeper = asyncio.create_task(keep_the_log_folded(database, stop, interval=0.005))
        # Waited FOR rather than slept THROUGH. A fixed sleep makes this a test of how fast the
        # machine happens to be, which is a test that passes here and fails in a full run.
        await asyncio.wait_for(tried_again.wait(), timeout=5.0)

        assert calls >= 2, "it kept going after the first failure"
        stop.set()
        await asyncio.wait_for(keeper, timeout=1.0)
    finally:
        await database.close()


@pytest.mark.unit
def test_a_database_with_no_log_beside_it_measures_nothing(tmp_path: Path) -> None:
    """Asked on a timer against a database that may not have been written to yet. An error here
    would take the keeper down on the first tick of a quiet install."""
    assert db_module._log_bytes(tmp_path / "never-opened.sqlite3") == 0


@pytest.mark.unit
async def test_the_keeper_says_so_when_it_reclaims_something_worth_saying(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A quiet install must not narrate every tick, and a big reclaim must not be silent: the
    threshold is the whole of what separates the two."""
    database = Database(tmp_path / "test.sqlite3", readers=1)
    await database.connect()
    said_lines: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        db_writer.log, "info", lambda event, **fields: said_lines.append((event, fields))
    )
    sizes = iter([db_module.RECLAIM_WORTH_SAYING_BYTES * 2, 0])
    monkeypatch.setattr(db_writer, "_log_bytes", lambda _path: next(sizes, 0))
    stop = asyncio.Event()
    try:
        keeper = asyncio.create_task(keep_the_log_folded(database, stop, interval=0.005))
        await asyncio.sleep(0.05)
        stop.set()
        await asyncio.wait_for(keeper, timeout=1.0)
    finally:
        await database.close()

    assert [event for event, _ in said_lines if event.startswith("db.log")] == ["db.log_folded"]


@pytest.mark.unit
async def test_a_reclaim_too_small_to_be_worth_saying_is_not_said(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other side of the threshold, and the one that decides whether the line is worth having.

    This runs on a timer for the life of the process. A line per tick on an install that reclaims a
    few kilobytes is a log nobody can find a real event in.
    """
    database = Database(tmp_path / "test.sqlite3", readers=1)
    await database.connect()
    said_lines: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        db_writer.log, "info", lambda event, **fields: said_lines.append((event, fields))
    )
    sizes = iter([db_module.RECLAIM_WORTH_SAYING_BYTES // 2, 0])
    monkeypatch.setattr(db_writer, "_log_bytes", lambda _path: next(sizes, 0))
    stop = asyncio.Event()
    try:
        keeper = asyncio.create_task(keep_the_log_folded(database, stop, interval=0.005))
        await asyncio.sleep(0.05)
        stop.set()
        await asyncio.wait_for(keeper, timeout=1.0)
    finally:
        await database.close()

    assert [event for event, _ in said_lines if event.startswith("db.log")] == []


@pytest.mark.unit
async def test_a_log_that_could_not_be_started_over_is_a_quiet_line_and_not_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reader in the way is the ordinary case on a busy install. A log that never shrinks needs
    an explanation somewhere, or it reads as housekeeping that was never wired up."""
    database = Database(tmp_path / "test.sqlite3", readers=1)
    await database.connect()
    said_lines: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        db_writer.log, "debug", lambda event, **fields: said_lines.append((event, fields))
    )

    async def busy() -> tuple[bool, int]:
        return False, 12

    monkeypatch.setattr(database, "fold_the_log_back", busy)
    stop = asyncio.Event()
    try:
        keeper = asyncio.create_task(keep_the_log_folded(database, stop, interval=0.005))
        await asyncio.sleep(0.05)
        stop.set()
        await asyncio.wait_for(keeper, timeout=1.0)
    finally:
        await database.close()

    assert ("db.log_busy", {"log_mb": 0.0, "pages": 12}) in said_lines


@pytest.mark.unit
async def test_a_keeper_told_to_stop_before_it_starts_does_nothing_at_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Shutdown can beat startup, and a pass that ran anyway would be holding the writer's
    connection while the database is being closed."""
    database = Database(tmp_path / "test.sqlite3", readers=1)
    await database.connect()
    folds = 0

    async def counted() -> tuple[bool, int]:
        nonlocal folds
        folds += 1
        return True, 0

    monkeypatch.setattr(database, "fold_the_log_back", counted)
    stop = asyncio.Event()
    stop.set()
    try:
        await asyncio.wait_for(keep_the_log_folded(database, stop, interval=0.005), timeout=1.0)
    finally:
        await database.close()

    assert folds == 0


@pytest.mark.unit
async def test_refreshing_tells_the_planner_how_big_a_table_really_is(tmp_path: Path) -> None:
    database = Database(tmp_path / "stats.sqlite3")
    await database.connect()
    try:
        async with database.write() as connection:
            await connection.execute("CREATE TABLE note (id INTEGER PRIMARY KEY, who TEXT)")
            await connection.execute("CREATE INDEX ix_note_who ON note (who)")
            await connection.executemany(
                "INSERT INTO note (who) VALUES (?)", [(f"n{n}",) for n in range(2000)]
            )
        assert await database.refresh_statistics(reason="test", force=True) is True
        rows = await database.fetch_all(
            "SELECT stat FROM sqlite_stat1 WHERE idx = ?", ("ix_note_who",)
        )
        assert rows, "the planner has nothing recorded about the index at all"
        assert str(rows[0]["stat"]).startswith("2000"), str(rows[0]["stat"])
    finally:
        await database.close()


@pytest.mark.unit
async def test_the_boot_refreshes_the_statistics_after_the_migrations(tmp_path: Path) -> None:
    """Proved by the debounce: the next ask inside the minute is turned away, which can only happen
    if the boot had already run one."""
    database = Database(tmp_path / "boot.sqlite3")
    await database.connect()
    try:
        await database.initialize_schema()
        assert await database.refresh_statistics(reason="pass:scan") is False
    finally:
        await database.close()


@pytest.mark.unit
async def test_a_second_ask_inside_the_minute_costs_nothing_and_a_person_is_never_refused(
    tmp_path: Path,
) -> None:
    """Several whole-library passes drain in one tick and each of them asks. A person pressing the
    button on the maintenance screen is a different case and must never be told no by a timer."""
    database = Database(tmp_path / "debounce.sqlite3")
    await database.connect()
    try:
        assert await database.refresh_statistics(reason="pass:scan") is True
        assert await database.refresh_statistics(reason="pass:identify") is False
        assert await database.refresh_statistics(reason="tidy", force=True) is True
    finally:
        await database.close()


@pytest.mark.unit
async def test_refreshing_the_statistics_needs_an_open_database(tmp_path: Path) -> None:
    database = Database(tmp_path / "closed.sqlite3")
    with pytest.raises(DatabaseError):
        await database.refresh_statistics(reason="test")


@pytest.mark.unit
async def test_the_statistics_keeper_stops_when_it_is_told_to(tmp_path: Path) -> None:
    """It writes on the writer's own connection, so it has to be gone before the database closes."""
    database = Database(tmp_path / "keeper.sqlite3")
    await database.connect()
    stop = asyncio.Event()
    try:
        keeper = asyncio.create_task(keep_the_statistics_current(database, stop, interval=0.01))
        await asyncio.sleep(0.05)
        stop.set()
        await asyncio.wait_for(keeper, timeout=1.0)
    finally:
        await database.close()


@pytest.mark.unit
async def test_a_failed_refresh_does_not_take_the_statistics_keeper_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Housekeeping that dies of one bad pass is housekeeping that silently stopped."""
    database = Database(tmp_path / "keeper.sqlite3")
    await database.connect()
    calls = 0
    tried_again = asyncio.Event()

    async def angry(*, reason: str, every_table: bool = False, force: bool = False) -> bool:
        nonlocal calls
        calls += 1
        if calls >= 2:
            tried_again.set()
        raise RuntimeError("no")

    monkeypatch.setattr(database, "refresh_statistics", angry)
    stop = asyncio.Event()
    try:
        keeper = asyncio.create_task(keep_the_statistics_current(database, stop, interval=0.005))
        # Waited FOR rather than slept THROUGH, so this is not a test of how fast the machine is.
        await asyncio.wait_for(tried_again.wait(), timeout=5.0)
        assert calls >= 2, "it kept going after the first failure"
        stop.set()
        await asyncio.wait_for(keeper, timeout=1.0)
    finally:
        await database.close()


@pytest.mark.unit
async def test_a_keeper_told_to_stop_before_it_starts_does_nothing(tmp_path: Path) -> None:
    """A shutdown that lands before the first tick must not cost a refresh on the way out."""
    stop = asyncio.Event()
    stop.set()
    # Never connected: a keeper that touched the database here would raise, not return.
    await asyncio.wait_for(
        keep_the_statistics_current(Database(tmp_path / "never.sqlite3"), stop, interval=0.01),
        timeout=1.0,
    )


@pytest.mark.unit
async def test_a_commit_never_copies_the_log_back(tmp_path: Path) -> None:
    """The copy ran inside the write block that crossed the threshold, holding every other write
    for it: the writer leaves it to the keeper."""
    database = Database(tmp_path / "test.sqlite3")
    await database.connect()
    try:
        async with database.write() as writer:
            cursor = await writer.execute("PRAGMA wal_autocheckpoint")
            row = await cursor.fetchone()
            await cursor.close()
        assert row is not None and row[0] == 0
    finally:
        await database.close()


@pytest.mark.unit
async def test_a_log_copied_back_is_written_over_rather_than_grown(tmp_path: Path) -> None:
    """Copied in full, the next writes start the log over, so it stays the size of one burst."""
    database = Database(tmp_path / "test.sqlite3")
    await database.connect()
    log_file = tmp_path / "test.sqlite3-wal"
    try:
        await database.execute("CREATE TABLE wide (id INTEGER PRIMARY KEY, blob TEXT)")
        for _ in range(200):
            await database.execute("INSERT INTO wide (blob) VALUES (?)", ("x" * 4000,))
        one_burst = log_file.stat().st_size
        pages, copied = await database.copy_the_log_back()
        assert pages == copied > 0, "everything in the log was copied"
        for _ in range(200):
            await database.execute("INSERT INTO wide (blob) VALUES (?)", ("y" * 4000,))
        assert log_file.stat().st_size < one_burst * 1.5, "a second burst did not double it"
    finally:
        await database.close()


@pytest.mark.unit
async def test_the_keeper_copies_a_grown_log_and_folds_only_while_the_writer_is_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A copy whenever the log passes its size; the fold, which takes the writer, waits for a
    look when nobody holds it."""
    database = Database(tmp_path / "test.sqlite3")
    await database.connect()
    copies = folds = 0
    copied = asyncio.Event()
    folded = asyncio.Event()

    async def copy() -> tuple[int, int]:
        nonlocal copies
        copies += 1
        copied.set()
        return 0, 0

    async def fold() -> tuple[bool, int]:
        nonlocal folds
        folds += 1
        folded.set()
        return True, 0

    monkeypatch.setattr(database, "copy_the_log_back", copy)
    monkeypatch.setattr(database, "fold_the_log_back", fold)
    monkeypatch.setattr(db_writer, "_log_bytes", lambda _path: 10)
    stop = asyncio.Event()
    try:
        async with database.write():
            keeper = asyncio.create_task(
                keep_the_log_folded(database, stop, interval=0.005, look=0.005, copy_at=5)
            )
            await asyncio.wait_for(copied.wait(), timeout=5.0)
            assert folds == 0, "no fold while the writer is held"
        await asyncio.wait_for(folded.wait(), timeout=5.0)
        stop.set()
        await asyncio.wait_for(keeper, timeout=1.0)
    finally:
        await database.close()
