# SPDX-License-Identifier: AGPL-3.0-or-later
"""A locked tile reaches the screen as one: `locked` on the wire, and nothing that names the row.

The rule is `_LOCKED_TILE` in `kernel/access/repository/entities.py` and its own tests are
`kernel/tests/test_locked_tile.py`. What this holds is the step after it: the route's builder copies
the flag onto the row a card is drawn from. A flag the statement computes and the wire drops is a
blank card, which is one of the two shortcuts the rule was written to refuse.

The builders directly rather than over HTTP, because a builder is a pure function of the scoped row
and the route adds nothing a card reads beyond it, and a Site's is the one that could still name
the row by another road: its logo is matched on the address and the name, both withheld.
"""

from __future__ import annotations

from sift.kernel.access import PersonSuggestion, SiteSuggestion
from sift.slices.people.router import _person_from_suggestion, _site_from_suggestion


def test_a_locked_person_is_a_locked_row_on_the_wire() -> None:
    row = _person_from_suggestion(
        PersonSuggestion(id="p", name="", vault=False, asset_count=1, locked=True)
    )
    assert (row.locked, row.name, row.asset_count) == (True, "", 1)


def test_a_locked_site_carries_no_logo_to_name_it_by() -> None:
    row = _site_from_suggestion(
        SiteSuggestion(id="s", name="", asset_count=1, locked=True), {"people": 2}, art="a"
    )
    assert (row.locked, row.name, row.icon, row.site_url) == (True, "", None, None)
    assert row.counts == {"people": 2}, "the card's counts are why the tile is on the wall"


def test_a_named_row_is_not_locked() -> None:
    row = _person_from_suggestion(
        PersonSuggestion(id="p", name="Wren Kastellan", vault=False, asset_count=1)
    )
    assert row.locked is False
