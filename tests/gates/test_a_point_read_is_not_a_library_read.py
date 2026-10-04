# SPDX-License-Identifier: AGPL-3.0-or-later
"""Asking about ONE file costs one file's work, and the query plan is what says so.

The per-asset permission check shares its statement text with "which files may I see", which is
right (two copies of a permission rule drift), but must not share the execution: a check that
resolves the whole library per thumbnail reads like a single-row lookup. Only the plan can show it.

The rule: no statement on a per-asset path may SCAN a table that grows with the library. A SEARCH
is a seek and fine at any size; a SCAN of a small fixed table is fine. `LIBRARY_SIZED_TABLES`,
beside the sweep lane, is the written-down answer to what grows with the library.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

import sift.main  # noqa: F401 (imported for its side effect: every component registers itself)
from sift.kernel.access.constraints import NO_FILTER
from sift.kernel.access.repository.assets import DEFAULT_SORT, assets_query, point_query
from sift.kernel.db import (
    LIBRARY_SIZED_TABLES,
    Database,
    PointRead,
    registered_point_reads,
)

pytestmark = pytest.mark.gate

#: A `SCAN` step in a query plan; `SEARCH` is a seek.
_SCAN = "SCAN "

#: A table and its alias, as SQLite's plans report them: plans name the ALIAS (`SCAN l USING INDEX
#: ix_loc_asset`), so matching on table names alone would pass every aliased table.
_ALIASED = re.compile(
    r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)\s+(?:AS\s+)?([A-Za-z_][A-Za-z0-9_]*)\b",
    re.IGNORECASE,
)

#: Words that follow a table name without being an alias, so a clause boundary is not one.
_NOT_AN_ALIAS = frozenset(
    {"on", "where", "group", "order", "limit", "union", "left", "join", "inner", "cross", "using"}
)


def _names_for(statement: str) -> dict[str, str]:
    """Every name a library-sized table answers to in this statement, mapped back to the table.

    Built from the statement, since a hand-kept list of aliases would drift from the query.
    """
    names = {table: table for table in LIBRARY_SIZED_TABLES}
    for table, alias in _ALIASED.findall(statement):
        if table.lower() in LIBRARY_SIZED_TABLES and alias.lower() not in _NOT_AN_ALIAS:
            names[alias] = table.lower()
    return names


def _scanned_library_tables(plan: list[str], statement: str) -> set[str]:
    """Which library-sized tables this plan walks end to end; a SEARCH is a seek and not counted."""
    names = _names_for(statement)
    found: set[str] = set()
    for step in plan:
        if not step.startswith(_SCAN):
            continue
        rest = step[len(_SCAN) :].split()
        walked = rest[0] if rest else ""
        table = names.get(walked) or names.get(walked.lower())
        if table is not None:
            found.add(table)
    return found


async def _plan(tmp_path: Path, statement: str, params: dict[str, object]) -> list[str]:
    """The query plan for `statement`, against a fresh database carrying Sift's real schema.

    The real schema, because the indexes that exist decide a plan. Empty of rows, because the gate
    is about the shape the planner is FORCED into: one that needed rows would pass on a small
    library and fail on a big one.
    """
    database = Database(tmp_path / "plan.sqlite3")
    await database.connect()
    try:
        await database.initialize_schema()
        # EXPLAIN QUERY PLAN takes a STATEMENT, not a value, so it cannot be a placeholder. The
        # statement is a module constant; `params` still bind as they do in production.
        rows = await database.fetch_all(
            "EXPLAIN QUERY PLAN " + statement,  # nosemgrep: sift-no-string-built-sql
            params,
        )
        return [str(row["detail"]) for row in rows]
    finally:
        await database.close()


def _params(asset_id: str | None) -> dict[str, object]:
    _where, bound = NO_FILTER.predicate()
    return {
        "viewer": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
        "is_admin": 0,
        "asset_id": asset_id,
        "reveal": 0,
        "limit": 50,
        "offset": 0,
        "tag_id": None,
        "collection_id": None,
        "photo_set_id": None,
        "hidden_only": 0,
        # Hand-listed, so a new parameter reddens this gate and is given a value on purpose: SQLite
        # refuses a statement with a binding missing. 1, the arm every curated wall asks for.
        "pinned_first": 1,
        **bound,
    }


async def test_the_per_asset_check_never_walks_the_library(tmp_path: Path) -> None:
    """The point form answers about one file, so it may seek and may not sweep.

    The plan, not a timing: a millisecond threshold fails on a busy build machine and passes on a
    fast one, while "does it scan the library" is the same answer everywhere.
    """
    where, _bound = NO_FILTER.predicate()
    statement = point_query(where)
    plan = await _plan(tmp_path, statement, _params("01ARZ3NDEKTSV4RRFFQ69G5FAV"))
    walked = _scanned_library_tables(plan, statement)
    assert not walked, (
        "the per-asset permission check walks "
        + ", ".join(sorted(walked))
        + ": it is asking about ONE file and must seek it. See point_query in assets.py."
    )


async def test_the_per_asset_check_seeks_the_asset_by_its_primary_key(tmp_path: Path) -> None:
    """The point form reaches the asset row by its id, which not scanning the library alone does
    not prove: the two can come apart."""
    where, _bound = NO_FILTER.predicate()
    plan = await _plan(tmp_path, point_query(where), _params("01ARZ3NDEKTSV4RRFFQ69G5FAV"))
    assert any(step.startswith("SEARCH a ") for step in plan), (
        "the per-asset check no longer seeks the asset by id. The usual cause is an id predicate "
        "written as `(:asset_id IS NULL OR a.id = :asset_id)`, which no index can answer."
    )


async def test_the_page_walks_the_sort_index_and_materialises_nothing(tmp_path: Path) -> None:
    """A page of the grid walks one index and probes the stored verdict; it builds no set first.

    One SCAN, the sort index on `assets`, and no MATERIALIZE or automatic index over anything that
    grows with the library: those are the tell of resolving first and paging second.
    """
    where, _bound = NO_FILTER.predicate()
    statement = assets_query(DEFAULT_SORT, where, arranged=False, counted=False)
    plan = await _plan(tmp_path, statement, _params(None))
    walked = _scanned_library_tables(plan, statement)
    assert walked == {"assets"}, f"the page walks {sorted(walked)}; it should walk assets alone"
    names = _names_for(statement)
    for step in plan:
        if step.startswith("MATERIALIZE "):
            name = step.split()[1]
            assert names.get(name) is None, f"the page materialises {name} before paging"
        assert "AUTOMATIC" not in step or not any(
            step.startswith(f"SEARCH {alias} ") for alias in names
        ), f"the page builds an index over a library-sized table per query: {step}"


#: Named once, so the two checks that read the per-asset statement mean the same constant.
_ONE_ASSET_NAME = "_ONE_ASSET"

#: A bound value in a statement, positional or named, so bindings are derived from the statement
#: rather than from a list of example values that would drift.
_BINDING = re.compile(r"\?|:([A-Za-z_][A-Za-z0-9_]*)")


def _nothing_bound(statement: str) -> dict[str, None] | tuple[None, ...]:
    """Bindings of NULL for everything this statement binds, in the form it expects.

    The plan is the same as with realistic values: there are no `sqlite_stat` tables to weigh them.
    """
    named = {name for name in _BINDING.findall(statement) if name}
    if named:
        return dict.fromkeys(named)
    return (None,) * len(_BINDING.findall(statement))


async def _plan_of(tmp_path: Path, statement: str) -> list[str]:
    """The query plan for a statement, binding nothing to everything it binds."""
    return await _plan(tmp_path, statement, _nothing_bound(statement))


def _declared() -> list[PointRead]:
    """Every statement declared constant-time, in a stable order."""
    return sorted(registered_point_reads().values(), key=lambda read: read.name)


def test_something_is_actually_declared_a_point_read() -> None:
    """The point-read registry is not empty: the check below passes over an empty one."""
    assert _declared(), (
        "no statement is declared a point read, so the plan check below examines nothing. Either "
        "point_read has stopped being called, or the modules that call it are no longer imported."
    )


@pytest.mark.parametrize("declared", _declared(), ids=lambda read: read.name)
async def test_a_declared_point_read_never_walks_the_library(
    tmp_path: Path, declared: PointRead
) -> None:
    """Every statement allowed onto the event loop answers about one subject, from its plan.

    A point read runs where nothing else can until it finishes, so one that walks a library-sized
    table would stall the application while still returning one row.
    """
    plan = await _plan_of(tmp_path, declared.sql)
    walked = _scanned_library_tables(plan, declared.sql)
    assert not walked, (
        f"the point read {declared.name!r} walks "
        + ", ".join(sorted(walked))
        + ": it runs on the event loop, so it must seek. Either narrow it or stop declaring it "
        "one with point_read."
    )


#: Where the per-asset resolve lives, named, so a search that found nothing cannot pass silently.
_STORE = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "sift"
    / "kernel"
    / "access"
    / "repository"
    / "core.py"
)


def _called_in(function_name: str, source: str) -> set[str]:
    """Every function `function_name` calls, by name, read from the syntax tree so a mention in a
    docstring is not mistaken for a call."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef) and node.name == function_name:
            return {
                call.func.id
                for call in ast.walk(node)
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
            }
    raise AssertionError(f"{function_name} is no longer in {_STORE.name}: this gate is stale")


