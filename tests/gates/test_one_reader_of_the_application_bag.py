# SPDX-License-Identifier: AGPL-3.0-or-later
"""Two rules about which direction the code is allowed to depend in.

`app.state` is an untyped bag, and each read of it is an unchecked promise about its type.
`kernel/wiring.py` is the only module that touches it: a part is declared once beside its type and
read through the lookup there. Tests are exempt.

The kernel never imports a feature: a feature's part is declared by that feature, and the parts
several features share are typed by an interface in `kernel/seams`.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.gate, pytest.mark.unit]

REPO = Path(__file__).resolve().parents[2]
SOURCE = REPO / "src" / "sift"

#: The one module allowed to read the bag.
THE_ONE_READER = ("kernel", "wiring.py")


def reads_of_the_bag(source: str) -> list[int]:
    """The lines where this source reaches the application object: the bag ITSELF, so
    `getattr(request.app.state, "x", None)` counts as `request.app.state.x` does. A parsed walk, so
    prose naming `app.state.database` does not."""
    found: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Attribute) or node.attr != "state":
            continue
        holder = node.value
        named_app = isinstance(holder, ast.Attribute) and holder.attr == "app"
        is_app = isinstance(holder, ast.Name) and holder.id == "app"
        if named_app or is_app:
            found.append(node.lineno)
    return found


def _production_files() -> list[Path]:
    return [path for path in sorted(SOURCE.rglob("*.py")) if "tests" not in path.parts]


def test_only_the_wiring_module_reads_the_application_bag() -> None:
    offenders: list[str] = []
    for path in _production_files():
        if path.relative_to(SOURCE).parts == THE_ONE_READER:
            continue
        for line in reads_of_the_bag(path.read_text(encoding="utf-8")):
            offenders.append(f"{path.relative_to(REPO)}:{line}")

    assert not offenders, (
        "\nThese read the application object directly.\n\n"
        "Declare the part beside the type it holds and read it through kernel/wiring.py, so the\n"
        "name is written once and the type is known without promising the type checker anything.\n\n  "
        + "\n  ".join(offenders)
        + "\n"
    )


def test_the_reader_check_catches_a_direct_read() -> None:
    """A planted direct read is caught."""
    assert reads_of_the_bag("service = request.app.state.browse\n") == [1]
    assert reads_of_the_bag("queue = app.state.queue\n") == [1]


def test_the_reader_check_catches_the_read_spelled_as_a_lookup() -> None:
    """The read spelled as a `getattr` is caught too."""
    assert reads_of_the_bag('found = getattr(request.app.state, "database", None)\n') == [1]


def test_the_reader_check_is_not_fooled_by_prose() -> None:
    """A docstring naming `app.state` is not a read."""
    prose = '"""The removal seam. One per application, reached at `app.state.deleter`."""\n'
    assert reads_of_the_bag(prose) == []


def imported_by(source: str) -> set[str]:
    """Every module this source imports, as dotted names."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module)
    return names


def foreign_imports_from_the_kernel(source: str) -> list[str]:
    """The features this kernel source imports; empty is the only correct answer."""
    return sorted(name for name in imported_by(source) if name.startswith("sift.slices"))


def test_the_kernel_imports_no_feature() -> None:
    violations: list[str] = []
    for path in _production_files():
        if path.relative_to(SOURCE).parts[0] != "kernel":
            continue
        for imported in foreign_imports_from_the_kernel(path.read_text(encoding="utf-8")):
            violations.append(f"{path.relative_to(REPO)} imports {imported}")

    assert not violations, (
        "\nThe foundation is importing a feature. Features are built on the foundation and the\n"
        "foundation on nothing here; a part several features share is typed by an interface in\n"
        "kernel/seams, and a part belonging to one feature is declared by that feature.\n\n  "
        + "\n  ".join(violations)
        + "\n"
    )


def test_the_kernel_check_catches_an_upward_import() -> None:
    planted = "from sift.slices.browse.service import BrowseService\n"
    assert foreign_imports_from_the_kernel(planted) == ["sift.slices.browse.service"]


def test_the_kernel_check_allows_the_kernel_importing_itself() -> None:
    own = "from sift.kernel.access import Repository\nimport sift.kernel.seams\n"
    assert foreign_imports_from_the_kernel(own) == []
