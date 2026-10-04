# SPDX-License-Identifier: AGPL-3.0-or-later
"""The docs site's gates, held to the faults they exist to catch.

`scripts/check_docs.py` runs them over the real site; these tests plant one fault each and show it
refused, and read the client's declarations the way the generator does, so a declaration that
moves out of the reader's reach is a red here rather than an empty page there.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import check_docs  # noqa: E402
import docs_client  # noqa: E402
import docs_menus  # noqa: E402
import docs_vale_rules  # noqa: E402

pytestmark = [pytest.mark.gate, pytest.mark.unit]

PANES = {"Privacy": "privacy", "Folders": "library"}
KEYS = {"privacy": ["vault.lock_on_blur"], "library": []}


def test_a_path_is_a_plain_deep_link_to_its_pane_and_a_row_it_draws() -> None:
    good = "Open [Settings > Privacy > Lock](/settings/privacy#vault.lock_on_blur)."
    assert check_docs.check_paths(good, PANES, KEYS) == []
    assert check_docs.check_paths("Open Settings > Privacy > Lock.", PANES, KEYS)
    assert check_docs.check_paths("Open **Settings > Privacy > Lock**.", PANES, KEYS)
    ticked = "[`Settings > Privacy > Lock`](/settings/privacy#vault.lock_on_blur)"
    assert "code ticks" in check_docs.check_paths(ticked, PANES, KEYS)[0]
    assert check_docs.check_paths(
        "[Settings > Privacy > Lock](/settings/library#vault.lock_on_blur)", PANES, KEYS
    )
    assert check_docs.check_paths(
        "[Settings > Privacy > Lock](/settings/privacy#vault.nothing)", PANES, KEYS
    )
    assert check_docs.check_paths("[Settings > Privacy > Lock](/settings/privacy)", PANES, KEYS)


def test_a_page_opens_with_what_it_is_and_where_it_is() -> None:
    assert check_docs.check_shape("Sift sorts. To open it, click [Browse](/browse).") == []
    assert check_docs.check_shape("Sift sorts files in [Browse](/browse).")
    assert check_docs.check_shape("Sift sorts. It is fast.")
    assert check_docs.check_shape("A thing. Open [it](/browse).\n\n1. One.\n\n1. Two.")
    assert check_docs.check_shape("A thing. Ask on [Discord](https://discord.gg/abc).")
    assert check_docs.check_shape("A thing. Ask on [Discord]({{discord}}).") == []


def test_a_link_to_another_page_names_its_heading() -> None:
    site = {
        "a": "---\ntitle: A\n---\n\nSee [b](/b/#the-part) and [c](/b/).\n",
        "b": "---\ntitle: B\n---\n\n## The part\n\nText.\n",
    }
    found = check_docs.check_links(site, {"/browse"})
    assert found == ["a: '/b/' names no heading; link to the page and its heading"]
    site["a"] = "[x](/b/#missing) [y](/nowhere/#_top) [z](b/) [w](/browse) [d]({{discord}})"
    assert len(check_docs.check_links(site, {"/browse"})) == 3


def test_every_rail_destination_and_settings_pane_has_a_page_titled_as_the_screen() -> None:
    assert check_docs.check_coverage(check_docs.pages()) == []
    site = check_docs.pages()
    site.pop("library/browse")
    site["settings/privacy"] = "---\ntitle: Private\n---\n"
    assert len(check_docs.check_coverage(site)) == 2


def test_get_started_is_numbered_in_reading_order_and_a_link_names_the_step() -> None:
    site = check_docs.pages()
    assert check_docs.check_steps(site) == []
    first, second = check_docs.STEPS[:2]
    moved = dict(site, **{first: site[first].replace("order: 1", "order: 2")})
    assert "sidebar order" in check_docs.check_steps(moved)[0]
    unnumbered = dict(site, **{second: site[second].replace("'Step 2: ", "'")})
    assert check_docs.check_steps(unnumbered)
    named = dict(site, help=f"See [Install Sift](/{first}/#_top).")
    assert check_docs.check_steps(named) == [
        "help: a link names 'Install Sift'; name it 'Step 1: Install Sift'"
    ]
    phone = "get-started/sift-on-your-phone"
    numbered = dict(site, **{phone: "---\ntitle: 'Step 5: Phone'\n---\n"})
    assert check_docs.check_steps(numbered)


def _declared(css: str) -> dict[str, str]:
    return {name: value.strip() for name, value in re.findall(r"(--p-[\w-]+):\s*([^;]+);", css)}


def test_the_site_wears_the_apps_default_theme() -> None:
    """Every app primitive the docs stylesheet copies holds the app's default base and accent."""
    app = (ROOT / "frontend" / "src" / "app.css").read_text(encoding="utf-8")
    defaults: dict[str, str] = {}
    for head in (":root,\n[data-base='midnight'] {", ":root,\n[data-accent='blue'] {"):
        start = app.index(head)
        defaults |= _declared(
            re.sub(r"/\*.*?\*/", "", app[start : app.index("\n}", start)], flags=re.S)
        )
    docs = _declared(
        (ROOT / "docs-site" / "src" / "styles" / "sift.css").read_text(encoding="utf-8")
    )
    assert len(docs) > 20
    assert {name: value for name, value in docs.items() if defaults.get(name) != value} == {}


def test_the_vocabulary_offers_the_docs_their_second_person_word() -> None:
    loose, _ = docs_vale_rules.swaps(
        {
            "wrong_words": [
                {"word": "Measuring this device", "instead": "Benchmarking this device"}
            ],
            "retired_words": [{"pattern": r"\bthe machine\b", "instead": "this device"}],
            "banned_everywhere": {"entries": []},
        }
    )
    assert loose[r"\bthe machine\b"] == "your computer, or the computer Sift runs on"
    assert loose[r"\bMeasuring\ this\ device\b"] == "Benchmarking this device"


def test_the_client_declarations_are_read() -> None:
    panes = {pane.id: pane for pane in docs_client.panes()}
    assert len(panes) > 20 and panes["sites"].label == "Sites and Tunnels"
    assert [item.label for item in docs_client.rail()][:2] == ["Browse", "People"]
    assert docs_client.drawn_at("Sites and Tunnels", "swap.guest_tunnel").pane == "sites"
    assert docs_client.drawn_at("Logs", "logs.detail").pane == "tasks"


def test_every_menu_source_still_yields_its_labels() -> None:
    wanted = docs_menus.declared()
    assert {"Don't swap", "Newest first", "Media", "Save as Loop"} <= set(wanted["library/browse"])
    assert "Merge" in wanted["library/people"] and "Merge" not in wanted["library/tags"]
    assert "Every cell" in wanted["library/theater"] and "Artist A-Z" in wanted["library/music"]
    missing = docs_menus.missing(
        {"library/theater": "Every cell"}, {"library/theater": ["Every cell", "Shuffle"]}
    )
    assert missing == {"library/theater": ["Shuffle"]}


def test_a_ratchet_refuses_a_rise_and_asks_for_a_fall_to_be_recorded() -> None:
    kept = {"n": {"a": 2}}
    assert "has 3, the record says 2" in check_docs.ratchet("n", {"a": 3}, kept, record=True)[0]
    assert kept["n"] == {"a": 2}
    assert check_docs.ratchet("n", {"a": 1}, kept, record=False)
    assert check_docs.ratchet("n", {"a": 1}, kept, record=True) == [] and kept["n"] == {"a": 1}
    assert check_docs.ratchet("m", {"a": 1}, kept, record=False)
