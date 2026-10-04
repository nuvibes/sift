# SPDX-License-Identifier: AGPL-3.0-or-later
"""The gate that refuses a style rule matching nothing, tested on a planted one and on the tree.

A control whose rules stopped matching comes out in the browser's own chrome while the markup and
the stylesheet each still read correctly; a renamed ancestor class can silence many rules at once.
This runs before anything is drawn, where page checks see only the screens somebody opened.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.gates import the_client_tree

pytestmark = [pytest.mark.gate]

REPO = Path(__file__).resolve().parents[2]
FRONTEND = REPO / "frontend"
GATE = FRONTEND / "scripts" / "check_no_dead_css.js"

#: A component with one rule that reaches nothing. Written into the real source tree, because the
#: checker reads the project's own configuration and a copy of it would be a different project.
PLANTED = FRONTEND / "src" / "lib" / "components" / "GateFixtureDeadCss.svelte"
_PLANT = """<div class="alive">nothing</div>

<style>
\t.alive {
\t\tcolor: var(--sift-ink);
\t}

\t.nothing-wears-this {
\t\tcolor: var(--sift-ink);
\t}
</style>
"""


def _run() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["node", str(GATE)],
        cwd=FRONTEND,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )


def test_the_tree_has_no_rule_that_matches_nothing() -> None:
    assert GATE.is_file(), "the gate script is not where this test expects it"
    # Sole use of the tree, or this reads a fixture another gate's test planted a moment ago and
    # reports a dead rule in a file that is already gone.
    with the_client_tree():
        answer = _run()

    assert answer.returncode == 0, f"{answer.stdout}\n{answer.stderr}"


def test_a_planted_dead_rule_is_caught() -> None:
    """A gate nobody has watched fail is a gate nobody knows the shape of."""
    with the_client_tree():
        PLANTED.write_text(_PLANT, encoding="utf-8")
        try:
            answer = _run()
        finally:
            PLANTED.unlink(missing_ok=True)

    assert answer.returncode == 1, "the gate passed a rule that reaches no element"
    assert "nothing-wears-this" in answer.stderr
