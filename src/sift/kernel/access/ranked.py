# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which files a ranking made before the visibility read may hold, for one viewer."""

from __future__ import annotations

from collections.abc import Mapping

from sift.kernel.access.viewer import Viewer
from sift.kernel.db import Database

#: The viewer's own files, their vault's only while it is open. Binds `:viewer` and `:open`.
VIEWER_FILES = (
    "SELECT va.asset_id FROM viewer_assets va"
    " WHERE va.user_id = :viewer AND (:open = 1 OR va.concealed = 0)"
)

_HIDES_ANY = "SELECT 1 FROM viewer_assets WHERE user_id = ? AND concealed = 1 LIMIT 1"


async def ranked_among(database: Database, viewer: Viewer) -> Mapping[str, object] | None:
    """What `VIEWER_FILES` binds for this viewer, or None where every file may be ranked."""
    if viewer.is_admin and (
        viewer.show_hidden or await database.fetch_one(_HIDES_ANY, (viewer.id,)) is None
    ):
        return None
    return {"viewer": viewer.id, "open": 1 if viewer.show_hidden else 0}
