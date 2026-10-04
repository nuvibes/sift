# SPDX-License-Identifier: AGPL-3.0-or-later
"""A settings pane that draws its own controls says how to find them.

The registry feeds the search index by itself, but many panes draw controls with no registered key
(users, stash-boxes, tunnels, Sites, the graphics card, storage folders), and each declares what it
holds in a sibling `<Pane>.search.ts`. A forgotten declaration fails silently: search never finds
the thing. Per FILE, a pane under `settings-ui/` that draws a control draws registry rows, has a
`.search.ts`, or is named in `NOT_SEARCHABLE`; and the collector imports every declaration.

It does not prove a declaration covers every control on its pane: that would need a hand-added
marker on each control, the same failure one level down.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.gate, pytest.mark.unit]

REPO = Path(__file__).resolve().parents[2]
PANES = REPO / "frontend" / "src" / "lib" / "settings-ui"

#: What counts as a control somebody presses, types in or chooses from.
CONTROLS = re.compile(r"<(button|input|select|textarea|Switch|Select|NumberInput|ChooseFile)\b")

#: Drawing rows FROM the registry, which reach the index by themselves. `SettingGroup` is not one:
#: it is a heading that panes use to dress hand-written content.
FROM_REGISTRY = ("SettingRow", "SettingsList", "PresetGroup")

#: One of those DRAWN, as a tag: a comment naming `SettingRow` does not count.
DRAWS_FROM_REGISTRY = re.compile(r"<(" + "|".join(FROM_REGISTRY) + r")\b")

#: Panes that draw a control and are deliberately NOT searchable, with the reason. Each entry must
#: keep earning its place: the tests below fail on a file that no longer exists and on an entry that
#: changes no outcome.
NOT_SEARCHABLE: dict[str, str] = {
    # A `.test.svelte` is a harness a test mounts, never a pane.
    "SettingRow.svelte": "the row primitive itself, not a pane",
    "Tunnels.svelte": "drawn inside Sites, whose declaration covers the whole block",
    "Routing.svelte": "drawn inside Sites, whose 'Site tunnels' entry IS this",
    "DatabaseSwitcher.svelte": "drawn inside Backup, whose 'Libraries' entry IS this",
    "FromStash.svelte": "drawn inside Backup, whose 'Migrate from Stash' entry IS this",
    "ApplicationLog.svelte": "drawn inside Logs, whose 'Log' entry IS this",
    "ServerSharing.svelte": "drawn inside General for a window on another computer; its one switch wears the Network sharing entry's own name, which the index already finds",
    "NameTemplateField.svelte": "drawn inside NamingTemplate, whose 'Name template' entry IS this",
    "FolderImportRow.svelte": "drawn inside Faces, whose 'Import a structured folder' entry IS this",
    # Every control is a registered setting, chosen by looking at a swatch rather than from a row,
    # so a declaration would put each in the results twice.
    "ThemeChoices.svelte": "every control on it is a registered setting, drawn as a swatch",
    # Its one choice is the task's registered When; Run now is found under the task's entry.
    "TaskWhen.svelte": "its choice is the task's registered When, found under the task's title",
    # The group primitive: the settings it sets are drawn as registry rows on the sub-page.
    "PresetGroup.svelte": "the group primitive; the settings it sets are registered rows",
}


def _panes() -> list[Path]:
    return sorted(
        path
        for path in PANES.glob("*.svelte")
        if not path.name.endswith((".test.ts", ".test.svelte"))
    )


def _draws_a_control(source: str) -> bool:
    return CONTROLS.search(source) is not None


def _draws_registry_rows(source: str) -> bool:
    return DRAWS_FROM_REGISTRY.search(source) is not None


def test_there_are_panes_to_check() -> None:
    """A KNOWN POSITIVE for the glob: one matching nothing passes every loop in silence."""
    assert len(_panes()) > 20


def test_the_control_pattern_actually_finds_controls() -> None:
    """A KNOWN POSITIVE for the PATTERN: broken to match nothing, every pane would be skipped while
    the files are still there. So it is put to each kind of control, and to lines that are not."""
    for drawn in (
        "<button type='button'>Press</button>",
        '<input type="text" />',
        "<Switch checked={on} />",
        "<Select items={choices} />",
        "<NumberInput bind:value={size} />",
        "<ChooseFile onchoose={pick} />",
    ):
        assert _draws_a_control(drawn), drawn

    assert not _draws_a_control("<p>Nothing to press here.</p>")
    # Same five letters, not a control.
    assert not _draws_a_control("<selection-bar />")

    assert _draws_registry_rows("<SettingRow key={key} />")
    assert _draws_registry_rows("<SettingsList\n\tkeys={keys} />")
    assert not _draws_registry_rows("* ## Why it is not a `SettingRow`")
    assert not _draws_registry_rows("<SettingRows />")

    # It fires on the real files.
    assert sum(1 for pane in _panes() if _draws_a_control(pane.read_text(encoding="utf-8"))) > 15


def test_every_pane_that_draws_its_own_control_says_how_to_find_it() -> None:
    """A pane with a control is reachable by search, draws registry rows, or is excused."""
    unfindable: list[str] = []
    for pane in _panes():
        if pane.name in NOT_SEARCHABLE:
            continue
        source = pane.read_text(encoding="utf-8")
        if not _draws_a_control(source):
            continue
        if _draws_registry_rows(source):
            continue
        if not pane.with_suffix(".search.ts").exists():
            unfindable.append(pane.name)

    assert not unfindable, (
        "These settings panes draw a control that the search index cannot reach:\n  "
        + "\n  ".join(sorted(unfindable))
        + "\n\nAdd a `<Pane>.search.ts` beside the component naming what somebody would type to\n"
        "find it, and import it in `search.ts`. If it genuinely should not be findable, add it\n"
        "to NOT_SEARCHABLE in this file with the reason."
    )


def test_the_collector_imports_every_declaration_that_exists() -> None:
    """The collector imports every declaration: one nothing imports compiles and does nothing."""
    collector = (PANES / "search.ts").read_text(encoding="utf-8")
    missing = [
        found.name
        for found in sorted(PANES.glob("*.search.ts"))
        if f"./{found.name.removesuffix('.ts')}'" not in collector
    ]

    assert not missing, (
        "These declarations exist and nothing reads them:\n  "
        + "\n  ".join(missing)
        + "\n\nImport each in `search.ts` and add it to DECLARED."
    )


def test_every_excuse_is_actually_excusing_something() -> None:
    """Every excuse does work: remove one and the gate above goes red. An entry for a file that
    draws no control would vouch silently for the button it grows later."""
    inert: list[str] = []
    for name in NOT_SEARCHABLE:
        source = (PANES / name).read_text(encoding="utf-8")
        if not _draws_a_control(source) or _draws_registry_rows(source):
            inert.append(name)

    assert not inert, (
        "These excuses excuse nothing. The pane is already skipped for another reason:\n  "
        + "\n  ".join(sorted(inert))
        + "\n\nRemove them. An entry that changes no outcome cannot be told from one that does,\n"
        "and it will vouch for the file silently the day it grows a control."
    )


def test_every_excuse_still_names_a_file_that_exists() -> None:
    """Every excuse names a file that exists, or a renamed pane would slip by undeclared."""
    stale = [name for name in NOT_SEARCHABLE if not (PANES / name).exists()]

    assert not stale, f"NOT_SEARCHABLE names files that no longer exist: {sorted(stale)}"
