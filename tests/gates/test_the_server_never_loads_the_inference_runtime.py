# SPDX-License-Identifier: AGPL-3.0-or-later
"""The server never loads the inference runtime or the vocabulary: only the model process does.

Both are native code that can crash as it loads and take its process with it, so neither may be in
the server's reach. Every import counts, at any depth, except one under `if TYPE_CHECKING:`.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = [pytest.mark.gate, pytest.mark.unit]

SOURCE = Path(__file__).resolve().parents[2] / "src"

#: What the desktop app starts, and what the model process starts.
SERVER = "sift.main"
MODEL_PROCESS = "sift.kernel.ml.worker"

#: The one module that imports the runtime (see `test_one_import_of_the_inference_runtime.py`).
THE_ONE_PLACE = "sift.kernel.ml.session"

#: The native libraries only the model process loads.
CHILD_ONLY = ("onnxruntime", "sentencepiece")


def _file_of(name: str) -> Path | None:
    base = SOURCE.joinpath(*name.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _nodes(node: ast.AST) -> Iterator[ast.AST]:
    """Every node below this one, leaving out what only a type checker reads."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.If) and _is_type_checking(child.test):
            for other in child.orelse:
                yield other
                yield from _nodes(other)
            continue
        yield child
        yield from _nodes(child)


def imported_by(name: str, source: str, *, package: bool = False) -> set[str]:
    """The `sift` modules a module's source imports, its parent packages included."""
    found: set[str] = set()
    here = name.split(".") if package else name.split(".")[:-1]
    for node in _nodes(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            stem = here[: len(here) - node.level + 1] if node.level else []
            module = ".".join([*stem, *([node.module] if node.module else [])])
            found.add(module)
            found.update(
                f"{module}.{alias.name}"
                for alias in node.names
                if _file_of(f"{module}.{alias.name}")
            )
    with_parents = {
        ".".join(one.split(".")[:end]) for one in found for end in range(1, one.count(".") + 2)
    }
    return {one for one in with_parents if one.split(".")[0] == "sift" and _file_of(one)}


def reached_from(start: str) -> set[str]:
    reached: set[str] = set()
    waiting = [start]
    while waiting:
        name = waiting.pop()
        if name in reached:
            continue
        reached.add(name)
        path = _file_of(name)
        assert path is not None, name
        source = path.read_text(encoding="utf-8")
        waiting.extend(imported_by(name, source, package=path.name == "__init__.py"))
    return reached


def test_the_server_never_reaches_the_module_that_loads_the_runtime() -> None:
    reached = reached_from(SERVER)

    assert THE_ONE_PLACE not in reached, (
        f"The server imports {THE_ONE_PLACE}, so a runtime that crashes as it loads would take the "
        "server with it. Ask `sift.kernel.ml.child.devices_here` or a `ChildRunner` instead."
    )
    assert MODEL_PROCESS not in reached


def libraries_imported_by(source: str) -> set[str]:
    """The top-level names a source imports, at any depth, as `imported_by` counts them."""
    found: set[str] = set()
    for node in _nodes(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found.add(node.module.split(".")[0])
    return found


def test_no_module_the_server_reaches_imports_a_child_only_library() -> None:
    guilty = sorted(
        f"{name} imports {library}"
        for name in reached_from(SERVER)
        for library in libraries_imported_by(_source(name)) & set(CHILD_ONLY)
    )

    assert guilty == [], (
        f"The server would load native code only the model process may: {guilty}. Ask a "
        "`ChildRunner` instead, so a crash as it loads costs a child."
    )


def _source(name: str) -> str:
    path = _file_of(name)
    assert path is not None, name
    return path.read_text(encoding="utf-8")


def test_the_model_process_does_reach_it() -> None:
    """Proof the walk sees what it is looking for."""
    assert THE_ONE_PLACE in reached_from(MODEL_PROCESS)
    assert set(CHILD_ONLY) <= libraries_imported_by(_source(THE_ONE_PLACE))


def test_an_import_inside_a_function_counts_and_one_for_the_type_checker_does_not() -> None:
    source = (
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from sift.kernel.ml import worker\n"
        "else:\n"
        "    import sift.kernel.log\n"
        "def later():\n"
        "    from sift.kernel.ml import session\n"
        "    from . import child\n"
    )

    found = imported_by("sift.kernel.ml.runtime", source)

    assert {"sift.kernel.ml.session", "sift.kernel.ml.child", "sift.kernel.log"} <= found
    assert "sift.kernel.ml.worker" not in found
