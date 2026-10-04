# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every menu item, verb, Sort and Filter choice, and the docs page that has to explain it.

Read from the client's own declarations: the folder menu (`library/folder-verbs.ts`), the walls'
verbs (`components/entity/verbs.ts`, offered per kind as `KIND_FACTS` allows), the player's acts
(`lib/player/acts.ts`), the Sort choices (`grid/sort-state.svelte.ts`) and the Filter facets
(`components/shell/facet-labels.ts`). A label missing from its page is counted by `check_docs.py`.
"""

from __future__ import annotations

import re

import docs_client

LIB = docs_client.ROOT / "frontend" / "src" / "lib"
_LABEL = re.compile(r"""\blabel:\s*(?:'([^']+)'|"([^"]+)")""")
_TERNARY = re.compile(
    r"""\blabel:\s*[\w.!]+\s*\?\s*(?:'([^']+)'|"([^"]+)")\s*:\s*(?:'([^']+)'|"([^"]+)")"""
)
_ACT = re.compile(r"^\s*(\w+):\s*'([^']+)',?\s*$", re.M)
_KIND = re.compile(r"^\t(\w+): \{(.*?)^\t\}", re.M | re.S)
#: Which verb a wall offers only when its kind says so, and the field that says it.
_NEEDS = {
    "Tag": "tagWall",
    "Enrich": "enrichAs",
    "Auto-enrich": "enrichAs",
    "Don't enrich": "enrichAs",
    "Allow enrichment": "enrichAs",
    "Merge": "mergeable",
}


def _read(rel: str) -> str:
    return docs_client._bare((LIB / rel).read_text(encoding="utf-8"))


def _between(text: str, start: str, end: str) -> str:
    at = text.index(start)
    return text[at : text.index(end, at)]


def _labels(text: str) -> list[str]:
    found = [a or b for a, b in _LABEL.findall(text)]
    for pair in _TERNARY.findall(text):
        found += [one for one in pair if one]
    return list(dict.fromkeys(found))


def _page(href: str) -> str:
    rail = {item.href: docs_client.slug(item.label) for item in docs_client.rail()}
    return f"library/{rail[href]}"


def _walls() -> dict[str, list[str]]:
    verbs = _labels(_read("components/entity/verbs.ts"))
    facts = _between(_read("components/entity/wall-verbs.svelte.ts"), "KIND_FACTS", "\n};")
    sorts = _read("grid/sort-state.svelte.ts")
    counted = [a or b for a, b in _LABEL.findall(_between(sorts, "SORT_OPTIONS", "] as const"))]
    instead = dict(re.findall(r"(\w+): '([^']+)'", _between(sorts, "COUNTED_INSTEAD", "};")))
    universal = ["Newest first", "Oldest first", "Recently edited", "Name A-Z", "Name Z-A"]
    universal = [one for one in universal if one in counted] + list(instead.values())
    opinions = _labels(_between(sorts, "ENTITY_OPINION_SORTS", "];"))
    found: dict[str, list[str]] = {}
    for _kind, block in _KIND.findall(facts):
        wall = re.search(r"wall: '([^']+)'", block)
        if wall is None:
            continue
        offered = [verb for verb in verbs if f"{_NEEDS.get(verb, 'x')}: null" not in block]
        found[_page("/" + wall.group(1))] = offered + universal + opinions
    return found


def _player() -> dict[str, list[str]]:
    acts = _read("player/acts.ts")
    names = dict(_ACT.findall(_between(acts, "export const ACTS", "} as const")))
    keys = _between(acts, "export const ACT_KEYS", "\n};")
    theater_only = {
        name for name in names if re.search(rf"\b{name}: \{{ theater: '[^']+' \}}", keys)
    }
    return {
        _page("/theater"): [names[one] for one in names if one in theater_only],
        _page("/browse"): [names[one] for one in names if one not in theater_only],
    }


def declared() -> dict[str, list[str]]:
    """Each page and the labels it has to explain, in the order they are declared."""
    found: dict[str, list[str]] = {}

    def add(page: str, labels: list[str]) -> None:
        found[page] = list(dict.fromkeys(found.get(page, []) + labels))

    sorts = _read("grid/sort-state.svelte.ts")
    facets = _between(
        _read("components/shell/facet-labels.ts"), "export const FACETS", "] as const"
    )
    filters = [
        a or b for a, b in _LABEL.findall(re.sub(r"\{[^{}]*retired: true[^{}]*\}", "", facets))
    ]
    add(_page("/browse"), [*_labels(_read("library/folder-verbs.ts")), "Share"])
    add(
        _page("/browse"),
        [a or b for a, b in _LABEL.findall(_between(sorts, "SORT_OPTIONS", "] as const"))],
    )
    add(_page("/browse"), filters)
    for page, labels in [*_walls().items(), *_player().items()]:
        add(page, labels)
    site = re.search(r"SITE_ORDER = \{[^}]*label: '([^']+)'", sorts)
    artist = re.search(r"ARTIST_ORDER = \{[^}]*label: '([^']+)'", sorts)
    add(_page("/downloads"), [site.group(1)] if site else [])
    add(_page("/songs"), [artist.group(1)] if artist else [])
    return found


def missing(
    site: dict[str, str], wanted: dict[str, list[str]] | None = None
) -> dict[str, list[str]]:
    """The labels each page does not mention yet."""
    found = {}
    for page, labels in (declared() if wanted is None else wanted).items():
        text = site.get(page, "")
        found[page] = [label for label in labels if label not in text]
    return found


if __name__ == "__main__":
    for page, labels in missing(__import__("check_docs").pages()).items():
        print(f"{page}: {len(labels)} missing: {', '.join(labels)}")
