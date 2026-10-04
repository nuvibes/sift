# SPDX-License-Identifier: AGPL-3.0-or-later
"""Three rules about backups.

A backup saved by hand is never rotated away; the general settings door refuses the same backup
folders the schedule's own door refuses; and one piece of work on the library as a whole runs at a
time: a scheduled backup that comes due during another waits rather than failing.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from sift.kernel.config import Settings
from sift.kernel.content import LibraryStore
from sift.kernel.db import Database
from sift.kernel.jobs import JobQueue, JobState
from sift.kernel.jobs.schedules import when_key
from sift.slices.backup.jobs import BACKUP_RUN, BUSY_RETRY_SECONDS, register_handlers, run_backup
from sift.slices.backup.service import (
    DUPLICATING,
    FOLDER_KEY,
    KEEP_KEY,
    BackupService,
    Busy,
    filename_for,
    is_backup_filename,
)
from sift.slices.settings_hub import SettingsService
from sift.testing.fixtures import Actors, FakeClock

pytestmark = pytest.mark.anyio


@pytest.fixture
async def queue(prepared_db: Database, fake_clock: FakeClock) -> JobQueue:
    return JobQueue(prepared_db, clock=fake_clock.now)


def test_a_backup_saved_by_hand_is_never_one_rotation_may_delete() -> None:
    mine = "0123456789ab"
    saved = filename_for(1_700_000_000.0, library=mine, saved=True)
    assert saved.endswith("-saved.zip")
    assert not is_backup_filename(saved, library=mine)
    assert is_backup_filename(filename_for(1_700_000_000.0, library=mine), library=mine)


async def test_rotation_keeps_a_backup_somebody_saved_into_the_folder(
    backup: BackupService,
    preferences: SettingsService,
    fake_clock: FakeClock,
    elsewhere: Path,
    actors: Actors,
) -> None:
    mine = await backup.library_mark()
    kept = elsewhere / filename_for(fake_clock.now() - 3600, library=mine, saved=True)
    kept.write_bytes(b"a backup somebody saved")

    await preferences.apply(actors.admin, {FOLDER_KEY: str(elsewhere), KEEP_KEY: 1})
    for _ in range(2):
        await backup.run_scheduled()
        fake_clock.advance(24 * 3600)

    assert kept.is_file()
    assert (
        len([one for one in elsewhere.iterdir() if is_backup_filename(one.name, library=mine)]) == 1
    )


async def test_the_settings_door_refuses_what_the_schedule_door_refuses(
    backup: BackupService, prepared_db: Database, settings: Settings, tmp_path: Path
) -> None:
    given = tmp_path / "drive-d"
    library = given / "videos"
    library.mkdir(parents=True)
    store = LibraryStore(prepared_db, settings)
    await store.grant(given)
    await store.create_root(name="Videos", abs_path=library)
    beside = given / "sift-backups"
    beside.mkdir()

    refused = await backup.folder_refusal(str(library))
    assert refused is not None and "part of your library" in refused
    assert await backup.folder_refusal(str(beside)) is None
    assert await backup.folder_refusal("") is None


async def test_a_restore_is_refused_while_the_library_is_being_duplicated(
    backup: BackupService, tmp_path: Path
) -> None:
    async with backup.exclusively(DUPLICATING):
        assert "being duplicated" in (backup.refusal_while_busy() or "")
        with pytest.raises(Busy, match="being duplicated"):
            await backup.restore(tmp_path / "anything.zip")
    assert backup.refusal_while_busy() is None


async def test_a_scheduled_backup_due_while_busy_waits_instead_of_failing(
    backup: BackupService,
    preferences: SettingsService,
    queue: JobQueue,
    fake_clock: FakeClock,
    actors: Actors,
) -> None:
    await preferences.apply(actors.admin, {when_key("backup"): "work"})
    register_handlers(service=backup, queue=queue)

    class Context:
        job = SimpleNamespace(timing="quiet")

        async def set_progress(self, _fraction: float) -> None:
            raise AssertionError("a deferred backup reports nothing")

    async with backup.exclusively(DUPLICATING):
        await run_backup(Context(), service=backup, queue=queue)  # type: ignore[arg-type]

    waiting = await queue.list(job_type=BACKUP_RUN, state=JobState.QUEUED, limit=5)
    assert waiting.total == 1
    assert waiting.jobs[0].run_after == int(fake_clock.now()) + BUSY_RETRY_SECONDS
    # Still the schedule's run, not a press: its timing came with it.
    assert waiting.jobs[0].timing == "quiet"
