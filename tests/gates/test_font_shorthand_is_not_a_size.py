# SPDX-License-Identifier: AGPL-3.0-or-later
"""A font shorthand handed to `font-size` is dropped, and nothing says so.

The type tokens are `font` shorthands, and CSS drops a whole declaration that gives one to a single
property, silently; the element keeps its inherited size, which looks deliberate. So a token whose
value is a shorthand may only be used as `font`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.gates import client_source

pytestmark = [pytest.mark.gate, pytest.mark.unit]

REPO = Path(__file__).resolve().parents[2]
SHEET = REPO / "frontend" / "src" / "app.css"
SOURCE = REPO / "frontend" / "src"

#: `--name: <value>;` at the top of the sheet, where the type scale is declared.
_TOKEN = re.compile(r"--(?P<name>[a-z0-9-]+):\s*(?P<value>[^;]+);")

#: A shorthand carries a size and a line height separated by a slash, and then a family. A plain
#: length ("0.875rem") has neither, which is what tells the two apart without a CSS parser.
_SHORTHAND = re.compile(r"/\s*[\d.]+\s")


def _shorthand_tokens() -> set[str]:
    return {
        found["name"]
        for found in _TOKEN.finditer(SHEET.read_text(encoding="utf-8"))
        if _SHORTHAND.search(found["value"]) and "var(--font-" in found["value"]
    }


def _styled_files() -> list[Path]:
    return [path for path in client_source(SOURCE, ".svelte", ".css") if path.is_file()]


def test_the_sheet_really_declares_shorthand_tokens() -> None:
    """Meta-check. A sheet this gate cannot read would pass it while proving nothing."""
    tokens = _shorthand_tokens()

    assert len(tokens) >= 5, f"the type scale should be shorthands; found {sorted(tokens)}"
    assert "text-body" in tokens


def test_no_shorthand_token_is_used_as_a_size() -> None:
    files = _styled_files()
    assert len(files) > 100, "the frontend source was not found where this gate expects it"

    tokens = _shorthand_tokens()
    wanted = re.compile(r"font-size:\s*var\(--(" + "|".join(sorted(tokens)) + r")\)")
    offenders = [
        f"{path.relative_to(REPO)}:{number}"
        for path in files
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if wanted.search(line)
    ]

    assert not offenders, (
        "these declarations are dropped by the browser and the element keeps its inherited size; "
        f"write `font:` instead of `font-size:`: {offenders}"
    )
