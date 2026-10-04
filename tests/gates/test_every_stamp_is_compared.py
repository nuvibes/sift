# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every version stamp a table keeps is compared by a statement that decides what work is left.

A stamp (a `*_version`, a model `revision`, an `algorithm` number) is written beside a result
so that a better recipe can find the rows made by an older one. Written and never compared, raising
it redoes nothing: every older row still counts as done and no count says otherwise. So each stamp
column must appear in a read that treats an older value as still to do: compared with `<`, `<=`,
`>=`, `<>`, `!=` or `IS NOT` against a bound value, or with `=` inside a `NOT EXISTS`, or against a
number written into the statement.

Read from the source rather than a database, like the other statement gates: the tables are the
`CREATE TABLE` and `ADD COLUMN` statements in the tree, and the reads are every other statement.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from pathlib import Path

import pytest

pytestmark = [pytest.mark.gate, pytest.mark.unit]

REPO = Path(__file__).resolve().parents[2]
SOURCE = REPO / "src" / "sift"

#: What a stamp column is called.
STAMP_NAME = re.compile(r"^(?:\w+_version|revision|algorithm)$")

#: Stamps that are not about a result and so have nothing to compare, each with why.
NOT_RESULTS: dict[tuple[str, str], str] = {
    ("backup_manifest", "format_version"): "the layout of a backup, read when it is opened",
    ("backup_manifest", "app_version"): "which Sift wrote a backup, shown beside it",
    ("face_weights", "revision"): "which revision of a model file was downloaded",
    ("semantic_frames", "revision"): "the key the vectors of each model revision are kept under; "
    "which files are described at the current one is semantic_indexed.revision",
    ("semantic_pooled", "revision"): "the same key, for the pooled vectors",
    ("watermark_reads", "revision"): "copied from the file's scan row in the same write; the "
    "count compares watermark_scans.revision",
}

#: Stamps no count reads yet. Each is a fault; the gate fails when one is fixed and still listed.
OWED: dict[tuple[str, str], str] = {}

_CREATE = re.compile(
    r"CREATE\s+(?:VIRTUAL\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s*(?:USING\s+\w+\s*)?\((.*)\)",
    re.IGNORECASE | re.DOTALL,
)
_ADD_COLUMN = re.compile(r"ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+(\w+)", re.IGNORECASE)
#: The first word of each column definition: at the start of the body or after a comma.
_COLUMN = re.compile(r"(?:^|,)\s*(\w+)\b")
_BOUND = r"(?:\?|:\w+)"


def _strings(path: Path) -> Iterable[str]:
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value


def _modules() -> list[Path]:
    return [path for path in SOURCE.rglob("*.py") if "tests" not in path.parts]


def stamps(sources: Iterable[str]) -> set[tuple[str, str]]:
    """Every (table, column) whose column is named like a stamp, from the statements given."""
    found: set[tuple[str, str]] = set()
    for text in sources:
        for table, body in _CREATE.findall(text):
            for column in _COLUMN.findall(body):
                if STAMP_NAME.match(column):
                    found.add((table, column))
        for table, column in _ADD_COLUMN.findall(text):
            if STAMP_NAME.match(column):
                found.add((table, column))
    return found


def _is_writer(statement: str) -> bool:
    opening = statement.lstrip().split(None, 1)[:1]
    return bool(opening) and opening[0].upper() in {"INSERT", "UPDATE", "CREATE", "ALTER", "DROP"}


def compared(table: str, column: str, statement: str) -> bool:
    """Whether this statement reads `column` of `table` as a staleness test."""
    if _is_writer(statement) or not re.search(rf"\b{table}\b", statement):
        return False
    name = rf"(?:\b\w+\.)?\b{column}\b"
    if re.search(rf"{name}\s*(?:<=|>=|<>|!=|<|>|IS\s+NOT)\s*{_BOUND}", statement, re.IGNORECASE):
        return True
    if re.search(rf"{name}\s*=\s*\d+\b", statement):
        return True
    return "NOT EXISTS" in statement.upper() and bool(
        re.search(rf"{name}\s*=\s*{_BOUND}", statement)
    )


def uncompared(sources: list[str]) -> set[tuple[str, str]]:
    """The stamps no statement among these compares."""
    return {
        (table, column)
        for table, column in stamps(sources)
        if not any(compared(table, column, one) for one in sources)
    }


def _tree() -> list[str]:
    return [text for path in _modules() for text in _strings(path)]


def test_every_stamp_is_compared_by_a_count() -> None:
    missing = uncompared(_tree()) - set(NOT_RESULTS) - set(OWED)
    assert not missing, (
        "These stamp columns are written and never compared by a read that decides what work is "
        f"left, so raising one redoes nothing: {sorted(missing)}. Compare the stamp in the "
        "product's lacking count, or name it in NOT_RESULTS with why it is not about a result."
    )


def test_an_owed_stamp_is_still_owed() -> None:
    fixed = set(OWED) - uncompared(_tree())
    assert not fixed, f"Compared now, take these out of OWED: {sorted(fixed)}"


def test_every_listed_stamp_exists() -> None:
    found = stamps(_tree())
    stale = (set(NOT_RESULTS) | set(OWED)) - found
    assert not stale, f"Listed but no longer in any table: {sorted(stale)}"


def test_the_gate_refuses_a_stamp_only_written() -> None:
    """The known positive: a stamp written by a writer and counted without it is refused."""
    table = "CREATE TABLE IF NOT EXISTS widgets (id TEXT, recipe_version INTEGER NOT NULL)"
    write = "INSERT INTO widgets (id, recipe_version) VALUES (?, ?)"
    count = "SELECT COUNT(*) FROM widgets WHERE recipe_version = ?"
    assert uncompared([table, write, count]) == {("widgets", "recipe_version")}
    stale = "SELECT id FROM things t WHERE NOT EXISTS (SELECT 1 FROM widgets w WHERE w.recipe_version >= ?)"
    assert uncompared([table, write, stale]) == set()
