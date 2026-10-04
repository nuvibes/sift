# SPDX-License-Identifier: AGPL-3.0-or-later
"""A row drawn on a settings sub-page can be found: by search, by a pasted path, by a deep link.

## Why a row one level in needs more than a row on the pane

A settings pane is in the document while it is open, so a deep link to a row on it finds the row.
A sub-page (More settings, an Edit page) draws its rows only while it is open, so a deep link has
to open the page first, and only the page's own claim (`drilldown.own`) can say which page that is.
The search needs the row in its index as well. A row drawn by hand on a sub-page is in neither by
itself: the registry does not know it, and nothing can read a snippet to find out what it draws.

So such a row is DECLARED once, in its pane's `<Pane>.search.ts`, as an entry filed under the
page's title (`page:`), and the page claims every entry filed under it (`filedUnder` in
`drilldown.svelte.ts`). One declaration feeds the index and the claim, so the two cannot drift.

## What it proves

For every sub-page a pane draws (a `SubPage` value, a `drilldown.open(title, snippet)` call, or a
`PresetGroup`'s children), every `id="..."` written in what the page draws, following the snippets
it renders:

- a registered setting's key is in the index already; the pane names that key, or a constant
  holding it, where it claims the page's keys;
- any other id is the key of an entry in the pane's own `.search.ts` filed under that page's
  title, written the same way the page writes it, and the pane claims that page's entries with
  `filedUnder(..., <title>)`.

And in the other direction: every entry filed under a page names a row that page draws, so a
row that moved or went cannot leave an entry opening a page it is not on.

An id written as an expression (`id={...}`) on a sub-page is refused: no static read can say what
it will be, so nothing could check it.

## What it does not prove

Rows drawn by a COMPONENT placed on the page (a `TaskWhen`, a `SettingRow` given its entry) are not
followed into that component. A registry row carries its key by construction and is in the index
from the registry; a component drawing a hand row inside a sub-page would be missed here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pytest

import sift.main  # noqa: F401 (settings register when their slice is imported)
from sift.kernel.settings_registry import registered_settings

pytestmark = [pytest.mark.gate, pytest.mark.unit]

REPO = Path(__file__).resolve().parents[2]
LIB = REPO / "frontend" / "src" / "lib"
PANES = LIB / "settings-ui"

#: `{ title: X, ... body: snippet }`: a `SubPage` value. Nothing between them opens a brace.
SUB_PAGE_VALUE = re.compile(r"title:\s*(?P<title>[^,\n]+),[^{}]*?body:\s*(?P<body>\w+)", re.S)
#: `drilldown.open(title, snippet, ...)`.
OPENED = re.compile(r"drilldown\.open\(\s*(?P<title>[^,]+?),\s*(?P<body>\w+)\s*[,)]")
#: `<PresetGroup ... pageTitle=X ...> children </PresetGroup>`. An arrow in an attribute is not
#: the end of the tag.
PRESET = re.compile(r"<PresetGroup\b(?P<attrs>(?:=>|[^>])*)>(?P<children>.*?)</PresetGroup>", re.S)
PRESET_TITLE = re.compile(r"""pageTitle=(?:\{(?P<expression>[^}]+)\}|(?P<words>"[^"]*"))""")
#: An id written out, and one written as an expression.
WRITTEN_ID = re.compile(r"""\bid=["'](?P<id>[^"']+)["']""")
EXPRESSION_ID = re.compile(r"\bid=\{")
RENDERED = re.compile(r"\{@render\s+(?P<name>\w+)\(")
#: One entry of a `SEARCHABLE` list: a brace at one tab to the brace that closes it.
ENTRY = re.compile(r"^\t\{\n(?P<body>.*?)^\t\}", re.S | re.M)
ENTRY_KEY = re.compile(r"^\t\tkey: '(?P<key>[^']+)',$", re.M)
ENTRY_PAGE = re.compile(r"^\t\tpage: (?P<page>.+),$", re.M)
#: `export const NAME = 'value'` anywhere in the client's library: how a key is named by constant.
CONSTANT = re.compile(r"""export const (?P<name>\w+)\s*=\s*['"](?P<value>[^'"]+)['"]""")


@dataclass(frozen=True)
class SubPage:
    pane: str
    title: str
    ids: tuple[str, ...]
    expression_ids: int


def _snippet(source: str, name: str) -> str | None:
    """The text of `{#snippet name(...)}` up to its matching `{/snippet}`, or None."""
    start = re.search(r"\{#snippet " + re.escape(name) + r"\(", source)
    if start is None:
        return None
    depth = 1
    for mark in re.finditer(r"\{#snippet |\{/snippet\}", source[start.end() :]):
        depth += 1 if mark.group(0).startswith("{#") else -1
        if depth == 0:
            return source[start.start() : start.end() + mark.end()]
    return None


def _drawn(source: str, text: str) -> str:
    """What a page draws: its own text and every local snippet it renders, followed through."""
    seen: set[str] = set()
    parts = [text]
    waiting = [match.group("name") for match in RENDERED.finditer(text)]
    while waiting:
        name = waiting.pop()
        if name in seen:
            continue
        seen.add(name)
        body = _snippet(source, name)
        if body is None:
            continue
        parts.append(body)
        waiting.extend(match.group("name") for match in RENDERED.finditer(body))
    return "\n".join(parts)


def _said(title: str) -> str:
    """A title as written, with the quotes of a literal taken off: `'A'` and `"A"` are one title."""
    title = title.strip()
    if len(title) >= 2 and title[0] == title[-1] and title[0] in "'\"":
        return title[1:-1]
    return title


