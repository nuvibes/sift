# SPDX-License-Identifier: AGPL-3.0-or-later
"""A question about the queue's live rows is a seek, never a read of the whole table.

`jobs` keeps settled rows for a week, so it is large while few rows are live, and statistics taken
on an empty queue make a walk look as cheap as a seek. The statements say otherwise
(`unlikely(...)` on the live-state term) and `ix_jobs_by_type` starts from the type. The plans are
read on a queue shaped as the statistics describe, after `ANALYZE` and before it, for every
statement of `kernel/jobs/queue.py` naming a live state: no `SCAN` of the jobs table, and a
question about one type never seeks a state's rows without the type beside it.
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3  # nosemgrep: sift-no-database-driver-outside-kernel
from collections.abc import Iterator
from typing import Any, cast

import pytest

from sift.kernel.db import IN_MARKER, Connection
from sift.kernel.jobs import queue
from sift.kernel.jobs import schema as jobs_schema

pytestmark = [pytest.mark.gate, pytest.mark.integration]

#: The states a row is live in: waiting, working, or held on something.
LIVE_STATES = ("queued", "running", "blocked", "paused")

#: A live state named in a statement's text.
_NAMES_A_LIVE_STATE = re.compile(r"'(?:queued|running|blocked|paused)'")

#: A whole statement, not a fragment one is built from (`_FOLDED`).
_STATEMENT = re.compile(r"\s*(?:SELECT|WITH|UPDATE|DELETE|INSERT)\b", re.IGNORECASE)

#: A statement over the jobs table, read or write.
_OVER_JOBS = re.compile(r"\b(?:FROM|UPDATE|JOIN)\s+jobs\b", re.IGNORECASE)

#: A term that makes the statement a question about some types: the type compared with a value, a
#: list, or another table's column, at the top of a condition. The quiet-hours clause names types
#: too (`timing IS NULL AND type IN ...`), as a rule about which rows wait, not as the question.
_ABOUT_A_TYPE = re.compile(
    r"(?<!timing IS NULL )(?:\bWHERE|\bAND|\bON)\s+(?:\w+\.)?type\s*(?:=|IN\b)",
    re.IGNORECASE,
)

#: The names the jobs table answers to in a statement: itself and every alias given to it.
_ALIASED = re.compile(r"\b(?:FROM|JOIN)\s+jobs(?:\s+AS)?\s+([A-Za-z_]\w*)", re.IGNORECASE)
_NOT_AN_ALIAS = frozenset(
    {"on", "where", "group", "order", "limit", "union", "left", "join", "inner", "cross", "set"}
)

#: A placeholder by name, bound below.
_NAMED = re.compile(r"(?<![:\w]):([A-Za-z_]\w*)")

#: The settled rows the queue is filled with, by type: the shape of a library that has worked for
#: a week. Several types, so a type is a fraction of the table, as it is on a real one.
SETTLED_TYPES = ("probe", "thumbnail", "preview", "scan", "stash_box_scan", "generate_file")


#: Statements that name a live state and read every row of their kind by design, each with why.
#: A statement is added here with its reason or not at all.
WHOLE_BY_DESIGN = {
    # The tally of every family by the state its row shows: every top is a family on the page.
    "_FAMILY_TALLY_HEAD": "counts every top by its folded state, the page's tabs",
    # The prune walks the settled and parked rows it deletes, a batch at a time.
    "_PRUNE_SETTLED": "reads one arm per state through its index; kept here because the gate would read `newer.type = old.type` as a question about one type",
}


def _statements_naming_a_live_state() -> dict[str, str]:
    """Every module-level statement of the queue that names a live state, by its name."""
    found: dict[str, str] = {}
    for name, value in sorted(vars(queue).items()):
        if (
            not isinstance(value, str)
            or not _STATEMENT.match(value)
            or not _OVER_JOBS.search(value)
        ):
            continue
        if _NAMES_A_LIVE_STATE.search(value) and name not in WHOLE_BY_DESIGN:
            found[name] = value
    return found


#: The statements named on purpose, whether or not their text names a live state today: each
#: binds or implies one. Kept beside the ones found by reading, never instead of them.
NAMED = (
    "_UNFINISHED_BY_TYPE",
    "_DEMAND_BY_TYPE",
    "_HELD_BY_TYPE",
    "_OUTSTANDING",
    "_LIVE_PAYLOADS",
    "_LIVE_LIKE",
    "_EXCLUSIVE_HELD",
    "_BURST_START",
    "_TASK_RUNS",
    "_NEXT_SCHEDULED",
)


def _composed() -> Iterator[tuple[str, str]]:
    """The list's own statements for a live state: alone, of one type, of the tops only, and the
    count of one type in every state, which is a range of the type's index."""
    for state in LIVE_STATES:
        for extra in ({}, {"job_type": "probe"}, {"tops_only": True}):
            params = {"state": state, **extra, "limit": 50, "offset": 0}
            label = "+".join(["list", state, *extra])
            yield label + " page", queue._list_page(params)
            yield label + " total", queue._list_total(params)
    yield "list type total", queue._list_total({"job_type": "probe"})


def _bindings(sql: str) -> tuple[str, Any]:
    """The statement with one value for each placeholder: a JSON list where it is read as one."""
    text = sql.replace(IN_MARKER, "(?)")
    names = _NAMED.findall(text)
    if names:
        named: dict[str, Any] = {}
        for name in names:
            listed = re.search(r"json_each\(\s*:" + name + r"\b", text) is not None
            named[name] = json.dumps(["probe"]) if listed else 1
        return text, named
    values: list[Any] = []
    for match in re.finditer(r"\?", text):
        listed = text[max(0, match.start() - 10) : match.start()].endswith("json_each(")
        values.append(json.dumps(["probe"]) if listed else 1)
    return text, values


