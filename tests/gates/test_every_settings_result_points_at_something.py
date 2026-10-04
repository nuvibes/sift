# SPDX-License-Identifier: AGPL-3.0-or-later
"""A settings search result that names a row can actually find it, ON THE PANE IT OPENS.

A result opens a section and rings the row its key names (`revealSetting`), giving up quietly when
nothing matches, so a broken key looks exactly like no key. So a declared key, a `SettingLink`
and an `openSettings(section, key)` are followed through the redirects (`MOVED_TO`,
`KEY_MOVED_TO`) to the pane `SettingsPane` draws for the landing section, and the row must be
there; a registered setting must have its server section drawn on that pane (`REGISTRY_HOME`).
A key is optional: a result with none opens the section and stops.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import sift.main  # noqa: F401 (settings register when their slice is imported)
from sift.kernel.settings_registry import registered_settings

pytestmark = [pytest.mark.gate, pytest.mark.unit]

REPO = Path(__file__).resolve().parents[2]
CLIENT = REPO / "frontend" / "src"
PANES = CLIENT / "lib" / "settings-ui"

#: `key: 'some.key',` inside a declaration.
DECLARED_KEY = re.compile(r"^\t\tkey: '([^']+)',$", re.M)
#: `id="some.key"` in markup or passed to a component, which `getElementById` finds alike.
WRITTEN_ID = re.compile(r"""\bid=["']([A-Za-z][\w.\-]*)["']""")


def _declared() -> dict[str, str]:
    """Every key a hand-written pane declares, and its file."""
    found: dict[str, str] = {}
    for path in sorted(PANES.glob("*.search.ts")):
        for key in DECLARED_KEY.findall(path.read_text(encoding="utf-8")):
            found[key] = path.name
    return found


def _written() -> set[str]:
    """Every id written anywhere in the client's markup."""
    ids: set[str] = set()
    for path in CLIENT.rglob("*.svelte"):
        ids.update(WRITTEN_ID.findall(path.read_text(encoding="utf-8")))
    return ids


SECTIONS_TS = PANES / "sections.ts"
SETTINGS_PANE = PANES / "SettingsPane.svelte"

#: `{#if showing === 'library'}` ... `<LibraryScreen />`: a section and the component it draws.
BRANCH = re.compile(r"showing === '([\w-]+)'\}\s*(?:<!--.*?-->\s*)*<([A-Z]\w*)", re.S)
#: `import Name from './Name.svelte';` or from `$lib/...svelte`.
SVELTE_IMPORT = re.compile(r"import\s+(\w+)\s+from\s+'([^']+\.svelte)'")
#: `theater: { section: 'playback' },`
MOVED = re.compile(r"^\t'?([\w-]+)'?:\s*\{\s*section:\s*'([^']+)'", re.M)
#: `'appearance.links_open_in': { section: 'general', key: 'general.links_open_in' },`
KEY_MOVED = re.compile(r"^\t'([^']+)':\s*\{\s*section:\s*'([^']+)',\s*key:\s*'([^']+)'", re.M)
#: `Library: 'library',` or `'Smart Search': 'semantic',`
HOME = re.compile(r"^\t(?:'([^']+)'|([A-Za-z]+)):\s*'([^']+)',?$", re.M)
#: A declaration's section and key, inside one `{ ... }` of a `.search.ts`.
DECLARED_SECTION = re.compile(r"^\t\tsection: '([^']+)',$", re.M)
#: `<SettingLink section="x" setting="y">`, the attributes in either order.
SETTING_LINK = re.compile(r"<SettingLink\b([^>]*)>", re.S)
ATTRIBUTE = re.compile(r"""\b(section|setting)=["']([^"'{}]+)["']""")
#: `openSettings('x', 'y')` / `showSettingsSection('x', 'y')` with a literal key.
OPEN_CALL = re.compile(r"\b(?:openSettings|showSettingsSection)\('([\w-]+)',\s*'([^']+)'")
#: `openSettings('x')`: a press that opens a pane and names no row.
BARE_OPEN = re.compile(r"\b(?:openSettings|showSettingsSection)\('([\w-]+)'\)")

#: The presses that open a pane WHOLE, by `<file>:<section>`, each with why it names no row; a press
#: changing one rule names its key.
OPENS_THE_PANE: dict[str, str] = {
    "routes/downloads/DownloadOptions.svelte:downloads": (
        "the Options menu's Open settings: the whole Downloads pane is what it offers"
    ),
    "lib/settings-ui/Backup.svelte:tasks": (
        "the backup's When is a task row on Tasks and Activity, drawn from the task list under the"
        " task's title, not a key the pane rings"
    ),
}


def _block(name: str) -> str:
    text = SECTIONS_TS.read_text(encoding="utf-8")
    found = re.search(rf"const {name}\b[^=]*=\s*\{{(.*?)\n\}};", text, re.S)
    assert found, f"`{name}` was not found in {SECTIONS_TS.name}"
    return found.group(1)


def _resolve(section: str, key: str) -> tuple[str, str]:
    """`resolveAddress`'s two halves: where a row lands, and what it is called."""
    for old, new_section, new_key in KEY_MOVED.findall(_block("KEY_MOVED_TO")):
        if old == key:
            return new_section, new_key
    return dict(MOVED.findall(_block("MOVED_TO"))).get(section, section), key


def _resolve_import(source: Path, spec: str) -> Path | None:
    if spec.startswith("$lib/"):
        return CLIENT / "lib" / spec.removeprefix("$lib/")
    if spec.startswith("."):
        return (source.parent / spec).resolve()
    return None


def _closure(start: Path) -> set[Path]:
    """A component and every component it draws, through its `.svelte` imports."""
    seen: set[Path] = set()
    todo = [start]
    while todo:
        path = todo.pop()
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        for _name, spec in SVELTE_IMPORT.findall(path.read_text(encoding="utf-8")):
            found = _resolve_import(path, spec)
            if found is not None:
                todo.append(found)
    return seen


def _pane_ids() -> dict[str, set[str]]:
    """Section id -> every id in the pane `SettingsPane` draws for it, and below."""
    text = SETTINGS_PANE.read_text(encoding="utf-8")
    imports = dict(SVELTE_IMPORT.findall(text))
    ids: dict[str, set[str]] = {}
    for section, component in BRANCH.findall(text):
        spec = imports.get(component)
        start = _resolve_import(SETTINGS_PANE, spec) if spec else None
        written: set[str] = set()
        for path in _closure(start) if start else set():
            written.update(WRITTEN_ID.findall(path.read_text(encoding="utf-8")))
        ids[section] = written
    return ids


def _registry_lands() -> dict[str, str]:
    """Registered key -> the section its row is drawn on."""
    homes = {(quoted or bare): home for quoted, bare, home in HOME.findall(_block("REGISTRY_HOME"))}
    moved = dict(MOVED.findall(_block("MOVED_TO")))
    return {
        key: moved.get(homes.get(setting.section, ""), homes.get(setting.section, ""))
        for key, setting in registered_settings().items()
    }


def _declared_addresses() -> list[tuple[str, str, str]]:
    """Every (file, section, key) a hand-written pane declares."""
    found: list[tuple[str, str, str]] = []
    for path in sorted(PANES.glob("*.search.ts")):
        text = path.read_text(encoding="utf-8")
        for chunk in re.split(r"\n\t\{\n", text)[1:]:
            chunk = chunk.split("\n\t}", 1)[0]
            keys = DECLARED_KEY.findall(chunk)
            sections = DECLARED_SECTION.findall(chunk)
            if keys and sections:
                found.append((path.name, sections[0], keys[0]))
    return found


def _links() -> list[tuple[str, str, str]]:
    """Every (file, section, key) a `SettingLink` or an open call names."""
    found: list[tuple[str, str, str]] = []
    for path in CLIENT.rglob("*"):
        if path.suffix not in (".svelte", ".ts") or ".test." in path.name:
            continue
        if "routes/design" in path.as_posix():
            continue
        text = path.read_text(encoding="utf-8")
        where = path.relative_to(CLIENT).as_posix()
        for match in SETTING_LINK.finditer(text):
            attributes = dict(ATTRIBUTE.findall(match.group(1)))
            if "section" in attributes and "setting" in attributes:
                found.append((where, attributes["section"], attributes["setting"]))
        for section, key in OPEN_CALL.findall(text):
            found.append((where, section, key))
    return found


def _unresolved(addresses: list[tuple[str, str, str]]) -> list[str]:
    panes = _pane_ids()
    registry = _registry_lands()
    missing: list[str] = []
    for where, section, key in addresses:
        lands, row = _resolve(section, key)
        if lands not in panes:
            missing.append(f"{where}: {section}#{key} lands on `{lands}`, which no pane draws")
        elif row in registry:
            if registry[row] != lands:
                missing.append(
                    f"{where}: {section}#{key} is a registered setting drawn on "
                    f"`{registry[row]}`, and the link lands on `{lands}`"
                )
        elif row not in panes[lands]:
            missing.append(f"{where}: {section}#{key}: `{lands}` draws no element with that id")
    return missing


def test_there_are_keys_to_check() -> None:
    """A KNOWN POSITIVE: there are keys to check."""
    assert len(_declared()) >= 20


def test_the_patterns_find_what_they_are_aimed_at() -> None:
    """A KNOWN POSITIVE for each pattern."""
    assert DECLARED_KEY.findall("\t\tkey: 'sites.logins',") == ["sites.logins"]
    assert set(WRITTEN_ID.findall('<h2 id="sites.logins">Logins</h2>')) == {"sites.logins"}
    assert set(WRITTEN_ID.findall("<SettingGroup id='users.guests' />")) == {"users.guests"}
    assert WRITTEN_ID.findall('<div class="row">') == []
    assert BARE_OPEN.findall("onclick={() => openSettings('tasks')}") == ["tasks"]
    assert BARE_OPEN.findall("openSettings('tasks', 'quarantine.keep_days')") == []


def test_every_declared_key_names_something_a_screen_draws() -> None:
    """Every declared key is an id a screen draws."""
    written = _written()

    unreachable = {key: where for key, where in _declared().items() if key not in written}

    assert not unreachable, (
        "These settings-search results name a row that nothing draws, so pressing one opens the\n"
        "right pane and points at nothing, which looks exactly like a result with no key at all,\n"
        "because `revealSetting` gives up quietly by design:\n  "
        + "\n  ".join(f"{key}  (declared in {where})" for key, where in sorted(unreachable.items()))
        + '\n\nEither put `id="<key>"` on the block it names, or take the key off and let the\n'
        "result open the section and stop there, which is honest."
    )


def test_the_resolution_reads_what_it_is_aimed_at() -> None:
    """KNOWN POSITIVES for the resolution's reading."""
    panes = _pane_ids()
    assert len(panes) >= 20, f"SettingsPane parsed into {len(panes)} branches"
    assert "performance.repair_playback" in _registry_lands()
    assert _resolve("theater", "theater.layout") == ("playback", "theater.layout")
    assert _resolve("appearance", "appearance.links_open_in") == (
        "general",
        "general.links_open_in",
    )
    assert len(_declared_addresses()) >= 20
    assert len(_links()) >= 5


def test_every_declared_key_resolves_to_a_row_on_the_pane_it_opens() -> None:
    """Every declared key resolves to a row on the pane it opens."""
    missing = _unresolved(_declared_addresses())

    assert not missing, (
        "These settings-search results open a pane and ring nothing. The deep link a result makes\n"
        "has to land on its row every time:\n  " + "\n  ".join(missing)
    )


def test_every_setting_link_resolves_to_a_row_on_the_pane_it_opens() -> None:
    """Every `SettingLink` and literal `openSettings(section, key)` resolves likewise."""
    missing = _unresolved(_links())

    assert not missing, (
        "These links into Settings open a pane and ring nothing:\n  "
        + "\n  ".join(missing)
        + "\n\nPoint the link at the section that draws the row, or move the row, or add the move to "
        "`KEY_MOVED_TO` / `MOVED_TO` in sections.ts so old addresses follow it."
    )


def _bare_presses() -> list[str]:
    """Every `<file>:<section>` opened with no row named."""
    found: list[str] = []
    for path in CLIENT.rglob("*"):
        if path.suffix not in (".svelte", ".ts") or ".test." in path.name:
            continue
        if "routes/design" in path.as_posix():
            continue
        for section in BARE_OPEN.findall(path.read_text(encoding="utf-8")):
            found.append(f"{path.relative_to(CLIENT).as_posix()}:{section}")
    return found


def test_a_press_into_settings_names_its_row_or_says_it_opens_the_pane() -> None:
    """A press into Settings names its row, or is listed as opening the pane whole."""
    pressed = _bare_presses()
    unnamed = sorted(set(pressed) - set(OPENS_THE_PANE))
    stale = sorted(set(OPENS_THE_PANE) - set(pressed))

    assert not unnamed, (
        "These open a Settings pane and name no row:\n  "
        + "\n  ".join(unnamed)
        + "\n\nName the row: openSettings('<section>', '<key>'), so the pane rings it and a moved row"
        " is followed. A press that really opens the whole pane goes in OPENS_THE_PANE with why."
    )
    assert not stale, "OPENS_THE_PANE names a press that is gone: " + ", ".join(stale)
