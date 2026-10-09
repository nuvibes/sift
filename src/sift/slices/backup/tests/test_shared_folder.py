# SPDX-License-Identifier: AGPL-3.0-or-later
"""Two libraries pointed at one backup folder each keep their own last few.

A duplicate and an imported library start out pointed wherever their original was, and somebody
may point two libraries at one folder on purpose. Rotation deletes only the backups THIS library
made, recognised by the mark in their names, so neither library can take the other's.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

import pytest

from sift.slices.backup.naming import LIBRARY_MARK_FILENAME, filename_for, mark_of_library
from sift.slices.backup.service import FOLDER_KEY, KEEP_KEY, BackupService
from sift.slices.settings_hub import SettingsService
from sift.testing.fixtures import Actors, FakeClock

pytestmark = pytest.mark.anyio


async def test_rotation_never_deletes_another_librarys_backups_in_a_shared_folder(
    backup: BackupService,
    preferences: SettingsService,
    fake_clock: FakeClock,
    elsewhere: Path,
    actors: Actors,
) -> None:
    """The other library's backups are OLDER than every one this library writes, which is exactly
    what a rotation counting the whole folder would delete first."""
    theirs = "ba9876543210"
    assert await backup.library_mark() != theirs
    other = [
        elsewhere / filename_for(fake_clock.now() - days * 86400, library=theirs)
        for days in (3, 2, 1)
    ]
    for one in other:
        one.write_bytes(b"the other library's backup")

    await preferences.apply(actors.admin, {FOLDER_KEY: str(elsewhere), KEEP_KEY: 1})
    written = []
    for _ in range(3):
        written.append(await backup.run_scheduled())
        fake_clock.advance(24 * 3600)

    assert all(one.is_file() for one in other)
    assert sorted(entry.name for entry in elsewhere.iterdir()) == sorted(
        [one.name for one in other] + [written[-1].name]
    )


def test_a_library_keeps_its_mark_and_a_second_library_gets_its_own(tmp_path: Path) -> None:
    first = tmp_path / "first" / "data"
    second = tmp_path / "second" / "data"
    mark = mark_of_library(first)
    assert mark == mark_of_library(first)
    assert mark_of_library(second) != mark


def test_a_library_copied_by_hand_stops_sharing_its_originals_mark(tmp_path: Path) -> None:
    """The mark file travels with a folder somebody copies; the copy must not rotate the
    original's backups, so a mark written for another folder is made again."""
    original = tmp_path / "original" / "data"
    mark = mark_of_library(original)
    copied = tmp_path / "copied" / "data"
    shutil.copytree(original, copied)
    assert json.loads((copied / LIBRARY_MARK_FILENAME).read_text(encoding="utf-8"))["mark"] == mark
    assert mark_of_library(copied) != mark
    assert mark_of_library(original) == mark


def test_a_mark_that_is_not_one_is_made_again(tmp_path: Path) -> None:
    """The mark goes into backup names that rotation matches; a damaged one would put a name on
    a backup that rotation can never recognise again, so it is replaced, never carried."""
    data_dir = tmp_path / "library" / "data"
    data_dir.mkdir(parents=True)
    here = os.path.normcase(os.path.abspath(data_dir))
    record = data_dir / LIBRARY_MARK_FILENAME
    record.write_text(json.dumps({"mark": "../not-a-mark", "for": here}), encoding="utf-8")

    mark = mark_of_library(data_dir)

    assert mark != "../not-a-mark"
    assert re.fullmatch(r"[0-9a-f]+", mark)
    assert json.loads(record.read_text(encoding="utf-8")) == {"mark": mark, "for": here}
