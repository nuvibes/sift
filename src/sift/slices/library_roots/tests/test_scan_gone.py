# SPDX-License-Identifier: AGPL-3.0-or-later
"""A folder taken away while it is scanned: one finding for the folder, its files left as they were."""

from __future__ import annotations

import os
import shutil
import zipfile
from collections import Counter
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from sift.kernel.archives import inspect as inspect_archive
from sift.kernel.config import Settings
from sift.kernel.content import LibraryStore, Root
from sift.kernel.db import Database
from sift.kernel.ingress import IngressRejected, IngressResult, Reason, verify_ingress
from sift.kernel.jobs import JobContext, JobQueue, SystemCapabilities
from sift.slices.library_roots import jobs, sweeping, taking_in, walking
from sift.slices.library_roots.service import LibraryService
from sift.slices.library_roots.tests.conftest import WINDOWS_ONLY, RecordingReindexer
from sift.testing.library import a_png

pytestmark = pytest.mark.usefixtures("handlers")

Context = Callable[[str, dict[str, object]], Awaitable[JobContext]]

#: 8,614 files with 770 read before the folder went, scaled down.
FILES, READ_FIRST = 100, 9


def _pictures(directory: Path, count: int, *, prefix: str = "p") -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for index in range(count):
        (directory / f"{prefix}{index:03}.png").write_bytes(a_png(index + 1, 8))


def _gate_that_moves(
    monkeypatch: pytest.MonkeyPatch,
    folder: Path,
    away: Path,
    *,
    after: int,
    once_passed: bool = False,
) -> Counter[str]:
    """The real gate, with `folder` moved away on read `after + 1`: before it, or once it passed."""
    real = verify_ingress
    seen: Counter[str] = Counter()

    def gate(path: Path, **options: object) -> IngressResult:
        seen["reads"] += 1
        moving = seen["reads"] == after + 1
        if moving and not once_passed:
            shutil.move(folder, away)
        try:
            result = real(path, **options)  # type: ignore[arg-type]
        except IngressRejected as refused:
            seen[str(refused.reason)] += 1
            raise
        if moving and once_passed:
            shutil.move(folder, away)
        return result

    monkeypatch.setattr(taking_in, "verify_ingress", gate)
    return seen


async def _scan(
    context_for: Context,
    root: Root,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
) -> JobContext:
    context = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(context, settings=settings, service=service, reindexer=reindexer)
    return context


async def _statuses(temp_db: Database) -> dict[str, int]:
    rows = await temp_db.fetch_all(
        "SELECT status, COUNT(*) AS c FROM asset_locations GROUP BY status"
    )
    return {str(row["status"]): int(row["c"]) for row in rows}


async def _refusals(temp_db: Database) -> int:
    rows = await temp_db.fetch_all("SELECT COUNT(*) AS c FROM scan_rejections")
    return int(rows[0]["c"])


async def _note(job_queue: JobQueue, context: JobContext) -> str | None:
    row = await job_queue.get(context.job.id)
    assert row is not None
    return row.note


async def test_a_library_folder_taken_away_part_way_ends_the_scan_with_one_finding(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    temp_db: Database,
) -> None:
    _pictures(root_path, FILES)
    away = tmp_path / "moved"
    seen = _gate_that_moves(monkeypatch, root_path, away, after=READ_FIRST)

    with pytest.raises(walking.FolderStoppedAnswering) as ended:
        await _scan(context_for, root, settings, service, reindexer)

    assert str(ended.value) == jobs.STOPPED_ANSWERING
    assert seen[str(Reason.UNREADABLE)] == 1, "only the read that found it gone, not every file"
    assert seen["reads"] == READ_FIRST + 1
    assert await _statuses(temp_db) == {"present": READ_FIRST}
    assert await _refusals(temp_db) == 0

    shutil.move(away, root_path)
    monkeypatch.undo()
    await _scan(context_for, root, settings, service, reindexer)
    assert await _statuses(temp_db) == {"present": FILES}
    assert await _refusals(temp_db) == 0


