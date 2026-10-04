# SPDX-License-Identifier: AGPL-3.0-or-later
"""Save a backup on the Backup pane: one backup into the backup folder, saved by hand.

The press writes where the automatic backups go, under the name no rule deletes, and the pane lists
it with the other backups no rule takes. History says a backup was saved and the folder it went to,
never what it holds. A browser on another device asks for a copy of it by its name, and only a
name the list shows is ever handed out.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sift.kernel.access import sentences as say
from sift.kernel.config import Settings
from sift.kernel.db import Database
from sift.slices.backup.service import (
    FOLDER_KEY,
    SAVED_MARK,
    BackupService,
    NotThere,
    filename_for,
)
from sift.slices.settings_hub import SettingsService
from sift.testing.fixtures import Actors, FakeClock

pytestmark = pytest.mark.anyio


async def test_a_press_saves_into_the_backup_folder_as_one_saved_by_hand(
    backup: BackupService,
    preferences: SettingsService,
    fake_clock: FakeClock,
    elsewhere: Path,
    actors: Actors,
) -> None:
    await preferences.apply(actors.admin, {FOLDER_KEY: str(elsewhere)})

    saved = await backup.save_now(actors.admin)

    assert saved.parent == elsewhere.resolve()
    assert saved.is_file()
    assert saved.name.endswith(SAVED_MARK + ".zip")
    # Nothing staged for a request is left behind, and nothing else is written beside it.
    assert [entry.name for entry in elsewhere.iterdir()] == [saved.name]


async def test_with_no_folder_chosen_it_goes_where_the_automatic_ones_go(
    backup: BackupService, settings: Settings, actors: Actors
) -> None:
    saved = await backup.save_now(actors.admin)

    assert saved.parent == settings.data_dir / "backups"


async def test_the_pane_lists_it_with_the_others_no_rule_takes(
    backup: BackupService,
    preferences: SettingsService,
    fake_clock: FakeClock,
    elsewhere: Path,
    actors: Actors,
) -> None:
    await preferences.apply(actors.admin, {FOLDER_KEY: str(elsewhere)})
    unmarked = elsewhere / "sift-backup-20230101-030000-0.1.150.zip"
    unmarked.write_bytes(b"old")
    automatic = elsewhere / filename_for(fake_clock.now() - 60, library=await backup.library_mark())
    automatic.write_bytes(b"rotated by its rules")
    theirs = elsewhere / filename_for(fake_clock.now(), library="ba9876543210", saved=True)
    theirs.write_bytes(b"another library's")

    saved = await backup.save_now(actors.admin)

    listed = await backup.unmarked()
    assert [(one.name, one.saved) for one in listed] == [
        (saved.name, True),
        (unmarked.name, False),
    ]


async def test_history_says_it_was_saved_and_where_and_nothing_of_what_it_holds(
    backup: BackupService,
    prepared_db: Database,
    preferences: SettingsService,
    fake_clock: FakeClock,
    elsewhere: Path,
    actors: Actors,
) -> None:
    await preferences.apply(actors.admin, {FOLDER_KEY: str(elsewhere)})

    saved = await backup.save_now(actors.admin)

    rows = await prepared_db.fetch_all(
        "SELECT d.actor_kind AS actor_kind, d.actor_id AS actor_id, d.payload AS payload,"
        " s.kind AS kind, s.subject_id AS id, s.name AS name FROM workbench_decisions d"
        " JOIN workbench_decision_subjects s ON s.decision_id = d.id WHERE d.verb = 'saved'"
    )
    assert [tuple(row) for row in rows] == [
        (
            "user",
            actors.admin.id,
            json.dumps({"folder": str(saved.parent)}),
            "backup",
            saved.name,
            "the backup from 14 November 2023",
        )
    ]
    line = say.feed_line(
        "saved",
        by=say.YOU,
        subjects=[("backup", say.thing("backup", saved.name, "the backup from 14 November 2023"))],
        payload={"folder": str(saved.parent)},
    )
    assert say.text_of(line.pieces) == (
        f"You saved the backup from 14 November 2023 in {saved.parent}"
    )


def test_a_folder_that_could_read_as_a_slot_is_said_as_the_backup_folder() -> None:
    line = say.feed_line(
        "saved",
        by=say.YOU,
        subjects=[("backup", say.thing("backup", "x.zip", "the backup from 14 November 2023"))],
        payload={"folder": "D:\\{by}"},
    )
    assert say.text_of(line.pieces) == (
        "You saved the backup from 14 November 2023 in the backup folder"
    )


async def test_a_copy_is_handed_out_only_for_a_name_the_list_shows(
    backup: BackupService,
    preferences: SettingsService,
    elsewhere: Path,
    actors: Actors,
) -> None:
    await preferences.apply(actors.admin, {FOLDER_KEY: str(elsewhere)})
    saved = await backup.save_now(actors.admin)
    stranger = elsewhere / "holiday.zip"
    stranger.write_bytes(b"not a backup")

    assert await backup.left_alone_file(saved.name) == saved

    for name in (stranger.name, "..", "../" + saved.name, "nothing.zip"):
        with pytest.raises(NotThere):
            await backup.left_alone_file(name)
