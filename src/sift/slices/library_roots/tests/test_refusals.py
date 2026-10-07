# SPDX-License-Identifier: AGPL-3.0-or-later
"""A scan's refusal memory: a file the gate turned away is not read again until it changes."""

from __future__ import annotations

import errno
import os
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from sift.kernel.config import Settings
from sift.kernel.content import Root
from sift.kernel.db import Database, Row
from sift.kernel.ingress import verify_ingress
from sift.kernel.jobs import (
    JobQueue,
    register_handler,
)
from sift.slices.library_roots import jobs, sweeping, taking_in
from sift.slices.library_roots.service import LibraryService
from sift.slices.library_roots.tests.conftest import (
    VIDEO_SECONDS,
    RecordingReindexer,
    draw,
    refused_file,
)
from sift.slices.library_roots.tests.test_jobs import (
    _SETTLES_INTO,
    Context,
    _nothing,
    assets_in,
    finished,
    scans_asked_for,
)

# --- the refusal memory ----------------------------------------------------------------------


async def _remembered(service: LibraryService, root_id: str) -> list[Row]:
    """What the root is refusing, read as the Skipped screen's store reads it."""
    return await service._db.fetch_all(
        "SELECT * FROM scan_rejections WHERE root_id = ? ORDER BY rel_path LIMIT 10", (root_id,)
    )


