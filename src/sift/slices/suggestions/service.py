# SPDX-License-Identifier: AGPL-3.0-or-later
"""The pass that reads the library, and the one action that answers a question it asked.

The pass runs for nobody: it reads the whole library unscoped and never puts anything on a screen.
The screen is served to somebody, and everything it says is resolved against that user, so a
suggestion made from a library holding concealed things never leaks a count of them.

The service is put together from one mixin per rule (`service_base` and its neighbours); this
module is its door, and every name a caller imports from it is re-exported here.
"""

from __future__ import annotations

from sift.kernel.wiring import Part
from sift.slices.suggestions.ladder import dominant
from sift.slices.suggestions.service_base import (
    FILED_QUEUE,
    FILENAMES_QUEUE,
    FROM_FILENAME,
    PAGE,
    QUEUE,
    SILENT,
    TAKEN_BACK,
    Applied,
    Arrivals,
    FolderTakenOff,
    MadeSet,
    NotFound,
    Page,
    PictureFields,
    PostSets,
    Proposal,
    SuggestionError,
    SwapFolders,
    Written,
)
from sift.slices.suggestions.service_confirm import ConfirmMixin
from sift.slices.suggestions.service_pass import PassMixin, _signature
from sift.slices.suggestions.service_screen import ScreenMixin, _a_face_to_show
from sift.slices.suggestions.service_undo import UndoMixin


class SuggestionService(PassMixin, ConfirmMixin, UndoMixin, ScreenMixin):
    """Reads folders for names, and turns one answer into every write it implies."""


__all__ = [
    "FILED_QUEUE",
    "FILENAMES_QUEUE",
    "FROM_FILENAME",
    "PAGE",
    "QUEUE",
    "SILENT",
    "TAKEN_BACK",
    "Applied",
    "Arrivals",
    "FolderTakenOff",
    "MadeSet",
    "NotFound",
    "Page",
    "PictureFields",
    "PostSets",
    "Proposal",
    "SuggestionError",
    "SuggestionService",
    "SwapFolders",
    "Written",
    "_a_face_to_show",
    "_signature",
    "dominant",
]


#: Reading the folder tree somebody already organised.
SERVICE: Part[SuggestionService] = Part("suggestions")
