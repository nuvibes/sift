# SPDX-License-Identifier: AGPL-3.0-or-later
"""The workers start after every handler is registered.

A worker claims anything the moment it exists. A job whose type has no handler yet is failed for
good as "no longer exists in this version", which is right for a type an upgrade removed, and
wrong for a type registered late: if the pool started before the backup's handler was registered,
a backup that fell due while Sift was closed would be claimed in that gap at start and failed in
0 seconds.

The durable answer is the ORDER, not a queue that leaves a handler-less job waiting: a waiting row
for a type nothing will ever register is a row that waits for ever, and the "no longer exists"
failure is the one honest answer for it. So the composition root builds the pool, registers every
handler, and only then starts it, and this reads the composition root to hold that order.

HOW: every function in the composition root (`sift/wiring/`) that registers a handler, directly
or through another function there, is found; the start-up's statement that starts the pool must
come after every statement that calls one of them. The function that makes the pool must not start
it itself. The steps are read across every module of the package, because each lives in the module
of the concern it wires and the start-up (`start_up`, which the lifespan runs) calls them by name.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.gate, pytest.mark.unit]

WIRING = Path(__file__).resolve().parents[2] / "src" / "sift" / "wiring"

_REGISTERS = {"register_handlers", "register_handler"}


def _called_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for inner in ast.walk(node):
        if isinstance(inner, ast.Call):
            target = inner.func
            if isinstance(target, ast.Name):
                names.add(target.id)
            elif isinstance(target, ast.Attribute):
                names.add(target.attr)
    return names


def _starts_the_pool(node: ast.AST) -> bool:
    for inner in ast.walk(node):
        if (
            isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Attribute)
            and inner.func.attr == "start"
            and isinstance(inner.func.value, ast.Name)
            and inner.func.value.id == "pool"
        ):
            return True
    return False


def _functions(*trees: ast.Module) -> dict[str, ast.AST]:
    return {
        node.name: node
        for tree in trees
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def _registering(functions: dict[str, ast.AST]) -> set[str]:
    """Every function in main that registers a handler, directly or through another one."""
    calls = {name: _called_names(node) for name, node in functions.items()}
    found = {name for name, called in calls.items() if called & _REGISTERS}
    while True:
        more = {name for name, called in calls.items() if called & found} - found
        if not more:
            return found
        found |= more


def check(*sources: str) -> list[str]:
    """What is out of order, in words. Empty when the pool starts after every registration."""
    functions = _functions(*(ast.parse(source) for source in sources))
    start_up = functions.get("start_up")
    if not isinstance(start_up, ast.AsyncFunctionDef):
        return ["no async start_up in the composition root"]
    registering = _registering(functions)
    problems: list[str] = []
    builder = functions.get("build_workers")
    if builder is not None and _starts_the_pool(builder):
        problems.append("build_workers starts the pool itself, before the handlers after it")
    starts = [index for index, stmt in enumerate(start_up.body) if _starts_the_pool(stmt)]
    if len(starts) != 1:
        return [*problems, f"the start-up starts the pool {len(starts)} times, not once"]
    for index, stmt in enumerate(start_up.body):
        late = _called_names(stmt) & registering
        if late and index > starts[0]:
            problems.append(
                f"line {stmt.lineno} registers handlers ({', '.join(sorted(late))}) "
                "after the workers start"
            )
    return problems


def test_the_workers_start_after_every_handler_is_registered() -> None:
    sources = [path.read_text(encoding="utf-8") for path in sorted(WIRING.glob("*.py"))]
    assert sources, "the composition root has no modules to read"
    assert check(*sources) == []


def test_the_gate_reads_the_steps_across_modules() -> None:
    """A registration two modules away from the start-up still counts."""
    step = "def build_backup():\n    backup.register_handlers(service=None)\n"
    late = "async def start_up(app):\n    await pool.start()\n    build_backup()\n"
    early = "async def start_up(app):\n    build_backup()\n    await pool.start()\n"
    assert check(step, late) != []
    assert check(step, early) == []


def test_the_gate_sees_a_registration_after_the_start() -> None:
    """A known positive: silence and success must not be the same answer."""
    planted = """
async def _build_backup():
    backup.register_handlers(service=None)

async def start_up(app):
    await pool.start()
    await _build_backup()
"""
    assert check(planted) != []
