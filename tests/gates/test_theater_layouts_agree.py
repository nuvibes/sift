# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Theater presets the server counts and the browser draws agree by name, by cell count and by
the ceiling on feeds, or a wall draws and cannot be saved; and Theater and the player, which may
not import each other, offer the same three end behaviours.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from sift.kernel.settings_registry import Setting, get_registered
from sift.slices.player.router import LOOP_MODE_KEY
from sift.slices.theater import END_BEHAVIOURS, LAYOUT_CELLS, LAYOUT_KEY
from sift.slices.theater.service import MOST_CELLS

pytestmark = [pytest.mark.gate, pytest.mark.unit]

_CLIENT = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib"
_LAYOUTS = _CLIENT / "theater" / "layouts.ts"
_LOOP_MODES = _CLIENT / "player" / "loop-modes.ts"
_CELL = _CLIENT / "theater" / "cell.svelte.ts"

#: The end behaviour a cell falls back to: the wall's default, since it is chosen per cell.
_CELL_FALLBACK = re.compile(
    r"isLoopMode\(saved\.end_behaviour\)\s*\?\s*saved\.end_behaviour\s*:\s*'([a-z_]+)'"
)

#: Where one preset entry BEGINS: up to the next is that entry, in whatever order or line shape.
_ENTRY = re.compile(r"id:\s*'([a-z_]+)'")
_SLOT = re.compile(r"\bat\(")
#: The strip a preset opens with: a Center stage wall is its shape's places PLUS its strip.
_STRIP = re.compile(r"strip:\s*([A-Za-z_]+|\d+)")
#: How many previews a preset that declares one opens with.
_MOST_PREVIEWS = re.compile(r"const MOST_PREVIEWS = (\d+);")

#: A preset's name on screen.
_LABEL = re.compile(r"label:\s*'([^']+)'")

#: The browser's own ceiling, which the server has to match.
_MOST = re.compile(r"export const MOST_CELLS = (\d+);")

#: The browser's `LoopMode` union, as written.
_LOOP_MODE_UNION = re.compile(r"export type LoopMode =([^;]+);")


def _drawn_layouts() -> dict[str, int]:
    """Each preset the browser offers, and how many cells it holds in all, cut at each `id:`."""
    whole = _LAYOUTS.read_text()
    (previews,) = _MOST_PREVIEWS.findall(whole)
    known = {"MOST_PREVIEWS": int(previews)}

    # The list itself and nothing after it: `at(` is used again in functions below the list.
    opens = whole.index("export const LAYOUTS")
    source = whole[opens : whole.index("\n];", opens)]

    starts = [(match.group(1), match.start()) for match in _ENTRY.finditer(source)]
    assert starts, "no layouts were found in the client: this gate is reading the wrong shape"

    found: dict[str, int] = {}
    for at, (name, begins) in enumerate(starts):
        ends = starts[at + 1][1] if at + 1 < len(starts) else len(source)
        entry = source[begins:ends]
        strip = _STRIP.findall(entry)
        # Shape places plus the opening strip, as the server counts (`LAYOUT_CELLS`).
        waiting = 0
        if strip:
            waiting = known.get(strip[0], 0) if not strip[0].isdigit() else int(strip[0])
            assert waiting, f"{name} declares a strip of {strip[0]!r}, which this gate cannot read"
        found[name] = len(_SLOT.findall(entry)) + waiting

    assert all(found.values()), f"a preset was read as holding no cells at all: {found}"
    return found


#: Names the picker retired that the server must still take: saved walls are filed under them.
_RETIRED = {"stacked_three", "one_above_two", "two_above_one"}


def test_every_preset_the_browser_offers_is_one_the_server_accepts() -> None:
    assert set(_drawn_layouts()) <= set(LAYOUT_CELLS)


def test_each_preset_holds_the_same_number_of_cells_on_both_sides() -> None:
    drawn = _drawn_layouts()
    assert drawn == {name: count for name, count in LAYOUT_CELLS.items() if name in drawn}


def test_the_server_still_takes_the_names_the_picker_stopped_offering() -> None:
    assert set(LAYOUT_CELLS) >= _RETIRED


def test_nothing_is_accepted_that_is_neither_offered_nor_retired() -> None:
    assert set(LAYOUT_CELLS) - _RETIRED == set(_drawn_layouts())


def test_the_default_layout_setting_offers_the_pickers_list_in_its_words() -> None:
    """Settings offers what the picker offers, in its order and its words."""
    whole = _LAYOUTS.read_text()
    opens = whole.index("export const LAYOUTS")
    source = whole[opens : whole.index("\n];", opens)]
    setting = _setting(LAYOUT_KEY)
    assert setting.choices == tuple(_drawn_layouts())
    assert setting.choice_labels == tuple(_LABEL.findall(source))


def test_both_sides_stop_at_the_same_number_of_feeds() -> None:
    """Both sides stop at the same number of feeds, or a built wall is refused when saved."""
    (drawn,) = _MOST.findall(_LAYOUTS.read_text())
    assert int(drawn) == MOST_CELLS


def test_the_browser_knows_the_same_three_end_behaviours() -> None:
    (union,) = _LOOP_MODE_UNION.findall(_LOOP_MODES.read_text())
    drawn = set(re.findall(r"'([a-z_]+)'", union))
    assert drawn, "no end behaviours were found in the client"
    assert drawn == set(END_BEHAVIOURS)


def _setting(key: str) -> Setting:
    """A registered setting, or a failure naming the key (missing at import means renamed)."""
    found = get_registered(key)
    assert found is not None, f"{key} is not registered"
    return found


def test_theater_and_the_player_offer_the_same_three() -> None:
    """Theater's `END_BEHAVIOURS`, against which a stored cell is validated, equal the player's."""
    assert set(END_BEHAVIOURS) == set(_setting(LOOP_MODE_KEY).choices or ())


def test_both_start_on_carrying_on_to_the_next_file() -> None:
    """Both default to carrying on to the next file, the wall's read from the client's fallback
    after a known positive, so a pattern matching nothing cannot agree with anything."""
    found = _CELL_FALLBACK.findall(_CELL.read_text())
    assert found, "the cell's end-behaviour fallback was not found in the client"
    assert set(found) == {"loop_all"}
    assert _setting(LOOP_MODE_KEY).default == "loop_all"
