# SPDX-License-Identifier: AGPL-3.0-or-later
"""A sheet that needs to be wider than the default says so with a custom property, never a size.

`.sheet` in `app.css` sizes every dialog `Modal` draws. A component's own `inline-size` on its
sheet class has the same specificity, so which wins depends on stylesheet load order, which a
route-split build does not fix; no check that does not paint can see it lose. `.sheet` reads
`--sheet-inline` with a fallback and a component sets the property, so nothing competes. The
classes are read from the `sheetClass=` attributes, so a new sheet is covered unedited.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.gates import client_source

pytestmark = [pytest.mark.gate, pytest.mark.unit]

REPO = Path(__file__).resolve().parents[2]
CLIENT = REPO / "frontend" / "src"

#: The one place a sheet's width may be decided from outside `app.css`.
PROPERTY = "--sheet-inline"

#: What a component hands `Modal` to key its stylesheet on.
_SHEET_CLASS = re.compile(r'sheetClass=(?:"([^"]+)"|\{[\'"]([^\'"]+)[\'"]\})')

#: A size written directly: `width`, `inline-size` and their bounds, which lose the same way.
_A_SIZE = re.compile(
    r"^\s*(?:width|inline-size|min-width|max-width|min-inline-size|max-inline-size)\s*:",
    re.MULTILINE,
)

#: One flat rule, a selector and its declarations: enough for a scoped component stylesheet.
_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}", re.MULTILINE)

#: What separates one compound selector from the next.
_COMBINATOR = re.compile(r"[\s>+~]+")


def _rule_bodies(source: str, klass: str) -> list[str]:
    """Every declaration block that dresses the ELEMENT this class is on: the last compound selector
    only, since `.pick-sheet input { inline-size: 100% }` sizes a box inside the sheet."""
    found: list[str] = []
    for selectors, body in _RULE.findall(source):
        for one in selectors.split(","):
            cleaned = one.replace(":global(", " ").replace(")", " ").strip()
            if not cleaned:
                continue
            last = _COMBINATOR.split(cleaned)[-1]
            if re.search(r"\." + re.escape(klass) + r"(?![\w-])", last):
                found.append(body)
                break
    return found


def _sheet_classes(source: str) -> set[str]:
    return {
        name
        for match in _SHEET_CLASS.finditer(source)
        for name in match.groups()
        if name
        # A class list is one string; every name in it keys the same element.
        for name in name.split()
    }


@pytest.mark.regression
def test_no_component_writes_a_size_onto_a_sheet() -> None:
    complaints: list[str] = []
    for path in client_source(CLIENT, ".svelte"):
        source = path.read_text(encoding="utf-8")
        for klass in _sheet_classes(source):
            for body in _rule_bodies(source, klass):
                if _A_SIZE.search(body):
                    complaints.append(
                        f"{path.relative_to(REPO)}: .{klass} is a sheet and sets its own size."
                        f" Set {PROPERTY} instead: app.css reads it, and a size written here"
                        " ties with `.sheet` and is decided by load order."
                    )

    assert not complaints, (
        "\nA sheet decides its own width with a property `.sheet` also writes.\n\n"
        "The two selectors have the same specificity, so which one wins is whichever stylesheet\n"
        "the browser loaded last, and with a route-split build that is not knowable: a sheet\n"
        "declaring 46rem can draw at 420 with the rule present and correct.\n\n  "
        + "\n  ".join(complaints)
    )


@pytest.mark.regression
def test_every_widened_sheet_widens_through_the_property() -> None:
    """A sheet class that is styled with a width expresses it as the property; most say nothing."""
    widened = {
        path.relative_to(REPO).as_posix()
        for path in client_source(CLIENT, ".svelte")
        if PROPERTY in path.read_text(encoding="utf-8")
    }

    assert widened, (
        f"nothing sets {PROPERTY} anywhere. Either every sheet is the default width now, or the"
        " property was renamed and this gate has stopped reading anything."
    )


@pytest.mark.regression
def test_the_gate_can_actually_see_one() -> None:
    """The shape that lost is caught and the shape that replaced it passes."""
    lost = """<Modal sheetClass="wide-thing" />\n<style>\n\t:global(.wide-thing) {\n\t\tinline-size: 46rem;\n\t}\n</style>"""
    assert _sheet_classes(lost) == {"wide-thing"}
    assert _A_SIZE.search(_rule_bodies(lost, "wide-thing")[0])

    kept = """<Modal sheetClass="wide-thing" />\n<style>\n\t:global(.wide-thing) {\n\t\t--sheet-inline: 46rem;\n\t}\n</style>"""
    assert not _A_SIZE.search(_rule_bodies(kept, "wide-thing")[0])

    # A box INSIDE a sheet is not the sheet's width.
    inside = """<style>\n\t:global(.wide-thing input) {\n\t\tinline-size: 100%;\n\t}\n</style>"""
    assert _rule_bodies(inside, "wide-thing") == []

    # Nor a class whose name merely starts with a sheet's.
    other = """<style>\n\t:global(.wide-thing-foot) {\n\t\tinline-size: 100%;\n\t}\n</style>"""
    assert _rule_bodies(other, "wide-thing") == []

    # A pseudo-class is still the same element.
    hovered = """<style>\n\t:global(.wide-thing:hover) {\n\t\tinline-size: 46rem;\n\t}\n</style>"""
    assert _A_SIZE.search(_rule_bodies(hovered, "wide-thing")[0])

    # The attribute in its expression spelling.
    assert _sheet_classes("""<Modal sheetClass={'picked'} />""") == {"picked"}