async def test_a_folder_inside_taken_away_part_way_is_said_once_and_left_unjudged(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    temp_db: Database,
    job_queue: JobQueue,
) -> None:
    _pictures(root_path, 5, prefix="top")
    _pictures(root_path / "shoot" / "day", 1, prefix="old")
    await _scan(context_for, root, settings, service, reindexer)
    (root_path / "shoot" / "day" / "old000.png").unlink()
    _pictures(root_path / "shoot" / "day", FILES)
    seen = _gate_that_moves(monkeypatch, root_path / "shoot", tmp_path / "moved", after=READ_FIRST)

    context = await _scan(context_for, root, settings, service, reindexer)

    assert await _note(job_queue, context) == (
        "1 folder stopped answering partway through, so nothing in it was marked missing or"
        " unreadable."
    )
    assert seen[str(Reason.UNREADABLE)] == 1
    assert await _statuses(temp_db) == {"present": 6 + READ_FIRST}, "the old file is not judged"
    folder = await temp_db.fetch_one(
        "SELECT seen_mtime FROM folders WHERE rel_path = ?", ("shoot/day",)
    )
    assert folder is not None and folder["seen_mtime"] is None, "looked at again at the next start"


async def test_a_folder_gone_between_the_gate_and_the_read_is_one_finding(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    temp_db: Database,
    job_queue: JobQueue,
) -> None:
    _pictures(root_path / "shoot", FILES)
    seen = _gate_that_moves(
        monkeypatch, root_path / "shoot", tmp_path / "moved", after=READ_FIRST, once_passed=True
    )

    context = await _scan(context_for, root, settings, service, reindexer)

    assert await _note(job_queue, context) == jobs._went_quiet(1)
    assert seen[str(Reason.UNREADABLE)] == 0, "found at the read, so no later file is opened"
    assert await _statuses(temp_db) == {"present": READ_FIRST}


async def test_one_file_gone_before_its_read_is_that_file_only(
    monkeypatch: pytest.MonkeyPatch,
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    temp_db: Database,
    job_queue: JobQueue,
) -> None:
    _pictures(root_path, 10)
    real = verify_ingress

    def gate(path: Path, **options: object) -> IngressResult:
        if path.name == "p004.png":
            path.unlink()
        return real(path, **options)  # type: ignore[arg-type]

    monkeypatch.setattr(taking_in, "verify_ingress", gate)
    context = await _scan(context_for, root, settings, service, reindexer)

    assert await _note(job_queue, context) is None
    assert await _statuses(temp_db) == {"present": 9}


async def test_an_archive_whose_folder_went_is_neither_refused_nor_swept(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    temp_db: Database,
) -> None:
    sets = root_path / "sets"
    sets.mkdir()
    with zipfile.ZipFile(sets / "shoot.zip", "w") as writing:
        for index in range(3):
            writing.writestr(f"{index}.png", a_png(index + 2, 8))
    await _scan(context_for, root, settings, service, reindexer)
    real = inspect_archive

    def inspect(path: Path, **options: object) -> object:
        shutil.move(sets, tmp_path / "moved")
        return real(path, **options)  # type: ignore[arg-type]

    monkeypatch.setattr(taking_in, "inspect_archive", inspect)
    await _scan(context_for, root, settings, service, reindexer)

    assert await _refusals(temp_db) == 0, "a folder that went is not a broken archive"
    assert await _statuses(temp_db) == {"present": 3}


async def test_a_folder_gone_before_its_own_listing_keeps_its_row_and_its_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    temp_db: Database,
) -> None:
    _pictures(root_path / "sub", 5)
    await _scan(context_for, root, settings, service, reindexer)
    real = os.scandir

    def scandir(directory: Path) -> object:
        if Path(directory).name == "sub":
            shutil.move(root_path / "sub", tmp_path / "moved")
        return real(directory)

    monkeypatch.setattr(os, "scandir", scandir)
    await _scan(context_for, root, settings, service, reindexer)

    assert await _statuses(temp_db) == {"present": 5}
    folder = await temp_db.fetch_one("SELECT seen_mtime FROM folders WHERE rel_path = 'sub'")
    assert folder is not None and folder["seen_mtime"] is None


