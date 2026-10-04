# SPDX-License-Identifier: AGPL-3.0-or-later
"""The schema a library has is pinned whole, so it cannot change without a version moving.

`CREATE TABLE IF NOT EXISTS` is the whole of a component's first step, and the clause that makes it
safe to re-run is the clause that makes it blind: on a database that already has the table it does
nothing at all. So an edit to the text inside that statement changes what a NEW library gets and
leaves every EXISTING library as it was, with no error at any point in between. A spelling
correction that renames a column inside a CREATE is the classic case: new libraries get the new
name, every existing one keeps the old, and the first write to the column fails.

**So the check is not "is the schema right". It is "did the schema change while the versions stood
still".** `schema_shape.json` records every table (each column with its type and constraints, and
the table's own constraints), every index, every virtual table and view, every trigger by the table
it is on, the rows a new library starts with, and the version of every component. Change any of it
and the pin disagrees; the two honest ways out are to put it back, or to raise the component's
version, add an `if 0 < on_disk < N:` step that brings an existing library across, and update the
pin in the same commit.

What is deliberately NOT pinned:

- Column ORDER. A column an existing library gains by `ALTER TABLE` lands at the end of its table
  while a new library's CREATE may name it anywhere, and nothing reads a column by position.
- The spelling of the SQL: comments, quoting, case and spacing. SQLite keeps the text a table was
  made with, so a rename or an added column changes the text without changing the table.
- A trigger's body. The triggers are written by the boot's invariants (`visibility.keep_true`,
  `waiting.keep_true`), which rewrite any whose text differs at every start, so a changed body
  reaches every library without a version; only which triggers exist, and on what, is pinned.

The same pin holds the library an install opens by default, where the tests run beside one: a
library whose every component is at the pinned version has exactly the pinned schema. That is the
property each component's first step depends on, since it creates the pinned shape and nothing
brings a library at that version any further.
"""

from __future__ import annotations

import json
import sqlite3  # nosemgrep: sift-no-database-driver-outside-kernel
from pathlib import Path
from typing import Any

import pytest

import sift.main  # noqa: F401 (imported so every component registers itself)
from sift.kernel.config import Settings
from sift.kernel.db import Database, library_database, registered_components

pytestmark = pytest.mark.gate

PIN = Path(__file__).parent / "schema_shape.json"

#: The tables SQLite makes for a full-text index: its own storage, not part of anybody's schema.
_FTS5_SHADOWS = ("data", "idx", "content", "docsize", "config")

#: Virtual tables a feature makes when it first stores something, not when the schema is built. A
#: new library has none of them, so they and the storage their module makes beside them are left
#: out of the comparison rather than pinned.
MADE_ON_FIRST_USE = ("semantic_frames",)

#: A table constraint begins with one of these words; anything else in the list is a column.
_CONSTRAINT_WORDS = frozenset({"constraint", "primary", "unique", "check", "foreign"})

_TWO_CHARACTER_OPERATORS = ("<=", ">=", "<>", "!=", "==", "||", "<<", ">>", "->")


def tokens(sql: str) -> list[str]:
    """SQL as its words: comments dropped, identifiers unquoted and lowercased, literals kept."""
    out: list[str] = []
    at, end = 0, len(sql)
    while at < end:
        char = sql[at]
        if char.isspace():
            at += 1
        elif sql.startswith("--", at):
            newline = sql.find("\n", at)
            at = end if newline < 0 else newline + 1
        elif sql.startswith("/*", at):
            close = sql.find("*/", at + 2)
            at = end if close < 0 else close + 2
        elif char == "'":
            close = at + 1
            while True:
                close = sql.find("'", close)
                if close < 0 or not sql.startswith("''", close):
                    break
                close += 2
            close = end - 1 if close < 0 else close
            out.append(sql[at : close + 1])
            at = close + 1
        elif char in '"`[':
            close = sql.find("]" if char == "[" else char, at + 1)
            out.append(sql[at + 1 : close].lower())
            at = close + 1
        elif char.isalnum() or char == "_":
            stop = at
            while stop < end and (sql[stop].isalnum() or sql[stop] in "_$"):
                stop += 1
            out.append(sql[at:stop].lower())
            at = stop
        elif sql[at : at + 2] in _TWO_CHARACTER_OPERATORS:
            out.append(sql[at : at + 2])
            at += 2
        else:
            out.append(char)
            at += 1
    return out


def _table(sql: str) -> dict[str, Any]:
    """One CREATE TABLE as its columns by name, its table constraints, and what follows the list."""
    words = tokens(sql)
    items: list[list[str]] = [[]]
    depth = 0
    tail: list[str] = []
    for position, word in enumerate(words):
        if word == "(":
            depth += 1
            if depth == 1:
                continue
        elif word == ")":
            depth -= 1
            if depth == 0:
                tail = words[position + 1 :]
                break
        if depth == 1 and word == ",":
            items.append([])
        elif depth >= 1:
            items[-1].append(word)
    columns = {item[0]: " ".join(item[1:]) for item in items if item[0] not in _CONSTRAINT_WORDS}
    constraints = sorted(" ".join(item) for item in items if item[0] in _CONSTRAINT_WORDS)
    return {"columns": columns, "constraints": constraints, "options": " ".join(tail)}


