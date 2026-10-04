# SPDX-License-Identifier: AGPL-3.0-or-later
"""The server and the client agree about which tabs an entity page has, in order.

`TABS_FOR` in `slices/related/router.py` decides which numbers the counts endpoint answers with, and
`TABS_FOR` in `frontend/src/lib/entity/related.svelte.ts` which tabs are drawn: out of step, a tab
has
no number, or a number is counted for nothing. Files is first on every page.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.gate, pytest.mark.unit]

REPO = Path(__file__).resolve().parents[2]
CLIENT = REPO / "frontend" / "src" / "lib" / "entity" / "related.svelte.ts"

#: The client's declaration as text, matched rather than parsed.
_CLIENT_BLOCK = re.compile(
    r"const TABS_FOR: Record<EntityKind, RelatedKind\[\]> = \{(.*?)\n\};", re.S
)
_CLIENT_ROW = re.compile(r"^\s*(\w+):\s*\[(.*?)\],?\s*$", re.M)


def _server_tabs() -> dict[str, tuple[str, ...]]:
    from sift.slices.related import TABS_FOR

    return dict(TABS_FOR)


def _client_tabs() -> dict[str, tuple[str, ...]]:
    source = CLIENT.read_text(encoding="utf-8")
    block = _CLIENT_BLOCK.search(source)
    assert block is not None, (
        f"{CLIENT} no longer declares `const TABS_FOR: Record<EntityKind, RelatedKind[]>`. "
        "This gate reads that declaration by name; if the tab set moved, move this with it: "
        "a gate that cannot find its subject must fail, not pass."
    )
    found: dict[str, tuple[str, ...]] = {}
    for row in _CLIENT_ROW.finditer(block.group(1)):
        kind = row.group(1)
        walls = tuple(ast.literal_eval(f"[{row.group(2)}]"))
        found[kind] = walls
    assert found, "the client's TABS_FOR was found and parsed as empty, which cannot be right"
    return found


def test_both_sides_know_about_the_same_kinds_of_page() -> None:
    assert sorted(_server_tabs()) == sorted(_client_tabs())


def test_every_page_shows_exactly_the_walls_the_server_counts() -> None:
    """Same walls, same order, on every kind of entity page."""
    server, client = _server_tabs(), _client_tabs()
    for kind in sorted(server):
        assert server[kind] == client[kind], (
            f"the {kind} page draws {client[kind]} and the counts endpoint answers for "
            f"{server[kind]}. A tab the server does not count has no number; a wall the client "
            "does not draw is a query run for nobody."
        )
