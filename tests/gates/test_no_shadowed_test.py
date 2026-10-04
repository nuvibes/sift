# SPDX-License-Identifier: AGPL-3.0-or-later
"""Two tests with one name in one file, where the second silently replaces the first.

A module is a namespace. Defining `test_x` twice in it binds the name twice, and only the second
survives, so the first test is not skipped, not reported, and not run. It leaves no trace anywhere:
the count goes down by one in a suite of thousands, the file still passes, and whatever the lost test
was guarding is now guarded by nothing.

It is easy to do and hard to see: in a file of hundreds of tests, a new test at the bottom can take
the name of one near the top, and the only sign may be a coverage gate dropping by a single
statement, which is a piece of luck, not a check. The gate the project already has for two FILES
differing only by case is this same shape one level up; this is the level where it actually bites.

Classes count as their own namespace, which is what makes a per-class check necessary rather than a
per-module one: two test classes may each have a `test_it_refuses`, and should.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.gate, pytest.mark.unit]

REPO = Path(__file__).resolve().parents[2]

#: Where tests live. Everything under these that pytest would collect.
LOOKS_AT = (REPO / "src" / "sift", REPO / "tests")


def _test_files() -> list[Path]:
    found: list[Path] = []
    for root in LOOKS_AT:
        found.extend(
            path
            for path in root.rglob("test_*.py")
            if ".venv" not in path.parts and "__pycache__" not in path.parts
        )
    return sorted(found)


def _shadowed(body: list[ast.stmt]) -> list[str]:
    """Names defined more than once in one namespace, in the order they were first seen."""
    seen: dict[str, int] = {}
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
            "test_"
        ):
            seen[node.name] = seen.get(node.name, 0) + 1
    return [name for name, count in seen.items() if count > 1]


def test_no_test_is_shadowed_by_a_later_one_of_the_same_name() -> None:
    files = _test_files()
    # A meta-check on the gate itself: a walk that finds nothing passes, and would go on passing
    # after somebody moved the tests.
    assert len(files) > 50, (
        f"only found {len(files)} test files: the walk is looking in the wrong place"
    )

    offences: list[str] = []
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        where = path.relative_to(REPO)
        for name in _shadowed(tree.body):
            offences.append(f"{where}: {name} is defined twice at the top level")
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                for name in _shadowed(node.body):
                    offences.append(f"{where}: {node.name}.{name} is defined twice")

    assert not offences, (
        "these tests are shadowed. The first of each pair never runs, and nothing reports it:\n"
        + "\n".join(offences)
    )


def test_the_gate_can_see_a_shadowed_test(tmp_path: Path) -> None:
    """The gate broken on purpose, because a walk that quietly matches nothing passes forever."""
    source = tmp_path / "test_planted.py"
    source.write_text(
        "def test_one() -> None:\n    pass\n\n\ndef test_one() -> None:\n    pass\n",
        encoding="utf-8",
    )

    assert _shadowed(ast.parse(source.read_text(encoding="utf-8")).body) == ["test_one"]


def test_two_classes_may_each_have_a_test_of_the_same_name() -> None:
    """The case that must NOT be reported. Two suites of the same rule are two namespaces."""
    source = (
        "class TestOne:\n    def test_it_refuses(self) -> None:\n        pass\n\n\n"
        "class TestTwo:\n    def test_it_refuses(self) -> None:\n        pass\n"
    )
    tree = ast.parse(source)

    assert _shadowed(tree.body) == []
    for node in tree.body:
        assert isinstance(node, ast.ClassDef)
        assert _shadowed(node.body) == []
