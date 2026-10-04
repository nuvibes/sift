# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every read and write the face feature makes, and the only place that knows any SQL.

**A face's numbers are stored**, which is why adding a person is arithmetic over data already
held rather than a second read of every file. **A pass over one file replaces that file's previous
results outright**, but what a person decided about a face (named, refused, set aside, grouped,
removed) is remembered against its description and put back when a fresh face in the same file
agrees closely (`ALREADY_DECIDED`). Every value is bound as a parameter: there is no ORM, and
nothing here builds SQL by formatting a string.
"""

from __future__ import annotations

from sift.slices.faces.store_found import FoundStore
from sift.slices.faces.store_grouping import ByHandStore
from sift.slices.faces.store_models import ModelsStore
from sift.slices.faces.store_people import PeopleStore
from sift.slices.faces.store_piles import PilesStore
from sift.slices.faces.store_records import (
    NAMES_THE_FILE,
    PRODUCT,
    FiledFace,
    FiledOff,
    PassRecord,
    PileProposal,
    Remeasured,
    Ruling,
    Standing,
    StoredTrack,
    clearest,
    now_ms,
)
from sift.slices.faces.store_references import ReferencesStore
from sift.slices.faces.store_removals import RemovalsStore

#: The names callers import from here, wherever in the store they are defined.
__all__ = [
    "NAMES_THE_FILE",
    "PRODUCT",
    "FiledFace",
    "FiledOff",
    "PassRecord",
    "PileProposal",
    "Remeasured",
    "Ruling",
    "Standing",
    "Store",
    "StoredTrack",
    "clearest",
    "now_ms",
]


class Store(
    FoundStore,
    RemovalsStore,
    PilesStore,
    ByHandStore,
    ReferencesStore,
    PeopleStore,
    ModelsStore,
):
    """The face tables, one part per subject, each standing on `PicturesStore`."""
