# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every module that ships is measured by a coverage gate of its own.

Coverage held at 100% per module is worth something only if every module has a gate; a module
without one can lose a branch under a green summary. A text check over the shared gate list, for a
module added later by somebody who never read this file.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.gate, pytest.mark.regression, pytest.mark.unit]

REPO = Path(__file__).resolve().parents[2]
SOURCE = REPO / "src" / "sift"
GATES = REPO / "scripts" / "coverage_gates.sh"
PACKAGE_INI = REPO / "scripts" / "coverage_package.ini"
PYPROJECT = REPO / "pyproject.toml"


def _module_name(path: Path) -> str:
    """`src/sift/kernel/db.py` -> `sift.kernel.db`, a package's own file as the package."""
    parts = path.relative_to(REPO / "src").with_suffix("").parts
    name = ".".join(parts)
    return name.removesuffix(".__init__")


def _measured_names() -> set[str]:
    """Every `--cov=` target in the shared gate list."""
    return set(re.findall(r"--cov=([A-Za-z0-9_.]+)", GATES.read_text(encoding="utf-8")))


def _named_by_include() -> set[str]:
    """The single files gated through an include list instead: a package's `__init__.py` cannot be
    named to `--cov=`."""
    found = re.search(r"include =\n((?:\s+\S+\n)+)", PACKAGE_INI.read_text(encoding="utf-8"))
    assert found is not None, "coverage_package.ini no longer declares an include list"
    return {_module_name(REPO / line.strip()) for line in found.group(1).splitlines()}


def _omitted_prefixes() -> tuple[str, ...]:
    """What the project omits from coverage, read from where the setting is declared."""
    text = PYPROJECT.read_text(encoding="utf-8")
    found = re.search(r"\nomit = \[\n((?:.*\n)*?)\]", text)
    assert found is not None, "pyproject no longer declares a coverage omit list"
    prefixes = []
    for entry in re.findall(r'"([^"]+)"', found.group(1)):
        if "tests" in entry:
            continue
        prefixes.append(_module_name(REPO / entry.replace("/*", "")))
    return tuple(prefixes)


def _shipping_modules() -> list[str]:
    names = []
    for path in sorted(SOURCE.rglob("*.py")):
        if "tests" in path.relative_to(SOURCE).parts:
            continue
        names.append(_module_name(path))
    return names


def test_there_are_modules_to_check() -> None:
    """A floor: there are hundreds of modules."""
    assert len(_shipping_modules()) > 200


def test_the_gate_list_names_something() -> None:
    """The gate list is read."""
    assert len(_measured_names()) > 50


def test_every_module_that_ships_is_measured_by_a_gate() -> None:
    omitted = _omitted_prefixes()
    measured = _measured_names()
    by_include = _named_by_include()

    ungated = []
    for name in _shipping_modules():
        if any(name == one or name.startswith(f"{one}.") for one in omitted):
            continue
        if name in by_include:
            continue
        if any(name == one or name.startswith(f"{one}.") for one in measured):
            continue
        ungated.append(name)

    assert not ungated, (
        "\nThese modules ship and no coverage gate measures them.\n\n"
        "A module with no gate can lose a branch and the summary still reads green.\n\n"
        "Add a `_cov_<name>` function to scripts/coverage_gates.sh naming its tests and its\n"
        "`--cov=` target, and add its label to COV_LABELS. The runner refuses to start if the\n"
        "two lists disagree about how many there are.\n\n  " + "\n  ".join(ungated) + "\n"
    )


def test_nothing_is_omitted_that_actually_ships() -> None:
    """Only test-support code may be omitted: the built image carries nothing under `testing/`."""
    for omitted in _omitted_prefixes():
        assert omitted == "sift.testing", (
            f"{omitted} is omitted from coverage. Only the test-support package may be, and only\n"
            "because it does not ship. Anything else needs a gate."
        )
