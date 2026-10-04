# SPDX-License-Identifier: AGPL-3.0-or-later
"""A read of the whole library goes through the lane, or it does not go.

The cost of a read is the rows crossing out of SQLite into Python, multiplied by the readers doing
it at once: several passes reading whole tables together can make every request time out, though
no query is slow. One at a time through the lane is faster for the passes and for everyone else.
A pass left off the lane looks like one ordinary query among hundreds, so only this finds it.

The rule is `is_whole_library_read`, beside the lane so the two agree; library-sized is a
written-down list of tables that grow with the media somebody put in.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from sift.kernel.db import LIBRARY_SIZED_TABLES, is_whole_library_read

pytestmark = [pytest.mark.gate, pytest.mark.unit]

REPO = Path(__file__).resolve().parents[2]
SOURCE = REPO / "src" / "sift"

#: The reading calls that materialise every row they are given; `fetch_one` takes one row and stops.
MATERIALISING = frozenset({"fetch_all"})

#: The calls that are already the lane.
ON_THE_LANE = frozenset({"sweep_all", "sweep"})


def _module_queries(tree: ast.Module) -> dict[str, str]:
    """Every module-level name bound to a string that reads like SQL: queries are module
    constants."""
    found: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.Constant) or not isinstance(node.value.value, str):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                found[target.id] = node.value.value
    return found


def _tuple_queries(tree: ast.Module) -> dict[str, list[str]]:
    """The same for a name bound to a tuple of statements."""
    found: dict[str, list[str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Tuple):
            continue
        texts = [
            element.value
            for element in node.value.elts
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        ]
        if not texts:
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                found[target.id] = texts
    return found


def _sql_of(argument: ast.expr, queries: dict[str, str], tuples: dict[str, list[str]]) -> list[str]:
    """The statement text a call's first argument stands for, as far as it reads statically; an
    unbound name or an expression reads as nothing, a hole stated rather than hidden."""
    if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
        return [argument.value]
    if isinstance(argument, ast.Name):
        if argument.id in queries:
            return [queries[argument.id]]
        if argument.id in tuples:
            return tuples[argument.id]
    return []


def _loop_names(tree: ast.Module, tuples: dict[str, list[str]]) -> dict[str, list[str]]:
    """Names bound by `for query in _SOME_TUPLE:`, whose call site says `fetch_all(query)`."""
    bound: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.For) or not isinstance(node.iter, ast.Name):
            continue
        texts = tuples.get(node.iter.id)
        if texts and isinstance(node.target, ast.Name):
            bound.setdefault(node.target.id, []).extend(texts)
    return bound


def _offenders_in(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    queries = _module_queries(tree)
    tuples = _tuple_queries(tree)
    tuples = {**tuples, **_loop_names(tree, tuples)}
    found: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in MATERIALISING or not node.args:
            continue
        for sql in _sql_of(node.args[0], queries, tuples):
            if is_whole_library_read(sql):
                one_line = " ".join(sql.split())[:90]
                found.append(f"{path.relative_to(REPO)}:{node.lineno}: {one_line}")
    return found


def test_no_whole_library_read_goes_through_the_ordinary_pool() -> None:
    """No whole-library read goes through the ordinary pool: it is handed to `sweep_all`, or read
    inside a `sweep()` block, on the lane's own connection.

    A query that trips this and is not a sweep is narrower than it looks (give it the bound value or
    LIMIT), or its table does not grow with the library and belongs on the list beside the lane.
    """
    offenders: list[str] = []
    for path in sorted(SOURCE.rglob("*.py")):
        if "/tests/" in path.as_posix() or path.name.startswith("test_"):
            continue
        offenders.extend(_offenders_in(path))

    assert offenders == [], (
        "these read a whole library-sized table through the ordinary connection pool, which can "
        "make the application unusable for minutes. Hand each to sweep_all(), or open it "
        "inside a sweep() block:\n  " + "\n  ".join(offenders)
    )


def test_the_rule_can_fail() -> None:
    """The rule's distinctions, pinned: getting any backwards switches the gate off for a class."""
    assert is_whole_library_read("SELECT rel_cache_path FROM derivatives")
    assert is_whole_library_read("SELECT id FROM face_tracks")
    # A WHERE that filters nothing.
    assert is_whole_library_read(
        "SELECT asset_id, folder_id FROM asset_locations WHERE folder_id IS NOT NULL"
    )
    # A group per file is still a row per file.
    assert is_whole_library_read(
        "SELECT asset_id, COUNT(*) AS n FROM face_tracks GROUP BY asset_id"
    )
    # A statement from a loop over a tuple.
    assert is_whole_library_read("SELECT crop_path FROM face_detections")
    # And the four things that make a read narrow.
    assert not is_whole_library_read("SELECT id FROM assets WHERE id = ?")
    assert not is_whole_library_read("SELECT id FROM assets ORDER BY added_at LIMIT 50")
    assert not is_whole_library_read("SELECT COUNT(*) AS n FROM assets")
    assert not is_whole_library_read("SELECT key, value FROM app_settings")


def test_the_table_list_is_not_empty_or_the_gate_passes_everything() -> None:
    """The table list is not empty, or no read would count and the gate would pass everything."""
    assert LIBRARY_SIZED_TABLES, "an empty table list switches the gate off silently"
    assert "assets" in LIBRARY_SIZED_TABLES