def test_the_folder_that_stopped_answering_is_the_highest_one_that_did(tmp_path: Path) -> None:
    base = tmp_path / "base"
    (base / "a" / "b").mkdir(parents=True)
    shutil.move(base / "a", tmp_path / "away")
    assert walking._quiet_from(base, base / "a" / "b") == base / "a"
    shutil.move(base, tmp_path / "gone")
    assert walking._quiet_from(base, base / "a" / "b") == base
    assert walking._quiet_from(base, tmp_path / "elsewhere") == tmp_path / "elsewhere"


@WINDOWS_ONLY
async def test_an_archive_on_a_share_that_went_silent_is_not_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class _Silent:
        def __init__(self, *_args: object) -> None:
            raise FileNotFoundError(2, "the share did not answer", "x", 53)

    class _Content:
        async def container_path_of(self, _location: object) -> Path:
            return tmp_path / "shoot.zip"

    class _Rows:
        content = _Content()

    monkeypatch.setattr(zipfile, "ZipFile", _Silent)
    location = type("Location", (), {"member_path": "1.png"})()
    assert await sweeping._member_still_there(_Rows(), location, tmp_path)  # type: ignore[arg-type]


async def _claimed(
    job_queue: JobQueue,
    capabilities: SystemCapabilities,
    payload: dict[str, object],
    pressed_by: str | None,
) -> JobContext:
    job_id = await job_queue.enqueue(jobs.SCAN, payload, requested_by=pressed_by)
    while (job := await job_queue.claim("worker")) is not None and job.id != job_id:
        pass
    assert job is not None
    return JobContext(job=job, worker_id="worker", queue=job_queue, capabilities=capabilities)


@pytest.mark.parametrize("named", [True, False], ids=["named files", "the folder"])
async def test_a_folder_inside_going_never_ends_a_scan_nobody_pressed(
    named: bool,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    temp_db: Database,
    job_queue: JobQueue,
    capabilities: SystemCapabilities,
    library_store: LibraryStore,
) -> None:
    batch = root_path / "outer" / "batch"
    _pictures(batch, 20)
    folder = await library_store.upsert_folder(root.id, "outer/batch")
    payload: dict[str, object] = jobs.scan_shape(root.id, folder)
    if named:
        payload["paths"] = [f"outer/batch/p{index:03}.png" for index in range(20)]
    seen = _gate_that_moves(monkeypatch, batch, tmp_path / "moved", after=READ_FIRST)
    context = await _claimed(job_queue, capabilities, payload, None)

    await jobs.scan(context, settings=settings, service=service, reindexer=reindexer)

    assert await _note(job_queue, context) == jobs._went_quiet(1)
    assert seen[str(Reason.UNREADABLE)] == 1
    assert await _statuses(temp_db) == {"present": READ_FIRST}
    assert context.units == READ_FIRST, "History counts the files read, not the files meant"


async def test_a_scan_ends_for_the_folder_pressed_and_for_the_library_folder(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    job_queue: JobQueue,
    capabilities: SystemCapabilities,
    library_store: LibraryStore,
) -> None:
    _pictures(root_path / "pressed", 20)
    folder = await library_store.upsert_folder(root.id, "pressed")
    _gate_that_moves(monkeypatch, root_path / "pressed", tmp_path / "moved", after=READ_FIRST)
    pressed = await _claimed(job_queue, capabilities, jobs.scan_shape(root.id, folder), "someone")
    with pytest.raises(walking.FolderStoppedAnswering):
        await jobs.scan(pressed, settings=settings, service=service, reindexer=reindexer)
    assert pressed.units == READ_FIRST

    monkeypatch.undo()
    _pictures(root_path / "watched", 20)
    named = jobs.scan_shape(root.id, await library_store.upsert_folder(root.id, "watched"))
    named["paths"] = [f"watched/p{index:03}.png" for index in range(20)]
    _gate_that_moves(monkeypatch, root_path, tmp_path / "root-moved", after=READ_FIRST)
    watched = await _claimed(job_queue, capabilities, named, None)
    with pytest.raises(walking.FolderStoppedAnswering):
        await jobs.scan(watched, settings=settings, service=service, reindexer=reindexer)
