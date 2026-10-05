# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the blocking helpers the library switcher reads and writes a library file with."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from sift.kernel.db import execute_blocking, fetch_blocking


def test_a_blocking_write_is_committed_before_it_returns(tmp_path: Path) -> None:
    """The switcher writes a new library's first row and then hands the file to a `Database` on
    another connection, which sees only what was committed."""
    database = tmp_path / "library.sqlite3"
    execute_blocking(database, "CREATE TABLE t (x INTEGER)")
    execute_blocking(database, "INSERT INTO t VALUES (?)", (7,))

    other = sqlite3.connect(database)
    try:
        assert other.execute("SELECT x FROM t").fetchall() == [(7,)]
    finally:
        other.close()


def test_a_blocking_write_cannot_leave_a_row_without_its_parent(tmp_path: Path) -> None:
    """A delete through the helper takes the rows that hang off it, as every other write does."""
    database = tmp_path / "library.sqlite3"
    execute_blocking(database, "CREATE TABLE parent (id INTEGER PRIMARY KEY)")
    execute_blocking(
        database, "CREATE TABLE child (parent_id INTEGER REFERENCES parent(id) ON DELETE CASCADE)"
    )
    execute_blocking(database, "INSERT INTO parent VALUES (1)")
    execute_blocking(database, "INSERT INTO child VALUES (1)")

    execute_blocking(database, "DELETE FROM parent WHERE id = 1")

    assert fetch_blocking(database, "SELECT COUNT(*) FROM child") == [(0,)]
    with pytest.raises(sqlite3.IntegrityError):
        execute_blocking(database, "INSERT INTO child VALUES (2)")


def test_a_blocking_read_answers_plain_tuples_and_cannot_write(tmp_path: Path) -> None:
    """Read-only by the connection, not by the statement: a read helper handed a write refuses it,
    so looking at somebody's file never changes it."""
    database = tmp_path / "library #2.sqlite3"
    execute_blocking(database, "CREATE TABLE t (x INTEGER, y TEXT)")
    execute_blocking(database, "INSERT INTO t VALUES (1, 'one')")

    assert fetch_blocking(database, "SELECT x, y FROM t WHERE x = ?", (1,)) == [(1, "one")]
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        fetch_blocking(database, "INSERT INTO t VALUES (2, 'two')")
    assert fetch_blocking(database, "SELECT COUNT(*) FROM t") == [(1,)]
