# SPDX-License-Identifier: AGPL-3.0-or-later
"""A listing is ordered by the row's id, never by the wall clock, unless it says why it cannot be.

`created_at` is a wall-clock second, and a clock corrected while Sift runs steps backwards, so two
rows list the wrong way round. An id from `kernel.ids.new_id` is minted under a floor that never
goes down, so it holds the order rows were made in. Every SQL string in `src/sift` whose ORDER BY
names a `created_at` carries `-- ordered by the clock: <why the id cannot order it>`: a table with
no id of its own, ids not minted by `new_id`, or the moment itself being the answer. `REASONED` is
how many do, and it may only fall.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.gate, pytest.mark.unit]

REPO = Path(__file__).resolve().parents[2]
SOURCE = REPO / "src" / "sift"

#: How many `ORDER BY` clauses in `src/sift` order by the clock with a reason given. May only fall.
REASONED = 8

_ORDER_BY = re.compile(r"\bORDER\s+BY\b", re.IGNORECASE)

#: The end of an ORDER BY clause at its own depth: what may follow one in a SQLite statement.
_CLAUSE_END = re.compile(r"\b(?:LIMIT|OFFSET)\b|;", re.IGNORECASE)

#: A `created_at` column as a sort key, bare or qualified (`g.created_at`), not a longer name.
_CLOCK_COLUMN = re.compile(r"(?<![\w])created_at\b", re.IGNORECASE)

#: The reason, with at least three words after it: the marker alone says nothing.
_REASON = re.compile(r"--[ \t]*ordered by the clock:[ \t]*\S+[ \t]+\S+[ \t]+\S+", re.IGNORECASE)

_SQL_COMMENT = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)


def _order_clauses(sql: str) -> list[str]:
    """Every ORDER BY clause in one SQL string, each to where it ends at its own depth (a LIMIT or
    OFFSET, a `;`, or the `)` closing its window or subquery)."""
    code = _SQL_COMMENT.sub(" ", sql)
    clauses = []
    for found in _ORDER_BY.finditer(code):
        depth = 0
        end = len(code)
        for index in range(found.end(), len(code)):
            char = code[index]
            if char == "(":
                depth += 1
            elif char == ")":
                if depth == 0:
                    end = index
                    break
                depth -= 1
            elif depth == 0 and _CLAUSE_END.match(code, index):
                end = index
                break
        clauses.append(code[found.end() : end])
    return clauses


def _strings(tree: ast.AST) -> list[tuple[int, str]]:
    """Every string constant that is not a docstring or a bare string, with its line."""
    bare = {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
    }
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in bare
    ]


def clock_orders(source: str) -> list[tuple[int, bool]]:
    """`(line, reasoned)` for every ORDER BY on a `created_at` column in one module's source."""
    found = []
    for line, value in _strings(ast.parse(source)):
        clocked = sum(1 for clause in _order_clauses(value) if _CLOCK_COLUMN.search(clause))
        if not clocked:
            continue
        reasons = len(_REASON.findall(value))
        found.extend((line, index < reasons) for index in range(clocked))
    return found


def _tree_orders() -> list[tuple[str, int, bool]]:
    return [
        (str(path.relative_to(REPO)), line, reasoned)
        for path in sorted(SOURCE.rglob("*.py"))
        if "tests" not in path.relative_to(SOURCE).parts
        for line, reasoned in clock_orders(path.read_text(encoding="utf-8"))
    ]


def test_every_listing_ordered_by_the_clock_says_why() -> None:
    unreasoned = [f"{path}:{line}" for path, line, reasoned in _tree_orders() if not reasoned]

    assert unreasoned == [], (
        "these order by `created_at`, a wall clock that can step backwards, and say nothing about"
        " why. Order by the row's id (a ULID `new_id` minted, which never goes back), or, where"
        " the id truly cannot order the rows, say why in the statement: `-- ordered by the clock:"
        " <why>`.\n  " + "\n  ".join(unreasoned)
    )


def test_the_clock_orders_may_only_fall() -> None:
    reasoned = sum(1 for _path, _line, is_reasoned in _tree_orders() if is_reasoned)

    assert reasoned <= REASONED, (
        f"{reasoned} listings order by the clock with a reason, and {REASONED} is the most there may"
        " be. A new one is a new place for rows to list out of order: order it by the id instead."
    )
    assert reasoned == REASONED, (
        f"{reasoned} listings order by the clock now, down from {REASONED}. Record the fall:"
        f" set REASONED = {reasoned} in {Path(__file__).name}."
    )


# --- the gate reads what it claims to read -------------------------------------------------------

#: `name -> (module source, what the gate must answer)`: None for "not a clock order at all".
_PLANTED: dict[str, tuple[str, bool | None]] = {
    "bare": ('S = "SELECT * FROM t ORDER BY created_at, id"', False),
    "qualified": ('S = """\nSELECT * FROM t x\n ORDER BY x.created_at DESC, x.id DESC\n"""', False),
    "a window": ('S = "SELECT ROW_NUMBER() OVER (ORDER BY priority, created_at) FROM t"', False),
    "a subquery": (
        'S = "SELECT (SELECT a FROM u ORDER BY u.created_at LIMIT 1) AS a FROM t ORDER BY t.id"',
        False,
    ),
    "a fold": ('S = "SELECT * FROM t ORDER BY COALESCE(started_at, created_at) DESC"', False),
    "split": ('S = (\n    "SELECT * FROM t"\n    " ORDER BY created_at"\n)', False),
    "a reason": (
        'S = "SELECT * FROM t\\n -- ordered by the clock: the table has no id\\n ORDER BY created_at"',
        True,
    ),
    "an empty reason": (
        'S = "SELECT * FROM t\\n -- ordered by the clock:\\n ORDER BY created_at"',
        False,
    ),
    "a docstring": ('"""ORDER BY created_at is prose here."""', None),
    "by id": ('S = "SELECT created_at FROM t ORDER BY id"', None),
    "another column": ('S = "SELECT * FROM t ORDER BY first_seen_at, recreated_at, id"', None),
    "a comment": ('S = "SELECT * FROM t\\n -- not ORDER BY created_at\\n ORDER BY id"', None),
}


@pytest.mark.parametrize("name", sorted(_PLANTED))
def test_the_gate_reads_every_shape_a_clock_order_is_written_in(name: str) -> None:
    source, expected = _PLANTED[name]

    found = clock_orders(source)

    if expected is None:
        assert found == [], f"{name}: not a clock order, and the gate read one"
    else:
        assert [reasoned for _line, reasoned in found] == [expected], (
            f"{name}: the gate answered {found}, and should have answered one order, "
            + ("reasoned" if expected else "with no reason")
        )
