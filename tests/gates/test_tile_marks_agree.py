# SPDX-License-Identifier: AGPL-3.0-or-later
"""The tile's marks are one list, declared on the server and named in the browser.

The registry declares every mark, its default and its words; the client names each key to put a
glyph in its corner, and neither can import the other. A mark never drawn is caught by
`test_every_setting_reaches_a_screen`; this catches a key named only in the client, which the server
refuses so the badge springs back, and answers that disagree across the wire.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import sift.main  # noqa: F401 (registers every slice's settings)
from sift.kernel.settings_registry import registered_settings
from sift.slices.browse.tile_marks import ALWAYS, ANSWERS, NEVER, ON_HOVER, TILE_MARK_KEYS

pytestmark = [pytest.mark.gate, pytest.mark.unit]

REPO = Path(__file__).resolve().parents[2]
CLIENT = REPO / "frontend" / "src" / "lib" / "grid" / "tile-marks.svelte.ts"
LAYOUT = REPO / "frontend" / "src" / "routes" / "+layout.svelte"

#: `export const NAME = 'value';`, the only shape this file declares a key or an answer in.
_DECLARED = re.compile(r"^export const (?P<name>[A-Z_]+) = '(?P<value>[^']+)';$", re.M)


def _client() -> dict[str, str]:
    assert CLIENT.is_file(), f"{CLIENT.name} has moved; this gate now checks nothing"
    return {
        found["name"]: found["value"] for found in _DECLARED.finditer(CLIENT.read_text("utf-8"))
    }


def test_the_client_names_exactly_the_marks_the_server_declares() -> None:
    declared = _client()
    named = {value for name, value in declared.items() if name.endswith("_MARK")}

    assert named == set(TILE_MARK_KEYS), (
        "the browser and the registry disagree about which marks a tile has. "
        f"Only in the browser: {sorted(named - set(TILE_MARK_KEYS))}. "
        f"Only on the server: {sorted(set(TILE_MARK_KEYS) - named)}"
    )


def test_every_mark_is_a_registered_setting() -> None:
    """Every mark is a registered setting, or its badge has no label, help or storage."""
    known = registered_settings()
    missing = sorted(key for key in TILE_MARK_KEYS if key not in known)

    assert not missing, f"{missing} is drawn on a tile and is not a registered setting"


def test_the_three_answers_are_spelled_the_same_on_both_sides() -> None:
    declared = _client()
    theirs = (declared.get("ALWAYS"), declared.get("ON_HOVER"), declared.get("NEVER"))

    assert theirs == (ALWAYS, ON_HOVER, NEVER)
    assert set(theirs) == set(ANSWERS), "the client offers answers the server would refuse"


def test_the_registry_agrees_that_a_mark_takes_those_answers() -> None:
    """Each mark's registered choices are the client's answers."""
    known = registered_settings()
    for key in sorted(TILE_MARK_KEYS):
        assert known[key].choices == tuple(ANSWERS), f"{key} does not offer the three answers"


def test_the_defaults_are_a_quiet_tile() -> None:
    """A tile at rest shows only the GIF word and the length: a drifting default changes every
    library on an update."""
    known = registered_settings()
    was = {
        "appearance.tile.views": ON_HOVER,
        "appearance.tile.o_count": ON_HOVER,
        "appearance.tile.pinned": ON_HOVER,
        "appearance.tile.sharing": ON_HOVER,
        "appearance.tile.hidden": ON_HOVER,
        "appearance.tile.favorite": ON_HOVER,
        "appearance.tile.rating": ON_HOVER,
        "appearance.tile.gif": ALWAYS,
        "appearance.tile.duration": ALWAYS,
    }
    assert {key: known[key].default for key in was} == was


def test_the_root_layout_is_what_loads_them() -> None:
    """The root layout loads the marks, with the theme and rating scale it already loads, since
    every
    screen reads them: loaded only by the Settings pane, a wall drew the defaults."""
    assert LAYOUT.is_file(), "the root layout moved; this gate now checks nothing"
    text = LAYOUT.read_text(encoding="utf-8")

    # Through the one list of account-scoped preferences (`lib/shell/account-scoped.ts`), or a
    # literal `tileMarks.load()`.
    scoped = (LAYOUT.parent.parent / "lib" / "shell" / "account-scoped.ts").read_text(
        encoding="utf-8"
    )
    through_the_list = "loadAccountScopedPreferences()" in text and "tileMarks" in scoped
    assert "tileMarks.load()" in text or through_the_list, (
        "the root layout does not load the tile marks, so every screen but Settings draws the "
        "defaults whatever anybody has chosen"
    )
