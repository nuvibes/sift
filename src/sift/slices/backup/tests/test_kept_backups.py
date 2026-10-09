# SPDX-License-Identifier: AGPL-3.0-or-later
"""What is kept: the count, the age rule beside it, and the backups no rule ever takes.

Two rules delete this library's automatic backups after each automatic backup: all but the newest
`backup.keep`, and any older than `backup.keep_days` (zero is never). Neither ever takes a backup
saved by hand, another library's, or one whose name carries no library's mark. Those last are
listed for the Backup pane and deleted only by a person's own press, by the name the list showed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sift.kernel.db import Database
from sift.slices.backup import recycle
from sift.slices.backup.naming import filename_for
from sift.slices.backup.service import (
    FOLDER_KEY,
    KEEP_DAYS_KEY,
    KEEP_KEY,
    BackupService,
    NotThere,
    keep_days_from,
)
from sift.slices.settings_hub import SettingsService
from sift.testing.fixtures import Actors, FakeClock

pytestmark = pytest.mark.anyio

DAY = 24 * 3600
_THEIRS = "ba9876543210"


@pytest.fixture(autouse=True)
def no_real_bin(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Never this machine's own Recycle Bin: a drive without one, and a move that records."""
    moved: list[Path] = []
    monkeypatch.setattr(recycle, "has_recycle_bin", lambda _place: False)
    monkeypatch.setattr(recycle, "to_recycle_bin", moved.append)
    return moved


async def _daily(backup: BackupService, fake_clock: FakeClock, days: int) -> list[Path]:
    written = []
    for _ in range(days):
        written.append(await backup.run_scheduled())
        fake_clock.advance(DAY)
    return written


async def test_a_new_library_deletes_automatic_backups_older_than_seven_days(
    backup: BackupService,
    preferences: SettingsService,
    fake_clock: FakeClock,
    elsewhere: Path,
    actors: Actors,
) -> None:
    """Ten daily backups with room to keep sixty: the age rule alone decides, and the eight from
    the last seven days stay (the one exactly seven days old is not older than seven days)."""
    await preferences.apply(actors.admin, {FOLDER_KEY: str(elsewhere), KEEP_KEY: 60})
    assert keep_days_from(await preferences.get_app(KEEP_DAYS_KEY)) == 7

    written = await _daily(backup, fake_clock, 10)

    assert sorted(entry.name for entry in elsewhere.iterdir()) == sorted(
        path.name for path in written[2:]
    )


async def test_never_keeps_them_however_old_and_the_count_still_applies(
    backup: BackupService,
    preferences: SettingsService,
    fake_clock: FakeClock,
    elsewhere: Path,
    actors: Actors,
) -> None:
    await preferences.apply(
        actors.admin, {FOLDER_KEY: str(elsewhere), KEEP_KEY: 60, KEEP_DAYS_KEY: 0}
    )
    assert len(await _daily(backup, fake_clock, 10)) == len(list(elsewhere.iterdir()))

    await preferences.apply(actors.admin, {KEEP_KEY: 3})
    written = await _daily(backup, fake_clock, 1)
    assert len(list(elsewhere.iterdir())) == 3
    assert written[0].is_file()


async def test_the_count_and_the_age_together_keep_whichever_is_fewer(
    backup: BackupService,
    preferences: SettingsService,
    fake_clock: FakeClock,
    elsewhere: Path,
    actors: Actors,
) -> None:
    """ "The newest 7, and none older than 2 days": two days of daily backups is three files."""
    await preferences.apply(
        actors.admin, {FOLDER_KEY: str(elsewhere), KEEP_KEY: 7, KEEP_DAYS_KEY: 2}
    )
    written = await _daily(backup, fake_clock, 6)
    assert sorted(entry.name for entry in elsewhere.iterdir()) == sorted(
        path.name for path in written[3:]
    )


async def test_no_rule_takes_a_saved_one_another_librarys_or_an_unmarked_one(
    backup: BackupService,
    preferences: SettingsService,
    fake_clock: FakeClock,
    elsewhere: Path,
    actors: Actors,
) -> None:
    """A year old, every one of them, and both rules at their tightest: only this library's own
    automatic backups are ever deleted."""
    await preferences.apply(
        actors.admin, {FOLDER_KEY: str(elsewhere), KEEP_KEY: 1, KEEP_DAYS_KEY: 1}
    )
    long_ago = fake_clock.now() - 365 * DAY
    mine = await backup.library_mark()
    left_alone = [
        elsewhere / filename_for(long_ago, library=mine, saved=True),
        elsewhere / filename_for(long_ago, library=_THEIRS),
        elsewhere / "sift-backup-20230101-030000-0.1.150.zip",
        elsewhere / "sift-backup-20230101-030000-0.1.150-saved.zip",
        elsewhere / "sift-backup-20230101-030000-0.1.90.sqlite3",
    ]
    for one in left_alone:
        one.write_bytes(b"a backup")

    await _daily(backup, fake_clock, 3)

    assert all(one.is_file() for one in left_alone)
    assert len(list(elsewhere.iterdir())) == len(left_alone) + 1


