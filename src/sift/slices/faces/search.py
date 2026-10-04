# SPDX-License-Identifier: AGPL-3.0-or-later
"""A tab's search box on the Faces lists: only the rows naming a person the search found.

`who` is the People a search found (`FaceService.people_called`), or None when nothing is
searched. A list narrows BEFORE its page is taken, so the count and the pages follow the words
rather than being a filter over the page in hand.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence


def kept(who: frozenset[str] | None, person_id: str) -> bool:
    """Whether a row naming this person stays on a list under this search."""
    return who is None or person_id in who


def searched_page[T](
    rows: Sequence[T],
    who: frozenset[str] | None,
    person_of: Callable[[T], str],
    *,
    offset: int,
    limit: int,
) -> tuple[list[T], int]:
    """One page of the rows this search keeps, and how many it keeps."""
    found = [row for row in rows if kept(who, person_of(row))]
    begin = max(0, offset)
    return found[begin : begin + limit], len(found)
