# SPDX-License-Identifier: AGPL-3.0-or-later
"""No slice imports another slice, except `auth`'s front door.

A feature reaching into another couples them, so neither can be built, tested or replaced apart.
`auth` is cross-cutting and publishes a front door; nothing behind it is public. Read per file,
where the owning slice is known from the path, which a pattern matcher cannot know.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.gate, pytest.mark.unit]

# The one slice every other may import, and only from its front door.
_SHARED = "auth"


def _slices_dir() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "src" / "sift" / "slices"
        if candidate.is_dir():
            return candidate
    raise AssertionError("could not find src/sift/slices from the test file")


def _imported_slice(module: str | None) -> tuple[str, bool] | None:
    """The slice a dotted module path names, and whether it stops at that slice's front door:
    `sift.slices.browse` -> ("browse", True), `sift.slices.browse.service` -> ("browse", False)."""
    if not module:
        return None
    parts = module.split(".")
    if len(parts) >= 3 and parts[0] == "sift" and parts[1] == "slices":
        return parts[2], len(parts) == 3
    return None


def foreign_slice_imports(owning_slice: str, source: str) -> list[str]:
    """What this source imports that it may not: another slice at all, or `auth` past its door."""
    tree = ast.parse(source)
    offenders: list[str] = []

    def consider(imported: tuple[str, bool] | None) -> None:
        if imported is None:
            return
        name, at_the_front_door = imported
        if name == owning_slice:
            return
        if name == _SHARED:
            if not at_the_front_door:
                offenders.append(f"{_SHARED} past its front door")
            return
        offenders.append(name)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                consider(_imported_slice(alias.name))
        # A relative import stays within its own package.
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            consider(_imported_slice(node.module))
    return offenders


def _slice_files() -> list[tuple[str, Path]]:
    """Every slice source file with its slice; tests may drive more than one slice."""
    out: list[tuple[str, Path]] = []
    for path in _slices_dir().rglob("*.py"):
        if "tests" in path.parts:
            continue
        relative = path.relative_to(_slices_dir())
        slice_name = relative.parts[0]
        if slice_name.endswith(".py"):
            continue
        out.append((slice_name, path))
    return out


def test_no_slice_imports_another_slice() -> None:
    violations: list[str] = []
    for slice_name, path in _slice_files():
        for imported in foreign_slice_imports(slice_name, path.read_text("utf-8")):
            violations.append(f"{path.name} (in {slice_name}) imports the {imported} slice")
    assert not violations, "slices may only import the kernel and the auth slice:\n" + "\n".join(
        violations
    )


def test_the_gate_catches_a_cross_slice_import() -> None:
    """A planted cross-slice import is caught."""
    planted = "from sift.slices.browse.service import BrowseService\n"
    assert foreign_slice_imports("download", planted) == ["browse"]


def test_the_gate_allows_importing_the_shared_auth_slice() -> None:
    allowed = "from sift.slices.auth import current_viewer, master_key\n"
    assert foreign_slice_imports("download", allowed) == []


def test_the_gate_refuses_reaching_past_the_shared_slices_front_door() -> None:
    """Reaching past `auth`'s front door is refused."""
    planted = "from sift.slices.auth.crypto import seal_secret\n"
    assert foreign_slice_imports("download", planted) == ["auth past its front door"]

    by_module = "import sift.slices.auth.tuning\n"
    assert foreign_slice_imports("media_jobs", by_module) == ["auth past its front door"]


def test_the_gate_allows_the_shared_slice_reaching_into_itself() -> None:
    """`auth` is an ordinary package to its own files."""
    own = "from sift.slices.auth.crypto import seal_secret\n"
    assert foreign_slice_imports("auth", own) == []


def test_the_gate_allows_a_slice_importing_its_own_modules() -> None:
    own = "from sift.slices.browse.service import BrowseService\n"
    assert foreign_slice_imports("browse", own) == []
