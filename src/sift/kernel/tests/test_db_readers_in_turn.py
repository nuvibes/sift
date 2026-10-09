# SPDX-License-Identifier: AGPL-3.0-or-later
"""A read beside busy Python threads: the interpreter handed over quickly, and one hop per read."""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import aiosqlite
import pytest

from sift.kernel.db import Database
from sift.kernel.db_readers import READER_SWITCH_SECONDS, keep_readers_in_turn


@pytest.fixture
def default_switch() -> Iterator[None]:
    """The interpreter's own interval for the test, and whatever it was put back after."""
    was = sys.getswitchinterval()
    sys.setswitchinterval(0.005)
    yield
    sys.setswitchinterval(was)


@pytest.mark.integration
async def test_opening_the_database_shortens_the_switch_interval(
    tmp_path: Path, default_switch: None
) -> None:
    database = Database(tmp_path / "test.sqlite3", readers=1)
    await database.connect()
    try:
        assert sys.getswitchinterval() <= READER_SWITCH_SECONDS
    finally:
        await database.close()


@pytest.mark.unit
def test_a_shorter_interval_is_never_lengthened(default_switch: None) -> None:
    sys.setswitchinterval(READER_SWITCH_SECONDS / 2)
    keep_readers_in_turn()
    assert sys.getswitchinterval() == pytest.approx(READER_SWITCH_SECONDS / 2)


def _counting_hops(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    hops = [0]
    real: Callable[..., Any] = aiosqlite.Connection._execute

    async def counted(self: aiosqlite.Connection, fn: Any, *args: Any, **kwargs: Any) -> Any:
        hops[0] += 1
        return await real(self, fn, *args, **kwargs)

    monkeypatch.setattr(aiosqlite.Connection, "_execute", counted)
    return hops


@pytest.mark.integration
async def test_a_read_and_a_sweep_each_take_one_hop_and_leave_no_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = Database(tmp_path / "test.sqlite3", readers=1)
    await database.connect()
    try:
        await database.execute("CREATE TABLE things (id INTEGER PRIMARY KEY)")
        await database.execute(
            "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 500)"
            " INSERT INTO things (id) SELECT i FROM n"
        )
        hops = _counting_hops(monkeypatch)

        rows = await database.fetch_all("SELECT id FROM things ORDER BY id")
        assert hops[0] == 1
        swept = await database.sweep_all("SELECT id FROM things ORDER BY id")
        assert hops[0] == 3, "the sweep's read and its commit"
        assert len(rows) == len(swept) == 500
        await database.execute("INSERT INTO things (id) VALUES (501)")
        folded, remaining = await database.fold_the_log_back()
        assert folded is True and remaining == 0, "a read left its statement open"
    finally:
        await database.close()