async def test_the_unmarked_ones_are_listed_newest_first_and_nothing_else_is(
    backup: BackupService,
    preferences: SettingsService,
    fake_clock: FakeClock,
    elsewhere: Path,
    actors: Actors,
) -> None:
    await preferences.apply(actors.admin, {FOLDER_KEY: str(elsewhere)})
    mine = await backup.library_mark()
    older = elsewhere / "sift-backup-20230101-030000-0.1.150.zip"
    newer = elsewhere / "sift-backup-20230102-030000-0500-0.1.151-saved.zip"
    bare = elsewhere / "sift-backup-20221231-030000-0.1.90.sqlite3"
    for size, one in enumerate((older, newer, bare), start=1):
        one.write_bytes(b"x" * size * 100)
    (elsewhere / filename_for(fake_clock.now(), library=mine)).write_bytes(b"mine")
    (elsewhere / filename_for(fake_clock.now(), library=_THEIRS)).write_bytes(b"theirs")
    (elsewhere / "holiday.zip").write_bytes(b"not a backup")
    (elsewhere / "sift-backup-20230103-030000-0.1.152.zip").mkdir()

    listed = await backup.unmarked()

    assert [(one.name, one.size_bytes) for one in listed] == [
        (newer.name, 200),
        (older.name, 100),
        (bare.name, 300),
    ]
    assert listed[0].taken_at == 1672646400


async def test_a_folder_that_cannot_be_used_lists_nothing(
    backup: BackupService, preferences: SettingsService, elsewhere: Path, actors: Actors
) -> None:
    await preferences.apply(actors.admin, {FOLDER_KEY: str(elsewhere)})
    elsewhere.rmdir()
    assert await backup.unmarked() == []
    assert await backup.recycles() is False


async def test_sifts_own_folder_before_its_first_backup_lists_nothing(
    backup: BackupService,
) -> None:
    assert await backup.unmarked() == []


async def test_a_delete_takes_only_a_name_the_list_shows_and_is_on_history(
    backup: BackupService,
    prepared_db: Database,
    preferences: SettingsService,
    fake_clock: FakeClock,
    elsewhere: Path,
    actors: Actors,
) -> None:
    await preferences.apply(actors.admin, {FOLDER_KEY: str(elsewhere)})
    mine = await backup.library_mark()
    unmarked = elsewhere / "sift-backup-20230101-030000-0.1.150.zip"
    unmarked.write_bytes(b"old")
    ours = elsewhere / filename_for(fake_clock.now(), library=mine)
    ours.write_bytes(b"mine")
    stranger = elsewhere / "holiday.zip"
    stranger.write_bytes(b"not a backup")

    for name in (ours.name, stranger.name, "..", "../" + unmarked.name, "nothing.zip"):
        with pytest.raises(NotThere):
            await backup.delete_unmarked(name, actors.admin)
    assert ours.is_file() and stranger.is_file() and unmarked.is_file()

    assert await backup.delete_unmarked(unmarked.name, actors.admin) is False
    assert not unmarked.exists()
    rows = await prepared_db.fetch_all(
        "SELECT d.actor_kind AS actor_kind, d.actor_id AS actor_id, s.name AS name"
        " FROM workbench_decisions d"
        " JOIN workbench_decision_subjects s ON s.decision_id = d.id WHERE d.verb = 'deleted'"
    )
    assert [tuple(row) for row in rows] == [
        ("user", actors.admin.id, "the backup from 1 January 2023")
    ]


async def test_where_the_drive_has_a_bin_a_delete_goes_to_it(
    backup: BackupService,
    preferences: SettingsService,
    elsewhere: Path,
    actors: Actors,
    no_real_bin: list[Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await preferences.apply(actors.admin, {FOLDER_KEY: str(elsewhere)})
    monkeypatch.setattr(recycle, "has_recycle_bin", lambda _place: True)
    unmarked = elsewhere / "sift-backup-20230101-030000-0.1.150.zip"
    unmarked.write_bytes(b"old")

    assert await backup.recycles() is True
    assert await backup.delete_unmarked(unmarked.name, actors.admin) is True
    assert no_real_bin == [elsewhere.resolve() / unmarked.name]


async def test_a_move_windows_refuses_deletes_nothing(
    backup: BackupService,
    preferences: SettingsService,
    elsewhere: Path,
    actors: Actors,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await preferences.apply(actors.admin, {FOLDER_KEY: str(elsewhere)})
    monkeypatch.setattr(recycle, "has_recycle_bin", lambda _place: True)

    def refuse(_path: Path) -> None:
        raise recycle.NoRecycleBin("no")

    monkeypatch.setattr(recycle, "to_recycle_bin", refuse)
    unmarked = elsewhere / "sift-backup-20230101-030000-0.1.150.zip"
    unmarked.write_bytes(b"old")

    with pytest.raises(NotThere, match="Nothing was deleted"):
        await backup.delete_unmarked(unmarked.name, actors.admin)
    assert unmarked.is_file()


def test_the_age_rule_reads_what_is_stored_as_days() -> None:
    assert keep_days_from(14) == 14
    assert keep_days_from(-3) == 0
    assert keep_days_from(None) == 7
    assert keep_days_from("soon") == 7
