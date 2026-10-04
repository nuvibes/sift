# SPDX-License-Identifier: AGPL-3.0-or-later
"""A real WAL database, the real settings store, and a recorded worker pool: a backup is about a
WAL database and its copy, and a restore stops and starts the workers around the swap."""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

# Importing the application registers every feature's schema and preferences.
import sift.main  # noqa: F401
from sift.kernel.config import Settings
from sift.kernel.db import Database
from sift.slices.backup.service import BackupService
from sift.slices.settings_hub import SettingsService
from sift.testing.fixtures import FakeClock


@dataclass
class RecordedWorkers:
    """The worker pool, recording the two calls a restore makes on it."""

    calls: list[str] = field(default_factory=list)

    async def start(self) -> None:
        self.calls.append("start")

    async def stop(self) -> None:
        self.calls.append("stop")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Settings with every directory under `tmp_path`, beside a media area that exists."""
    (tmp_path / "media").mkdir()
    return Settings(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")


@pytest.fixture
async def prepared_db(temp_db: Database) -> Database:
    """The database with every feature's tables created, which is what an export copies."""
    await temp_db.initialize_schema()
    return temp_db


@pytest.fixture
def preferences(prepared_db: Database) -> SettingsService:
    return SettingsService(prepared_db)


@pytest.fixture
def workers() -> RecordedWorkers:
    return RecordedWorkers()


@pytest.fixture
def backup(
    prepared_db: Database,
    settings: Settings,
    preferences: SettingsService,
    workers: RecordedWorkers,
    fake_clock: FakeClock,
) -> BackupService:
    return BackupService(
        prepared_db,
        settings,
        preferences.get_app,
        preferences.apply,
        workers=workers,
        clock=fake_clock.now,
    )


@pytest.fixture
async def elsewhere(settings: Settings) -> AsyncIterator[Path]:
    """A writable folder inside the media area, which is where a chosen destination has to be."""
    folder = settings.data_dir.parent / "media" / "nas" / "sift-backups"
    folder.mkdir(parents=True)
    yield folder


#: A full path spelled as this machine spells one: the backup folder must be absolute, and a
#: leading slash is relative on Windows.
def a_full_path(*parts: str) -> str:
    """`/one/two` on POSIX, `C:\\one\\two` on Windows."""
    return str(Path(Path(sys.executable).anchor) / Path(*parts))


def database_in(archive: Path) -> Path:
    """The database member of an export, written beside it so a test can read it as a stranger
    with a SQLite client would."""
    import zipfile

    from sift.slices.backup.service import DATABASE_MEMBER

    unpacked = archive.with_name(archive.name + ".member.sqlite3")
    with zipfile.ZipFile(archive) as packed:
        unpacked.write_bytes(packed.read(DATABASE_MEMBER))
    return unpacked
