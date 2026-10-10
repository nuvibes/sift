# SPDX-License-Identifier: AGPL-3.0-or-later
"""The pieces around a write: the IN clause writer, the commit hooks and the log copy."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from contextvars import ContextVar
from typing import Any

import aiosqlite

from sift.kernel.db_base import IN_MARKER, DatabaseError
from sift.kernel.log import get_logger

log = get_logger(__name__)


def in_clause(sql: str, values: Sequence[Any]) -> tuple[str, list[Any]]:
    """Expand `IN (?*)` to `IN (?, ?, ?)`: only `?` and `,` are ever added, never a name."""
    if sql.count(IN_MARKER) != 1:
        raise ValueError(f"the query needs exactly one {IN_MARKER} marker: {sql!r}")
    if not values:
        # `IN ()` is a syntax error, and "match nothing" is the caller's decision to make.
        raise ValueError("in_clause needs at least one value")

    placeholders = "(" + ",".join("?" * len(values)) + ")"
    return sql.replace(IN_MARKER, placeholders), list(values)


_IN_WRITE: ContextVar[bool] = ContextVar("sift_db_in_write", default=False)
_IN_SWEEP: ContextVar[bool] = ContextVar("sift_db_in_sweep", default=False)

# What runs once this task's write has landed; dropped unrun when the write fails.
_AFTER_COMMIT: ContextVar[list[Callable[[], None]] | None] = ContextVar(
    "sift_db_after_commit", default=None
)


#: What every write does last, inside its transaction (`visibility` registers its fold here).
_BEFORE_COMMIT: list[Callable[[aiosqlite.Connection], Awaitable[None]]] = []


def before_commit(work: Callable[[aiosqlite.Connection], Awaitable[None]]) -> None:
    """Run this at the end of every write, before it commits and in the same transaction."""
    _BEFORE_COMMIT.append(work)


def after_commit(work: Callable[[], None]) -> None:
    """Run once the write in progress has committed; dropped when it rolls back."""
    pending = _AFTER_COMMIT.get()
    if pending is None:
        raise DatabaseError(
            "after_commit() was called outside a write(). Nothing is committing, so the work "
            "would run at a moment nobody chose. Open the write first, or do the work directly."
        )
    pending.append(work)


#: The guarded copy runs only once an unguarded copy caught up (a copy with pages to write waits
#: on the disk's flush, seconds under load); `_MOST_LOG_COPIES` unguarded copies a call at most.
_COPY_UNDER_GUARD_PAGES = 0
_MOST_LOG_COPIES = 8

_LOG_CAP_PAGES = 65_536


async def _copy_back(connection: aiosqlite.Connection) -> tuple[int, int]:
    """One passive copy of the log on this connection: (pages in the log, pages copied)."""
    cursor = await connection.execute("PRAGMA wal_checkpoint(PASSIVE)")
    try:
        row = await cursor.fetchone()
    finally:
        await cursor.close()
    if row is None:  # pragma: no cover (the pragma always answers)
        return 0, 0
    return int(row[1]), int(row[2])
