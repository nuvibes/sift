# SPDX-License-Identifier: AGPL-3.0-or-later
"""There is one grid of files, and no screen builds a second one.

Every screen that is a grid of files draws `AssetGrid`, where layout, picking, the context menu and
the action bar live; a second copy forks silently, one getting a feature the other lacks. A page
under `routes/` holding both a `Selection` and an `ActionBar` must be drawing the shared grid.
Tiles alone (a wall of collections) are not a grid of files.
"""

from __future__ import annotations

import re
import textwrap
from pathlib import Path

import pytest

pytestmark = [pytest.mark.gate, pytest.mark.unit]

REPO = Path(__file__).resolve().parents[2]
ROUTES = REPO / "frontend" / "src" / "routes"

#: The component every screen made of files draws.
GRID = "AssetGrid"

#: Both together, not either: a wall of people offers a bulk rename with `Selection` alone.
MACHINERY = ("Selection", "ActionBar")

#: Screens allowed to hold both, each with what it is a grid OF.
ALLOWED: dict[str, str] = {
    "frontend/src/routes/people/identified/+page.svelte": (
        "A wall of faces, not of files. The things picked here are face groups, and what the bar "
        "does to them (merge, name, set aside) has no meaning for an asset."
    ),
    "frontend/src/routes/people/identified/[id]/+page.svelte": (
        "The same, for one person's faces: the rows are appearances rather than files."
    ),
    "frontend/src/routes/downloads/+page.svelte": (
        "A queue, not a grid of files. The rows are downloads (links being fetched, some with no "
        "file yet and some that never will have one), drawn as DataRows, and what the bar does to "
        "the picked ones (pause, resume, try again, move to the front, remove from the list, "
        "cancel) is about the fetch, with no meaning for an asset."
    ),
    "frontend/src/routes/collections/[id]/+page.svelte": (
        "A HAND-ARRANGED wall: the order is the collection's own and a row can be dragged to a new "
        "place, which the shared grid has no mode for (it draws a query's order). It takes the "
        "shared verbs, bar and tile menu from FileVerbs, so what it keeps of its own is "
        "the arrangement alone; the day AssetGrid learns an arranged order this entry goes."
    ),
}

# The design gallery is optional and not part of this tree, and an excuse for a missing file is
# refused, so its entry is added only when it is present.

if (REPO / "frontend/src/routes/design/+page.svelte").is_file():
    ALLOWED["frontend/src/routes/design/+page.svelte"] = (
        "The gallery, which draws every shared component as a specimen of itself. Its action bar "
        "is given a count by hand and acts on nothing, and its selection holds the specimens on "
        "the page rather than any file. A screen whose subject IS the components cannot reach "
        "them through a component."
    )


def _pages() -> list[Path]:
    return sorted(ROUTES.rglob("+page.svelte"))


def _imports(source: str) -> set[str]:
    """What a page imports, read from its import lines only."""
    found: set[str] = set()
    for line in re.findall(r"^\s*import\s+.*?;\s*$", source, re.MULTILINE | re.DOTALL):
        found.update(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", line))
    return found


def test_no_screen_builds_a_second_grid_of_files() -> None:
    offenders: list[str] = []
    for page in _pages():
        relative = page.relative_to(REPO).as_posix()
        if relative in ALLOWED:
            continue
        brought_in = _imports(page.read_text(encoding="utf-8"))
        if not all(name in brought_in for name in MACHINERY):
            continue
        if GRID in brought_in:
            continue
        offenders.append(relative)

    assert not offenders, textwrap.dedent(
        f"""
        These screens assemble their own grid of files instead of drawing the one component:

        {chr(10).join("          " + name for name in offenders)}

        Everything that makes a grid a grid (the layout, the selection gesture, the action bar,
        the menu) lives in {GRID}. A screen that needs a grid of files draws it and passes the
        query it wants; anything that component cannot yet do, it gains.

        A second copy does not fail. It drifts: one gets the next fix and the other does not, and
        nothing says which is right.
        """
    ).strip()


def test_the_gate_can_see_the_shape_it_is_for() -> None:
    """A page built the wrong way is caught."""
    invented = (
        "import { ActionBar, Selection } from '$lib/components/common';\nimport Tile from 'x';"
    )

    brought_in = _imports(invented)

    assert all(name in brought_in for name in MACHINERY)
    assert GRID not in brought_in


def test_every_screen_made_of_files_is_still_found_by_the_scan() -> None:
    """The scan still finds every screen made of files."""
    pages = _pages()

    assert len(pages) > 5
    assert any(GRID in _imports(page.read_text(encoding="utf-8")) for page in pages)
