# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every migration step in a schema initializer is its own `if`, never an `elif`, and every
component says which version its first step creates.

The kernel stamps the declared version once the initializer returns, whatever it did, so chained
steps (`if on_disk < 1: ... elif on_disk < 2: ...`) apply ONE step per boot and stamp the rest as
done: a column silently never added. Checked over the registry, so a new component is covered.

A component's first step creates its tables at its `baseline`; a library recorded between 1 and the
baseline is refused before anything changes (`db.too_old_to_bring_forward`), so every component
declares one.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

import sift.main  # noqa: F401 (imported for its side effect: every component registers itself)
from sift.kernel.db import STEPS_LAST_SHIPPED_IN, registered_components, too_old_to_bring_forward

pytestmark = [pytest.mark.gate, pytest.mark.unit]


def chained_branches(source: str) -> list[int]:
    """The line numbers of the version branches that are not independent: a top-level step with an
    `elif` or an `else`. A branch inside a step's body is that step's logic."""
    tree = ast.parse(textwrap.dedent(source))
    function = tree.body[0]
    if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
        raise AssertionError("that source is not a single function definition")
    return [
        statement.lineno
        for statement in function.body
        if isinstance(statement, ast.If) and statement.orelse
    ]


@pytest.mark.regression
def test_no_registered_initializer_chains_its_steps() -> None:
    offenders: list[str] = []
    for component in registered_components().values():
        source = inspect.getsource(component.initialize)
        # Relative to the function, for somebody reading the failure beside it.
        for line in chained_branches(source):
            offenders.append(f"{component.name} ({component.initialize.__qualname__}), step {line}")

    assert not offenders, (
        "these migration steps hang off a previous step, so a database more than one version "
        "behind applies the first and silently skips the rest: " + ", ".join(offenders)
    )


def test_the_check_catches_a_planted_chain() -> None:
    """A planted chain is caught."""
    planted = """
        async def initialize(connection, on_disk):
            if on_disk < 1:
                await connection.execute("CREATE TABLE t (id TEXT)")
            elif on_disk < 2:
                await connection.execute("ALTER TABLE t ADD COLUMN a TEXT")
    """
    # The `if` the `elif` hangs off, counted from the `def` (line 2 after the leading newline).
    assert chained_branches(planted) == [3]


def test_the_check_passes_independent_steps() -> None:
    clean = """
        async def initialize(connection, on_disk):
            if on_disk < 1:
                await connection.execute("CREATE TABLE t (id TEXT)")
            if 0 < on_disk < 2:
                await connection.execute("ALTER TABLE t ADD COLUMN a TEXT")
            if 0 < on_disk < 3:
                await connection.execute("ALTER TABLE t ADD COLUMN b TEXT")
    """
    assert chained_branches(clean) == []


def test_a_branch_inside_a_step_is_left_alone() -> None:
    """A branch inside a step is left alone."""
    nested = """
        async def initialize(connection, on_disk):
            if on_disk < 1:
                if connection is None:
                    return
                else:
                    await connection.execute("CREATE TABLE t (id TEXT)")
    """
    assert chained_branches(nested) == []


def test_every_component_says_which_version_its_first_step_creates() -> None:
    missing = sorted(
        name for name, component in registered_components().items() if component.baseline is None
    )
    assert not missing, (
        "these components declare no baseline, so a library older than their first step would "
        "be handed to it: " + ", ".join(missing)
    )


def test_a_library_below_a_baseline_is_named_and_one_at_it_is_not() -> None:
    """A library below a baseline is named and one at it is not, against the real registry."""
    at_the_baseline = {
        name: component.baseline or component.version
        for name, component in registered_components().items()
    }
    assert too_old_to_bring_forward(at_the_baseline) is None
    assert too_old_to_bring_forward({}) is None, "a new library has nothing recorded"

    faces = registered_components()["faces"]
    assert faces.baseline is not None and faces.baseline > 1
    refused = too_old_to_bring_forward({**at_the_baseline, "faces": faces.baseline - 1})
    assert refused is not None
    assert f"Sift {STEPS_LAST_SHIPPED_IN}" in refused, "the refusal names the release to use"
    assert "Nothing has been changed" in refused
