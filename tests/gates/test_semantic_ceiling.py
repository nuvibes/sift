# SPDX-License-Identifier: AGPL-3.0-or-later
"""The deepest a search by meaning may look is a depth the vector index will actually answer.

Two constants, in two slices, that have to agree. The search widens its ask when a page comes back
short, up to a ceiling of its own; the vector store multiplies whatever it is asked for by a margin,
because a lookup runs before any permission rule does and some of what it returns is filtered out
afterwards; and the index refuses any single lookup past a hard limit compiled into it. Set the
ceiling above what survives that multiplication and the refusal arrives as a database error in the
middle of an ordinary search: a page near the end of a result set, where the count can never grow
enough to satisfy the loop, widens until it crosses the limit.

Neither slice can hold this on its own. The search does not know the margin and must not: it talks
to a seam, and a second vector store would have a margin of its own. The store does not know the
ceiling and should not, because widening is the caller's policy. So the agreement is checked here,
where both are in view, and it is a gate rather than a comment because nothing about breaking it
shows up until somebody scrolls far enough to reach the deep case.
"""

from __future__ import annotations

import pytest

from sift.slices.search.filters import CANDIDATE_CEILING
from sift.slices.semantic.store import MAX_NEIGHBOURS

pytestmark = [pytest.mark.gate, pytest.mark.unit]


def test_the_search_never_widens_past_what_the_index_will_answer() -> None:
    assert CANDIDATE_CEILING <= MAX_NEIGHBOURS, (
        f"a search by meaning widens to {CANDIDATE_CEILING} files, and the vector index will only"
        f" answer {MAX_NEIGHBOURS} in one lookup. The ask past that point fails outright rather"
        " than coming back short, and it is reached by paging into a result set."
    )
