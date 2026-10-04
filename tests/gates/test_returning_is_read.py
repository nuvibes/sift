# SPDX-License-Identifier: AGPL-3.0-or-later
"""A RETURNING statement whose rows nobody reads cannot be committed.

SQLite keeps a statement with a RETURNING clause IN PROGRESS until its rows are stepped through, so
an `execute` that ignores them fails the commit ("SQL statements in progress") and takes the write
with it. The shape reads like any other write; `execute_fetchall` is the fix this keeps.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.gate, pytest.mark.unit]

SOURCE = Path(__file__).resolve().parents[2] / "src" / "sift"

#: A module-level SQL constant, as this project writes them.
_CONSTANT = re.compile(r'^(_[A-Z0-9_]+)\s*=\s*(?:"""|")(.*?)(?:"""|")\s*$', re.S | re.M)


def _returning_constants(text: str) -> set[str]:
    """The SQL constants in one file whose statement carries a RETURNING clause, read from the
    assignment: the call site names only the constant."""
    found = set()
    for match in _CONSTANT.finditer(text):
        statement = match.group(2)
        # The clause is written in capitals, a comment's mention is not.
        if re.search(r"\bRETURNING\b", statement):
            found.add(match.group(1))
    return found


def test_no_returning_statement_is_run_without_its_rows_being_read() -> None:
    unread: list[str] = []
    for path in sorted(SOURCE.rglob("*.py")):
        if "tests" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        names = _returning_constants(text)
        if not names:
            continue
        for name in names:
            for match in re.finditer(rf"\bexecute\(\s*{name}\b", text):
                line = text[: match.start()].count("\n") + 1
                unread.append(f"{path.relative_to(SOURCE.parents[1])}:{line} runs {name}")

    assert not unread, (
        "\nThese run a RETURNING statement with `execute` and never read the rows, so the statement\n"
        "is still in progress when the transaction commits, which raises and loses the write.\n"
        "Use `execute_fetchall` instead.\n\n  " + "\n  ".join(unread) + "\n"
    )


def test_the_check_can_tell_when_one_is_not_read() -> None:
    """A planted unread statement is caught, from text."""
    planted = (
        '_SET_IT = "UPDATE things SET name = ? WHERE id = ? RETURNING id"\n'
        "\n"
        "async def write(connection, name, thing_id):\n"
        "    await connection.execute(_SET_IT, (name, thing_id))\n"
    )

    assert _returning_constants(planted) == {"_SET_IT"}
    assert re.search(r"\bexecute\(\s*_SET_IT\b", planted)

    read_properly = planted.replace("connection.execute(", "connection.execute_fetchall(")
    assert not re.search(r"(?<!_fetchall)\bexecute\(\s*_SET_IT\b", read_properly)


def test_a_statement_with_no_returning_clause_is_not_reported() -> None:
    """A write with no RETURNING clause is left alone."""
    plain = '_SET_IT = "UPDATE things SET name = ? WHERE id = ?"\n'

    assert _returning_constants(plain) == set()


def test_prose_about_returning_something_is_not_a_clause() -> None:
    """Prose saying a query "returns" something is not a clause."""
    commented = (
        '_MAKE_IT = """\nCREATE TABLE things (\n  -- a search returning none\n  id TEXT\n)\n"""\n'
    )

    assert _returning_constants(commented) == set()
