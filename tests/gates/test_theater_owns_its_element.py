# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every value Theater decides (each name in `OWNED`) is written onto the video element explicitly,
every render, since an unset value moves with the browser; `loop` and `autoplay` are always off.
The list and `settle` are checked against each other both ways.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.gate, pytest.mark.unit]

_ELEMENT = (
    Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "theater" / "element.ts"
)

#: The declared list: `export const OWNED = [...] as const`.
_OWNED = re.compile(r"export const OWNED = \[([^\]]*)\]")

#: The body of `settle`, which writes them.
_SETTLE = re.compile(r"export function settle\([^)]*\)[^{]*\{(.*?)\n\}", re.DOTALL)

#: Each `video.<name> = ` inside it.
_ASSIGNED = re.compile(r"\bvideo\.([A-Za-z]+)\s*=")


def _source() -> str:
    return _ELEMENT.read_text()


def declared() -> set[str]:
    (listed,) = _OWNED.findall(_source())
    names = set(re.findall(r"'([A-Za-z]+)'", listed))
    assert names, "no owned values were found: this gate is reading the wrong shape"
    return names


def written() -> set[str]:
    (body,) = _SETTLE.findall(_source())
    names = set(_ASSIGNED.findall(body))
    assert names, "nothing is written onto the element: this gate is reading the wrong shape"
    return names


def test_every_value_sift_owns_is_written_onto_the_element() -> None:
    missing = declared() - written()
    assert not missing, f"declared as Sift's and left to the browser: {sorted(missing)}"


def test_nothing_is_written_that_was_not_declared() -> None:
    undeclared = written() - declared()
    assert not undeclared, f"written onto the element without being declared: {sorted(undeclared)}"


def test_the_element_never_loops_on_its_own() -> None:
    """The element never loops on its own: a cell set to anything else would repeat anyway."""
    (body,) = _SETTLE.findall(_source())
    assert re.search(r"\bvideo\.loop\s*=\s*false\b", body), (
        "the element is allowed to loop itself, and whether a cell repeats is the cell's decision, "
        "because only it knows whether 'again' means this file or the next one in its run"
    )


def test_the_element_never_starts_itself() -> None:
    """The element never starts itself, or a wall would begin as each source attached."""
    (body,) = _SETTLE.findall(_source())
    assert re.search(r"\bvideo\.autoplay\s*=\s*false\b", body)


def test_nothing_is_fetched_before_a_cell_is_asked_to_play() -> None:
    """Nothing is fetched before a cell plays: long-lived streams use up the origin's
    connections."""
    (body,) = _SETTLE.findall(_source())
    assert re.search(r"\bvideo\.preload\s*=\s*'none'", body)
