# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where a deleted file went, as its line on History says it: the disk, Sift only, or an archive.

Beside `test_sentences.py`, whose table holds every line once; these read `deleted_where`, the
phrase the delete lines end with, word for word.
"""

from __future__ import annotations

from sift.kernel.access import sentences as say


def test_a_delete_says_where_the_file_went_only_where_it_was_recorded() -> None:
    assert say.deleted_where({"from": "disk"}) == " from the disk"
    assert say.deleted_where({"from": "sift"}) == " from Sift only"
    assert say.deleted_where({}) == ""
    assert say.deleted_where({"under_floor": 10}) == " because it had fewer than 10 photos"
    assert (
        say.deleted_where({"why": "taken_back"})
        == ", created by a stash-box answer that was later undone"
    )
    assert say.deleted_where({"why": "no such reason"}) == ""


def test_a_picture_removed_out_of_an_archive_says_the_archive_was_left_alone() -> None:
    """Saying only where the file went reads as a file still loose on the disk; this one is in a ZIP that
    Sift did not change, and the scan leaves it out from then on."""
    assert say.deleted_where({"from": "sift", "archive": True}) == (
        " from Sift only and kept it out of later scans; its ZIP file wasn't changed"
    )
    assert say.deleted_where({"from": "disk", "archive": True}) == " from the disk"
