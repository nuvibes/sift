# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the Enriched from filenames pile says beside its count: how many IDs are waiting, worded the
way every screen writes a number, as a line of its own with the one place it is done.
"""

from __future__ import annotations

from sift.slices.suggestions.queue import FILED_ADVICE, _filed_aside


def test_a_count_the_card_does_say_has_its_thousands_separated() -> None:
    aside = _filed_aside(1204)
    assert aside is not None
    assert aside.said.startswith("1,204 IDs are waiting for a username.")


def test_the_ids_waiting_are_a_line_of_their_own_with_a_link() -> None:
    """Not the tail of the advice: a thing to do on another screen, drawn as its own line with the
    one place it is done."""
    assert "waiting" not in FILED_ADVICE
    one = _filed_aside(1)
    assert one is not None
    assert one.said.startswith("1 ID is waiting for a username.")
    assert (one.link, one.href) == ("Open Sites", "/sites")
    assert _filed_aside(0) is None
