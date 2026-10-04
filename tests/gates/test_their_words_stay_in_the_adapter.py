# SPDX-License-Identifier: AGPL-3.0-or-later
"""The stash-boxes Sift asks have their own words for things Sift already has names for.

A stash-box calls a person a performer, a Site a studio and a video a scene; the adapter translates,
and past it everything is in Sift's words. `test_one_word_per_thing.py` holds the copy; this holds
what becomes permanent: column names, wire-model field names, route paths, and the values a CHECK
constraint allows (the schema's own vocabulary). A row holding the word because a box sent it is a
fact about the world and not checked.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.gate, pytest.mark.unit]

REPO = Path(__file__).resolve().parents[2]
SOURCE = REPO / "src" / "sift"

#: The one file allowed to speak their language.
ADAPTER = SOURCE / "slices" / "stash_boxes" / "adapter.py"

#: Their word, and what Sift calls the same thing.
THEIR_WORD: dict[str, str] = {
    "performer": "person",
    "studio": "site",
    "scene": "asset (a file)",
}

_WORD = re.compile(r"\b(" + "|".join(THEIR_WORD) + r")s?\b", re.IGNORECASE)

#: The current shape of every table, read from the schema gate's pin rather than the migration
#: steps, which record old names that later steps renamed.
PIN = Path(__file__).with_name("schema_shape.json")


def _offending(name: str) -> str | None:
    """Their word inside this name, or None, matched on word parts rather than substrings."""
    parts = re.split(r"[_\W]+", name)
    for part in parts:
        folded = part.lower().rstrip("s") if len(part) > 1 else part.lower()
        if folded in THEIR_WORD:
            return folded
    return None


#: The values a `CHECK(x IN ('a','b'))` will accept.
_CONSTRAINT = re.compile(r"CHECK\s*\([^)]*?\bIN\s*\(([^)]*)\)", re.IGNORECASE | re.DOTALL)
_QUOTED = re.compile(r"'([^']*)'")


def _constrained_values(source: str) -> list[str]:
    """Every word a CHECK constraint in this text will accept."""
    return [value for allowed in _CONSTRAINT.findall(source) for value in _QUOTED.findall(allowed)]


def _python_files() -> list[Path]:
    return [
        path
        for path in SOURCE.rglob("*.py")
        if path != ADAPTER and "tests" not in path.parts and path.name != "conftest.py"
    ]


@pytest.mark.regression
def test_no_column_is_named_in_their_words() -> None:
    complaints: list[str] = []
    tables = json.loads(PIN.read_text(encoding="utf-8"))["tables"]
    for table, columns in sorted(tables.items()):
        for column in columns:
            word = _offending(column)
            if word is not None:
                complaints.append(
                    f"{table}.{column} is named for {word!r}; Sift calls it {THEIR_WORD[word]}"
                )

    assert not complaints, (
        "\nA column is named in a stash-box's vocabulary.\n\n"
        "It is the hardest one to take back: every query, every screen and every client that\n"
        "ever reads it inherits the word, and correcting it costs a migration.\n\n  "
        + "\n  ".join(complaints)
    )


@pytest.mark.regression
def test_no_wire_field_is_named_in_their_words() -> None:
    """No wire field is named in their words; their word stops at the adapter's own types."""
    complaints: list[str] = []
    for path in _python_files():
        source = path.read_text(encoding="utf-8")
        if not _WORD.search(source):
            continue
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.ClassDef):
                continue
            bases = {
                base.id if isinstance(base, ast.Name) else getattr(base, "attr", "")
                for base in node.bases
            }
            # Pydantic models are what a client is handed; a slice's plain dataclass is internal.
            if "BaseModel" not in bases:
                continue
            for statement in node.body:
                if not isinstance(statement, ast.AnnAssign) or not isinstance(
                    statement.target, ast.Name
                ):
                    continue
                word = _offending(statement.target.id)
                if word is not None:
                    complaints.append(
                        f"{path.relative_to(REPO)}: {node.name}.{statement.target.id} is named"
                        f" for {word!r}; Sift calls it {THEIR_WORD[word]}"
                    )

    assert not complaints, (
        "\nA field a client reads is named in a stash-box's vocabulary.\n\n"
        "The translation is the adapter's job and it did not happen. Rename the field; the\n"
        "adapter is where their word is allowed to appear.\n\n  " + "\n  ".join(complaints)
    )


@pytest.mark.regression
def test_no_check_constraint_allows_one_of_their_words() -> None:
    """No CHECK constraint allows one of their words. Read from the schema statements, since the pin
    records no constraints: a step adding a constraint adds it now. Only what is inside
    `CHECK(... IN (...))` counts, not a `CASE WHEN` comparison."""
    complaints: list[str] = []
    for path in SOURCE.rglob("schema.py"):
        for value in _constrained_values(path.read_text(encoding="utf-8")):
            word = _offending(value)
            if word is not None:
                complaints.append(
                    f"{path.relative_to(REPO)}: a column will only accept {value!r},"
                    f" which says {word!r}; Sift calls it {THEIR_WORD[word]}"
                )

    assert not complaints, (
        "\nA schema will only accept a value written in a stash-box's vocabulary.\n\n"
        "A constraint is not data about the world: it is the set of words Sift chose, and\n"
        "changing it later means rebuilding the table. Pick Sift's own word now.\n\n  "
        + "\n  ".join(complaints)
    )


@pytest.mark.regression
def test_no_route_is_addressed_in_their_words() -> None:
    complaints: list[str] = []
    route = re.compile(r'@router\.\w+\(\s*[\'"]([^\'"]+)[\'"]')
    for path in SOURCE.rglob("*router*.py"):
        source = path.read_text(encoding="utf-8")
        for address in route.findall(source):
            for piece in address.split("/"):
                word = _offending(piece.strip("{}"))
                if word is not None:
                    complaints.append(
                        f"{path.relative_to(REPO)}: {address!r} says {word!r};"
                        f" Sift calls it {THEIR_WORD[word]}"
                    )

    assert not complaints, (
        "\nAn address is written in a stash-box's vocabulary.\n\n"
        "An address outlives every screen that calls it. Rename it now.\n\n  "
        + "\n  ".join(complaints)
    )


@pytest.mark.regression
def test_the_gate_can_actually_see_one() -> None:
    """Planted examples, so a detector finding nothing on a healthy tree is shown to work."""
    assert _offending("performer_id") == "performer"
    assert _offending("scene_phash") == "scene"
    assert _offending("studios") == "studio"
    # The shapes it must NOT match.
    assert _offending("obscene") is None, "a word that merely CONTAINS one is not one"
    assert _offending("kind") is None

    # The constraint reader picks allowed words out and leaves a plain comparison alone.
    assert _constrained_values("kind TEXT CHECK(kind IN ('person','site'))") == [
        "person",
        "site",
    ]
    assert _constrained_values("CASE WHEN kind = 'studio' THEN 'site' END") == []
