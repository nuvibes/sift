# SPDX-License-Identifier: AGPL-3.0-or-later
"""A page of the grid is fifty rows, and it must not be paid for with the whole library.

Who may see what is stored, so a page is a walk of a sort index with a probe per row. Two things
would put the whole library back in front of a page: `COUNT(*) OVER ()`, which computes every column
for every permitted asset before fifty are kept (the total comes from the stored counts or the count
statement instead), and the `CASE` sort arms for a pin and an arranged position, which force a full
sort. Which side the planner walks first may change the cost, never the rows.
"""

from __future__ import annotations

import re

import pytest

from sift.kernel.access.repository.assets import (
    _CONDITIONS,
    _ORDER_TAILS,
    _ORDERING,
    _PAGE_TAIL,
    _TOTAL_COLUMN,
    _VISIBLE_ASSETS_HEAD,
    DRIVE_LIBRARY,
    DRIVE_VERDICT,
    assets_count_query,
    assets_query,
    drive_for,
)

pytestmark = pytest.mark.unit

#: The filter seam's quietest occupant: what a page that narrowed nothing binds.
_NOTHING_FILTERED = "\n1"


def test_the_page_carries_neither_thing_that_makes_it_walk_the_library() -> None:
    """Neither thing that makes a page walk the library is in it."""
    for sort in _ORDER_TAILS:
        page = assets_query(sort, _NOTHING_FILTERED, arranged=False, counted=False)
        assert "COUNT(*) OVER ()" not in page, f"the {sort} page still computes a running total"
        order_by = page[page.rindex("ORDER BY") :]
        assert "CASE" not in order_by, (
            f"a CASE in the {sort} sort keys keeps the page off its index"
        )
    newest = assets_query("newest", _NOTHING_FILTERED, arranged=False, counted=False)
    assert re.search(r"a\.added_at DESC,\s*a\.id DESC", newest[newest.rindex("ORDER BY") :])


def _without_comments(statement: str) -> str:
    return "\n".join(line for line in statement.splitlines() if not line.strip().startswith("--"))


def test_the_two_walks_are_one_statement() -> None:
    """The library-first and verdict-first walks are one statement with the join spelling and its
    comments taken out: a cost decision must never become a difference in rows."""
    spellings = {
        DRIVE_LIBRARY: (
            "FROM assets a\n  CROSS JOIN viewer_assets v ON v.asset_id = a.id AND v.user_id = :viewer"
        ),
        DRIVE_VERDICT: (
            "FROM viewer_assets v\n  CROSS JOIN assets a ON a.id = v.asset_id AND v.user_id = :viewer"
        ),
    }
    for sort in _ORDER_TAILS:
        forms = {}
        for drive, spelling in spellings.items():
            text = _without_comments(
                assets_query(sort, _NOTHING_FILTERED, arranged=False, counted=False, drive=drive)
            )
            assert text.count(spelling) == 1, f"the {drive} form of {sort} lost its join"
            forms[drive] = text.replace(spelling, "<join>")
        assert forms[DRIVE_LIBRARY] == forms[DRIVE_VERDICT], f"the two walks differ for {sort}"


def test_the_walk_is_chosen_from_how_much_the_user_may_see() -> None:
    """An admin walks the index; a user that may see a sliver walks its own rows. Pinned at both
    ends
    and at the crossover."""
    assert drive_for(65_000, 65_000) == DRIVE_LIBRARY
    assert drive_for(2_000_000, 2_000_000) == DRIVE_LIBRARY
    assert drive_for(165, 65_000) == DRIVE_VERDICT
    assert drive_for(3, 2_000_000) == DRIVE_VERDICT
    # 25 * library is the crossover.
    assert drive_for(1_274, 65_000) == DRIVE_VERDICT
    assert drive_for(1_275, 65_000) == DRIVE_LIBRARY
    # Nothing to see walks its own empty rows.
    assert drive_for(0, 0) == DRIVE_VERDICT


def test_the_count_asks_the_same_question_as_the_page() -> None:
    """The count is built from the page's seams and drops only what a count cannot need, never a
    condition: a count over a different set would disclose a file."""
    count = assets_count_query(_NOTHING_FILTERED)

    assert count.count(_CONDITIONS) == 1, "the count no longer carries the concealment rules"
    assert _NOTHING_FILTERED in count, "the count no longer carries the filter the page was given"
    # By the seams: what must not be here is the page's own ordering and window.
    assert _ORDERING not in count, "the count is sorting a set it only has to size"
    assert _PAGE_TAIL not in count, "the count is being asked for one page of itself"
    assert "COUNT(*) AS total_count" in count, "the count stopped being a count"
    assert "JOIN viewer_assets v" in count, "the count stopped reading the stored verdict"


def test_the_window_count_is_still_where_the_page_form_cuts_it_out() -> None:
    """The window count is still the text `assets_query` cuts out, or the replace does nothing."""
    assert _VISIBLE_ASSETS_HEAD.count(_TOTAL_COLUMN) == 1
    assert _TOTAL_COLUMN not in assets_query(
        "newest", _NOTHING_FILTERED, arranged=False, counted=False
    )
    assert _TOTAL_COLUMN in assets_query("newest", _NOTHING_FILTERED, arranged=False, counted=True)
