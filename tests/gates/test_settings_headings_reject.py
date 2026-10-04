# SPDX-License-Identifier: AGPL-3.0-or-later
"""The settings-headings gate, watched rejecting the thing it exists to catch.

`frontend/scripts/check_settings_headings.js` refuses a heading written by hand on a settings
screen: a section's title is the frame's, drawn from the section's declaration, and a group's
heading is `SectionHeading`. A ratchet standing at zero reports zero whether it can see or not, so
this plants each shape it counts (a heading element, a heading style rule) into the real
`settings-ui/` folder, where the gate looks, and asks it.

Same discipline as `test_web_gates_reject.py`: the plant carries the `GateFixture` prefix, it is
written under the lock that keeps every other tree-reading gate out while it exists, and the
assertions name the fixture rather than trusting the exit code alone.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from tests.gates import PLANTED_PREFIX, the_client_tree

pytestmark = [pytest.mark.gate]

REPO = Path(__file__).resolve().parents[2]
FRONTEND = REPO / "frontend"
PANES = FRONTEND / "src" / "lib" / "settings-ui"
GATE = FRONTEND / "scripts" / "check_settings_headings.js"


def _run() -> subprocess.CompletedProcess[str]:
    assert GATE.is_file(), "the settings-headings gate is not where this test expects it"
    return subprocess.run(
        ["node", str(GATE)],
        cwd=FRONTEND,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


@contextmanager
def _planted(name: str, body: str) -> Iterator[None]:
    assert name.startswith(PLANTED_PREFIX), "a plant must carry the prefix every gate skips"
    where = PANES / name
    with the_client_tree():
        where.write_text(body, encoding="utf-8")
        try:
            yield
        finally:
            where.unlink(missing_ok=True)


def test_a_heading_written_by_hand_on_a_pane_is_caught() -> None:
    """The element: a pane writing its own `<h2>` instead of passing its words to the component."""
    with _planted("GateFixtureHandHeading.svelte", "<h2>Things</h2>\n<p>Rows.</p>\n"):
        answer = _run()

    assert answer.returncode == 1, "the settings-headings gate passed a hand-written heading"
    assert "GateFixtureHandHeading" in answer.stderr


def test_a_heading_style_written_by_hand_on_a_pane_is_caught() -> None:
    """The LOOK: a pane deciding for itself what a heading looks like, with no heading in sight:
    a rule reaching into a child component's `h3` is how one screen grows a heading size of its
    own."""
    body = (
        "<div class='block'><p>Rows.</p></div>\n\n"
        "<style>\n"
        "\t.block :global(h3) {\n"
        "\t\tfont: var(--text-body-sm);\n"
        "\t}\n"
        "</style>\n"
    )
    with _planted("GateFixtureHandHeadingRule.svelte", body):
        answer = _run()

    assert answer.returncode == 1, "the settings-headings gate passed a hand-written heading rule"
    assert "GateFixtureHandHeadingRule" in answer.stderr


def test_the_component_is_not_counted() -> None:
    """The way it is meant to be done costs the number nothing, or the ratchet punishes the fix."""
    body = (
        "<script lang='ts'>\n"
        "\timport SectionHeading from '$lib/components/common/SectionHeading.svelte';\n"
        "</script>\n\n"
        "<SectionHeading>Things</SectionHeading>\n"
    )
    with _planted("GateFixtureComponentHeading.svelte", body):
        answer = _run()

    assert "GateFixtureComponentHeading" not in answer.stderr, (
        "a heading drawn by SectionHeading was counted as hand-written"
    )