def _plan(connection: sqlite3.Connection, sql: str) -> list[str]:
    text, values = _bindings(sql)
    # EXPLAIN QUERY PLAN takes a STATEMENT, not a value, so there is no placeholder to pass it
    # through. The text is one of the queue's own module constants, read by this test.
    planned = connection.execute(
        "EXPLAIN QUERY PLAN " + text,  # nosemgrep: sift-no-string-built-sql
        values,
    )
    return [str(row[3]) for row in planned]


def faults(sql: str, plan: list[str]) -> list[str]:
    """What is wrong with this plan for this statement, as lines; none for a good one."""
    names = {"jobs"} | {
        alias for alias in _ALIASED.findall(sql) if alias.lower() not in _NOT_AN_ALIAS
    }
    about_a_type = _ABOUT_A_TYPE.search(sql) is not None
    found: list[str] = []
    for line in plan:
        step = re.match(r"(SCAN|SEARCH) (\w+)\b(.*)", line)
        if step is None or step.group(2) not in names:
            continue
        verb, rest = step.group(1), step.group(3)
        if verb == "SCAN":
            found.append(f"walks the table: {line}")
        elif about_a_type and re.match(r"[^(]*\(state=", rest) and "type=" not in rest:
            found.append(f"walks every row of a state to answer for some types: {line}")
    return found


class _Raw:
    """A kernel connection over a plain one, for the schema's own steps."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    async def execute(self, sql: str, params: Any = ()) -> None:
        self._connection.execute(sql, params)


def _queue_database(*, analysed: bool = True) -> sqlite3.Connection:
    """The jobs table with every index, settled rows then `ANALYZE` (when `analysed`) then the few
    live ones, as the statistics find it."""
    connection = sqlite3.connect(":memory:")
    asyncio.run(jobs_schema.initialize(cast(Connection, _Raw(connection)), 0))
    rows: list[tuple[Any, ...]] = []
    serial = 0

    def add(job_type: str, state: str, parent: str | None = None) -> str:
        nonlocal serial
        serial += 1
        job_id = f"J{serial:08d}"
        root = rows[int(parent[1:]) - 1][7] if parent is not None else job_id
        rows.append((job_id, parent, job_type, state, "{}", 1, 1, root, 1, None))
        return job_id

    for index in range(6000):
        ended = ("done", "done", "done", "failed", "canceled")[index % 5]
        head = add(SETTLED_TYPES[index % len(SETTLED_TYPES)], ended)
        if index % 3 == 0:
            add(SETTLED_TYPES[(index + 1) % len(SETTLED_TYPES)], "done", head)
    insert = (
        "INSERT INTO jobs (id, parent_id, type, state, payload, created_at, updated_at, root_id,"
        " started_at, requested_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    connection.executemany(insert, rows)
    connection.commit()
    if analysed:
        connection.execute("ANALYZE")
    settled = len(rows)
    run = add("generate", "running")
    for state in ("queued", "queued", "running", "blocked", "paused"):
        add("generate_file", state, run)
    add("backup_run", "queued")
    connection.executemany(insert, rows[settled:])
    connection.commit()
    return connection


def _every_case() -> dict[str, str]:
    cases = _statements_naming_a_live_state()
    for name in NAMED:
        cases.setdefault(name, getattr(queue, name))
    cases.update(dict(_composed()))
    return cases


@pytest.mark.regression
@pytest.mark.parametrize("analysed", [True, False], ids=["analysed", "new library"])
def test_no_question_about_live_rows_walks_the_table(analysed: bool) -> None:
    connection = _queue_database(analysed=analysed)
    try:
        found = {
            name: lines
            for name, sql in _every_case().items()
            if (lines := faults(sql, _plan(connection, sql)))
        }
    finally:
        connection.close()
    assert not found, (
        "a statement about the queue's live rows walks the table, or every waiting row of a state "
        "to answer for one type. Say the live-state term is rarely true (`unlikely(state IN "
        "(...))`) and let `ix_jobs_by_type` or `ix_jobs_state_by_id` drive it:\n  "
        + "\n  ".join(f"{name}: {'; '.join(lines)}" for name, lines in sorted(found.items()))
    )


def test_the_statements_are_found_by_reading_the_module() -> None:
    """The list is read from the module: a statement added tomorrow that names a live state is in
    it without anybody adding it here, and the named ones are still statements of the queue."""
    found = _statements_naming_a_live_state()
    assert {"_EXCLUSIVE_HELD", "_LIVE_PAYLOADS", "_CLAIM"} <= set(found)
    assert all(isinstance(getattr(queue, name, None), str) for name in NAMED)


def test_the_gate_sees_a_walk() -> None:
    """The two shapes it refuses, planted: a scan of the table, and a type question answered from
    a state's rows."""
    connection = _queue_database()
    try:
        scan = "SELECT type, COUNT(*) FROM jobs NOT INDEXED WHERE state = 'queued' GROUP BY type"
        assert faults(scan, _plan(connection, scan)), "a read kept off every index walks the table"
        walk = "SELECT id FROM jobs INDEXED BY ix_jobs_watchdog WHERE type = ? AND state = 'queued'"
        assert faults(walk, _plan(connection, walk)), "a type read through a state walks the state"
        good = "SELECT id FROM jobs WHERE type = ? AND unlikely(state IN ('queued', 'running'))"
        assert not faults(good, _plan(connection, good))
    finally:
        connection.close()
