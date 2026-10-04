# SPDX-License-Identifier: AGPL-3.0-or-later
"""The title strip's height is one number written twice: `--window-chrome` in `app.css`, from the
server, and `TITLE_BAR_HEIGHT` in `desktop/src/main.ts`, read before any page loads. A mismatch is
cosmetic and invisible to browser tests, where the strip is `0px`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.gate, pytest.mark.unit]

REPO = Path(__file__).resolve().parents[2]
STYLESHEET = REPO / "frontend" / "src" / "app.css"
SHELL = REPO / "desktop" / "src" / "main.ts"

#: `--window-chrome: 36px;` under the rule that turns the strip on, not the browser's `0px` default.
IN_THE_STYLESHEET = re.compile(
    r"\[data-window='overlaid'\]\s*\{[^}]*?--window-chrome:\s*(\d+)px", re.S
)

#: `const TITLE_BAR_HEIGHT = 36;` in the shell.
IN_THE_SHELL = re.compile(r"\bconst\s+TITLE_BAR_HEIGHT\s*=\s*(\d+)\s*;")


def _one(pattern: re.Pattern[str], where: Path) -> int:
    found = pattern.findall(where.read_text(encoding="utf-8"))
    assert len(found) == 1, (
        f"{where.name} declares the strip height {len(found)} times, and this gate reads one. "
        "Two declarations in one file is the fault this exists to catch, one level in."
    )
    return int(found[0])


def test_the_strip_and_the_caption_buttons_are_the_same_height() -> None:
    """The strip and the caption buttons are the same height."""
    stylesheet = _one(IN_THE_STYLESHEET, STYLESHEET)
    shell = _one(IN_THE_SHELL, SHELL)

    assert stylesheet == shell, (
        f"`--window-chrome` is {stylesheet}px in app.css and `TITLE_BAR_HEIGHT` is {shell} in "
        "desktop/src/main.ts. They are one number written down twice: the stylesheet ships with "
        "the server and the window is made before any page loads, so they cannot be one "
        "declaration. Change both, and the note at each site says why."
    )


def test_both_files_still_declare_it_at_all() -> None:
    """A KNOWN POSITIVE: both patterns match, so the comparison is not nothing against nothing."""
    assert _one(IN_THE_STYLESHEET, STYLESHEET) > 0
    assert _one(IN_THE_SHELL, SHELL) > 0
    # The browser's default is a different number, so the overlaid rule is what was read.
    assert ":root {" in STYLESHEET.read_text(encoding="utf-8")
    assert "--window-chrome: 0px" in STYLESHEET.read_text(encoding="utf-8")
