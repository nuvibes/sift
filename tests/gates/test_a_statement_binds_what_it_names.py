# SPDX-License-Identifier: AGPL-3.0-or-later
"""An INSERT that names more columns than it binds fails at the moment somebody presses a button.

SQLite checks a statement only when it runs, so a column added to an `INSERT` without its `?` is an
ordinary string until the request that uses it. This compares the count of named columns with the
`VALUES (...)` items, as text; a wrong value bound is a slice test's business. `INSERT ... SELECT`
is skipped.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.gate, pytest.mark.unit]

REPO = Path(__file__).resolve().parents[2]
SOURCE = REPO / "src" / "sift"

#: Adjacent string literals joined, so a statement split across lines reads as one.
_JOINED = re.compile(r'"\s*\n\s*"')

_HEAD = re.compile(r"INSERT (?:OR (?:IGNORE|REPLACE) )?INTO \w+\s*\(", re.S)


def _balanced(text: str, opened: int) -> tuple[str, int] | None:
    """The text inside the parenthesis opening at `opened`, and where it closes: counted, since a
    VALUES list may hold `(SELECT id FROM sites WHERE name = ?)`."""
    depth, quoted = 0, False
    for i in range(opened, len(text)):
        if text[i] == "'":
            quoted = not quoted
        elif quoted:
            continue
        elif text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[opened + 1 : i], i
    return None


def _count(names: str) -> int:
    """How many items a comma-separated list holds, ignoring commas inside a nested call or a
    quoted literal (`'[1, 2]'` is one value)."""
    items, depth, quoted, current = 0, 0, False, ""
    for ch in names:
        if ch == "'":
            quoted = not quoted
        elif not quoted and ch == "(":
            depth += 1
        elif not quoted and ch == ")":
            depth -= 1
        if ch == "," and depth == 0 and not quoted:
            items += 1 if current.strip() else 0
            current = ""
        else:
            current += ch
    return items + (1 if current.strip() else 0)


def test_every_insert_binds_as_many_values_as_it_names_columns() -> None:
    guilty = []
    for path in sorted(SOURCE.rglob("*.py")):
        text = _JOINED.sub("", path.read_text(encoding="utf-8", errors="replace"))
        for match in _HEAD.finditer(text):
            names = _balanced(text, match.end() - 1)
            if names is None:
                continue
            column_text, after = names
            rest = text[after + 1 : after + 40]
            if not re.match(r"\s*VALUES\s*\(", rest):
                # `INSERT ... SELECT`, or a statement whose VALUES is built elsewhere. Its arity is
                # the SELECT's business and is not a thing this can read.
                continue
            opened = text.index("(", after + 1)
            binding = _balanced(text, opened)
            if binding is None:
                continue
            columns, values = _count(column_text), _count(binding[0])
            if columns == values:
                continue
            line = text[: match.start()].count("\n") + 1
            statement = " ".join(text[match.start() : binding[1] + 1].split())[:90]
            guilty.append(
                f"{path.relative_to(REPO)}:{line}  {columns} columns, {values} values\n"
                f"      {statement}"
            )

    assert not guilty, (
        "these statements name a different number of columns than they bind values, and SQLite\n"
        "will not say so until the moment somebody presses the button that runs one:\n\n  "
        + "\n  ".join(guilty)
    )
