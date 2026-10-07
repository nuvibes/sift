# SPDX-License-Identifier: AGPL-3.0-or-later
"""Taking one watermark filing back.

**A reverser and not a queue, which is to say: no card anywhere.** A hint somebody has to review is
more work than it saves, so nothing this pass does is ever waiting on a person. An exact address
files the file; a doubtful reading is written on the file and decides nothing. There is no pile, so
there is nothing to survey and nothing to draw.

What there IS is a receipt per file, and an Undo on the file's own History that reads it back. That
is what this exists for: the ability to take a decision back outlives any screen that might have
offered it, and the kernel splits the two precisely so a pass can have the second without the first.
"""

from __future__ import annotations

import json

from sift.kernel.access import Viewer
from sift.kernel.workbench import ASSET, Preview
from sift.slices.watermarks.service import QUEUE, WatermarkService

#: How many stills one record shows.
PREVIEW = 4


class WatermarkFilings:
    """Reads one filing's receipt back, and puts the filing back where it was."""

    name = QUEUE
    #: Every filing wrote a receipt naming the one file it filed, and `reverse` puts it back.
    reversible = True

    def __init__(self, service: WatermarkService) -> None:
        self._service = service

    async def pictures_of(self, viewer: Viewer, payload: str) -> tuple[Preview, ...]:
        """A still of the file one filing was about, scoped to whoever is reading the record.

        Scoped rather than shown: a file restricted since the decision is one this user may no
        longer be shown, and a record of having filed it is not a licence to draw it.
        """
        wanted = _record(payload)[2][:PREVIEW]
        if not wanted:
            return ()
        allowed = await self._service.visible_of(viewer, wanted)
        return tuple(
            Preview(kind=ASSET, id=one, href=f"/asset/{one}") for one in wanted if one in allowed
        )

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        """Take one filing off the file it was put on, and nothing else.

        Every field is reached for rather than assumed: a record can outlive the version that wrote
        it (a restored backup, an older release, a row somebody edited) and a payload missing a
        key it once had is a record this cannot reverse. Answering "nothing was put back" is the
        honest reading of that, and it is why this returns False rather than failing the request,
        which would read as Undo being broken rather than as the record being unreadable.
        """
        username_id, site, assets = _record(payload)
        if not assets or not (username_id or site):
            return False
        return await self._service.take_back(
            username_id=username_id or None, site=site or None, asset_ids=assets
        )


def _record(payload: str) -> tuple[str, str, list[str]]:
    """One filing read back out of its own record: the username, the site, and the files.

    ONE parse for both callers rather than one each, and one place that knows what an unreadable
    record answers. A filing made under a site with nobody named carries no username, so BOTH are
    read and either may be empty: what may not be empty is both together.
    """
    try:
        found = json.loads(payload)
    except (TypeError, ValueError):
        return "", "", []
    if not isinstance(found, dict) or found.get("kind") != "filed":
        return "", "", []
    assets = found.get("assets")
    return (
        str(found.get("username_id") or ""),
        # `site`, and the former word as well. A receipt is a STORED payload: an older filing
        # carries the old key, and a reader that knew only the new one would answer an empty site
        # for all of them, which reads as a filing under nobody rather than as a key that moved.
        str(found.get("site") or found.get("platform") or ""),
        [str(one) for one in assets] if isinstance(assets, list) else [],
    )
