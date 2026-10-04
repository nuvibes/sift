# SPDX-License-Identifier: AGPL-3.0-or-later
"""Duplicate this library, and the rule it shares with importing one: a copy starts signed out."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import pytest

from sift.kernel.access import Role, Viewer
from sift.kernel.config import Settings
from sift.kernel.db import DATABASE_FILENAME, Database, adopt_database, fetch_blocking
from sift.kernel.jobs import (
    JobCanceled,
    JobContext,
    JobFailedPermanently,
    JobQueue,
    JobState,
    registered_handlers,
)
from sift.slices.backup import libraries
from sift.slices.backup.libraries import (
    HANDOFF_FILENAME,
    LIBRARY_DUPLICATE,
    LibrariesService,
    NameTaken,
    NoRoom,
    register_library_handlers,
)
from sift.slices.backup.service import RESTORING, BackupService, Busy

pytestmark = pytest.mark.anyio

MAKER = Viewer(id="maker-1", role=Role.ADMIN)


@dataclass
class Ran:
    """A job context holding a payload and recording what the handler reports."""

    payload: dict[str, Any]
    progress: list[float] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    stop: str | None = None

    def stopping(self) -> str | None:
        return self.stop

    async def report_progress(self, fraction: float) -> None:
        self.progress.append(fraction)

    async def set_progress(self, fraction: float) -> None:
        self.progress.append(fraction)

    async def set_note(self, note: str) -> None:
        self.notes.append(note)


@dataclass
class Supervisor:
    asked: list[bool] = field(default_factory=list)

    def can(self) -> bool:
        return True

    def ask(self) -> bool:
        self.asked.append(True)
        return True


@pytest.fixture
def supervisor() -> Supervisor:
    return Supervisor()


@pytest.fixture
async def service(
    prepared_db: Database, settings: Settings, backup: BackupService, supervisor: Supervisor
) -> LibrariesService:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    await prepared_db.execute(
        "INSERT INTO users (id, username, password_hash, role, created_at) "
        "VALUES (?, 'ada', 'hash-of-a-password', 'admin', 1)",
        (MAKER.id,),
    )
    await prepared_db.execute(
        "INSERT INTO sessions (id, user_id, token_hash, created_at, last_seen_at, expires_at) "
        "VALUES ('s1', ?, 'token-hash', 1, 1, 4102444800)",
        (MAKER.id,),
    )
    return LibrariesService(
        prepared_db, settings, backup, can_restart=supervisor.can, ask_to_restart=supervisor.ask
    )


@pytest.fixture
async def queue(prepared_db: Database) -> JobQueue:
    return JobQueue(prepared_db)


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


@pytest.fixture
def made_things(settings: Settings) -> None:
    """One of each kind of folder a library holds beside its database."""
    data, cache = settings.data_dir, settings.cache_dir
    _write(data / "faces" / "references" / "confirmed.jpg", b"confirmed")
    _write(data / "faces" / "detected" / "found.jpg", b"found")
    _write(data / "faces" / "models" / "left-behind.onnx", b"model")
    _write(cache / "covers" / "uploaded.jpg", b"cover")
    _write(cache / "AB" / "thumb.webp", b"thumbnail")
    _write(cache / "transcode" / "segment.ts", b"segment")
    _write(cache / "incoming" / "arriving.part", b"arriving")
    _write(cache / "sift-download-x1" / "piece", b"piece")
    _write(data / "backups" / "sift-backup-20260101-000000-1.0.0.zip", b"backup")


async def _duplicate(service: LibrariesService, name: str, *, pictures: bool) -> Ran:
    ran = Ran({"name": name, "pictures": pictures})
    await service.run_duplicate(cast(JobContext, ran))
    return ran


def _count(database: Path, sql: str) -> int:
    return int(str(fetch_blocking(database, sql)[0][0]))


@pytest.mark.usefixtures("made_things")
async def test_a_duplicate_carries_the_records_starts_signed_out_and_does_no_work(
    service: LibrariesService,
    queue: JobQueue,
    supervisor: Supervisor,
    settings: Settings,
    prepared_db: Database,
) -> None:
    await queue.enqueue("some_pass", {}, require_handler=False)

    ran = await _duplicate(service, "Copy", pictures=False)

    root = service.folder / "Copy"
    database = root / "data" / DATABASE_FILENAME
    assert _count(database, "SELECT COUNT(*) FROM users") == 1
    assert _count(database, "SELECT COUNT(*) FROM sessions") == 0
    assert _count(database, "SELECT COUNT(*) FROM jobs WHERE state = 'queued'") == 0
    assert _count(database, "SELECT COUNT(*) FROM jobs WHERE state = 'canceled'") == 1
    assert (root / "data" / "faces" / "references" / "confirmed.jpg").read_bytes() == b"confirmed"
    assert (root / "cache" / "covers" / "uploaded.jpg").is_file()
    # Without the pictures: nothing a scan makes again.
    assert not (root / "cache" / "AB").exists()
    assert not (root / "data" / "faces" / "detected").exists()
    # The original is untouched, and nobody was switched anywhere.
    assert _count(prepared_db.path, "SELECT COUNT(*) FROM sessions") == 1
    assert supervisor.asked == []
    assert not (settings.data_dir / HANDOFF_FILENAME).exists()
    assert ran.progress[-1] == 1.0
    assert ran.notes == ["Copy is ready. Open it from the list whenever you want."]
    listed = await service.listed()
    assert [one["name"] for one in listed] == [listed[0]["name"], "Copy"]


@pytest.mark.usefixtures("made_things")
async def test_the_pictures_come_along_when_asked_and_work_in_flight_never_does(
    service: LibrariesService,
) -> None:
    await _duplicate(service, "Whole", pictures=True)

    root = service.folder / "Whole"
    assert (root / "cache" / "AB" / "thumb.webp").read_bytes() == b"thumbnail"
    assert (root / "data" / "faces" / "detected" / "found.jpg").is_file()
    for never in (
        root / "cache" / "transcode",
        root / "cache" / "incoming",
        root / "cache" / "sift-download-x1",
        root / "data" / "faces" / "models",
        root / "data" / "backups",
    ):
        assert not never.exists(), never


@pytest.mark.usefixtures("made_things")
async def test_the_plan_measures_the_pictures_apart_from_the_records(
    service: LibrariesService,
) -> None:
    plan = await service.duplicate_plan()
    assert plan["pictures_bytes"] == len(b"thumbnail") + len(b"found")
    assert plan["records_bytes"] > len(b"confirmed") + len(b"cover")
    assert plan["refusal"] is None
    assert plan["folder"] == str(service.folder)


async def test_a_taken_name_is_refused_before_anything_is_queued(
    service: LibrariesService, queue: JobQueue
) -> None:
    (service.folder / "Work" / "data").mkdir(parents=True)
    with pytest.raises(NameTaken):
        await service.ask_to_duplicate("work", pictures=True, actor=MAKER, queue=queue)
    assert (await queue.list(job_type=LIBRARY_DUPLICATE, limit=5)).total == 0


async def test_a_duplicate_is_refused_while_other_library_work_runs(
    service: LibrariesService, backup: BackupService, queue: JobQueue
) -> None:
    async with backup.exclusively(RESTORING):
        with pytest.raises(Busy, match="being restored"):
            await service.ask_to_duplicate("Copy", pictures=True, actor=MAKER, queue=queue)
        with pytest.raises(JobFailedPermanently, match="being restored"):
            await _duplicate(service, "Copy", pictures=True)
    assert not (service.folder / "Copy").exists()


async def test_a_second_duplicate_waits_for_the_first(
    service: LibrariesService, queue: JobQueue
) -> None:
    register_library_handlers(service)
    await service.ask_to_duplicate("One", pictures=False, actor=MAKER, queue=queue)
    with pytest.raises(Busy, match="already being duplicated"):
        await service.ask_to_duplicate("Two", pictures=False, actor=MAKER, queue=queue)
    queued = await queue.list(job_type=LIBRARY_DUPLICATE, state=JobState.QUEUED, limit=5)
    assert queued.total == 1
    assert queued.jobs[0].payload == {"name": "One", "pictures": False}


async def test_the_handler_the_boot_registers_runs_the_duplicate(
    service: LibrariesService, clean_handlers: None
) -> None:
    register_library_handlers(service)
    ran = Ran({"name": "Handled", "pictures": False})
    await registered_handlers()[LIBRARY_DUPLICATE](cast(JobContext, ran))
    assert (service.folder / "Handled").is_dir()


async def test_a_drive_without_room_is_refused(
    service: LibrariesService, queue: JobQueue, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(libraries, "_free_bytes", lambda _place: 0)
    with pytest.raises(NoRoom, match="not enough free space"):
        await service.ask_to_duplicate("Copy", pictures=False, actor=MAKER, queue=queue)
    with pytest.raises(JobFailedPermanently, match="not enough free space"):
        await _duplicate(service, "Copy", pictures=False)
    assert not (service.folder / "Copy").exists()


@pytest.mark.usefixtures("made_things")
async def test_a_copy_that_fails_part_way_leaves_no_library_behind(
    service: LibrariesService, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(_files: list[tuple[Path, Path]]) -> int:
        raise OSError("the drive went away")

    monkeypatch.setattr(libraries, "_copy_files", broken)
    with pytest.raises(OSError, match="went away"):
        await _duplicate(service, "Copy", pictures=True)
    assert not (service.folder / "Copy").exists()


async def test_a_cancel_stops_the_copy_and_leaves_no_library_behind(
    service: LibrariesService, settings: Settings
) -> None:
    _write(settings.cache_dir / "covers" / "uploaded.jpg", b"cover")
    ran = Ran({"name": "Copy", "pictures": True}, stop="cancel")
    with pytest.raises(JobCanceled):
        await service.run_duplicate(cast(JobContext, ran))
    assert not (service.folder / "Copy").exists()


async def test_an_imported_backup_starts_signed_out(
    service: LibrariesService, backup: BackupService, tmp_path: Path
) -> None:
    """A backup carries the sign-ins live when it was taken; the library made from it must not."""
    exported = tmp_path / "exported.zip"
    await backup.export_to(exported)
    await service.import_file(exported, "Restored")

    database = service.folder / "Restored" / "data" / DATABASE_FILENAME
    assert _count(database, "SELECT COUNT(*) FROM sessions") == 0
    assert _count(database, "SELECT COUNT(*) FROM users") == 1


async def test_an_imported_database_file_starts_signed_out(
    service: LibrariesService, prepared_db: Database, tmp_path: Path
) -> None:
    given = tmp_path / "given"
    given.mkdir()
    source = adopt_database(prepared_db.path, given)
    await service.import_file(source, "Given")

    database = service.folder / "Given" / "data" / DATABASE_FILENAME
    assert _count(database, "SELECT COUNT(*) FROM sessions") == 0
    assert _count(source, "SELECT COUNT(*) FROM sessions") == 1
