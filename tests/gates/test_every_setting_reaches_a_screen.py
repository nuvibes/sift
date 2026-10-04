# SPDX-License-Identifier: AGPL-3.0-or-later
"""A registered setting is drawn on some screen: a file that renders a settings control, or a
module it imports, names its key, its whole section, or its prefix. Being drawable and being read
(the two neighbouring gates) do not put a setting on a screen anybody can open.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import sift.main  # noqa: F401 (settings register when their slice is imported)
from sift.kernel.settings_registry import registered_settings
from tests.gates import client_source

pytestmark = [pytest.mark.gate, pytest.mark.unit]

REPO = Path(__file__).resolve().parents[2]
CLIENT = REPO / "frontend" / "src"

#: The controls a settings screen is built from, and the calls that write a setting, so a hand-made
#: control counts; a pane given a panel by its parent draws on the parent's screen.
DRAWS_SETTINGS = ("SettingRow", "SettingsList", "ChoiceCard", "saveSettings", "new SettingsPanel(")

#: Settings that no screen draws, each with why nobody should ever press it. A setting waiting for
#: its screen does not work, so the honest state of this dictionary is empty.
DRAWN_NOWHERE_ON_PURPOSE: dict[str, str] = {
    # The player is the control for these three; a second one on a pane would disagree with it.
    "playback.volume": "the player's own volume slider is its control",
    "playback.muted": "the player's own mute button is its control",
    "playback.loop_mode": "the player's own loop button is its control",
}

#: Any string literal, in the three quotings the client uses.
_LITERALS = re.compile(r"""['"`]([^'"`\n]{2,120})['"`]""")

#: Where an import comes from; only the last segment, the file name every spelling agrees on.
_IMPORTS = re.compile(r"""import\s+[^;]*?from\s+['"]([^'"]+)['"]""", re.S)


def _client_files() -> dict[Path, str]:
    return {
        path: path.read_text(encoding="utf-8", errors="ignore")
        for path in client_source(CLIENT, ".svelte", ".ts")
    }


def _screens(files: dict[Path, str]) -> set[Path]:
    """Every file that draws a settings control, and every module one of them imports."""
    drawing = {path for path, text in files.items() if any(one in text for one in DRAWS_SETTINGS)}
    by_name: dict[str, list[Path]] = {}
    for path in files:
        by_name.setdefault(path.name, []).append(path)

    reached = set(drawing)
    for path in drawing:
        for found in _IMPORTS.finditer(files[path]):
            last = found.group(1).rsplit("/", 1)[-1]
            for spelling in (last, f"{last}.ts", f"{last}.svelte"):
                reached.update(by_name.get(spelling, ()))
    return reached


def _spelled(files: dict[Path, str], screens: set[Path]) -> set[str]:
    """Every string literal those files spell out: a setting named in a comment is not drawn."""
    return {found.group(1) for path in screens for found in _LITERALS.finditer(files.get(path, ""))}


#: What a file that draws a whole SECTION also says: the word "Appearance" alone also appears in a
#: design registry that has nothing to do with settings.
READS_A_SECTION = ("fetchSettings", "SettingsPanel")

#: A file holding this draws a HAND-PICKED list, so it vouches for the keys it names and not for
#: its whole section: a pane may fetch a section and render only the keys it lists.
HAND_PICKS_KEYS = "keys: ["


def _sections_drawn(files: dict[Path, str], screens: set[Path]) -> set[str]:
    """Every section a file reads and draws whole; a file that hand-picks keys does not count."""
    return {
        found.group(1)
        for path in screens
        if any(one in files.get(path, "") for one in READS_A_SECTION)
        and HAND_PICKS_KEYS not in files.get(path, "")
        for found in _LITERALS.finditer(files.get(path, ""))
    }


def test_every_registered_setting_is_drawn_by_some_screen() -> None:
    files = _client_files()
    screens = _screens(files)
    spelled = _spelled(files, screens)
    sections = _sections_drawn(files, screens)
    prefixes = {one for one in spelled if one.endswith(".") and len(one) > 2}

    for key, setting in sorted(registered_settings().items()):
        if key in DRAWN_NOWHERE_ON_PURPOSE:
            continue
        reached = (
            key in spelled
            or setting.section in sections
            or any(key.startswith(prefix) for prefix in prefixes)
        )
        assert reached, (
            f"`{key}` is registered into the {setting.section!r} section and no settings screen"
            " names it, so it is stored, read, and impossible to press. Draw its row on the pane"
            " for that section, naming the key, the section, or a prefix it starts with."
        )


def test_nothing_is_excused_by_accident() -> None:
    """A misspelt key in the exception list excuses a setting that does not exist."""
    known = registered_settings()
    for key in DRAWN_NOWHERE_ON_PURPOSE:
        assert key in known, f"`{key}` is excused here and is not a registered setting"


def test_a_screen_is_more_than_any_file_that_mentions_the_key() -> None:
    """The files consulted are fewer than the client: the module that READS a setting names it."""
    files = _client_files()
    screens = _screens(files)
    assert 0 < len(screens) < len(files), "settings screens are no longer a subset of the client"


def test_the_registry_is_not_empty() -> None:
    """The registry is not empty: settings register when their slice is imported, and an empty one
    would make every check above pass over nothing."""
    assert len(registered_settings()) > 100, (
        "the settings registry is empty or nearly so, which means the application was not"
        " imported and every check in this file ran over nothing"
    )
