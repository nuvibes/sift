# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every picture the server asks for is one the client can draw.

Python and the client's TypeScript icon union share no type, so each `icon="..."` in the server's
source is checked against the generated codepoint map the client itself reads; a name outside it
draws a queue with no glyph.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.gate, pytest.mark.regression, pytest.mark.unit]

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src" / "sift"

#: What the client resolves a name through: the generated file itself, not a second list.
CODEPOINTS = ROOT / "frontend" / "src" / "lib" / "generated" / "icon-codepoints.json"


def _declared_icons() -> list[tuple[Path, int, str]]:
    """Every `icon="..."` keyword argument in the server's source, with where it is; parsed, so a
    name in a comment or docstring is not one passed."""
    found: list[tuple[Path, int, str]] = []
    for path in sorted(SOURCE.rglob("*.py")):
        if "/tests/" in path.as_posix():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg != "icon":
                    continue
                if isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
                    found.append((path, node.lineno, keyword.value.value))
    return found


def test_the_codepoint_map_is_where_it_is_expected() -> None:
    """The codepoint map is where expected, or every check would pass vacuously."""
    assert CODEPOINTS.is_file(), f"no icon codepoints at {CODEPOINTS}"
    assert len(json.loads(CODEPOINTS.read_text(encoding="utf-8"))) > 50


def test_the_source_is_read_at_all() -> None:
    """The walk finds icons: the workbench alone declares several."""
    assert len(_declared_icons()) >= 5


def test_every_icon_the_server_names_is_one_the_client_ships() -> None:
    """Every icon the server names is one the client ships, or a queue draws with no glyph."""
    known = set(json.loads(CODEPOINTS.read_text(encoding="utf-8")))
    wrong = [
        f"{path.relative_to(ROOT)}:{line} — icon={name!r} is not one of the icons Sift ships"
        for path, line, name in _declared_icons()
        if name not in known
    ]
    assert not wrong, "\n" + "\n".join(wrong) + "\n"
