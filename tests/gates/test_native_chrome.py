# SPDX-License-Identifier: AGPL-3.0-or-later
"""The gate that keeps the browser's own chrome out of the interface, tested both ways: planted
violations of each kind are caught and the real tree passes. The finest line is a `title` attribute
on an HTML element (an operating-system tooltip) against a `title` prop on a component."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tests.gates import the_client_tree

pytestmark = [pytest.mark.gate]

REPO = Path(__file__).resolve().parents[2]
GATE = REPO / "frontend" / "scripts" / "check_no_native_chrome.js"


def run_gate(project: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["node", str(project / "scripts" / "check_no_native_chrome.js")],
        cwd=project,
        capture_output=True,
        text=True,
        check=False,
    )


#: A stylesheet satisfying everything the gate asks of the app's own: the scrollbar rules, checked
#: for their PRESENCE, and the text-box baseline, so each quiet case fails only on what it is about.
DRESSED = """\
* {
  scrollbar-width: thin;
  scrollbar-color: var(--thumb) transparent;
}
::-webkit-scrollbar-thumb {
  background: var(--thumb);
}
input:not([type='checkbox']):not([type='radio']),
textarea,
select {
  background: var(--surface);
  border: 1px solid var(--line);
  color: var(--ink);
}
"""


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A throwaway frontend with the real gate script in it, and nothing else it does not need."""
    if shutil.which("node") is None:  # pragma: no cover - node is installed in CI
        pytest.skip("node is not installed")

    (tmp_path / "scripts").mkdir()
    (tmp_path / "src").mkdir()
    shutil.copy(GATE, tmp_path / "scripts" / "check_no_native_chrome.js")
    (tmp_path / "src" / "app.css").write_text(DRESSED, encoding="utf-8")
    return tmp_path


def component(project: Path, name: str, body: str) -> None:
    (project / "src" / name).write_text(body, encoding="utf-8")


# --- it fires ---------------------------------------------------------------------------


def test_it_catches_a_title_attribute_on_an_element(project: Path) -> None:
    component(project, "Bad.svelte", '<button type="button" title="Hidden">x</button>\n')

    result = run_gate(project)
    assert result.returncode != 0
    assert "title=" in result.stderr


def test_it_catches_a_title_hiding_behind_an_arrow_function(project: Path) -> None:
    """An inline arrow handler's `>` does not end the tag before the attribute after it."""
    component(
        project,
        "Sneaky.svelte",
        '<button onclick={() => (open = !open)} title="Hidden">x</button>\n',
    )

    result = run_gate(project)
    assert result.returncode != 0
    assert "title=" in result.stderr


def test_it_catches_a_native_dialog(project: Path) -> None:
    component(project, "Shouty.svelte", "<script>\n  confirm('sure?');\n</script>\n")

    result = run_gate(project)
    assert result.returncode != 0
    assert "confirm()" in result.stderr


def test_it_catches_a_window_qualified_dialog_even_where_a_local_one_exists(
    project: Path,
) -> None:
    """A file that defines its own `confirm` is allowed to call it. It is not allowed to reach
    past its own and ask the browser."""
    component(
        project,
        "Mixed.svelte",
        "<script>\n  function confirm() {}\n  window.confirm('sure?');\n</script>\n",
    )

    result = run_gate(project)
    assert result.returncode != 0
    assert "confirm()" in result.stderr


def test_it_catches_a_focus_ring_removed_and_not_replaced(project: Path) -> None:
    component(project, "Blind.svelte", "<style>\n  .x { outline: none; }\n</style>\n")

    result = run_gate(project)
    assert result.returncode != 0
    assert "focus" in result.stderr


def test_it_catches_the_global_stylesheet_no_longer_dressing_the_scrollbars(project: Path) -> None:
    """The scrollbar rules are checked for their PRESENCE, so deleting them from the stylesheet
    fails."""
    (project / "src" / "app.css").write_text("body { margin: 0; }\n", encoding="utf-8")

    result = run_gate(project)
    assert result.returncode != 0
    assert "no scrollbar-width" in result.stderr
    assert "no scrollbar-color" in result.stderr
    assert "::-webkit-scrollbar-thumb" in result.stderr


def test_the_global_stylesheet_turning_the_scrollbar_off_is_not_dressing_it(project: Path) -> None:
    """`scrollbar-width: none` has the property and removes the scrollbar, so the value is read."""
    (project / "src" / "app.css").write_text(
        "* { scrollbar-width: none; scrollbar-color: var(--t) transparent; }\n"
        "::-webkit-scrollbar-thumb { background: var(--t); }\n",
        encoding="utf-8",
    )

    result = run_gate(project)
    assert result.returncode != 0
    assert "no scrollbar-width" in result.stderr


def test_a_comment_describing_the_rules_does_not_count_as_having_them(project: Path) -> None:
    """A comment naming all three properties does not satisfy the check: the rules must be real."""
    (project / "src" / "app.css").write_text(
        "/* scrollbar-width: thin; scrollbar-color: var(--t) transparent;\n"
        "   ::-webkit-scrollbar-thumb { background: var(--t); } */\n",
        encoding="utf-8",
    )

    result = run_gate(project)
    assert result.returncode != 0
    assert "no scrollbar-width" in result.stderr


def test_it_catches_a_component_hiding_its_own_scrollbar(project: Path) -> None:
    """A scrollbar nobody can see is a box whose length nobody can judge."""
    component(project, "Endless.svelte", "<style>\n  .pane { scrollbar-width: none; }\n</style>\n")

    result = run_gate(project)
    assert result.returncode != 0
    assert "a hidden scrollbar" in result.stderr


# --- it stays quiet ---------------------------------------------------------------------


def test_a_title_prop_on_a_component_is_not_a_tooltip(project: Path) -> None:
    """The false positive that would make the gate unusable: there are many of these and they are
    all correct."""
    component(
        project,
        "Fine.svelte",
        '<Placeholder title="Browse" />\n<ConfirmDialog title={question} consequence="x" />\n',
    )

    result = run_gate(project)
    assert result.returncode == 0, result.stderr


def test_a_local_function_named_confirm_is_allowed(project: Path) -> None:
    component(
        project,
        "Own.svelte",
        "<script>\n  function confirm() {\n    open = false;\n  }\n</script>\n",
    )

    result = run_gate(project)
    assert result.returncode == 0, result.stderr


def test_removing_the_outline_with_a_focus_style_beside_it_is_allowed(project: Path) -> None:
    """The normal case. Every control in the app does this: the default ring goes and the app's
    own takes its place."""
    component(
        project,
        "Sighted.svelte",
        "<style>\n  .x { outline: none; }\n  .x:focus-visible { box-shadow: var(--focus-ring); }\n</style>\n",
    )

    result = run_gate(project)
    assert result.returncode == 0, result.stderr


def test_the_gate_passes_on_the_real_tree() -> None:
    """The half that makes the other half mean something."""
    if shutil.which("node") is None:  # pragma: no cover - node is installed in CI
        pytest.skip("node is not installed")

    # Same reason as the dead-CSS gate's own whole-tree check: another test's fixture is a real
    # file in this tree while it is live, and reading one here reports it against nothing.
    with the_client_tree():
        result = subprocess.run(
            ["node", "scripts/check_no_native_chrome.js"],
            cwd=REPO / "frontend",
            capture_output=True,
            text=True,
            check=False,
        )
    assert result.returncode == 0, f"the interface is showing native chrome:\n{result.stderr}"