def _rows(connection: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    """A table's rows as column-to-value maps, in an order that does not depend on storage."""
    # The name comes out of the database's own catalog, never from a caller.
    cursor = connection.execute(f'SELECT * FROM "{table}"')  # noqa: S608  # nosemgrep: sift-no-string-built-sql
    names = [column[0] for column in cursor.description]
    found = [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]
    return sorted(found, key=lambda row: json.dumps(row, sort_keys=True, default=str))


def describe(connection: sqlite3.Connection, *, with_rows: bool = True) -> dict[str, Any]:
    """Everything a library's schema is, in a form where column order and spelling do not count.

    Automatic indexes are described by the constraint that makes them, a full-text index's own
    storage by the index, and a trigger by the table it is on (see the module docstring). The rows
    are read only when asked for: a new library's are part of its shape, and a library in use holds
    somebody's data, which a comparison of shapes has no reason to read.
    """
    rows = connection.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master"
        " WHERE name NOT LIKE 'sqlite_%' AND sql IS NOT NULL ORDER BY type, name"
    ).fetchall()
    virtual = {name for kind, name, _on, sql in rows if kind == "table" and _is_virtual(sql)}
    shadows = {f"{name}_{suffix}" for name in virtual for suffix in _FTS5_SHADOWS}
    first_use = tuple(f"{name}_" for name in MADE_ON_FIRST_USE)
    shape: dict[str, Any] = {
        "tables": {},
        "virtual": {},
        "indexes": {},
        "triggers": {},
        "views": {},
        "rows": {},
    }
    for kind, name, on, sql in rows:
        if name in shadows or name in MADE_ON_FIRST_USE or name.startswith(first_use):
            continue
        if kind == "table" and name in virtual:
            shape["virtual"][name] = " ".join(tokens(sql))
        elif kind == "table":
            shape["tables"][name] = _table(sql)
            held = _rows(connection, name) if with_rows and name != "schema_version" else []
            if held:
                shape["rows"][name] = held
        elif kind == "trigger":
            shape["triggers"][name] = on
        elif kind == "index":
            shape["indexes"][name] = " ".join(tokens(sql))
        else:
            shape["views"][name] = " ".join(tokens(sql))
    return shape


def _is_virtual(sql: str) -> bool:
    return tokens(sql)[:3] == ["create", "virtual", "table"]


def differences(pinned: Any, actual: Any, path: str = "") -> list[str]:
    """Every place two descriptions disagree, one line each, named by where in the schema."""
    if isinstance(pinned, dict) and isinstance(actual, dict):
        found: list[str] = []
        for key in sorted(set(pinned) | set(actual)):
            if key not in actual:
                found.append(f"{path}/{key} is pinned and missing: {pinned[key]}")
            elif key not in pinned:
                found.append(f"{path}/{key} is new: {actual[key]}")
            else:
                found.extend(differences(pinned[key], actual[key], f"{path}/{key}"))
        return found
    if pinned != actual:
        return [f"{path} changed\n      pinned: {pinned}\n      actual: {actual}"]
    return []


def _versions(connection: sqlite3.Connection) -> dict[str, int]:
    rows = connection.execute("SELECT component, version FROM schema_version").fetchall()
    return {str(name): int(version) for name, version in rows}


async def _new_library(tmp_path: Path) -> dict[str, Any]:
    """What a new library is: every component brought up from nothing, as a boot does."""
    target = tmp_path / "new.sqlite3"
    database = Database(target)
    await database.connect()
    try:
        await database.initialize_schema()
    finally:
        await database.close()
    connection = sqlite3.connect(f"{target.absolute().as_uri()}?mode=ro", uri=True)
    try:
        return {"versions": _versions(connection), **describe(connection)}
    finally:
        connection.close()


def _pinned() -> dict[str, Any]:
    pinned: dict[str, Any] = json.loads(PIN.read_text(encoding="utf-8"))
    return pinned


@pytest.mark.regression
async def test_a_new_library_has_the_pinned_schema(tmp_path: Path) -> None:
    """The gate itself: a new library, whole, against the pin, versions included."""
    found = differences(_pinned(), await _new_library(tmp_path))
    registered = {name: one.version for name, one in registered_components().items()}
    assert not found, (
        "the schema a new library gets is not the pinned one. An existing library will NOT get a "
        "change to a CREATE, because CREATE ... IF NOT EXISTS does not look inside a table it "
        "finds.\n\n  "
        + "\n  ".join(found)
        + "\n\nTo fix: raise the version of the component that owns it, add an independent "
        "`if 0 < on_disk < N:` step that brings an existing library across, and write the new "
        f"description to {PIN.name} in the same commit (the versions registered now are "
        f"{json.dumps(registered, sort_keys=True)})."
    )


