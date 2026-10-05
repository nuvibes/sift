# SPDX-License-Identifier: AGPL-3.0-or-later
"""There is exactly one place that imports the inference runtime.

The processor build and the graphics-card build can both be installed, and the FIRST import wins
for the life of the process, so the import goes through the function that puts the card's build in
front first; an extra import elsewhere silently decides the card cannot be used.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

#: The one module allowed to import it.
THE_ONE_PLACE = Path("src/sift/kernel/ml/session.py")

#: The library itself.
RUNTIME_NAMES = ("onnxruntime",)


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _imports_the_runtime(source: str) -> bool:
    """Whether this file imports the runtime at any depth, parsed so a docstring naming it is
    not."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            if any(alias.name.split(".")[0] in RUNTIME_NAMES for alias in node.names):
                return True
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.split(".")[0] in RUNTIME_NAMES
        ):
            return True
    return False


def _is_a_test(path: Path) -> bool:
    """Tests are exempt: each runs in a process of its own."""
    return path.name.startswith("test_") or "tests" in path.parts


def test_only_one_module_imports_the_inference_runtime() -> None:
    root = _repository_root()
    allowed = (root / THE_ONE_PLACE).resolve()

    guilty = [
        path.relative_to(root).as_posix()
        for path in sorted((root / "src" / "sift").rglob("*.py"))
        if path.resolve() != allowed
        and not _is_a_test(path)
        and _imports_the_runtime(path.read_text(encoding="utf-8"))
    ]

    assert guilty == [], (
        "These import the inference runtime directly. Ask `sift.kernel.ml.child.devices_here` "
        f"or a `ChildRunner` instead, so it loads in the model process: {guilty}"
    )


def test_the_one_place_really_does_import_it() -> None:
    """The one place really imports it."""
    source = (_repository_root() / THE_ONE_PLACE).read_text(encoding="utf-8")

    assert _imports_the_runtime(source)
