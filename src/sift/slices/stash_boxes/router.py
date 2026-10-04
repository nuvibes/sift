# SPDX-License-Identifier: AGPL-3.0-or-later
"""The stash-box endpoints. Every one that touches the network is admin-only.

Anything that makes Sift send a request to somebody else's service with a stored key is for an
admin alone, and so is configuring a box.

Reading the links already agreed for a subject is not: it reaches no network and spends no key.
It is Sift's own table, part of a record, and shown to everybody who may see the page it is on.

Nothing here writes to a stash-box. There is no route that edits, votes or submits a fingerprint,
and there is nothing behind these that does either.

One module per act keeps its own router, and this one takes their routes in a fixed order, the
order they are matched in.
"""

from __future__ import annotations

from fastapi import APIRouter

from sift.slices.stash_boxes import (
    router_boxes,
    router_ledger,
    router_links,
    router_matches,
    router_reconcile,
    router_scan,
)
from sift.slices.stash_boxes.match_words import _settled_words as _settled_words
from sift.slices.stash_boxes.router_base import _pack_slug as _pack_slug
from sift.slices.stash_boxes.router_links import _still_given as _still_given
from sift.slices.stash_boxes.router_matches import TAGGER as TAGGER
from sift.slices.stash_boxes.router_reconcile import _disagreement_view as _disagreement_view
from sift.slices.stash_boxes.router_scan import MOST_NAMED_FILES as MOST_NAMED_FILES

router = APIRouter(tags=["stash-boxes"])

for _act in (
    router_boxes,
    router_ledger,
    router_links,
    router_scan,
    router_matches,
    router_reconcile,
):
    router.routes.extend(_act.router.routes)