async def test_a_refused_file_is_never_opened_a_second_time(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    monkeypatch: pytest.MonkeyPatch,
    reindexer: RecordingReindexer,
) -> None:
    """The scanner remembers, because the file it refused is still there next time.

    Sift leaves a refused file exactly where it is, so a scanner with no memory finds it, refuses
    it and logs it again on every pass, forever: a log full of one refusal, and a watched folder
    that never goes quiet.

    What is counted is calls to the real gate, not rows in the table. The table has one row per
    path whether or not the memory is consulted, because the write is an upsert, so counting rows
    proves the upsert is an upsert and says nothing at all about whether the file was read again.
    The gate is wrapped, not replaced: it still runs, still refuses, and still decides.
    """
    refused_file(root_path / "not-really.mp4")

    opened = 0
    real_gate = verify_ingress

    def counting_gate(*args: object, **kwargs: object) -> object:
        nonlocal opened
        opened += 1
        return real_gate(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(taking_in, "verify_ingress", counting_gate)

    first = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(first, settings=settings, service=service, reindexer=reindexer)

    assert opened == 1
    remembered = await _remembered(service, root.id)
    assert len(remembered) == 1
    assert remembered[0]["reason"]

    second = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(second, settings=settings, service=service, reindexer=reindexer)

    assert opened == 1, "the second pass must not read a file it has already refused"
    assert len(await _remembered(service, root.id)) == 1
    # And the same fact as one number across every folder, which is what the Skipped Files card on
    # the Organize board draws. Its own statement rather than a listing per folder counted: asking
    # each root for its rows to count them is a read per library folder for a number a COUNT gives.
    assert await service.rejection_count() == 1


async def test_a_refused_file_that_changes_is_looked_at_again(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    temp_db: Database,
    reindexer: RecordingReindexer,
) -> None:
    """The memory is about the bytes that were there, not about the path.

    Somebody who replaces a broken file with a good one at the same name expects it to appear. A
    memory keyed on the path alone would keep it out forever.
    """
    path = refused_file(root_path / "clip.mp4")
    first = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(first, settings=settings, service=service, reindexer=reindexer)
    assert await assets_in(temp_db) == 0

    path.unlink()
    draw(path, "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)

    second = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(second, settings=settings, service=service, reindexer=reindexer)

    assert await assets_in(temp_db) == 1, "a real file at a refused path is still a real file"
    assert await _remembered(service, root.id) == [], "and the refusal is stale"


async def test_the_catch_up_does_not_offer_a_refused_file_again(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    job_queue: JobQueue,
    reindexer: RecordingReindexer,
) -> None:
    """A refused file is one its folder is known to hold. A catch-up at start that compared a
    changed folder's listing with the files Sift took in and nothing else would read an unchanged
    refused file as one that had just arrived and name it for a scan after every change to its
    folder, for ever. The file that really arrived beside it is still named."""
    draw(root_path / "clips" / "one.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)
    refused_file(root_path / "clips" / "not-really.mp4")
    first = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(first, settings=settings, service=service, reindexer=reindexer)
    await finished(first)
    assert len(await _remembered(service, root.id)) == 1

    shutil.copy(root_path / "clips" / "one.mp4", root_path / "clips" / "arrived.mp4")
    catching_up = await context_for(jobs.RECONCILE, {"root_id": root.id})
    await jobs.reconcile(catching_up, service=service)

    named: set[str] = set()
    for payload in await scans_asked_for(job_queue):
        carried = payload.get("paths", [])
        assert isinstance(carried, list)
        named.update(str(one) for one in carried)
    assert named == {"clips/arrived.mp4"}, "only the file that arrived is named"


async def test_a_refused_file_that_is_deleted_is_forgotten(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
) -> None:
    """The refusal of a file somebody deleted must go, or it stays in the count of files the
    scanner walks past and the catch-up keeps looking for it. A scan that looks and finds it gone
    forgets it, and a scan of named files forgets only what it was handed."""
    gone = refused_file(root_path / "clips" / "not-really.mp4")
    first = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(first, settings=settings, service=service, reindexer=reindexer)
    assert await service.rejection_count() == 1
    gone.unlink()

    elsewhere = await context_for(jobs.SCAN, {"root_id": root.id, "paths": ["clips/other.mp4"]})
    await jobs.scan(elsewhere, settings=settings, service=service, reindexer=reindexer)
    assert await service.rejection_count() == 1, "a scan handed another path concluded nothing"

    again = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(again, settings=settings, service=service, reindexer=reindexer)
    assert await service.rejection_count() == 0, "the refusal of a file that has gone is forgotten"


async def test_a_refused_file_the_walk_did_not_list_but_is_still_there_is_not_forgotten(
    root_path: Path, service: LibraryService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only a definite "not there" forgets a refusal: a file the walk missed but that answers a
    look keeps its refusal, and nothing is forgotten."""
    refused_file(root_path / "clips" / "still-here.mp4")
    forgets: list[str] = []

    async def counting_forget(*, root_id: str, rel_path: str) -> None:
        forgets.append(rel_path)

    monkeypatch.setattr(service, "forget_rejection", counting_forget)

    await sweeping._forget_gone_refusals(
        service,
        root_id="01R",
        root_abs=root_path,
        under="",
        refused={"clips/still-here.mp4": (1, 1)},
        walked=set(),
        only=None,
    )

    assert forgets == []


def test_only_a_definite_absence_counts_as_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refused, escaping the root, or there: none of those is "gone"."""
    root = tmp_path / "root"
    root.mkdir()
    (root / "here.mp4").write_bytes(b"x")
    (root / "locked.mp4").write_bytes(b"x")
    real_stat = os.stat

    def stat(path: Any, *args: Any, **kwargs: Any) -> Any:
        if Path(path).name == "locked.mp4":
            raise PermissionError(errno.EACCES, "access is denied", str(path))
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(sweeping, "os", SimpleNamespace(stat=stat))

    gone = sweeping._definitely_gone(
        root, ["here.mp4", "locked.mp4", "../outside.mp4", "never-was.mp4"]
    )

    assert gone == ["never-was.mp4"]


async def test_a_whole_library_pass_that_is_switched_off_is_not_asked_for_and_the_scan_finishes(
    context_for: Context,
    root: Root,
    settings: Settings,
    service: LibraryService,
    job_queue: JobQueue,
    reindexer: RecordingReindexer,
) -> None:
    """Asking is not the scan's decision to argue with: a pass somebody turned off is skipped,
    and the scan still completes rather than failing over it."""
    from sift.kernel.jobs.switchboard import Switch

    register_handler(_SETTLES_INTO, _nothing, name="Test job")

    async def off() -> bool:
        return False

    job_queue.switchboard.declare(
        Switch(key="test.off", refusal="switched off", on=off), _SETTLES_INTO
    )

    context = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(
        context,
        settings=settings,
        service=service,
        reindexer=reindexer,
        settles_into=(_SETTLES_INTO,),
    )

    assert (await job_queue.list(job_type=_SETTLES_INTO, limit=10)).total == 0
