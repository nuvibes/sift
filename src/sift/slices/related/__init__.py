# SPDX-License-Identifier: AGPL-3.0-or-later
"""Cross-association: what one thing in the catalog reaches, counted in one request.

Sift's four catalog entities plus photo sets and loops are a graph, and an entity page is a view of
one node in it: this person's files, this person's tags, the sites this person turns up on. The
narrowing that makes any of those questions askable lives in the kernel (`access/related.py`) and is
spliced into the listings every wall already uses: there is no query per pair and no table of
pairs, which is why a seventh kind of thing would inherit the whole matrix rather than costing
thirty-six new statements.

**What this slice owns is one route and nothing else.** The walls belong to the slices that own
them; what was missing was the numbers on the tab strip above them, which no single slice could
answer because the question spans all of them. It holds no table, no service and no write path.

**It reaches other slices' data and imports none of them.** Every listing it calls is on the
kernel's repository, which is where those statements live because the permission resolver is joined
into each one. So this is not a slice reaching sideways: it is a reader of the kernel, the same as
every other slice is.
"""

from __future__ import annotations

from sift.slices.related.models import RelatedCounts
from sift.slices.related.router import TABS_FOR, router

__all__ = ["TABS_FOR", "RelatedCounts", "router"]
