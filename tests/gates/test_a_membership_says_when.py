# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every write of a membership names its moment (`added_at`, or `sites.created_at`): the columns
are NULLABLE for rows older than them, so a forgetful write silently reads as old. Read from the SQL
text as `test_one_ordering.py` reads it, exempting an `INSERT ... SELECT` before the column existed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from sift.kernel.access.schema import _ADDED_AT_COLUMNS

pytestmark = [pytest.mark.gate, pytest.mark.unit]

REPO = Path(__file__).resolve().parents[2]
SOURCE = REPO / "src" / "sift"

#: `table -> column` from the schema's own list.
WHEN = dict(_ADDED_AT_COLUMNS)

#: Adjacent Python string literals joined, so a split statement reads as one.
_JOINED = re.compile(r'"\s*\n\s*"')

_INSERT = re.compile(r"INSERT (?:OR IGNORE )?INTO (\w+)\s*\(([^)]*)\)", re.S)


def _guilty() -> list[str]:
    found: list[str] = []
    for path in sorted(SOURCE.rglob("*.py")):
        # Shared fixtures (`src/sift/testing/`) count, as in `test_one_ordering.py`.
        if "tests" in path.parts:
            continue
        text = _JOINED.sub("", path.read_text(encoding="utf-8", errors="replace"))
        # `OR IGNORE` too: the Photo Set fold steps run before the column exists.
        text = re.sub(r"INSERT (?:OR IGNORE )?INTO \w+ \([^)]*\)\s*SELECT", "", text)
        for match in _INSERT.finditer(text):
            table, columns = match.group(1), match.group(2)
            column = WHEN.get(table)
            if column is None or re.search(rf"(?<![\w.]){column}\b", columns):
                continue
            found.append(f"{path.relative_to(REPO)}  INSERT {table}  ({columns.strip()[:70]})")
    return found


def test_every_write_of_a_membership_writes_when_it_was_made() -> None:
    guilty = _guilty()
    assert not guilty, (
        "these statements write a row whose moment the schema records and do not set it, so the\n"
        "rows they write read as having been made before anything wrote a moment down:\n\n  "
        + "\n  ".join(guilty)
        + "\n\nName the column in the statement and pass the moment beside it."
    )


def test_the_gate_can_see_a_statement_that_forgets() -> None:
    """A known positive: a regex that matched nothing would pass every run."""
    table, column = next(iter(WHEN.items()))
    # nosemgrep: sift-no-string-built-sql
    text = _JOINED.sub("", f'"INSERT INTO {table} (a, b) VALUES (?, ?)"')  # noqa: S608
    match = _INSERT.search(text)
    assert match is not None
    assert match.group(1) == table
    assert not re.search(rf"(?<![\w.]){column}\b", match.group(2))