def _module_level_call_for(name: str, source: str) -> set[str]:
    """Every function called while building the module-level constant `name`: the per-asset
    statement is assembled once at import, not inside `_one`."""
    tree = ast.parse(source)
    for node in tree.body:
        targets = (
            node.targets
            if isinstance(node, ast.Assign)
            else [node.target]
            if isinstance(node, ast.AnnAssign)
            else []
        )
        if any(isinstance(target, ast.Name) and target.id == name for target in targets):
            assert node.value is not None
            return {
                called.func.id
                for called in ast.walk(node.value)
                if isinstance(called, ast.Call) and isinstance(called.func, ast.Name)
            }
    raise AssertionError(f"{name} is no longer built at module level in {_STORE.name}")


def _names_read_in(function_name: str, source: str) -> set[str]:
    """Every name `function_name` reads. What it uses, as opposed to what it calls."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef) and node.name == function_name:
            return {
                child.id
                for child in ast.walk(node)
                if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)
            }
    raise AssertionError(f"{function_name} is no longer in {_STORE.name}: this gate is stale")


def test_the_per_asset_resolve_actually_uses_the_point_form() -> None:
    """The hot path uses the point form, which the plan checks above cannot see.

    Reverting `_one` to the grid statement leaves `point_query` untouched and every plan green. The
    statement is built at module level and used inside the function, so both halves are read.
    """
    source = _STORE.read_text()
    built_with = _module_level_call_for(_ONE_ASSET_NAME, source)
    assert "point_query" in built_with, (
        f"{_ONE_ASSET_NAME} is no longer built with point_query. Every per-asset route "
        "(thumbnail, hover clip, scrub strip, vault write) goes through it, so this is the whole "
        "library being resolved per picture again."
    )
    assert "assets_query" not in built_with, (
        f"{_ONE_ASSET_NAME} is built from the grid's set-form statement to answer about ONE asset. "
        "That is the fault point_query exists to fix; see its docstring."
    )
    used = _names_read_in("_one", source)
    assert _ONE_ASSET_NAME in used, (
        f"_one no longer reads {_ONE_ASSET_NAME}, so whatever that constant is built from, the hot "
        "path is answering with something else."
    )
    assert "assets_query" not in _called_in("_one", source), (
        "_one is building the grid's set-form statement to answer about ONE asset."
    )