def _page(pane: str, source: str, title: str, text: str) -> SubPage:
    drawn = _drawn(source, text)
    return SubPage(
        pane=pane,
        title=_said(title),
        ids=tuple(match.group("id") for match in WRITTEN_ID.finditer(drawn)),
        expression_ids=len(EXPRESSION_ID.findall(drawn)),
    )


def _sub_pages() -> list[SubPage]:
    found: list[SubPage] = []
    for path in sorted(PANES.glob("*.svelte")):
        if path.name.endswith(".test.svelte"):
            continue
        source = path.read_text(encoding="utf-8")
        for pattern in (SUB_PAGE_VALUE, OPENED):
            for match in pattern.finditer(source):
                body = _snippet(source, match.group("body"))
                if body is not None:
                    found.append(_page(path.name, source, match.group("title"), body))
        for match in PRESET.finditer(source):
            title = PRESET_TITLE.search(match.group("attrs"))
            if title is not None:
                said = title.group("expression") or title.group("words")
                found.append(_page(path.name, source, said, match.group("children")))
    return found


def _filed(pane: str) -> dict[str, str]:
    """The pane's own entries filed under a page: key -> the page's title, as written."""
    declaration = PANES / pane.replace(".svelte", ".search.ts")
    if not declaration.exists():
        return {}
    filed: dict[str, str] = {}
    for entry in ENTRY.finditer(declaration.read_text(encoding="utf-8")):
        key = ENTRY_KEY.search(entry.group("body"))
        page = ENTRY_PAGE.search(entry.group("body"))
        if key and page:
            filed[key.group("key")] = _said(page.group("page"))
    return filed


def _constants() -> dict[str, set[str]]:
    """Every string constant the library exports, by value: how a pane names a key it claims."""
    names: dict[str, set[str]] = {}
    for path in LIB.rglob("*.ts"):
        if ".test." in path.name:
            continue
        for match in CONSTANT.finditer(path.read_text(encoding="utf-8")):
            names.setdefault(match.group("value"), set()).add(match.group("name"))
    return names


def _script(source: str) -> str:
    """The pane with its written ids taken out, so an id is not its own claim."""
    return WRITTEN_ID.sub("", source)


def test_the_reader_finds_the_sub_pages_and_their_hand_rows() -> None:
    """A KNOWN POSITIVE. A pattern that matched nothing would pass every check below in silence.

    Three sections draw a hand row behind More settings (Faces, Smart Search, Watermarks), and
    Faces draws three of them.
    """
    pages = _sub_pages()
    assert len(pages) >= 8, [(page.pane, page.title) for page in pages]
    by_pane = {page.pane: set(page.ids) for page in pages if page.ids}
    assert {"faces.regroup", "faces.rescan", "faces.models"} <= by_pane.get("Faces.svelte", set())
    assert "semantic.models" in by_pane.get("Semantic.svelte", set())
    assert "watermarks.models" in by_pane.get("Watermarks.svelte", set())
    assert _filed("Faces.svelte").get("faces.regroup") == "COPY.more.label"


def test_the_reader_follows_a_rendered_snippet() -> None:
    """A row drawn by a snippet the page renders is on the page."""
    source = (
        "{#snippet page()}<p>top</p>{@render inner()}{/snippet}\n"
        '{#snippet inner()}<ActionRow id="x.deep" />{/snippet}\n'
    )
    body = _snippet(source, "page")
    assert body is not None
    assert "x.deep" in _page("X.svelte", source, "T", body).ids


def test_every_row_on_a_sub_page_is_in_the_index_and_opens_its_page() -> None:
    """The gate."""
    registered = registered_settings()
    constants = _constants()
    faults: list[str] = []
    for page in _sub_pages():
        where = f"{page.pane}, the page titled {page.title}"
        if page.expression_ids:
            faults.append(f"{where}: an id written as an expression, which nothing can check")
        source = (PANES / page.pane).read_text(encoding="utf-8")
        script = _script(source)
        filed = _filed(page.pane)
        for row in page.ids:
            if row in registered:
                named = {f"'{row}'", *constants.get(row, set())}
                if not any(name in script for name in named):
                    faults.append(f"{where}: {row} is drawn by hand and the pane never claims it")
                continue
            if filed.get(row) != page.title:
                faults.append(
                    f"{where}: {row} is not in {page.pane.replace('.svelte', '.search.ts')} "
                    f"filed under `page: {page.title}`"
                )
                continue
            claim = re.compile(r"filedUnder\(\s*\w+,\s*" + re.escape(page.title) + r"\s*\)")
            if not claim.search(source):
                faults.append(f"{where}: the page never claims `filedUnder(..., {page.title})`")

    assert not faults, (
        "Rows drawn on a settings sub-page that a search, a pasted path or a deep link cannot "
        "reach:\n  " + "\n  ".join(sorted(set(faults)))
    )


def test_every_entry_filed_under_a_page_is_drawn_there() -> None:
    """The other direction: an entry filed under a page whose row is not on it opens the wrong
    page, or a page with nothing to ring."""
    drawn: dict[tuple[str, str], set[str]] = {}
    for page in _sub_pages():
        drawn.setdefault((page.pane, page.title), set()).update(page.ids)
    stale: list[str] = []
    for declaration in sorted(PANES.glob("*.search.ts")):
        pane = declaration.name.replace(".search.ts", ".svelte")
        for key, title in _filed(pane).items():
            if key not in drawn.get((pane, title), set()):
                stale.append(
                    f"{declaration.name}: {key} is filed under {title} and not drawn there"
                )

    assert not stale, "\n".join(stale)
