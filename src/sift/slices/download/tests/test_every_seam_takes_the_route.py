# SPDX-License-Identifier: AGPL-3.0-or-later
"""Nothing in this slice reaches the network without a route. Read from the slice's own source:
any function accepting a route must be called with one, so new functions enrol themselves.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SLICE = Path(__file__).resolve().parents[1]
ROUTE = "proxy"

#: Where a route genuinely does not apply, with the reason. A name here is a claim that the call
#: cannot reach the network, not that passing one would be inconvenient.
EXEMPT: dict[str, str] = {}


def _sources() -> list[Path]:
    return sorted(path for path in SLICE.rglob("*.py") if "tests" not in path.parts)


def _takes_a_route(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    args = node.args
    named = [*args.args, *args.kwonlyargs, *args.posonlyargs]
    return any(argument.arg == ROUTE for argument in named)


def _called_name(call: ast.Call, modules: set[str]) -> str | None:
    """The name a call reaches for: a bare name, or an attribute of a MODULE this file imported.

    An attribute of anything else (`options.resolve(...)`, a store's own method that happens to
    share a name with the slice's resolver, like the site-options store's `resolve`) is not the
    seam and is not judged.
    """
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        owner = call.func.value
        if isinstance(owner, ast.Name) and owner.id in modules:
            return call.func.attr
    return None


def _imported_modules(tree: ast.Module) -> set[str]:
    """The names this file binds to a module: `import x`, `import x as y`, `from p import mod`."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update((alias.asname or alias.name).split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.update(alias.asname or alias.name for alias in node.names)
    return names


@pytest.fixture(scope="module")
def routed_functions() -> set[str]:
    names: set[str] = set()
    for path in _sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and _takes_a_route(node):
                names.add(node.name)
    return names


def test_the_four_ways_out_all_take_a_route(routed_functions: set[str]) -> None:
    """The check is worthless if it is watching nothing, and it would watch nothing the moment a
    rename slipped past. These four are the ways this slice can reach the internet."""
    for seam in ("guarded_session", "guarded_get", "guarded_post", "build_ytdlp_argv"):
        assert seam in routed_functions, f"{seam} no longer takes a route"


def test_every_call_that_can_take_a_route_is_given_one(routed_functions: set[str]) -> None:
    missing: list[str] = []
    trees = {path: ast.parse(path.read_text(encoding="utf-8")) for path in _sources()}
    for path, tree in trees.items():
        modules = _imported_modules(tree)
        local = _calls_to_a_local_function(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or id(node) in local:
                continue
            name = _called_name(node, modules)
            if name is None or name not in routed_functions or name in EXEMPT:
                continue
            keywords = {keyword.arg for keyword in node.keywords}
            if ROUTE in keywords or None in keywords:
                continue
            positional = len(node.args)
            # The definition may live in another file of the slice, imported by name.
            if any(_passes_route_positionally(one, name, positional) for one in trees.values()):
                continue
            missing.append(f"{path.relative_to(SLICE)}:{node.lineno} calls {name}()")
    assert not missing, (
        "these calls can reach the network and were not told which way out to use, so they would "
        "go out of the machine's own address on a site routed through a tunnel:\n  "
        + "\n  ".join(missing)
    )


def _calls_to_a_local_function(tree: ast.Module) -> set[int]:
    """The calls that reach a function defined inside the caller's own function and taking no
    route: a helper that closes over a session already given its way out. Its name may be a
    seam's name, and the call is to the helper, not to the seam."""
    local: set[int] = set()
    for outer in ast.walk(tree):
        if not isinstance(outer, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        helpers = {
            inner.name
            for inner in ast.walk(outer)
            if isinstance(inner, ast.FunctionDef | ast.AsyncFunctionDef)
            and inner is not outer
            and not _takes_a_route(inner)
        }
        for call in ast.walk(outer):
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id in helpers
            ):
                local.add(id(call))
    return local


def _passes_route_positionally(tree: ast.Module, name: str, positional: int) -> bool:
    """Whether that many positional arguments already reach the route parameter.

    Some of these are internal helpers whose route is an ordinary positional argument rather than a
    keyword one. Reading the definition is what makes the check honest about them instead of forcing
    a style on every private function in the slice.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name:
            names = [argument.arg for argument in [*node.args.posonlyargs, *node.args.args]]
            if names and names[0] in {"self", "cls"}:
                names = names[1:]  # the receiver is not one of the caller's arguments
            if ROUTE in names and names.index(ROUTE) < positional:
                return True
    return False
