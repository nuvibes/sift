# SPDX-License-Identifier: AGPL-3.0-or-later
"""Details is readable on a record and is not findable by search, and that is a decision.

"Anybody signed in can read what an admin wrote about somebody" and "anybody signed in can FIND
somebody by what an admin wrote about them" are different amounts of exposure. The first is
intended; the second is not.

That is defended by a test rather than by memory because it is a decision to NOT do something, and
those leave no trace in the tree. The read on the notes route is open now, so the next person
adding a field to the index will see notes sitting there unindexed, assume it was an oversight, and
add it in good faith. Nothing anywhere would say otherwise.

It is checked against the SQL that gathers the indexed text, which is where a well-meaning addition
would actually land, rather than by grepping for the word everywhere: notes are read, written and
drawn all over the app and every one of those is fine.
"""

from __future__ import annotations

import pytest

from sift.kernel.access import search_index

pytestmark = [pytest.mark.gate, pytest.mark.unit]


#: The columns holding text somebody typed that is deliberately not searchable, and why.
#:
#: A person's Details is the whole of it today. A site's and a photo set's are the same column and
#: the same decision, so they are listed with it: nothing has ever indexed them, and opening the
#: read on one of the three is not a reason to index any of them.
_NOT_INDEXED = ("notes",)


def test_the_gathered_text_does_not_include_what_an_admin_wrote() -> None:
    """A person's Details stays out of the index it would otherwise be found through."""
    gathered = search_index._TEXT_OF_ASSETS.lower()

    for column in _NOT_INDEXED:
        assert f".{column}" not in gathered, (
            f"`{column}` has been added to the text the search index gathers. That makes what an "
            "admin wrote about somebody a way to FIND them, which is a bigger change than making "
            "it readable on their record and is not intended. If it is now wanted, delete this "
            "test in the same commit and say so."
        )


def test_the_gate_is_looking_at_the_right_statement() -> None:
    """A known positive: the statement really is the one that feeds the index.

    Without this the check above passes just as happily against a constant that was renamed, moved
    or emptied: the gate would go green for ever while indexing whatever it liked.
    """
    gathered = search_index._TEXT_OF_ASSETS.lower()

    assert "assets" in gathered
    # The things that ARE indexed, so an empty or unrelated statement cannot satisfy this file.
    for indexed in ("al.alias", "t.name", "u.name"):
        assert indexed in gathered
