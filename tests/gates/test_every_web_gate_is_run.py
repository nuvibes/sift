# SPDX-License-Identifier: AGPL-3.0-or-later
"""A browser-client gate that nothing runs is not a gate.

Two runners run the client's gates: `scripts/ci-local.sh`, one step per gate, and the workflows,
which run every structural gate at once through `npm run gate:structure` (frontend/scripts/gates.js
runs each `check_*.js` beside it but the few it names in NOT_HERE) plus those few as steps of their
own. This holds the two to the same set, read through gates.js rather than from a copy of its list,
and catches the one thing a shared list would not: a gate in `package.json` that neither runs.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.gate, pytest.mark.unit]

REPO = Path(__file__).resolve().parents[2]
PACKAGE = REPO / "frontend" / "package.json"
LOCAL = REPO / "scripts" / "ci-local.sh"
WORKFLOWS = REPO / ".github" / "workflows"
GATES_JS = REPO / "frontend" / "scripts" / "gates.js"

#: Gates that are not a step of either runner's client checks, and where they run instead.
ELSEWHERE = {
    "gate:client-coverage": "its two halves are separate steps in both runners",
    "gate:structure": "the runner of the structural gates: the pre-commit hook and the workflows",
}


def _scripts() -> dict[str, str]:
    scripts: dict[str, str] = json.loads(PACKAGE.read_text(encoding="utf-8"))["scripts"]
    return scripts


def _declared() -> set[str]:
    """Every `gate:*` script in the client's package.json."""
    return {name for name in _scripts() if name.startswith("gate:")}


def _structural() -> set[str]:
    """The gates `gate:structure` runs: every one whose script is a check_*.js gates.js does not
    leave out."""
    source = GATES_JS.read_text(encoding="utf-8")
    left_out = set(re.findall(r"'(check_\w+\.js)':", source))
    assert left_out, "gates.js no longer lists what it leaves out where this test reads it"
    found = set()
    for name, command in _scripts().items():
        script = re.fullmatch(r"node scripts/(check_\w+\.js)", command.strip())
        if name.startswith("gate:") and script and script.group(1) not in left_out:
            found.add(name)
    return found


def _named(text: str) -> set[str]:
    """Every `npm run gate:...` a runner invokes, however it spells the invocation."""
    return set(re.findall(r"npm run (gate:[a-z0-9-]+)", text))


def _run_locally() -> set[str]:
    return _named(LOCAL.read_text(encoding="utf-8"))


def _run_by_the_workflows() -> set[str]:
    named = set()
    for path in sorted(WORKFLOWS.glob("*.yml")):
        named |= _named(path.read_text(encoding="utf-8"))
    return named | (_structural() if "gate:structure" in named else set())


def test_the_structural_runner_runs_most_gates() -> None:
    """A reader of gates.js that found nothing would make the rules below vacuous."""
    assert len(_structural()) >= 20, sorted(_structural())


def test_every_declared_gate_is_run_by_the_local_runner() -> None:
    missing = _declared() - _run_locally() - set(ELSEWHERE)
    assert not missing, (
        f"these gates exist and scripts/ci-local.sh never runs them: {sorted(missing)}. "
        "Add a step, or name it in ELSEWHERE with where it does run."
    )


def test_every_declared_gate_is_run_by_the_workflows() -> None:
    """A gate in the local runner alone is a gate a pull request skips."""
    missing = _declared() - _run_by_the_workflows() - set(ELSEWHERE)
    assert not missing, (
        f"these gates exist and no workflow runs them: {sorted(missing)}. Make it a check_*.js "
        "gates.js runs, add a step, or name it in ELSEWHERE with where it does run."
    )


def test_the_two_runners_run_the_same_gates() -> None:
    """Stated directly, because "these two disagree" is the failure a reader looks for."""
    local = _run_locally() - set(ELSEWHERE)
    remote = _run_by_the_workflows() - set(ELSEWHERE)
    assert local == remote, (
        f"only the local runner runs {sorted(local - remote)}; "
        f"only the workflows run {sorted(remote - local)}"
    )


def test_nothing_is_excused_that_no_longer_exists() -> None:
    """A stale excuse reads as coverage. If a gate is deleted, its line here must go with it."""
    gone = set(ELSEWHERE) - _declared()
    assert not gone, f"ELSEWHERE excuses gates that no longer exist: {sorted(gone)}"
