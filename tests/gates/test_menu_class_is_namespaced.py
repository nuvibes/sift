# SPDX-License-Identifier: AGPL-3.0-or-later
"""A class name that dresses a pop-up menu app-wide must not be a word anyone would reach for.

A global `.menu` would dress a settings row's chooser wrapper as a pop-up too, and the padding
between the two boxes swallows clicks. So the global name is namespaced, and the bare word is
refused.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.gates import client_source

pytestmark = [pytest.mark.gate, pytest.mark.unit]

REPO = Path(__file__).resolve().parents[2]
SOURCE = REPO / "frontend" / "src"

#: Global names general enough that a component would innocently pick the same word.
_TEMPTING = ("menu",)

_BARE = re.compile(r"""class=["'](?P<value>[^"']*)["']""")


def _wears_a_tempting_name(value: str) -> bool:
    """Whether this class attribute puts one of the global names on the element, read as a LIST
    (`class="menu compact"` matches too); a word holding an interpolation is skipped."""
    return any(
        word == token
        for token in value.split()
        if "{" not in token and "}" not in token
        for word in _TEMPTING
    )


def test_no_component_borrows_a_bare_global_name() -> None:
    files = client_source(SOURCE, ".svelte")
    assert len(files) > 100, "the frontend source was not found where this gate expects it"

    offenders = [
        f"{path.relative_to(REPO)}:{number}"
        for path in files
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        for found in _BARE.finditer(line)
        if _wears_a_tempting_name(found["value"])
    ]

    assert not offenders, (
        "this class is styled app-wide as a pop-up menu, so anything wearing it is drawn as one; "
        f"use the namespaced name instead: {offenders}"
    )
