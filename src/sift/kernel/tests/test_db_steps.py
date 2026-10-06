# SPDX-License-Identifier: AGPL-3.0-or-later
"""Statements named, timed and judged against their own cost, and steps counted only when asked."""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from sift.kernel import db as db_module
from sift.kernel import db_judged
from sift.kernel.db import (
    SLOW_QUERY_MS,
    Database,
    StatementBudget,
    StatementRun,
    point_read,
    statement_budget,
    statement_name,
)
from sift.kernel.log import Timing, timing_hook

pytestmark = pytest.mark.usefixtures("clean_registry")

_CREATE = "CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)"
_INSERT = "INSERT INTO notes (body) VALUES (?)"
_READ = "SELECT body FROM notes WHERE id = ?"


@pytest.fixture
def timed(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Every timing record the database layer opened, with the fields it was handed and measured."""
    records: list[dict[str, Any]] = []

    @contextmanager
    def recorded(stage: str, **fields: Any) -> Iterator[Timing]:
        with timing_hook(stage, **fields) as timing:
            yield timing
        records.append({"stage": stage, **fields, **timing._measured})

    monkeypatch.setattr(db_judged, "timing_hook", recorded)
    return records


@pytest.fixture
def hearing() -> Iterator[list[StatementRun]]:
    """The budget hears every statement, so connections opened now count their steps."""
    heard: list[StatementRun] = []
    statement_budget().heard = heard
    try:
        yield heard
    finally:
        statement_budget().heard = None


async def _opened(tmp_path: Path) -> Database:
    database = Database(tmp_path / "steps.sqlite3", readers=1)
    await database.connect()
    await database.execute(_CREATE)
    return database


async def test_every_statement_inside_a_write_is_named_and_timed(
    tmp_path: Path, timed: list[dict[str, Any]]
) -> None:
    """A press writes through `write()`, and its statements carry their names like a read's."""
    database = await _opened(tmp_path)
    try:
        async with database.write() as connection:
            await connection.execute(_INSERT, ("one",))
            await connection.executemany(_INSERT, [("two",), ("three",)])
            rows = await connection.execute_fetchall("SELECT count(*) FROM notes")
    finally:
        await database.close()

    assert next(iter(rows))[0] == 3
    written = [record["statement"] for record in timed if record["stage"] == "db.write"]
    assert written.count(statement_name(_INSERT)) == 2
    assert statement_name("SELECT count(*) FROM notes") in written


async def test_a_single_write_is_timed_once_with_its_wait(
    tmp_path: Path, timed: list[dict[str, Any]]
) -> None:
    """`Database.execute` judges its statement with the wait for the writer; the writer does not
    judge it a second time."""
    database = await _opened(tmp_path)
    try:
        await database.execute(_INSERT, ("one",))
    finally:
        await database.close()

    inserts = [record for record in timed if record.get("statement") == statement_name(_INSERT)]
    assert len(inserts) == 1


async def test_nothing_counts_steps_unless_the_budget_hears(
    tmp_path: Path, timed: list[dict[str, Any]]
) -> None:
    """Production never sets `heard`, so no connection carries a progress handler and no record
    carries steps: counting every instruction costs a statement several times over."""
    assert statement_budget().heard is None
    database = await _opened(tmp_path)
    try:
        await database.execute(_INSERT, ("one",))
        await database.fetch_all(_READ, (1,))
        assert db_judged._STEP_CELLS == {}
    finally:
        await database.close()

    assert timed
    assert all("steps" not in record for record in timed)


async def test_every_statement_counts_its_steps_when_heard(
    tmp_path: Path,
    timed: list[dict[str, Any]],
    hearing: list[StatementRun],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reads, writes, the sweep lane and a point read all carry their steps, and the budget hears
    each with what it bound."""
    # A machine fast enough for the inline lane, so its connection is counted too.
    monkeypatch.setattr(db_module, "time_a_point_read", lambda _path: 0.0)
    probe = point_read("steps.one_note", _READ)
    database = await _opened(tmp_path)
    try:
        await database.execute(_INSERT, ("one",))
        async with database.write() as connection:
            await connection.executemany(_INSERT, [("two",), ("three",)])
        await database.fetch_all(_READ, (1,))
        await database.fetch_one(probe, (1,))
        await database.sweep_all("SELECT body FROM notes")
        counted = len(db_judged._STEP_CELLS)
    finally:
        await database.close()

    assert counted == 4  # the writer, the sweeper, a reader, the inline lane
    assert db_judged._STEP_CELLS == {}
    stages = {run.stage for run in hearing}
    assert {"db.write", "db.read", "db.sweep"} <= stages
    # Counted in strides, so a statement of a few instructions may read as none.
    assert all(run.steps % db_judged.STEP_EVERY == 0 for run in hearing)
    assert sum(run.steps for run in hearing) > 0
    reads = [run for run in hearing if run.name == statement_name(_READ)]
    assert reads and reads[0].params == (1,)
    assert any(run.name == "steps.one_note" for run in hearing)
    assert all("steps" in record for record in timed if record.get("statement"))


async def test_a_bigger_read_takes_more_steps(tmp_path: Path, hearing: list[StatementRun]) -> None:
    """The count is SQLite's work, so a statement walking more rows says so."""
    database = await _opened(tmp_path)
    try:
        async with database.write() as connection:
            await connection.executemany(_INSERT, [(str(n),) for n in range(200)])
        hearing.clear()
        await database.fetch_all("SELECT body FROM notes WHERE id <= ?", (2,))
        await database.fetch_all("SELECT body FROM notes WHERE id <= ?", (200,))
    finally:
        await database.close()

    few, many = (run.steps for run in hearing)
    assert many > few * 10


def test_a_statement_from_no_counting_connection_has_no_steps() -> None:
    """A timing whose connection was never counted, or was closed meanwhile, reports nothing."""
    timing = Timing(0.0)
    assert db_judged._steps_taken(timing) is None
    db_judged._STEPS_FROM[id(timing)] = (12345, 0)
    assert db_judged._steps_taken(timing) is None
    assert db_judged._bound(None) == ()
    assert db_judged._bound(iter([1])) == ()
    assert db_judged._bound({"a": 1}) == {"a": 1}


# --- what a statement is called ---------------------------------------------------------------
#
# The log identifies a statement by name, not by carrying it: the whole query on every line makes a
# log of a hundred megabytes that names no fault. A name is what a record carries, and these hold
# the three properties that make a name worth having: it is the same every time for the same
# statement, it is different for a different one, and it is not the SQL.


@pytest.mark.unit
def test_a_declared_statement_is_named_by_the_name_it_was_declared_under() -> None:
    """`point_read` already asks for a name that says which read it is. Inventing a second one for
    the same statement would mean the log and the plan gate calling it two different things."""
    read = point_read("access.one_note", "SELECT id FROM notes WHERE id = ?")
    assert statement_name(read) == "access.one_note"


@pytest.mark.unit
def test_a_plain_statement_is_named_from_its_verb_its_table_and_a_digest() -> None:
    name = statement_name("SELECT who FROM asset_people WHERE asset_id = ?")
    assert re.fullmatch(r"select:asset_people#[0-9a-f]{8}", name), name


@pytest.mark.unit
def test_a_name_survives_reformatting_a_comment_and_a_longer_bound_list() -> None:
    """A name that moved when somebody reflowed a query, or when a bound list happened to be one
    longer, would file the same statement under a new heading and lose everything known about it,
    which is precisely when it stops being able to judge itself."""
    plain = statement_name("SELECT who FROM notes WHERE id IN (?,?,?)")
    dressed = statement_name(
        "SELECT who\n"
        "  FROM notes\n"
        " -- the list is as long as the caller's list, which is not a property of the statement\n"
        " WHERE id IN (?, ?, ?, ?, ?)"
    )
    assert plain == dressed


@pytest.mark.unit
def test_two_statements_over_one_table_are_named_apart() -> None:
    """The table alone is not a name: half the reads in the application are about `assets`."""
    assert statement_name("SELECT a FROM notes WHERE id = ?") != statement_name(
        "SELECT b FROM notes WHERE id = ?"
    )


@pytest.mark.unit
def test_a_name_is_ascii_and_carries_none_of_the_statement() -> None:
    name = statement_name("SELECT rude_column FROM notes WHERE stored_path = ? -- \u00e9")
    name.encode("ascii")
    assert "rude_column" not in name and "stored_path" not in name and "WHERE" not in name


# --- what counts as slow, per statement --------------------------------------------------------


@pytest.mark.unit
def test_a_statement_keeps_the_flat_bar_until_it_has_a_number_of_its_own() -> None:
    """The flat bar is the starting point, so a rarely-run statement loses nothing."""
    budget = StatementBudget(settled=5)
    for _ in range(4):
        budget.observed("select:notes#0000", 1.0)
    assert budget.threshold_ms("select:notes#0000") == SLOW_QUERY_MS
    budget.observed("select:notes#0000", 1.0)
    assert budget.threshold_ms("select:notes#0000") < SLOW_QUERY_MS


@pytest.mark.unit
def test_a_statement_that_is_always_this_slow_stops_warning_and_a_sudden_one_does_not() -> None:
    """A statement that honestly costs a hundred milliseconds is over the flat bar on every single
    run: thousands of warnings an afternoon, not one of them a fault. Judged against itself, its
    bar is eight hundred, and the run that takes eight hundred is the one worth a line."""
    budget = StatementBudget(settled=5)
    for _ in range(5):
        budget.observed("select:notes#0000", 100.0)
    assert budget.threshold_ms("select:notes#0000") == pytest.approx(800.0)


@pytest.mark.unit
def test_a_cheap_statement_is_not_judged_at_a_tenth_of_a_millisecond() -> None:
    """Eight times the usual cost of a point read is under two milliseconds, which the loop's own
    backlog moves on its own. The floor is what stops the bar becoming noise."""
    budget = StatementBudget(settled=3)
    for _ in range(3):
        budget.observed("access.one_note", 0.2)
    assert budget.threshold_ms("access.one_note") == pytest.approx(50.0)


@pytest.mark.unit
def test_the_window_forgets_so_a_growing_library_moves_the_bar_with_it() -> None:
    """A statement over a table that has grown a hundredfold is honestly slower. A window that
    never forgot would go on judging today against the day the library was empty."""
    budget = StatementBudget(window=4, settled=4)
    for _ in range(4):
        budget.observed("select:notes#0000", 1.0)
    assert budget.threshold_ms("select:notes#0000") == pytest.approx(50.0)
    for _ in range(4):
        budget.observed("select:notes#0000", 100.0)
    assert budget.threshold_ms("select:notes#0000") == pytest.approx(800.0)


@pytest.mark.unit
def test_only_so_many_statements_are_remembered_and_the_rest_keep_the_flat_bar() -> None:
    """A bound on a mistake rather than on ordinary use: the set of statements is fixed by the
    code. Past it the answer is the flat bar, which is the safe direction."""
    budget = StatementBudget(settled=1, keep=2)
    for name in ("a", "b", "c"):
        budget.observed(name, 1.0)
    assert budget.watching() == 2
    assert budget.threshold_ms("c") == SLOW_QUERY_MS


@pytest.mark.unit
async def test_a_read_teaches_the_budget_what_it_costs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wiring, which nothing else would catch: a budget nobody reports to answers the flat bar
    for ever and reads exactly like having no budget at all."""
    budget = StatementBudget(settled=2)
    monkeypatch.setattr(db_judged, "_BUDGET", budget)
    database = Database(tmp_path / "budget.sqlite3")
    await database.connect()
    try:
        for _ in range(3):
            await database.fetch_all("SELECT 1 FROM sqlite_schema WHERE name = ?", ("nothing",))
        assert budget.watching() == 1
    finally:
        await database.close()


@pytest.mark.unit
async def test_a_slow_statement_is_named_in_the_warning_and_its_text_is_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The redaction policy's shape, applied to the noisiest line in the log."""
    from sift.kernel.log import configure_logging

    monkeypatch.setattr(db_judged, "_BUDGET", StatementBudget(unsettled_ms=0.0))
    database = Database(tmp_path / "loud.sqlite3")
    await database.connect()
    configure_logging("INFO", redact_personal=True)
    try:
        await database.fetch_all("SELECT name FROM sqlite_schema WHERE name = ?", ("rude",))
    finally:
        await database.close()
    output = capsys.readouterr().out
    assert '"stage": "db.read"' in output, "a statement over its bar is still escalated"
    assert "select:sqlite_schema#" in output, "and it says which statement it was"
    assert "SELECT name" not in output, "and it does not carry the statement"


@pytest.mark.unit
def test_the_statement_names_are_a_bounded_memo(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every distinct statement text is a key, and a process that builds SQL with its values
    inside would grow the memo forever. Past the bound it starts again rather than growing."""
    monkeypatch.setattr(db_judged, "NAMES_KEPT", 1)
    first = statement_name("SELECT a FROM bounded_one WHERE id = ?")
    second = statement_name("SELECT b FROM bounded_two WHERE id = ?")
    assert first != second
    assert len(db_judged._NAMES) == 1
    # Forgotten is not wrong: the name is worked out again, identically.
    assert statement_name("SELECT a FROM bounded_one WHERE id = ?") == first


@pytest.mark.unit
def test_the_process_budget_is_one_object_and_forgetting_empties_it() -> None:
    assert statement_budget() is db_judged._BUDGET
    budget = StatementBudget(settled=1)
    budget.observed("select:notes#1", 3.0)
    assert budget.watching() == 1
    budget.forget()
    assert budget.watching() == 0
