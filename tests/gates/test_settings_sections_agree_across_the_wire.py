# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every settings section the server names is one the screen can open.

The server's `SECTIONS` and the browser's `SETTINGS_GROUPS` (id is the address, label the screen's
word) are joined by `REGISTRY_HOME` in `sections.ts`, written down rather than matched by name. An
unresolved section's results are silently dropped from the settings search, so: every server
section has an entry, every entry names a server section, and each lands, through the redirects, on
a section the screen draws.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from sift.kernel.settings_registry import SECTIONS

pytestmark = [pytest.mark.gate, pytest.mark.unit]

REPO = Path(__file__).resolve().parents[2]
SECTIONS_TS = REPO / "frontend" / "src" / "lib" / "settings-ui" / "sections.ts"

#: `{ id: 'library', label: 'Folders', icon: 'folder' }`, anchored on both key names.
ENTRY = re.compile(r"\{\s*id:\s*'([^']+)'\s*,\s*label:\s*'([^']+)'")


#: The body of one `const NAME ... = { ... };` block in `sections.ts`.
def _block(name: str) -> str:
    text = SECTIONS_TS.read_text(encoding="utf-8")
    found = re.search(rf"const {name}\b[^=]*=\s*\{{(.*?)\n\}};", text, re.S)
    assert found, f"`{name}` was not found in {SECTIONS_TS.name}"
    return found.group(1)


#: `Library: 'library',` or `'Smart Search': 'semantic',`
HOME = re.compile(r"^\t(?:'([^']+)'|([A-Za-z]+)):\s*'([^']+)',?$", re.M)
#: `theater: { section: 'playback' },` or `ledger: { section: 'jobs', show: 'history' },`
MOVED = re.compile(r"^\t'?([\w-]+)'?:\s*\{\s*section:\s*'([^']+)'", re.M)


def _screen_sections() -> dict[str, str]:
    """What the browser can open: id -> label."""
    return dict(ENTRY.findall(SECTIONS_TS.read_text(encoding="utf-8")))


def _homes() -> dict[str, str]:
    """`REGISTRY_HOME`: server section -> the address its settings are drawn at."""
    return {(quoted or bare): home for quoted, bare, home in HOME.findall(_block("REGISTRY_HOME"))}


def _moved() -> dict[str, str]:
    """`MOVED_TO`: retired address -> the section that inherited it."""
    return dict(MOVED.findall(_block("MOVED_TO")))


def _lands(address: str) -> str:
    """The section an address is drawn on, read once."""
    return _moved().get(address, address)


def test_there_are_two_lists_to_compare() -> None:
    """Both halves were read."""
    screen = _screen_sections()

    assert len(SECTIONS) >= 10, "the registry's section list came back too short to be real"
    assert len(screen) >= 10, f"{SECTIONS_TS.name} parsed into {len(screen)} sections"
    assert len(_homes()) >= 10, f"REGISTRY_HOME parsed into {len(_homes())} entries"
    assert _moved().get("theater") == "playback", "MOVED_TO did not parse"


def test_the_patterns_find_what_they_are_aimed_at() -> None:
    """A KNOWN POSITIVE for each pattern."""
    assert HOME.findall("\tLibrary: 'library',\n\t'Smart Search': 'semantic',") == [
        ("", "Library", "library"),
        ("Smart Search", "", "semantic"),
    ]
    assert MOVED.findall("\tledger: { section: 'jobs', show: 'history' },") == [("ledger", "jobs")]


def test_every_server_section_reaches_a_screen() -> None:
    screen = _screen_sections()
    homes = _homes()
    lost = [name for name in SECTIONS if _lands(homes.get(name, "")) not in screen]

    assert not lost, (
        "these settings sections exist on the server and land on nothing the screen draws, so every "
        f"setting on them drops out of the settings search: {lost}. Give each an entry in "
        "`REGISTRY_HOME` in sections.ts naming the section (or the tab address) that draws it."
    )


def test_the_join_names_no_server_section_that_is_gone() -> None:
    """No entry names a server section that is gone."""
    stale = sorted(set(_homes()) - set(SECTIONS))

    assert not stale, (
        f"`REGISTRY_HOME` names server sections that no longer exist: {stale}. The server renamed "
        "or removed them; move each entry to the new name."
    )


def test_the_screen_offers_no_section_the_server_has_never_heard_of() -> None:
    """Every screen section draws some server section's settings or is named as hand-built, so a new
    pane is a decision."""
    screen = _screen_sections()
    drawn_homes = {_lands(home) for home in _homes().values()}
    #: Sections the server has no settings for, each a pane built by hand.
    hand_written = {
        "sites": "Site logins and tunnels, none of them a registered setting",
        "profile": "the signed-in user's own name and password",
        "shortcuts": "the keyboard list, which is read from `shortcuts.ts`",
        "users": "the guest list under Users: rows on the server rather than settings",
        "general": "what the desktop app does on this device, answered by the shell",
        "get-to-know": "the learning path, drawn by Path from its own answer",
    }

    unexplained = [one for one in screen if one not in hand_written and one not in drawn_homes]

    assert not unexplained, (
        "these sections are on the settings list and no registered setting is drawn on them, and "
        f"they are not on the hand-written list either: {unexplained}. Either `REGISTRY_HOME` lost "
        "the entry that points here, or it is a new hand-built pane and belongs in `hand_written`."
    )
