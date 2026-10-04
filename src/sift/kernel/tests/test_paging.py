# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where a page asked for by a row begins, when that row may be gone.

A wall writes its page's first card (`from`) and its offset (`near`) into the address; discarding
that card's group is the ordinary way for it to be gone, and `near` keeps somebody on page three.
Every route taking `from` is held to the one function.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from sift.kernel.paging import resume_at

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[4]
SLICES = ROOT / "src" / "sift" / "slices"
SCHEMA = ROOT / "frontend" / "openapi.json"


def test_the_row_itself_wins_over_where_the_page_was() -> None:
    """Found is found: the list moved, the row is still in it, and its place now is the answer."""
    assert resume_at(40, 24) == 40
    assert resume_at(0, 24) == 0, "the row at the very top is found, not missing"


def test_a_row_that_is_gone_opens_where_the_page_was() -> None:
    """The ordinary case: the anchor was the group just discarded. Its neighbours close up, so the
    same offset is the same page with the gap filled."""
    assert resume_at(None, 48) == 48


def test_an_address_naming_neither_opens_the_top() -> None:
    assert resume_at(None, None) == 0


def test_a_negative_offset_is_the_top() -> None:
    """The routes declare `near` as `ge=0`, so this cannot arrive over HTTP; asserted anyway because
    the helper is a kernel function any caller may reach, and a negative offset is an SQL error."""
    assert resume_at(None, -5) == 0


def _routes_taking_from() -> list[tuple[str, str]]:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    found: list[tuple[str, str]] = []
    for path, methods in schema["paths"].items():
        for method, operation in methods.items():
            names = {one["name"] for one in operation.get("parameters", []) if one["in"] == "query"}
            if "from" in names:
                found.append((f"{method.upper()} {path}", "near" if "near" in names else ""))
    return found


def test_every_route_that_takes_from_takes_near_too() -> None:
    """Every route reading `from` reads `near` too: FastAPI drops an undeclared parameter
    silently."""
    routes = _routes_taking_from()
    assert len(routes) >= 14, "the schema should list every wall's route; read the wrong file?"
    missing = [route for route, near in routes if not near]
    assert not missing, f"these take `from` and not `near`: {missing}"


def _own_copies_of_the_rule(source: str) -> list[int]:
    """Lines that resolve an anchor to an offset themselves: `X if at is not None else <number>`."""
    lines: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.IfExp)
            and isinstance(node.test, ast.Compare)
            and isinstance(node.test.left, ast.Name)
            and node.test.left.id == "at"
            and isinstance(node.orelse, ast.Constant)
            and isinstance(node.orelse.value, int)
        ):
            lines.append(node.lineno)
    return lines


def test_no_route_writes_the_rule_out_for_itself() -> None:
    """A rule written out in every router is changed in all of them but one."""
    copies = [
        f"{path.relative_to(ROOT)}:{line}"
        for path in sorted(SLICES.glob("*/router*.py"))
        for line in _own_copies_of_the_rule(path.read_text(encoding="utf-8"))
    ]
    assert not copies, f"resolve an anchor through `resume_at`, not by hand: {copies}"


def test_the_copy_finder_finds_a_copy() -> None:
    """A check that has never failed is indistinguishable from one that cannot."""
    planted = "offset = at if at is not None else 0\n"
    assert _own_copies_of_the_rule(planted) == [1]
    assert _own_copies_of_the_rule("offset = resume_at(at, near)\n") == []
