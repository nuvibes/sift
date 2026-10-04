# SPDX-License-Identifier: AGPL-3.0-or-later
"""The state-matrix gate, watched refusing the two faults it exists to catch.

A primitive whose stylesheet draws a state the gallery's matrix does not draw, and a gallery cell
for a state the primitive does not have. Each is planted into the real tree (a primitive under the
primitives folder and a row beside the gallery) and removed again, under the lock every plant
takes, because the gate reads the project's own layout.

The design gallery is optional and not part of this tree; without it there is nothing to plant a
row beside, so these are skipped.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from tests.gates import PLANTED_PREFIX, the_client_tree

pytestmark = [pytest.mark.gate, pytest.mark.unit]

REPO = Path(__file__).resolve().parents[2]
FRONTEND = REPO / "frontend"
SOURCE = FRONTEND / "src"
GALLERY = SOURCE / "routes" / "design"

NAME = f"{PLANTED_PREFIX}States"
PRIMITIVE = SOURCE / "lib" / "components" / "common" / f"{NAME}.svelte"
ROW = GALLERY / f"{NAME}Row.svelte"

# A primitive that draws a hover and a press, and nothing else.
PRIMITIVE_BODY = (
    "<button class='x'>a fixture</button>\n\n"
    "<style>\n"
    "\t.x:hover {\n\t\tcolor: red;\n\t}\n\n"
    "\t.x:active {\n\t\tcolor: blue;\n\t}\n"
    "</style>\n"
)


def _row(states: str) -> str:
    return (
        "<script lang='ts'>\n\timport StateRow from './StateRow.svelte';\n</script>\n\n"
        f'<StateRow of="{NAME}" states={{[{states}]}}>\n'
        "\t{#snippet draw()}<span>a fixture</span>{/snippet}\n"
        "</StateRow>\n"
    )


@contextmanager
def _planted(row_states: str | None, marker: str = "") -> Iterator[None]:
    """The fixture primitive, with its row when `row_states` is given, and `marker` above it."""
    with the_client_tree():
        PRIMITIVE.write_text(marker + PRIMITIVE_BODY, encoding="utf-8")
        if row_states is not None:
            ROW.write_text(_row(row_states), encoding="utf-8")
        try:
            yield
        finally:
            PRIMITIVE.unlink(missing_ok=True)
            ROW.unlink(missing_ok=True)


def _run() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["node", str(FRONTEND / "scripts" / "check_state_matrix.js")],
        cwd=FRONTEND,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


needs_the_gallery = pytest.mark.skipif(
    not GALLERY.is_dir(), reason="the optional design gallery is not here"
)


@needs_the_gallery
def test_a_state_the_stylesheet_draws_and_the_gallery_does_not_is_caught() -> None:
    """The fixture draws a press; its row has no pressed cell."""
    with _planted("'rest', 'hover'"):
        answer = _run()

    assert answer.returncode == 1, "the gate passed a primitive whose press the gallery never draws"
    assert f"{NAME}.svelte: its stylesheet draws pressed" in answer.stderr


@needs_the_gallery
def test_a_cell_for_a_state_the_primitive_lacks_is_caught() -> None:
    """The fixture has no busy state; its row draws one anyway."""
    with _planted("'rest', 'hover', 'pressed', 'busy'"):
        answer = _run()

    assert answer.returncode == 1, "the gate passed a cell for a state the primitive does not have"
    assert f"{NAME}'s row draws busy" in answer.stderr
    assert "its stylesheet draws" not in answer.stderr


@needs_the_gallery
def test_a_primitive_that_draws_a_state_and_has_no_row_is_caught() -> None:
    """Every primitive is in the matrix: one with a state and no row at all is refused."""
    with _planted(None):
        answer = _run()

    assert answer.returncode == 1, "the gate passed a primitive the matrix does not draw"
    assert f"{NAME}.svelte  (hover, pressed)" in answer.stderr


@needs_the_gallery
def test_a_primitive_the_gallery_cannot_hold_is_not_asked_for_a_row() -> None:
    """The one toaster the layout draws says so, and is not counted as missing."""
    marker = "<!-- NOT ON THE GALLERY: a fixture standing in for a singleton. -->\n"
    with _planted(None, marker):
        answer = _run()

    assert answer.returncode == 0, answer.stderr
