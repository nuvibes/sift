# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every page under `routes/` is named as a destination somewhere else in the client (the
navigation, a link, a `goto`), parameters blanked so `/people/[id]` and `/people/${person.id}` are
one. The floor only: not that the link is visible or open to every role.
"""

from __future__ import annotations

import re
import textwrap
from pathlib import Path

import pytest

from tests.gates import client_source

pytestmark = [pytest.mark.gate, pytest.mark.unit]

REPO = Path(__file__).resolve().parents[2]
CLIENT = REPO / "frontend" / "src"
ROUTES = CLIENT / "routes"

#: Pages nothing links to, with why that is right: a screen reached only by typing its address does
#: not exist for almost everybody.
NOT_LINKED_FROM_ANYWHERE: dict[str, str] = {
    "/login": "where an unauthenticated request is sent. Linking to it from inside would be a link "
    "only somebody already signed in could follow",
    "/setup": "the first boot, before there is anything to link from",
    "/locked": "where the shell sends somebody when the app is locked, rather than somewhere they "
    "choose to go",
}


def _blanked(address: str) -> str:
    """An address with its parameters removed: `/people/[id]` and `/people/${person.id}` both
    reduce to `/people/*`."""
    with_params = re.sub(r"\[+[^\]]+\]+", "*", address)
    blanked = re.sub(r"\$\{[^}]*\}", "*", with_params)
    # Adjacent blanks are ONE: `/settings/${section}${key ? ...}` is declared as one `[[section]]`.
    # A slash between them is a second segment.
    return re.sub(r"\*{2,}", "*", blanked).rstrip("/") or "/"


def declared_pages(root: Path) -> set[str]:
    """Every address that renders a screen, from the folders that declare them."""
    return {
        "/" + str(page.parent.relative_to(root)).replace("\\", "/")
        for page in root.rglob("+page.svelte")
    } - {"/."}


#: Any address written down in the client, however it is used: a nav item, an href, a goto.
_WRITTEN = re.compile(r"""(['"`])(?P<address>/[^'"`\n]*)\1""")


def without_interpolations(source: str) -> str:
    """Every `${...}` replaced by a star, counting braces so a nested template literal (a second
    backtick inside the first) comes out whole rather than ending the address mid-expression."""
    out: list[str] = []
    at = 0
    while at < len(source):
        start = source.find("${", at)
        if start == -1:
            out.append(source[at:])
            break
        out.append(source[at:start])
        out.append("*")
        depth = 1
        here = start + 2
        while here < len(source) and depth > 0:
            if source[here] == "{":
                depth += 1
            elif source[here] == "}":
                depth -= 1
            here += 1
        if depth > 0:
            # Unbalanced: keep the rest verbatim, since a literal `'${'` would otherwise swallow
            # the rest of the file.
            out.append(source[start:])
            break
        at = here
    return "".join(out)


def addresses_written(source: str) -> set[str]:
    """Every address in one file, as destinations: no query, no fragment, no expressions.

    A fragment (`/settings/tags#enrich.site.name`) is a place on a screen, not another screen.
    """
    return {
        _blanked(match.group("address").split("?")[0].split("#")[0])
        for match in _WRITTEN.finditer(without_interpolations(source))
    }


def _mentioned_outside(page: str, root: Path) -> bool:
    """Whether anything outside this page's own folder names it: every screen names itself."""
    folder = root / page.lstrip("/")
    wanted = _blanked(page)
    for source in client_source(CLIENT, ".ts", ".svelte"):
        if folder in source.parents:
            continue
        if wanted in addresses_written(source.read_text(encoding="utf-8")):
            return True
    return False


@pytest.mark.regression
def test_every_page_can_be_got_to() -> None:
    unreachable = sorted(
        page
        for page in declared_pages(ROUTES)
        if page not in NOT_LINKED_FROM_ANYWHERE and not _mentioned_outside(page, ROUTES)
    )

    assert not unreachable, (
        "\nThese screens exist and nothing in the application links to them.\n\n"
        "They build, they render and they can only be reached by typing the address, which for\n"
        "almost everybody means they do not exist. Either link to one from where it belongs, or\n"
        "add it to NOT_LINKED_FROM_ANYWHERE with the reason.\n\n  "
        + "\n  ".join(unreachable)
        + "\n"
    )


def test_the_excuses_are_all_still_pages() -> None:
    """No excuse outlives the screen it excused."""
    stale = sorted(set(NOT_LINKED_FROM_ANYWHERE) - declared_pages(ROUTES))
    assert not stale, f"these are not screens any more: {stale}"


def test_a_parameter_reads_the_same_however_it_is_written() -> None:
    """A parameter reads the same however it is written."""
    assert _blanked("/people/[id]") == _blanked("/people/${person.id}")
    assert _blanked("/settings/[[section]]") == _blanked("/settings/${name}")
    assert _blanked("/people/[id]") != _blanked("/sites/[id]")
    # Two expressions written against each other are one blank; a slash between them is not.
    assert _blanked("/settings/${section}${key}") == _blanked("/settings/[[section]]")
    assert _blanked("/people/${id}/clips/${clip}") != _blanked("/people/[id]")


def test_a_link_is_found_however_it_is_written() -> None:
    """The three shapes that appear in this client: a nav list, an href, a goto."""
    source = textwrap.dedent("""
        const NAV = [{ href: '/people', label: 'People' }];
        <a href="/tags">Tags</a>
        void goto(`/people/${person.id}`);
    """)
    assert {"/people", "/tags", "/people/*"} <= addresses_written(source)


def test_an_address_built_from_a_nested_template_is_still_found() -> None:
    """An address built from a nested template literal is still found, so the settings panel is
    not reported unreachable."""
    source = "pushState(`/settings/${section}${key ? `#${key}` : ''}`, { settings: section });"

    assert "/settings/*" in addresses_written(source)


def test_a_deep_link_to_a_row_is_the_same_screen_as_the_pane() -> None:
    """A fragment names a place ON a screen, not a page of its own."""
    assert addresses_written("goto('/settings/tags#enrich.site.name')") == {"/settings/tags"}


def test_taking_the_expressions_out_leaves_the_words_alone() -> None:
    """The stripper keeps the words: broken to return nothing, the client's addresses would vanish
    and this gate pass for having read nothing."""
    assert without_interpolations("a ${b} c") == "a * c"
    assert without_interpolations("`${a ? `${b}` : c}`") == "`*`"
    assert without_interpolations("nothing to strip") == "nothing to strip"
    # Unbalanced: everything after it survives.
    assert "/people" in without_interpolations("const odd = '${'; const nav = '/people';")
