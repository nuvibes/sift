# SPDX-License-Identifier: AGPL-3.0-or-later
"""The SQL in this tree parses on the oldest SQLite Sift will run on: it uses the machine's SQLite,
so newer syntax fails only elsewhere. Checked by reading, to fail where it cannot be reproduced."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.gate, pytest.mark.unit]

REPO = Path(__file__).resolve().parents[2]

SEARCHED = ("src", "tests", "scripts")

# A string is treated as SQL when it opens with a statement keyword. Looser than that (matching
# the keyword anywhere) turns every sentence containing the word "update" into SQL.
SQL_OPENING = re.compile(
    r"^\s*(SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|PRAGMA|WITH|REPLACE)\b", re.IGNORECASE
)

# 1_000 and friends. Python reads the separator; SQLite only learned to in 3.46, and the versions
# below that are still shipping in supported distributions.
GROUPED_NUMBER = re.compile(r"\b\d+_\d")


def _sql_strings(source: str) -> list[str]:
    """Every string constant in a module that reads as a SQL statement."""
    return [
        node.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and SQL_OPENING.search(node.value)
    ]


def test_no_sql_uses_a_digit_separator() -> None:
    """No numeric literal carries `_` in SQL (`duration_ms = 300_000`), which is right in Python a
    line higher."""
    offenders = []
    for directory in SEARCHED:
        for path in (REPO / directory).rglob("*.py"):
            for statement in _sql_strings(path.read_text()):
                found = GROUPED_NUMBER.search(statement)
                if found:
                    offenders.append(f"{path.relative_to(REPO)}: {found.group(0)}")

    assert offenders == [], (
        "SQLite before 3.46 cannot read a digit separator, so these are a syntax error there: "
        f"{offenders}"
    )