def _installed_library() -> Path:
    """The database of the library an install opens by default, from the default data folder."""
    default = Settings.model_fields["data_dir"].default
    return library_database(Path(default))


@pytest.mark.regression
def test_the_library_here_has_the_pinned_schema() -> None:
    """A library at the pinned versions has the pinned schema, read without changing anything.

    What each component's first step relies on: it creates the pinned shape, so a library that
    reached a version by the steps before it has to hold the same shape, or it and a new library
    part ways for good. Rows are neither read nor compared: this library has data.
    """
    database = _installed_library()
    if not database.is_file():
        pytest.skip(f"no installed library on this machine at {database}, so nothing to compare")
    connection = sqlite3.connect(f"{database.absolute().as_uri()}?mode=ro", uri=True)
    try:
        versions = _versions(connection)
        if versions != _pinned()["versions"]:
            pytest.skip(
                "the installed library is at other versions than the pin, so its next start brings "
                "it forward and there is nothing to compare yet"
            )
        here = describe(connection, with_rows=False)
    finally:
        connection.close()
    pinned = {key: value for key, value in _pinned().items() if key not in ("versions", "rows")}
    found = differences(pinned, {key: value for key, value in here.items() if key != "rows"})
    assert not found, (
        "the installed library is at the pinned versions and does not have the pinned schema. A "
        "step is owed that brings a library at these versions to the shape a new one gets:\n  "
        + "\n  ".join(found)
    )


def _described(*statements: str) -> dict[str, Any]:
    connection = sqlite3.connect(":memory:")
    try:
        for statement in statements:
            connection.execute(statement)
        return describe(connection)
    finally:
        connection.close()


def test_column_order_quoting_and_comments_do_not_count() -> None:
    """The same table, made one way and brought to the same shape another: no difference."""
    made = _described(
        "CREATE TABLE t (id TEXT PRIMARY KEY, name TEXT NOT NULL, added INTEGER,"
        " CHECK (added >= 0))"
    )
    altered = _described(
        'CREATE TABLE "t" (\n  id TEXT PRIMARY KEY, -- the key\n  added INTEGER,\n'
        "  CHECK (added >= 0)\n)",
        "ALTER TABLE t ADD COLUMN name TEXT NOT NULL DEFAULT ''",
    )
    assert differences(made, made) == []
    assert differences(made, altered) == [
        "/tables/t/columns/name changed\n      pinned: text not null\n"
        "      actual: text not null default ''"
    ], "only the default a column added by ALTER must carry may differ"


def test_the_pin_notices_a_renamed_column_and_a_changed_constraint() -> None:
    """The edits a CREATE can take that an existing library never sees, planted: a column renamed,
    and a constraint added to a column or to the table."""
    pinned = _described("CREATE TABLE face_scans (id TEXT PRIMARY KEY, recognizer TEXT)")
    renamed = _described("CREATE TABLE face_scans (id TEXT PRIMARY KEY, recogniser TEXT)")
    checked = _described(
        "CREATE TABLE face_scans (id TEXT PRIMARY KEY,"
        " recognizer TEXT CHECK (recognizer IN ('a','b')))"
    )
    assert differences(pinned, renamed) == [
        "/tables/face_scans/columns/recogniser is new: text",
        "/tables/face_scans/columns/recognizer is pinned and missing: text",
    ]
    assert [line.split(" changed")[0] for line in differences(pinned, checked)] == [
        "/tables/face_scans/columns/recognizer"
    ]
    # A constraint of the table's own, written after its columns rather than on one of them.
    held_once = _described(
        "CREATE TABLE face_scans (id TEXT PRIMARY KEY, recognizer TEXT, UNIQUE (recognizer))"
    )
    assert [line.split(" changed")[0] for line in differences(pinned, held_once)] == [
        "/tables/face_scans/constraints"
    ]


def test_a_trigger_is_pinned_by_its_table_and_not_its_body() -> None:
    """A trigger the boot rewrites may change its body freely; one that appears or moves may not."""
    table = "CREATE TABLE t (id TEXT PRIMARY KEY, n INTEGER)"
    one = _described(table, "CREATE TRIGGER t_in AFTER INSERT ON t BEGIN SELECT 1; END")
    other_body = _described(table, "CREATE TRIGGER t_in AFTER INSERT ON t BEGIN SELECT 2; END")
    extra = _described(
        table,
        "CREATE TRIGGER t_in AFTER INSERT ON t BEGIN SELECT 1; END",
        "CREATE TRIGGER t_out AFTER DELETE ON t BEGIN SELECT 1; END",
    )
    assert differences(one, other_body) == []
    assert differences(one, extra) == ["/triggers/t_out is new: t"]
