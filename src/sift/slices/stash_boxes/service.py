# SPDX-License-Identifier: AGPL-3.0-or-later
"""The stash-boxes somebody configured, and the questions Sift is allowed to ask them.

This is the whole of what the rest of Sift may reach: the adapter is held here and handed out
nowhere, so pacing, caching and the hard stop on a refusal cannot be gone around.

A box that is switched off, unreachable or has no key is a feature that is ABSENT, never an error
on a screen. Sift works with no network at all and nothing here is on the path of anything else.

The service is built in layers, one module per act: `configured`, `asking`, `linking`,
`matches`, `scanning` and `ledger`. This module is the door the rest of Sift imports from.
"""

from __future__ import annotations

from sift.kernel.wiring import Part
from sift.slices.stash_boxes.adapter import EXACT as EXACT
from sift.slices.stash_boxes.asking import CACHE_DAYS as CACHE_DAYS
from sift.slices.stash_boxes.asking import KEPT_LOCAL as KEPT_LOCAL
from sift.slices.stash_boxes.asking import KEPT_LOCAL_INHERITED_WHY as KEPT_LOCAL_INHERITED_WHY
from sift.slices.stash_boxes.asking import KEPT_LOCAL_WHY as KEPT_LOCAL_WHY
from sift.slices.stash_boxes.asking import KeptLocal as KeptLocal
from sift.slices.stash_boxes.configured import Answer as Answer
from sift.slices.stash_boxes.configured import BoxView as BoxView
from sift.slices.stash_boxes.configured import Linked as Linked
from sift.slices.stash_boxes.configured import entry_page as entry_page
from sift.slices.stash_boxes.grades import SHORT_MS as SHORT_MS
from sift.slices.stash_boxes.grades import TIGHT_MS as TIGHT_MS
from sift.slices.stash_boxes.grades import Grade as Grade
from sift.slices.stash_boxes.grades import Match as Match
from sift.slices.stash_boxes.grades import grade_of as grade_of
from sift.slices.stash_boxes.grades import grade_unproven as grade_unproven
from sift.slices.stash_boxes.ledger import BoxLedger
from sift.slices.stash_boxes.ledger import LedgerKey as LedgerKey
from sift.slices.stash_boxes.ledger import LinkedSubject as LinkedSubject
from sift.slices.stash_boxes.ledger import Undecided as Undecided


class StashBoxService(BoxLedger):
    """Configure the boxes, and ask them things. One per application."""


#: The stash-boxes.
SERVICE: Part[StashBoxService] = Part("stash_boxes")
