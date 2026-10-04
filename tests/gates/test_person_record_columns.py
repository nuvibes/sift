# SPDX-License-Identifier: AGPL-3.0-or-later
"""A person's declared fields and their actual columns, held to each other.

The registry, the `people` table and `PROMOTED` (the pairing) must agree: a field with no column is
an empty row for everybody, a column no field claims is data nothing shows. The fields left out of
the pairing are named, each for its own reason: `name` and `details` are written by their own
statement; `aliases`, `links` and `tags` are tables.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from sift.kernel.records import Subject, fields_of
from sift.slices.people.service import PROMOTED

pytestmark = [pytest.mark.gate, pytest.mark.unit]

#: Declared fields that are deliberately not promoted columns, and what each one is instead.
_ELSEWHERE = {
    "name": "its own column, written by the statement that renames somebody",
    "details": "the `notes` column, written by the same statement",
    "aliases": "the `people_aliases` table",
    "links": "the `people_links` table",
    "tags": "the `person_tags` table",
    "sources": "the `person_stash_box_links` table, which the stash-box slice owns",
    "accounts": "the `usernames` table: a username is a row of its own, not a column",
    "age": "nothing: it is arithmetic on the birthdate, worked out on every read and never stored",
}


def _schema() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "src" / "sift" / "kernel" / "access" / "schema.py"
        if candidate.is_file():
            return candidate
    raise AssertionError("could not find the catalog schema from the test file")


def _people_columns() -> set[str]:
    """Every column the `people` table has, from its CREATE and every ALTER in the schema source."""
    source = _schema().read_text(encoding="utf-8")
    columns: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        # A SQL comment inside the statement would otherwise run into the next column.
        text = " ".join(re.sub(r"--[^\n]*", "", node.value).split())
        if text.startswith("ALTER TABLE people ADD COLUMN "):
            columns.add(text.removeprefix("ALTER TABLE people ADD COLUMN ").split()[0])
        elif text.startswith("CREATE TABLE IF NOT EXISTS people ("):
            inside = text[text.index("(") + 1 : text.rindex(")")]
            columns.update(one.split()[0] for one in inside.split(",") if one.split())
    assert "name" in columns, "read no columns at all: the parsing above has gone stale"
    return columns


def test_every_promoted_field_is_declared() -> None:
    declared = {one.key for one in fields_of(Subject.PERSON)}
    for key, _column in PROMOTED:
        assert key in declared, (
            f"`{key}` is written to a column and no field declares it, so nothing can ever show it"
        )


def test_every_declared_field_has_somewhere_to_live() -> None:
    promoted = {key for key, _column in PROMOTED}
    for one in fields_of(Subject.PERSON):
        if one.key in _ELSEWHERE:
            continue
        assert one.key in promoted, (
            f"`{one.key}` is declared on a person's record and has nowhere to be kept."
            " Add it to PROMOTED with its column, or to _ELSEWHERE saying what holds it instead."
        )


def test_every_promoted_column_exists() -> None:
    columns = _people_columns()
    for key, column in PROMOTED:
        assert column in columns, (
            f"`{key}` is paired with a `people.{column}` that the schema never creates"
        )


def test_nothing_is_left_out_by_accident() -> None:
    """Every name in the exception list is really a field."""
    declared = {one.key for one in fields_of(Subject.PERSON)}
    for key in _ELSEWHERE:
        assert key in declared, f"`{key}` is excused from needing a column and is not a field"
