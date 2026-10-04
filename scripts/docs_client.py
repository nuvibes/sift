# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the docs site reads from the client's own declarations, so no name is typed twice.

The settings panes, their groups and labels (`settings-ui/sections.ts`), where a registry section
and a moved row are drawn (`REGISTRY_HOME`, `MOVED_TO`, `KEY_MOVED_TO`), and the rail's
destinations (`components/shell/nav.ts`). Read as text: the docs build runs without a client build.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECTIONS_TS = ROOT / "frontend" / "src" / "lib" / "settings-ui" / "sections.ts"
NAV_TS = ROOT / "frontend" / "src" / "lib" / "components" / "shell" / "nav.ts"

_COMMENT = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)
_HEADING = re.compile(r"heading:\s*'([^']+)'")
_PANE = re.compile(r"\{\s*id:\s*'([^']+)',\s*label:\s*'([^']+)'[^}]*\}")
_PAIR = re.compile(r"(?:'([^']+)'|([\w$-]+))\s*:\s*'([^']+)'")
_TARGET = re.compile(r"(?:'([^']+)'|([\w$-]+))\s*:\s*\{([^}]*)\}")
_FIELD = re.compile(r"(\w+):\s*'([^']+)'")
_RAIL = re.compile(r"\{\s*id:\s*'([^']+)',\s*href:\s*'([^']+)',\s*label:\s*'([^']+)'")


@dataclass(frozen=True)
class Pane:
    id: str
    label: str
    group: str
    admin: bool


@dataclass(frozen=True)
class Address:
    pane: str
    key: str | None = None
    show: str | None = None


def _bare(text: str) -> str:
    return _COMMENT.sub("", text)


def _block(text: str, name: str) -> str:
    start = text.index(f"const {name}")
    open_at = text.index("= {", start) if f"const {name}:" in text else text.index("{", start)
    depth = 0
    for at in range(open_at, len(text)):
        depth += {"{": 1, "}": -1}.get(text[at], 0)
        if depth == 0 and text[at] == "}":
            return text[text.index("{", open_at) + 1 : at]
    raise ValueError(f"{name} never closes")


def panes(path: Path = SECTIONS_TS) -> list[Pane]:
    """Every settings pane, in the order the screen lists them, with the group it stands in."""
    text = _bare(path.read_text(encoding="utf-8"))
    body = text[text.index("SETTINGS_GROUPS") :]
    body = body[: body.index("];") + 2]
    found: list[Pane] = []
    marks = [(m.start(), m.group(1)) for m in _HEADING.finditer(body)]
    for match in _PANE.finditer(body):
        group = [name for at, name in marks if at < match.start()][-1]
        found.append(Pane(match.group(1), match.group(2), group, "admin: true" in match.group(0)))
    return found


def registry_home(path: Path = SECTIONS_TS) -> dict[str, str]:
    text = _bare(path.read_text(encoding="utf-8"))
    return {
        (m.group(1) or m.group(2)): m.group(3)
        for m in _PAIR.finditer(_block(text, "REGISTRY_HOME"))
    }


def _targets(text: str, name: str) -> dict[str, dict[str, str]]:
    return {
        (m.group(1) or m.group(2)): dict(_FIELD.findall(m.group(3)))
        for m in _TARGET.finditer(_block(text, name))
    }


def resolver(
    path: Path = SECTIONS_TS,
) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    """`(MOVED_TO, KEY_MOVED_TO)` as read from the client."""
    text = _bare(path.read_text(encoding="utf-8"))
    return _targets(text, "MOVED_TO"), _targets(text, "KEY_MOVED_TO")


Moves = dict[str, dict[str, str]]


def resolve(section: str, key: str | None, moved: Moves, key_moved: Moves) -> Address:
    """The client's `resolveAddress`: a moved row first, then a moved section."""
    row = key_moved.get(key) if key else None
    if row:
        home = moved.get(row["section"], {})
        return Address(
            home.get("section", row["section"]), row["key"], row.get("show", home.get("show"))
        )
    home = moved.get(section, {})
    return Address(home.get("section", section), key, home.get("show"))


def drawn_at(section: str, key: str, path: Path = SECTIONS_TS) -> Address:
    """The pane and row a registered setting is drawn on, from its registry section."""
    home = registry_home(path)
    moved, key_moved = resolver(path)
    return resolve(home.get(section, section.lower()), key, moved, key_moved)


@dataclass(frozen=True)
class RailItem:
    id: str
    href: str
    label: str


def rail(path: Path = NAV_TS) -> list[RailItem]:
    """The rail's destinations in the order they ship, Settings left out: Settings is its panes."""
    text = _bare(path.read_text(encoding="utf-8"))
    body = text[text.index("export const RAIL_NAV") :]
    body = body[: body.index("\n];") + 3]
    flat = re.sub(r"\s+", " ", body)
    return [RailItem(*m.groups()) for m in _RAIL.finditer(flat) if m.group(1) != "settings"]


def slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
