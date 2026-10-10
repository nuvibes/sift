# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every statement named, timed and judged against its own usual cost, writes included."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

import aiosqlite
from aiosqlite.context import contextmanager as _a_result

from sift.kernel.db_base import Params
from sift.kernel.db_readers import (
    _SQL_COMMENT,
    _TABLE_AFTER,
    NAMES_KEPT,
    PointRead,
    StatementBudget,
    StatementRun,
)
from sift.kernel.log import Timing, timing_hook

#: A run of bound values: `IN (?,?,?)` and `IN (?,?)` are one statement.
_PLACEHOLDER_RUN = re.compile(r"\?(?:\s*,\s*\?)+")

#: The table a write writes, so `insert:assets` names the write and not the table it selects from.
_WRITTEN_TABLE = re.compile(
    r"\b(?:INTO|UPDATE)\s+(?:OR\s+\w+\s+)?(?:[A-Za-z_][A-Za-z0-9_]*\.)?([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)

#: What may appear in the readable half of a name; the rest is dropped, since logs stay ASCII.
_NAME_SAFE = re.compile(r"[^a-z0-9_]")

#: Every name already worked out, by statement: naming costs about a point read. Cleared when full.
_NAMES: dict[str, str] = {}


def _readable(word: str) -> str:
    """One identifier, lowercased and cut to a length a log line can carry."""
    return _NAME_SAFE.sub("", word.lower())[:32] or "-"


def statement_name(statement: str | PointRead) -> str:
    """A short, stable name for a statement, carrying none of its text: a declared statement's own
    name, or the verb, the first table and four bytes of digest (`select:asset_people#1f3c9a2b`),
    over the text with comments, whitespace and placeholder runs folded so a reformat keeps it."""
    if isinstance(statement, PointRead):
        return statement.name
    known = _NAMES.get(statement)
    if known is not None:
        return known
    flat = " ".join(_SQL_COMMENT.sub(" ", statement).split())
    shape = _PLACEHOLDER_RUN.sub("?", flat)
    digest = hashlib.blake2s(shape.encode("utf-8", "replace"), digest_size=4).hexdigest()
    head = shape.lstrip("(").split(None, 1)
    verb = _readable(head[0]) if head else "sql"
    tables = _WRITTEN_TABLE.findall(shape) or _TABLE_AFTER.findall(shape)
    name = f"{verb}:{_readable(tables[0]) if tables else '-'}#{digest}"
    if len(_NAMES) >= NAMES_KEPT:
        _NAMES.clear()
    _NAMES[statement] = name
    return name


#: The running cost of every statement this process has run. One per process, like the log itself.
_BUDGET = StatementBudget()


def statement_budget() -> StatementBudget:
    """The process's statement budget. Tests reach for it; nothing else should have to."""
    return _BUDGET


# SQLite's own work count (one per virtual machine instruction, triggers included), the same on
# any machine and under any load. Test-only: a connection counts only when it was opened while the
# budget hears (`StatementBudget.heard`), and production never sets that.

#: How many instructions each call of the counter stands for: a call per instruction costs a
#: statement several times over, and a count within sixteen is all the ledger needs.
STEP_EVERY = 16

#: Each counting connection's steps so far, by the connection's id.
_STEP_CELLS: dict[int, list[int]] = {}

#: Where a judged statement's count started, by its timing's id: the connection's id and the count.
_STEPS_FROM: dict[int, tuple[int, int]] = {}


def _step_counter(cell: list[int]) -> Callable[[], int]:
    def count() -> int:
        cell[0] += STEP_EVERY
        return 0

    return count


def _counting_cell(connection: object) -> list[int] | None:
    """A new count for this connection, or None when nothing hears it (production)."""
    if _BUDGET.heard is None:
        return None
    cell = _STEP_CELLS[id(connection)] = [0]
    return cell


def _ran_on(timing: Timing, connection: object) -> None:
    """Say which connection a judged statement runs on, so its steps can be counted."""
    cell = _STEP_CELLS.get(id(connection))
    if cell is not None:
        _STEPS_FROM[id(timing)] = (id(connection), cell[0])


def _steps_taken(timing: Timing) -> int | None:
    started = _STEPS_FROM.pop(id(timing), None)
    if started is None:
        return None
    connection, before = started
    cell = _STEP_CELLS.get(connection)
    if cell is None:
        return None
    steps = cell[0] - before
    timing.measured(steps=steps)
    return steps


#: Set while the database keeps its own log: those statements are nobody's press, so the ledger
#: never hears them and the log names their stage.
_MAINTENANCE: ContextVar[bool] = ContextVar("db_maintenance", default=False)


@contextmanager
def _judged(
    stage: str, statement: str | PointRead, sql: str, params: Params = ()
) -> Iterator[Timing]:
    """Time one statement, judge it against its own usual cost, and remember what it cost: the
    record is told the NAME, and a statement that FAILED is not a reading at all."""
    maintenance = _MAINTENANCE.get()
    if maintenance:
        stage = "db.maintenance"
    name = statement_name(statement)
    steps: int | None = None
    with timing_hook(
        stage, sql=sql, statement=name, level="debug", slow_ms=_BUDGET.threshold_ms(name)
    ) as timing:
        try:
            yield timing
        finally:
            steps = _steps_taken(timing)
    _BUDGET.observed(
        name,
        timing.ran_ms,
        None if steps is None or maintenance else StatementRun(stage, name, steps, sql, params),
    )


#: aiosqlite's own default: rows handed across per hop when a cursor is iterated.
_ITER_CHUNK = 64


class _JudgedWriter(aiosqlite.Connection):
    """The write connection: every statement inside a write is named and timed like a read, so a
    press's own writes are in the log beside its reads. It counts the statements of the write
    block it is in, so a block that holds the writer too long can say what it was running."""

    statements = 0
    last_statement: str | None = None

    def began_block(self) -> None:
        self.statements = 0
        self.last_statement = None

    def _on_statement(self, sql: str) -> None:
        self.statements += 1
        self.last_statement = statement_name(sql)

    @_a_result
    async def execute(self, sql: str, parameters: Iterable[Any] | None = None) -> aiosqlite.Cursor:
        self._on_statement(sql)
        with _judged("db.write", sql, sql, _bound(parameters)) as timing:
            _ran_on(timing, self)
            return await super().execute(sql, parameters)

    @_a_result
    async def executemany(self, sql: str, parameters: Iterable[Iterable[Any]]) -> aiosqlite.Cursor:
        self._on_statement(sql)
        with _judged("db.write", sql, sql) as timing:
            _ran_on(timing, self)
            return await super().executemany(sql, parameters)

    @_a_result
    async def execute_fetchall(
        self, sql: str, parameters: Iterable[Any] | None = None
    ) -> Iterable[sqlite3.Row]:
        self._on_statement(sql)
        with _judged("db.write", sql, sql, _bound(parameters)) as timing:
            _ran_on(timing, self)
            return await super().execute_fetchall(sql, parameters)


def _bound(parameters: Iterable[Any] | None) -> Params:
    """What a statement bound, as the ledger keeps it: a mapping or a sequence, never a stream."""
    if parameters is None:
        return ()
    if isinstance(parameters, (Sequence, Mapping)):
        return parameters
    return ()
