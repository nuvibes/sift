# SPDX-License-Identifier: AGPL-3.0-or-later
"""A skipped file let through, worded when shown: who asked, and which file.

A stored title of "Asked Sift to look at a skipped file again" names nobody doing it and no file,
though the payload records its path; the line is worded from the payload when shown.
"""

from __future__ import annotations

import json

from sift.kernel.workbench import DOER, Recorded
from sift.slices.library_roots.queue import SkippedQueue


def _worded(payload: object):  # type: ignore[no-untyped-def]
    recorded = Recorded(
        id="01R", queue="skipped", payload=json.dumps(payload), title="", detail="", decided_at=0
    )
    return SkippedQueue.worded(SkippedQueue.__new__(SkippedQueue), recorded)


def test_the_file_is_named_by_its_own_name() -> None:
    said = _worded({"root_id": "01R", "rel_path": "Images/beach-walk.jpg"})
    assert said is not None
    assert said.said == (DOER, " asked Sift to include beach-walk.jpg in the next scan")


def test_a_record_with_no_path_keeps_its_stored_title() -> None:
    assert _worded({"root_id": "01R"}) is None


def test_a_record_that_cannot_be_read_keeps_its_stored_title() -> None:
    """A payload that is not JSON, or JSON that is not a record, names no file."""
    for payload in ("{not json", json.dumps(["Images/beach-walk.jpg"])):
        recorded = Recorded(
            id="01R", queue="skipped", payload=payload, title="", detail="", decided_at=0
        )
        assert SkippedQueue.worded(SkippedQueue.__new__(SkippedQueue), recorded) is None
