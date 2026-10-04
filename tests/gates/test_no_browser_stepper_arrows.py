# SPDX-License-Identifier: AGPL-3.0-or-later
"""No `type="number"` anywhere but inside `NumberInput.svelte`, the component that replaces it.

The raw element draws the operating system's stepper arrows inside a control the design otherwise
owns. A caller that does not reach for the component works and looks fine alone, so only a search
for the raw element finds it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.gates import client_source

pytestmark = [pytest.mark.gate, pytest.mark.unit]


def _root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "src" / "sift").is_dir():
            return parent
    raise AssertionError("could not find the repository root from the test file")


#: The one file allowed to name it.
_ALLOWED = ("frontend/src/lib/components/common/NumberInput.svelte",)

#: Where a number box would be written.
_SEARCHED = ("frontend/src",)

_LOOKING_FOR = re.compile(r"""type\s*=\s*["']number["']""")

#: Markup and script comments, blanked before the search: a comment explaining why an input was
#: converted names the element too.
_COMMENTS = re.compile(r"<!--.*?-->|/\*.*?\*/", re.DOTALL)


def without_comments(text: str) -> str:
    """The file with every comment blanked, keeping line numbers."""
    return _COMMENTS.sub(lambda found: re.sub(r"[^\n]", " ", found.group(0)), text)


def offenders_in(text: str) -> list[int]:
    """The line numbers in one file's source that ask the platform for a number box."""
    return [
        number
        for number, line in enumerate(without_comments(text).splitlines(), start=1)
        if _LOOKING_FOR.search(line)
    ]


def _offenders() -> list[str]:
    root = _root()
    allowed = {root / name for name in _ALLOWED}
    found: list[str] = []
    for where in _SEARCHED:
        for path in client_source(root / where, ".svelte"):
            if path in allowed:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            found += [f"{path.relative_to(root)}:{number}" for number in offenders_in(text)]
    return found


def test_nothing_asks_the_platform_to_draw_a_number_box() -> None:
    """`NumberInput` is the one place the element appears."""
    offenders = _offenders()

    assert not offenders, (
        "these draw the operating system's own stepper arrows inside Sift's chrome:\n  "
        + "\n  ".join(offenders)
        + "\n\nUse `NumberInput` from `$lib/components/common`. It keeps the platform's behaviour "
        "(the number pad on a phone, the arrow keys on a desktop) and draws the control itself."
    )


def test_the_check_sees_a_planted_one_and_ignores_one_being_talked_about() -> None:
    """A planted violation is caught and a comment about one, across lines, is not."""
    assert offenders_in('<input type="number" min="1" />') == [1]
    assert offenders_in("<input type='number' />") == [1]

    assert offenders_in('<!-- never write type="number" here -->') == []
    assert offenders_in('/* type="number" draws the platform arrows */') == []
    assert offenders_in('<!--\n  not type="number", because arrows\n-->') == []
    # A real one after a comment is found on the right line.
    assert offenders_in('<!--\n  about type="number"\n-->\n<input type="number" />') == [4]


def test_the_component_that_replaces_it_is_still_the_one_that_uses_it() -> None:
    """A known positive: the component still renders its numeric TEXT box."""
    root = _root()
    component = root / _ALLOWED[0]

    assert component.is_file(), "NumberInput has moved; this gate's allow-list is now a lie"
    text = component.read_text(encoding="utf-8")
    # `type="text"` asserted, so a rewrite back to the raw element fails here.
    assert 'type="text"' in text
    assert 'inputmode="numeric"' in text
