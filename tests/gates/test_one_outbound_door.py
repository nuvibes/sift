# SPDX-License-Identifier: AGPL-3.0-or-later
"""One place decides whether anything about this library leaves the machine.

A file or record marked KEPT LOCAL must never have its fingerprints or name sent to a stash-box,
and nothing but the code enforces it: a stash-box is an ordinary HTTPS request. So the refusal
holds where the bytes leave: only the stash-box service reaches the adapter, and every method of it
that does asks `_nothing_leaves` first, bar the two listed that name nothing local. Tests are
exempt.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.gate, pytest.mark.unit]

SOURCE = Path(__file__).resolve().parents[2] / "src" / "sift"

#: The one module allowed to ask a stash-box anything.
DOOR = SOURCE / "slices" / "stash_boxes" / "asking.py"

#: The guard's name, so a failure names the thing to call.
THE_GUARD = "_nothing_leaves"

#: The attribute every outbound question goes through.
THE_ADAPTER = "_adapter"

#: Where the adapter may be held without a door.
#: Where the adapter may be held without a door, each with its reason; it stays short.
ALLOWED_HOLDERS = {
    "slices/stash_boxes/asking.py",
    # The adapter itself, which holds no opinion about the library.
    "slices/stash_boxes/adapter.py",
    # Boot, where the adapter is built and handed in.
    "slices/stash_boxes/__init__.py",
}

#: The two methods inside the door that reach the adapter and name no local thing.
#: `check` sends a term nothing matches, and `picture` fetches an address the box just gave: neither
#: carries anything of this library out.
NAMES_NOTHING_LOCAL = {"check", "picture"}


def _python_files() -> list[Path]:
    return [
        path
        for path in SOURCE.rglob("*.py")
        if "tests" not in path.parts and "testing" not in path.parts
    ]


def _relative(path: Path) -> str:
    return path.relative_to(SOURCE).as_posix()


def reaches_the_adapter(tree: ast.AST) -> bool:
    """Whether this module READS `something._adapter`; the constructor storing it and a module
    naming its type do not."""
    return any(
        isinstance(node, ast.Attribute)
        and node.attr == THE_ADAPTER
        and isinstance(node.ctx, ast.Load)
        for node in ast.walk(tree)
    )


def undoored_methods(source: str) -> list[str]:
    """The door's methods that reach the adapter without asking the guard anywhere in their body."""
    tree = ast.parse(source)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if node.name in NAMES_NOTHING_LOCAL:
            continue
        body = ast.Module(body=node.body, type_ignores=[])
        if not reaches_the_adapter(body):
            continue
        asks = any(
            isinstance(call.func, ast.Attribute) and call.func.attr == THE_GUARD
            for call in ast.walk(body)
            if isinstance(call, ast.Call)
        )
        if not asks:
            offenders.append(node.name)
    return offenders


@pytest.mark.regression
def test_only_the_service_reaches_a_stash_box() -> None:
    offenders = [
        _relative(path)
        for path in _python_files()
        if _relative(path) not in ALLOWED_HOLDERS
        and reaches_the_adapter(ast.parse(path.read_text(encoding="utf-8")))
    ]
    assert not offenders, (
        "these hold a stash-box adapter outside the one service that has a door on it, so what "
        "they send is refused by nothing: " + ", ".join(offenders)
    )


@pytest.mark.regression
def test_every_outbound_method_refuses_a_kept_local_subject() -> None:
    offenders = undoored_methods(DOOR.read_text(encoding="utf-8"))
    assert not offenders, (
        f"these reach a stash-box without asking {THE_GUARD} first, so a file or a record marked "
        "kept local would be sent anyway: " + ", ".join(offenders)
    )


def test_the_check_catches_a_method_with_no_door() -> None:
    """A planted method with no door is caught."""
    planted = """
class Service:
    async def ask(self, box):
        return await self._adapter.search(box, "anybody")
"""
    assert undoored_methods(planted) == ["ask"]


def test_the_check_passes_a_method_that_refuses_first() -> None:
    mended = """
class Service:
    async def ask(self, box, about):
        await self._nothing_leaves(about)
        return await self._adapter.search(box, "anybody")
"""
    assert undoored_methods(mended) == []
