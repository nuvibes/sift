# SPDX-License-Identifier: AGPL-3.0-or-later
"""The figure line of every card on Organize stands on one line: the count and what it counts.

The card draws the count in the display face and the words beside it on one baseline, and the
narrowest card (four to a row at 1600, three at 1280) has room for about 296 px. Measured in that
face, 30 characters of words beside a count of up to four digits is the most that fits, so a
queue's plural words stay within that budget; a longer phrase wraps the words under the number.
A queue inside a group card is left out: that card lists its parts and draws no one figure.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.gate, pytest.mark.unit]

ROOT = Path(__file__).resolve().parents[2]
SLICES = ROOT / "src" / "sift" / "slices"

#: The longest plural words a card can carry beside its count on one line.
FIGURE_WORDS = 30


def _in_a_group(cls: ast.ClassDef) -> bool:
    """Whether a queue is part of a group card, which lists its parts instead of one figure."""
    for statement in cls.body:
        if isinstance(statement, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "group" for target in statement.targets
        ):
            return not (isinstance(statement.value, ast.Constant) and statement.value.value is None)
    return False


def card_words(tree: ast.AST) -> list[tuple[int, str]]:
    """The literal `verb=` words of every `Summary(...)` a module builds for a card of its own."""
    grouped: set[int] = set()
    for cls in ast.walk(tree):
        if isinstance(cls, ast.ClassDef) and _in_a_group(cls):
            grouped |= {id(node) for node in ast.walk(cls)}
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if id(node) in grouped:
            continue
        if not isinstance(node, ast.Call):
            continue
        name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
        if name != "Summary":
            continue
        for keyword in node.keywords:
            value = keyword.value
            if (
                keyword.arg == "verb"
                and isinstance(value, ast.Constant)
                and isinstance(value.value, str)
            ):
                found.append((node.lineno, value.value))
    return found


def test_every_cards_words_fit_beside_its_count() -> None:
    surveyed: list[str] = []
    long: list[str] = []
    for path in sorted(SLICES.rglob("*.py")):
        if "tests" in path.parts:
            continue
        for line, words in card_words(ast.parse(path.read_text(encoding="utf-8"))):
            surveyed.append(words)
            if len(words) > FIGURE_WORDS:
                long.append(f"{path.relative_to(ROOT)}:{line}: {words!r}")
    assert "files enriched from names" in surveyed, "the survey stopped reading the cards"
    assert long == []


def test_a_long_phrase_is_refused_and_a_grouped_one_is_not_read() -> None:
    planted = ast.parse('Summary(name="x", verb="files enriched from their very own names")')
    assert [len(words) > FIGURE_WORDS for _line, words in card_words(planted)] == [True]
    grouped = ast.parse(
        "class Q:\n    group = G\n    def survey(self):\n"
        "        return Summary(verb='files where the face and the name disagree')\n"
    )
    assert card_words(grouped) == []
