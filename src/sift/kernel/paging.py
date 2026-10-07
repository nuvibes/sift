# SPDX-License-Identifier: AGPL-3.0-or-later
"""How big a page is. The one number every listing in Sift shares.

Written out in several places, it would go wrong the way a copied number does: put beside the
thing it describes, and then only one of the copies edited.
"""

from __future__ import annotations

MAX_PAGE_SIZE = 200
"""The most rows any listing will build or accept, whatever a caller asks for.

Two things together, and they have to be one number. It is the CLAMP on the read: a route forwards
a page size from an untrusted request, and an unbounded one is a way to make the server materialise
millions of rows per request. It is also the CEILING every paged route declares, because the client
sizes a page by measuring the screen and stops here.

**One number, for the clamp and for every route's ceiling.** A route BELOW this refuses every
request from a monitor big enough to want more (a screen of groups would tell people their
groups were gone). A route ABOVE it promises a page that then gets silently cut down.
`tests/gates/test_one_page_ceiling.py` holds every route to this.
"""


def resume_at(found: int | None, near: int | None) -> int:
    """Where a page asked for by a row begins: that row, else where the page was, else the top.

    Every wall that pages by whole rows names the row a page began at in its address (`from`), so
    the way back opens it on the same cards. It names the OFFSET that row was at as well (`near`),
    and this is what the second one is for: the row can be gone by the time anybody comes back.
    Discarding a whole group of faces takes it off the list, and if it was the first card of the
    page it is exactly the row the address names. Answering that with the top of the list would
    put somebody who decided about the first group on page three back on page one.

    So, in order:

    1. **The row's own place** (`found`), when the list still holds it and this viewer may see it.
    2. **Where the page was** (`near`), when it does not. The cards after a removed one close up,
       so the same offset is the same page with the gap filled, which is what "where I was" means.
    3. **The top**, when the address named neither.

    A `near` past the end of a list that has since shrunk is served as asked and answers no rows.
    That is deliberate: stepping a page that begins past the end back to the list's last page is
    one rule the wall already applies to every page (`CardPaging` and the media grid), and the list
    length is not known here without a second count on every read.

    Nothing is learned by asking: a row being kept back from this viewer and a row that never
    existed both give the same answer: the `near` page, or the top.

    One function for every route that takes `from`, because a rule written out per route (`offset
    = at if at is not None else 0`) is changed in all but one of them. `tests/test_paging.py`
    holds every such route to it.
    """
    if found is not None:
        return found
    if near is not None:
        return max(0, near)
    return 0
