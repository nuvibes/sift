# SPDX-License-Identifier: AGPL-3.0-or-later
"""An extra copy removed, worded when shown: who removed it, and a copy of which file.

A stored title of "Removed an extra copy" names nobody and no file, though the payload records the
file; the line is worded from the payload when shown. The stored detail, which says the copy's own
path, stays under the line.
"""

from __future__ import annotations

import json

from sift.kernel.workbench import DOER, Named, Recorded
from sift.slices.dedup.queue import ReclaimQueue


def _worded(payload: object, detail: str = ""):  # type: ignore[no-untyped-def]
    recorded = Recorded(
        id="01R", queue="copies", payload=json.dumps(payload), title="", detail=detail, decided_at=0
    )
    return ReclaimQueue.worded(ReclaimQueue.__new__(ReclaimQueue), recorded)


def test_the_file_is_named_and_the_path_stays_under_the_line() -> None:
    said = _worded({"asset_id": "01A", "location_id": "01L"}, "clips/a.mp4 was deleted.")
    assert said is not None
    assert said.said == (DOER, " removed an extra copy of ", Named(kind="asset", id="01A"))
    assert said.more == ("clips/a.mp4 was deleted.",)
    assert _worded({"location_id": "01L"}) is None


def test_a_record_this_version_cannot_read_keeps_its_stored_words() -> None:
    """Unreadable or not an object: nothing to word it from, so the stored title stands."""
    unreadable = Recorded(
        id="01R", queue="copies", payload="{not json", title="", detail="", decided_at=0
    )
    assert ReclaimQueue.worded(ReclaimQueue.__new__(ReclaimQueue), unreadable) is None
    assert _worded(["01A"]) is None
