# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a face pass knows about a folder, in the kernel because two features that may not import
each other speak it."""

from __future__ import annotations

import pytest

from sift.kernel.attribution import FolderFaces, FolderStamp

pytestmark = pytest.mark.unit


def test_a_folder_nobody_has_looked_at_is_not_a_folder_with_nobody_in_it() -> None:
    """A folder no pass has finished differs from one looked at that holds nobody: collapsed, a new
    library would guess thousands of times."""
    untouched = FolderFaces()
    looked = FolderFaces(looked_at=12, with_faces=0)

    assert untouched.looked_at == 0
    assert untouched.with_faces == 0
    assert looked.looked_at == 12
    assert looked.with_faces == 0
    assert untouched != looked


def test_each_folder_gets_its_own_empty_collections() -> None:
    """Each folder gets its own dictionaries: the defaults are factories, not a shared literal."""
    one = FolderFaces()
    another = FolderFaces()

    one.piles["pile-1"] = 3
    one.named["person-1"] = 2
    one.portraits["pile-1"] = "face-1"

    assert another.piles == {}
    assert another.named == {}
    assert another.portraits == {}


def test_nothing_here_can_be_edited_after_it_is_made() -> None:
    """These cross a seam. A reader that could write one could change what the other side said."""
    found = FolderFaces(looked_at=1)
    with pytest.raises(AttributeError):
        found.looked_at = 2  # type: ignore[misc]


def test_the_stamp_is_a_short_string_that_moves_when_any_part_of_it_does() -> None:
    """It exists so a pass can tell which folders are worth reading properly, and it is compared as
    text, so two different sets of counts must never write the same one."""
    assert FolderStamp(tracks=3, looked_at=12, latest=1_700_000_000).as_text() == "3:12:1700000000"
    assert FolderStamp().as_text() == "0:0:0"

    written = {
        FolderStamp(tracks=1, looked_at=2, latest=3).as_text(),
        FolderStamp(tracks=1, looked_at=3, latest=2).as_text(),
        FolderStamp(tracks=2, looked_at=1, latest=3).as_text(),
        FolderStamp(tracks=3, looked_at=2, latest=1).as_text(),
    }
    assert len(written) == 4, "two different folders wrote the same stamp"
