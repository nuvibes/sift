# SPDX-License-Identifier: AGPL-3.0-or-later
"""Counts the areas whose decisions still show the words they were stored with. It may only fall.

A decision is worded when SHOWN, by the area that wrote it (`kernel.workbench.Words`, read in
`kernel/access/worded.py`); an area with no `worded` shows its stored titles for good, stale names
and retired words included. Every reverser class (answering `pictures_of` and `reverse`) that
cannot word is counted against `STILL_STORED`; a rise or a fall fails. Read from source, base
classes in the same module counting for their subclasses.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.gate, pytest.mark.unit]

REPO = Path(__file__).resolve().parents[2]
SLICES = REPO / "src" / "sift" / "slices"

#: How many reverser classes still show their decisions' stored titles. May only fall.
STILL_STORED = 14


def _methods(node: ast.ClassDef) -> set[str]:
    return {
        one.name for one in node.body if isinstance(one, ast.FunctionDef | ast.AsyncFunctionDef)
    } | {
        target.id
        for one in node.body
        if isinstance(one, ast.Assign)
        for target in one.targets
        if isinstance(target, ast.Name)
    }


def _answers(
    classes: dict[str, ast.ClassDef], name: str, seen: frozenset[str] = frozenset()
) -> set[str]:
    """What one class answers, with what every base defined in the same module answers."""
    node = classes.get(name)
    if node is None or name in seen:
        return set()
    said = _methods(node)
    for base in node.bases:
        if isinstance(base, ast.Name):
            said |= _answers(classes, base.id, seen | {name})
    return said


def reversers(root: Path = SLICES, base: Path = REPO) -> dict[str, bool]:
    """Every concrete reverser class under the slices, and whether it can word its decisions.

    Keyed `path::Class`. A class is a reverser when it, or a base defined in the same module,
    answers both `pictures_of` and `reverse`; it words when it or such a base answers `worded`.
    Protocols and underscored bases are the shape rather than an area, and are not counted.
    """
    found: dict[str, bool] = {}
    for path in sorted(root.rglob("*.py")):
        if "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        classes = {one.name: one for one in tree.body if isinstance(one, ast.ClassDef)}

        for name, node in classes.items():
            if name.startswith("_") or any(
                isinstance(base, ast.Name) and base.id == "Protocol" for base in node.bases
            ):
                continue
            said = _answers(classes, name)
            if {"pictures_of", "reverse"} <= said:
                found[f"{path.relative_to(base).as_posix()}::{name}"] = "worded" in said
    return found


def test_the_areas_showing_stored_titles_only_fall() -> None:
    found = reversers()
    stored = sorted(name for name, words in found.items() if not words)
    assert found, "no reverser was found at all: the gate has stopped reading the source"
    assert len(stored) <= STILL_STORED, (
        f"{len(stored)} areas show their decisions' stored titles, over the ceiling of "
        f"{STILL_STORED}. A new reverser words its decisions from what they recorded: give it "
        f"`worded` (kernel.workbench.Words). Still stored: {stored}"
    )
    assert len(stored) == STILL_STORED, (
        f"only {len(stored)} areas still show stored titles now. Lower STILL_STORED to "
        f"{len(stored)} so the ratchet holds what was won"
    )


def test_the_gate_reads_a_known_positive(tmp_path: Path) -> None:
    """The counting rule, on a module that holds one of each: a gate that silently matched
    nothing would pass forever."""
    area = tmp_path / "area"
    area.mkdir()
    (area / "queue.py").write_text(
        "class _Base:\n"
        "    async def pictures_of(self, viewer, payload): ...\n"
        "    async def reverse(self, viewer, receipt_id, payload): ...\n"
        "class Plain(_Base):\n"
        "    name = 'plain'\n"
        "class Worded(_Base):\n"
        "    def worded(self, recorded): ...\n"
        "class NotOne:\n"
        "    async def reverse(self, viewer, receipt_id, payload): ...\n",
        encoding="utf-8",
    )
    found = {key.split("::")[1]: words for key, words in reversers(tmp_path, tmp_path).items()}
    assert found == {"Plain": False, "Worded": True}
